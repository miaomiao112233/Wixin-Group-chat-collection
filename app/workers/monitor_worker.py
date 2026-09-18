# -*- coding: utf-8 -*-
"""采集 Worker：60 秒增量轮询 + 自适应总结调度（工作线程）。"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from app.config import (DAILY_FORCE_TIME, OUTPUT_DIR, POLL_INTERVAL_SEC,
                        get_auto_archive)
from app.core.archiver import Archiver
from app.core.interval_ctl import IntervalController
from app.core.message_poller import MessagePoller
from app.state_store import StateStore

log = logging.getLogger("app.schedule")

# 总结调度检查周期：每分钟按绝对时刻校验（不依赖一次性长定时器，
# 系统睡眠唤醒/事件循环短暂阻塞后，最迟下一分钟也能补发触发）
SCHEDULE_CHECK_SEC = 60


def _is_wechat_running() -> bool:
    """微信 4.x 进程名通常为 Weixin.exe（兼容旧版 WeChat.exe）。

    tasklist 在中文系统输出为 GBK，故用字节模式匹配避免解码异常。
    """
    import subprocess
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=10,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        ).stdout.lower()
        return b"weixin.exe" in out or b"wechat.exe" in out
    except Exception:
        return True   # 探测本身失败时不误报，交给库报错


class MonitorWorker(QObject):
    log = Signal(str)
    status = Signal(dict)            # {chatroom: {name, new_count, last_msg}}
    next_summary_at = Signal(str)    # "HH:MM" 或 "明天 HH:MM"
    ask_summary = Signal(str, str)   # chatroom_id, group_name
    connect_failed = Signal(str)     # 连接微信失败原因（UI 弹窗引导）
    connected = Signal()             # 连接成功（UI 恢复状态）
    request_poll = Signal()          # UI 添加群后请求立即轮询一次

    def __init__(self, store: StateStore):
        super().__init__()
        self._store = store
        self._db = None
        self._poller: MessagePoller | None = None
        self._archiver: Archiver | None = None
        self._ctl = IntervalController()
        self._poll_timer: QTimer | None = None
        self._summary_timer: QTimer | None = None
        self._next_summary_ts: float = 0.0   # 下一次总结的绝对时间戳
        self._next_is_daily: bool = False    # 下一次是否为每日强制点
        self._daily_done_date: str = ""
        # 今日消息计数（增量维护，避免每轮全量扫描大群）：
        # {chatroom: {"date": "YYYY-MM-DD", "count": int}}
        self._today_stat: dict[str, dict] = {}

    # ---------- 生命周期 ----------
    @Slot()
    def start_work(self):
        self._connect()

    def _connect(self) -> bool:
        """连接微信数据库并启动定时器；失败发信号由 UI 引导。"""
        try:
            from app.core.db_factory import open_wechat_db
            self.log.emit("正在连接微信数据库…")
            self._db = open_wechat_db("monitor")
            self._poller = MessagePoller(self._db)
            self._archiver = Archiver(self._db, self._store)
            self.log.emit("微信数据库连接成功")
        except Exception as e:                          # noqa: BLE001
            self._db = self._poller = self._archiver = None
            hint = self._build_failure_hint(e)
            self.log.emit(f"[错误] {hint}")
            self.connect_failed.emit(hint)
            return False

        if self._poll_timer is None:
            self._poll_timer = QTimer(self)
            self._poll_timer.setInterval(POLL_INTERVAL_SEC * 1000)
            self._poll_timer.timeout.connect(self.poll_tick)
            self._summary_timer = QTimer(self)
            self._summary_timer.setInterval(SCHEDULE_CHECK_SEC * 1000)
            self._summary_timer.timeout.connect(self._schedule_tick)
            # UI 线程 emit 后排队到本线程立即执行一次（不等 60 秒定时器）
            self.request_poll.connect(self.poll_tick)
        self._poll_timer.start()
        self._summary_timer.start()
        self.connected.emit()
        # 必须在 connected 之后发射：UI 的 connected 槽不应再覆盖状态栏，
        # 最终文字由这里的 next_summary_at 决定
        self._reschedule()
        self.poll_tick()
        return True

    @staticmethod
    def _build_failure_hint(e: Exception) -> str:
        msg = str(e)
        if not _is_wechat_running():
            return ("未检测到微信运行：请先登录微信 PC 版（4.0 以上版本），"
                    "登录后点「重试连接」。")
        if "未找到微信数据库目录" in msg:
            return ("已登录微信但找不到数据目录，可能安装位置较特殊。"
                    "请在「设置」中手动选择微信数据目录"
                    "（通常名为 xwechat_files 的文件夹）。")
        return f"连接微信失败：{msg}。请确认微信已登录后重试。"

    @Slot()
    def reconnect(self) -> bool:
        """手动指定目录/登录微信后，由 UI 触发重连。"""
        if self._poll_timer:
            self._poll_timer.stop()
        if self._summary_timer:
            self._summary_timer.stop()
        return self._connect()

    def stop(self):
        if self._poll_timer:
            self._poll_timer.stop()
        if self._summary_timer:
            self._summary_timer.stop()

    # ---------- 采集 ----------
    @Slot()
    def poll_tick(self):
        if self._poller is None:
            return
        status_out = {}
        total_new = 0
        today = f"{datetime.now():%Y-%m-%d}"
        for g in self._store.list_groups(enabled_only=True):
            chatroom, name = g["chatroom_id"], g["group_name"]
            try:
                cursor = self._store.get_cursor(chatroom)
                # 新群（游标 -1）：后台定位当天起点，首轮即拿到当天消息
                is_new = cursor < 0
                if is_new:
                    cursor = self._poller.day_start_seq(chatroom)
                    self._store.set_cursor(chatroom, cursor)
                    self.log.emit(f"[{name}] 新群监控已就绪（从今天开始）")
                msgs = self._poller.fetch_new(chatroom, cursor)
                if msgs:
                    self._store.advance_cursor(chatroom, msgs[-1].sort_seq)
                    # 新文件即时归档（用户可在设置中整体关闭）
                    if get_auto_archive():
                        archived = self._archiver.archive_messages(
                            msgs, OUTPUT_DIR, name,
                            since_date=g.get("monitor_start_date"))
                        if archived:
                            self.log.emit(
                                f"[{name}] 归档 {len(archived)} 个文件: "
                                + ", ".join(f"{f.orig_name}({f.status})"
                                            for f in archived))
                    preview = (f"{msgs[0].sender_name}: "
                               f"{msgs[0].content[:24]}")
                else:
                    preview = ""

                today_new = sum(
                    1 for m in msgs if f"{m.create_time:%Y-%m-%d}" == today)
                st = self._today_stat.get(chatroom)
                if is_new:
                    # 游标定位在当天起点，本批即今天全部消息（批次上限内）
                    today_count = today_new
                    self._today_stat[chatroom] = {"date": today,
                                                  "count": today_count}
                elif st is None or st["date"] != today:
                    # 重启后首轮 / 跨天：全量基准一次
                    today_count = self._poller.count_today(chatroom)
                    self._today_stat[chatroom] = {"date": today,
                                                  "count": today_count}
                else:
                    # 常规轮：纯增量累加，不再全量扫描
                    st["count"] += today_new
                    today_count = st["count"]

                status_out[chatroom] = {"name": name,
                                        "today_count": today_count,
                                        "new_count": len(msgs),
                                        "last_msg": preview}
                total_new += len(msgs)
            except Exception as e:                      # noqa: BLE001
                self.log.emit(f"[错误] 采集 {name} 失败: {e}")
        # 全部群算完后只反馈一次，避免多群时间隔被重复放大
        self._ctl.on_round(total_new)
        if status_out:
            self.status.emit(status_out)

    # ---------- 总结调度（绝对时刻，每分钟校验，抗睡眠/阻塞）----------
    def _reschedule(self, from_ts: float | None = None):
        """按当前自适应间隔排下一次总结，并与每日强制点取较早者。"""
        base = from_ts if from_ts is not None else time.time()
        regular = base + self._ctl.interval_sec
        force = self._daily_force_ts(base)
        self._next_is_daily = force <= regular
        self._next_summary_ts = min(regular, force)
        self._emit_next_label()
        log.info("下次总结排程 %s（间隔 %.0f 分钟%s）",
                 datetime.fromtimestamp(
                     self._next_summary_ts).strftime("%Y-%m-%d %H:%M"),
                 self._ctl.interval_sec / 60,
                 "，每日强制点" if self._next_is_daily else "")

    def _daily_force_ts(self, now_ts: float) -> float:
        hh, mm = DAILY_FORCE_TIME.split(":")
        now = datetime.fromtimestamp(now_ts)
        t = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if t.timestamp() <= now_ts:
            t += timedelta(days=1)
        return t.timestamp()

    def _emit_next_label(self):
        nxt = datetime.fromtimestamp(self._next_summary_ts)
        now = datetime.now()
        label = f"{nxt:%H:%M}" if nxt.date() == now.date() \
            else f"明天 {nxt:%H:%M}"
        self.next_summary_at.emit(label)

    @Slot()
    def _schedule_tick(self):
        """每分钟按绝对时刻校验：到点或已错过（睡眠唤醒等）都补发。"""
        if self._poller is None or self._next_summary_ts <= 0:
            return
        now = time.time()
        self._emit_next_label()           # 顺带刷新倒计时，保证 UI 不错位
        if now < self._next_summary_ts:
            return
        groups = self._store.list_groups(enabled_only=True)
        if not groups:
            self._reschedule(now)         # 无监控群：顺延继续等
            return
        is_daily = getattr(self, "_next_is_daily", False)
        for g in groups:
            self.ask_summary.emit(g["chatroom_id"], g["group_name"])
        log.info("到点触发自动总结，共 %d 个群%s", len(groups),
                 "（每日强制）" if is_daily else "")
        if is_daily:
            self._daily_done_date = f"{datetime.now():%Y-%m-%d}"
            self.log.emit("触发每日 23:30 强制总结")
        self._reschedule(now)

    @Slot()
    def restart_summary_schedule(self):
        """总结完成后由外部调用：从现在起重新排程并刷新显示。"""
        self._reschedule()

    # ---------- 手动 ----------
    @Slot(str, str)
    def manual_summary(self, chatroom_id: str, group_name: str):
        self.ask_summary.emit(chatroom_id, group_name)
