# -*- coding: utf-8 -*-
"""主窗口：群卡片网格 + 设置 + 日志。

使用自绘无边框窗口（app.ui.frameless）：原生标题栏被整个去掉，左上角
不会再有系统小图标；圆角、投影、拖动、四周缩放、最大化/还原、双击标题栏
都由图框架实现。标题栏右侧一行放「＋ 添加群 / 设置」与窗口按钮，QQ /
微信 风格。任务栏图标仍由 setWindowIcon 控制（绿色"总"字图标）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QThread, QTimer
from PySide6.QtWidgets import (QApplication, QGridLayout, QHBoxLayout,
                               QLabel, QPlainTextEdit, QPushButton, QScrollArea,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

from app.config import APP_VERSION, OUTPUT_DIR, get_skip_version
from app.core import maintenance
from app.core.archiver import safe_name
from app.state_store import StateStore
from app.ui import msgbox
from app.ui.frameless import FramelessWindow
from app.ui.group_card import CARD_WIDTH, GroupCard
from app.ui.group_dialog import GroupDialog
from app.ui.history_dialog import RecordsDialog
from app.ui.settings_dialog import SettingsDialog
from app.ui.shell import open_path, reveal_path
from app.ui.tray import TrayIcon
from app.ui.update_dialog import CheckThread, UpdateDialog
from app.workers.monitor_worker import MonitorWorker
from app.workers.summary_worker import SummaryWorker

_MAIN_QSS = """
QPushButton#topBtn {
    background: #FFFFFF; color: #2B3A35; border: 1px solid #E2E7E5;
    border-radius: 12px; padding: 6px 16px; font-size: 13px;
}
QPushButton#topBtn:hover { border-color: #07C160; color: #07C160; }
QPushButton#accentBtn {
    background: #07C160; color: white; border: none;
    border-radius: 12px; padding: 7px 18px; font-size: 13px; font-weight: 600;
}
QPushButton#accentBtn:hover { background: #06AD56; }
QScrollArea { border: none; background: transparent; }
QPlainTextEdit {
    background: #FFFFFF; border: 1px solid #E6EAE8; border-radius: 10px;
    color: #5B6663; font-size: 12px;
}
QLabel#statusText { font-size: 13px; color: #2B3A35; font-weight: 600; }
"""


class MainWindow(FramelessWindow):

    def __init__(self):
        super().__init__(
            title="微信群消息监控总结",
            subtitle="自动滚动总结群聊 · 归档群文件 · 生成 Word 总结")
        from app.ui.tray import _make_icon
        self.setWindowIcon(_make_icon())
        self._cols = 0                  # 卡片列数（随窗口宽度自适应）
        self.resize_content(1020, 680)
        self.setMinimumSize(560 + 2 * self.outer_margin,
                            480 + 2 * self.outer_margin)
        self.setStyleSheet(_MAIN_QSS)
        self._store = StateStore()
        self._really_quitting = False
        self._status_by_chatroom: dict[str, dict] = {}
        self._cards: dict[str, GroupCard] = {}
        self._empty_tip: QLabel | None = None
        self._connect_dlg_open = False
        self._records_dlg: RecordsDialog | None = None
        self._busy = 0                  # 正在总结的批数（托盘状态用）
        self._connected = False
        self._tray_docx = ""            # 点托盘气泡要打开的文档
        self._restore_geometry()

        self._build_ui()
        self._build_workers()
        self._tray = TrayIcon(self)
        self._tray.show()
        self.refresh_cards()
        # 启动 6 秒后静默检查更新（让主界面先就绪；网络失败不打扰）
        self._check_thread: CheckThread | None = None
        self._update_dlg: UpdateDialog | None = None
        QTimer.singleShot(6000, lambda: self.check_update(manual=False))
        # 启动 8 秒后按策略清理解密缓存（此时各 worker 的副本已是最新，
        # 只会删到过期的旧副本，不会影响正在进行的查询）
        QTimer.singleShot(8000, self._auto_prune_cache)
        self._update_tray_state()

    # ---------- UI ----------
    def _build_ui(self):
        # 顶栏按钮放进自绘标题栏：标题、操作、窗口按钮同一行（QQ / 微信 风格）
        assert self.titlebar is not None
        self.btn_add = QPushButton("＋ 添加群")
        self.btn_add.setObjectName("accentBtn")
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_settings = QPushButton("设置")
        self.btn_settings.setObjectName("topBtn")
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self.titlebar.add_extra(self.btn_add)
        self.titlebar.add_extra(self.btn_settings)

        content = QWidget()
        content.setObjectName("content")
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 4, 24, 16)
        root.setSpacing(12)
        self.body_layout.addWidget(content, 1)

        # 卡片区
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setSpacing(14)
        # 列数不满一行时整体居中，右侧不留大片空白
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.scroll.setWidget(self.grid_host)
        root.addWidget(self.scroll, 1)

        # 状态条
        status_row = QHBoxLayout()
        self.lb_status = QLabel("正在连接微信…")
        self.lb_status.setObjectName("statusText")
        status_row.addWidget(self.lb_status)
        status_row.addStretch(1)
        self.btn_sum_all = QPushButton("立即总结全部群")
        self.btn_sum_all.setObjectName("topBtn")
        self.btn_sum_all.setCursor(Qt.PointingHandCursor)
        status_row.addWidget(self.btn_sum_all)
        root.addLayout(status_row)

        # 日志
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("运行日志")
        self.log_view.setMaximumHeight(130)
        root.addWidget(self.log_view)

        self.btn_add.clicked.connect(self._on_add_group)
        self.btn_settings.clicked.connect(self._on_settings)
        self.btn_sum_all.clicked.connect(self.manual_summary_all)

    # ---------- Workers ----------
    def _build_workers(self):
        self._mon_thread = QThread(self)
        self._mon = MonitorWorker(self._store)
        self._mon.moveToThread(self._mon_thread)
        self._mon_thread.started.connect(self._mon.start_work)
        self._mon.log.connect(self.append_log)
        self._mon.status.connect(self._on_status)
        self._mon.next_summary_at.connect(self._on_next_summary)
        self._mon.connect_failed.connect(self._on_connect_failed)
        self._mon.connected.connect(self._on_connected)
        self._mon.keyword_alert.connect(self._on_keyword_alert)
        self._mon.ask_summary.connect(
            lambda c, n, d: self._submit_summary(c, n, d))

        self._sum_thread = QThread(self)
        self._sum = SummaryWorker(self._store)
        self._sum.moveToThread(self._sum_thread)
        self._sum_thread.started.connect(self._sum.start_work)
        self._sum.log.connect(self.append_log)
        self._sum.done.connect(self._on_summary_done)
        self._sum.failed.connect(self._on_summary_failed)

        self._mon_thread.start()
        self._sum_thread.start()

    # ---------- 卡片 ----------
    def refresh_cards(self):
        groups = self._store.list_groups()
        # 清空旧卡片
        for card in self._cards.values():
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        # 移除上一次的空态提示（如有）
        if getattr(self, "_empty_tip", None) is not None:
            self._empty_tip.setParent(None)
            self._empty_tip.deleteLater()
            self._empty_tip = None
        for g in groups:
            card = GroupCard(g, self._status_by_chatroom.get(g["chatroom_id"]),
                             has_doc=self._has_today_doc(g["chatroom_id"]))
            card.toggled.connect(self._on_group_toggle)
            card.summarize.connect(
                lambda c, n: self._submit_summary(c, n))
            card.open_folder.connect(self._open_group_folder)
            card.open_today.connect(self._open_group_today)
            card.removed.connect(self._on_remove_group)
            self._cards[g["chatroom_id"]] = card
        # 占位卡：空态提示
        if not groups:
            tip = QLabel("还没有监控群，点击右上角「＋ 添加群」开始\n"
                         "提示：需微信已登录")
            tip.setAlignment(Qt.AlignCenter)
            tip.setStyleSheet("color:#9AA4AE; font-size:14px;")
            self._empty_tip = tip
        self._cols = 0
        self._relayout_cards()

    # ---------- 卡片排布（列数随窗口宽度自适应） ----------
    def _column_count(self) -> int:
        spacing = self.grid.horizontalSpacing()
        avail = max(self.scroll.viewport().width() - 8, CARD_WIDTH)
        return max(1, (avail + spacing) // (CARD_WIDTH + spacing))

    def _relayout_cards(self, force: bool = False) -> None:
        """按当前宽度重排卡片；列数没变就什么都不做。

        只挪动已有卡片（不重建），避免丢失"今日已生成总结"等卡片内状态。
        """
        if not hasattr(self, "scroll") or not hasattr(self, "_cards"):
            return
        cols = self._column_count()
        if not force and cols == self._cols:
            return
        self._cols = cols
        for i, card in enumerate(self._cards.values()):
            self.grid.removeWidget(card)
            self.grid.addWidget(card, i // cols, i % cols)
        if self._empty_tip is not None:
            self.grid.addWidget(self._empty_tip, 0, 0, 1, cols)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._relayout_cards()

    def _card(self, chatroom: str) -> GroupCard | None:
        return self._cards.get(chatroom)

    # ---------- 卡片快捷入口 / 记录窗口 ----------
    @staticmethod
    def _today() -> str:
        return f"{datetime.now():%Y-%m-%d}"

    def _has_today_doc(self, chatroom: str) -> bool:
        try:
            return bool(self._store.today_docx(chatroom, self._today()))
        except Exception:                               # noqa: BLE001
            return False

    @staticmethod
    def _group_output_dir(name: str) -> Path:
        return Path(OUTPUT_DIR) / safe_name(name)

    def _open_group_folder(self, chatroom: str, name: str):
        """打开该群的输出文件夹（不存在先建出来，避免打开失败）。"""
        folder = self._group_output_dir(name)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if open_path(folder):
            self.append_log(f"已打开输出文件夹: {folder}")
        else:
            msgbox.warning(self, "打开失败", f"无法打开文件夹：\n{folder}")

    def _open_group_today(self, chatroom: str, name: str):
        """打开该群今天的 Word 总结。"""
        path = self._store.today_docx(chatroom, self._today())
        if not path:
            msgbox.info(self, "暂无今日总结",
                        f"「{name}」今天还没有生成 Word 总结。\n"
                        "可以点卡片上的「生成总结」，或等下一次自动总结。")
            return
        if not Path(path).exists():
            msgbox.warning(self, "文件不存在", f"文档已不在原位置：\n{path}")
            return
        open_path(path)
        self.append_log(f"已打开今日总结: {path}")

    def open_output_dir(self):
        """托盘菜单：打开输出总目录。"""
        folder = Path(OUTPUT_DIR)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if not open_path(folder):
            msgbox.warning(self, "打开失败", f"无法打开目录：\n{folder}")

    def show_records(self):
        """打开/前置「记录与诊断」窗口（非模态，方便边看边操作）。"""
        if self._records_dlg is None:
            dlg = RecordsDialog(self._store, self)
            dlg.regenerate.connect(self._on_regenerate)
            dlg.finished.connect(self._on_records_closed)
            self._records_dlg = dlg
        self._records_dlg.show()
        self._records_dlg.raise_()
        self._records_dlg.activateWindow()

    def _on_records_closed(self, _result=0):
        self._records_dlg = None        # 关掉释放，下次重新读数据

    def _on_regenerate(self, chatroom: str, name: str, date: str):
        """历史面板点「重新生成」：按记录里的日期重新总结同一批消息。"""
        self._submit_summary(chatroom, name, date)
        self.append_log(f"重新生成 {name} {date} 的总结…")

    # ---------- 关键词告警 ----------
    def _on_keyword_alert(self, chatroom: str, name: str, keyword: str,
                          snippet: str):
        """命中所配置关键词：弹托盘提醒，点提醒可打开当日总结。"""
        try:
            doc = self._store.today_docx(chatroom, self._today()) or ""
        except Exception:                               # noqa: BLE001
            doc = ""
        self._tray_docx = doc
        self._tray.showMessage(
            f"「{name}」命中关键词：{keyword}",
            snippet + ("\n（点击打开今日总结）" if doc else ""),
            QSystemTrayIcon.Information, 6000)
        self.append_log(f"[提醒] {name} 命中「{keyword}」: {snippet}")

    def on_tray_message_clicked(self):
        """点托盘气泡：优先打开刚提醒/刚生成的那份 Word。"""
        path = self._tray_docx
        if path and Path(path).exists():
            open_path(path)
            return
        self.show_and_raise()

    # ---------- 总结提交 / 托盘状态 ----------
    def _submit_summary(self, chatroom: str, name: str, day: str = ""):
        """统一的总结提交入口（统计在跑的批数，驱动托盘状态）。"""
        self._sum.submit(chatroom, name, day)
        self._busy += 1
        self._update_tray_state()

    def _done_one_summary(self):
        self._busy = max(0, self._busy - 1)
        self._update_tray_state()

    def _update_tray_state(self, hhmm: str | None = None):
        """托盘图标/提示：忙→蓝、未连接→橙、全暂停→灰、否则绿。"""
        if not hasattr(self, "_tray"):
            return
        if hhmm is not None:
            self._next_summary_label = hhmm
        groups = self._store.list_groups()
        enabled = [g for g in groups if g["enabled"]]
        if self._busy > 0:
            state = "busy"
        elif not self._connected:
            state = "error"
        elif groups and not enabled:
            state = "paused"
        else:
            state = "monitor"
        self._tray.set_state(state)
        bits = [f"监控 {len(enabled)}/{len(groups)} 个群"]
        label = getattr(self, "_next_summary_label", "")
        if label:
            bits.append(f"下次总结 {label}")
        if self._busy:
            bits.append(f"正在总结 {self._busy} 批")
        if not self._connected:
            bits.append("微信未连接")
        self._tray.set_tooltip(" · ".join(bits))

    # ---------- 窗口几何记忆 ----------
    def _restore_geometry(self):
        """恢复上次的窗口大小/位置（含最大化状态）。"""
        try:
            raw = self._store.get_kv("ui.window.geometry")
        except Exception:                               # noqa: BLE001
            raw = None
        if raw:
            try:
                if self.restoreGeometry(QByteArray.fromBase64(raw.encode())):
                    return
            except Exception:                           # noqa: BLE001
                pass
        self.resize_content(1020, 680)

    def _save_geometry(self):
        try:
            data = bytes(self.saveGeometry().toBase64()).decode("ascii")
            self._store.set_kv("ui.window.geometry", data)
        except Exception:                               # noqa: BLE001
            pass

    # ---------- 缓存维护 ----------
    def _auto_prune_cache(self):
        """按配置的保留策略清理解密缓存（只删过期副本）。"""
        try:
            freed, removed = maintenance.prune_dbcache()
        except Exception as e:                          # noqa: BLE001
            self.append_log(f"清理解密缓存失败: {e}")
            return
        if removed:
            msg = (f"已清理解密缓存 {removed} 个文件，释放 "
                   f"{maintenance.format_size(freed)}")
            self.append_log(msg)
            logging.getLogger("app").info(msg)

    # ---------- 交互 ----------
    def _on_add_group(self):
        try:
            from app.core.db_factory import open_wechat_db
            db = open_wechat_db("ui")
            groups = db.get_groups()
        except Exception as e:                          # noqa: BLE001
            msgbox.warning(self, "错误",
                           f"读取群列表失败（微信需已登录）:\n{e}")
            return
        existing = {g["chatroom_id"] for g in self._store.list_groups()}
        dlg = GroupDialog(groups, existing, self)
        if dlg.exec() == GroupDialog.Accepted and dlg.selected():
            chatroom, name = dlg.selected()
            today = f"{datetime.now():%Y-%m-%d}"
            self._store.upsert_group(chatroom, name, True,
                                     monitor_start_date=today)
            # 游标定位（大群可能数秒）交给采集线程后台做，不卡界面；
            # 立即请求一次轮询，新卡片马上拿到今日消息数
            self.refresh_cards()
            self.append_log(f"已添加监控群: {name}（仅归档 {today} 起的文件）")
            self._mon.request_poll.emit()

    def _on_remove_group(self, chatroom: str, name: str):
        if msgbox.question(self, "确认", f"移除监控群「{name}」？",
                           yes="移除", no="取消"):
            self._store.remove_group(chatroom)
            self.refresh_cards()
            self.append_log(f"已移除监控群: {name}")

    def _on_group_toggle(self, chatroom: str, name: str, enabled: bool):
        self._store.set_group_enabled(chatroom, enabled)
        self.append_log(f"{'开启' if enabled else '暂停'}监控: {name}")
        self._update_tray_state()

    def _on_settings(self) -> bool:
        """打开设置；返回微信目录是否发生变更。"""
        dlg = SettingsDialog(self)
        if dlg.exec() == SettingsDialog.Accepted:
            ai = dlg.ai_config()
            if ai is not None:
                self._sum.ai_config_set.emit(*ai)
                self.append_log("AI 服务配置已更新")
            if dlg.wechat_dir_changed():
                self.append_log("微信数据目录设置已变更，正在重连…")
                return True
        return False

    # ---------- 微信连接状态 ----------
    def _on_connected(self):
        # 不覆盖状态栏：倒计时文字由随后到达的 next_summary_at 信号写入
        self._connected = True
        self.refresh_cards()
        self._update_tray_state()

    def _on_connect_failed(self, reason: str):
        self._connected = False
        self._update_tray_state()
        self.lb_status.setText("微信未连接（点击「设置」可重试）")
        if self._connect_dlg_open:
            return
        self._connect_dlg_open = True
        try:
            picked = msgbox.choose(
                self, "无法连接微信", reason,
                [("打开设置", False), ("重试连接", True)], kind="warning")
            if picked == 1:
                self._mon.request_reconnect.emit()
            elif picked == 0:
                if self._on_settings():
                    self._mon.request_reconnect.emit()
            # picked == -1：直接关掉，等下一轮自动重连
        finally:
            self._connect_dlg_open = False

    def manual_summary_all(self):
        for g in self._store.list_groups(enabled_only=True):
            self._submit_summary(g["chatroom_id"], g["group_name"])
        self.show_and_raise()

    # ---------- 自动更新 ----------
    def check_update(self, manual: bool = False):
        """检查 GitHub Release。manual=True（托盘触发）时给出全部反馈。"""
        # 已有检查在跑 / 更新对话框开着时不重复
        if self._check_thread and self._check_thread.isRunning():
            return
        if self._update_dlg is not None:
            return
        if manual:
            self.append_log("正在检查更新…")
        self._check_thread = CheckThread(self)
        self._check_thread.ok.connect(
            lambda info: self._on_check_result(info, manual))
        self._check_thread.error.connect(
            lambda msg: self._on_check_error(msg, manual))
        self._check_thread.start()

    def _on_check_result(self, info, manual: bool):
        from app.core import updater
        newer = updater.is_newer(info.version, APP_VERSION)
        if not newer:
            if manual:
                msgbox.info(
                    self, "检查更新",
                    f"当前已是最新版本（v{APP_VERSION}）。")
            return
        if info.version == get_skip_version():
            if manual:
                # 手动检查时即使跳过过也展示
                self._show_update_dialog(info)
            return
        self.append_log(f"发现新版本 {info.tag}")
        self._show_update_dialog(info)

    def _show_update_dialog(self, info):
        dlg = UpdateDialog(info, self)
        self._update_dlg = dlg
        dlg.finished.connect(self._on_update_dlg_closed)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_update_dlg_closed(self):
        self._update_dlg = None

    def _on_check_error(self, msg: str, manual: bool):
        if manual:
            msgbox.warning(
                self, "检查更新失败",
                f"{msg}\n\n请检查网络后重试（需能访问 github.com）。")
            self.append_log(f"检查更新失败: {msg}")

    # ---------- 状态回调 ----------
    def _on_status(self, data: dict):
        for chatroom, st in data.items():
            self._status_by_chatroom[chatroom] = st
            card = self._card(chatroom)
            if card:
                card.update_status(st)

    def _on_next_summary(self, hhmm: str):
        self.lb_status.setText(f"下次自动总结: {hhmm}")
        self._update_tray_state(hhmm)

    def _on_summary_done(self, chatroom: str, path: str):
        self._store.mark_summaried(chatroom, path)
        card = self._card(chatroom)
        if card:
            card.mark_summaried()
            card.set_has_doc(True)
        self._tray_docx = path            # 点气泡可直接打开刚生成的文档
        self._tray.showMessage("总结完成", path,
                               QSystemTrayIcon.Information, 3000)
        self._done_one_summary()
        # 刚总结完，重置自动总结倒计时（经信号排队到采集线程执行）
        self._mon.reschedule_requested.emit()

    def _on_summary_failed(self, chatroom: str, err: str):
        self._tray.showMessage("总结失败", err[:120],
                               QSystemTrayIcon.Warning, 4000)
        self._done_one_summary()
        self.append_log(f"[错误] 总结失败: {err[:200]}")

    # ---------- 日志 / 窗口 ----------
    def append_log(self, text: str):
        self.log_view.appendPlainText(f"[{datetime.now():%H:%M:%S}] {text}")

    def show_and_raise(self):
        # 无边框窗口：隐藏/最小化后要按原状态恢复（别把最大化给还原了）
        if self.isMinimized():
            self.showNormal()
        elif not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, ev):
        if self._really_quitting:
            ev.accept()
            self._shutdown()
            QApplication.quit()          # 真正结束事件循环，进程才能退出
            return
        ev.ignore()
        self._save_geometry()            # 隐藏到托盘前记住窗口位置/大小
        self.hide()
        self._tray.showMessage("仍在运行", "已最小化到托盘，右键托盘可退出",
                               QSystemTrayIcon.Information, 2500)

    def _shutdown(self):
        """停 worker、收线程、隐藏托盘。"""
        self._tray.hide()
        self._mon.stop()
        self._sum.stop()
        for t in (self._mon_thread, self._sum_thread):
            t.quit()
            if not t.wait(2000):
                t.terminate()
                t.wait(1000)

    def really_quit(self):
        self._really_quitting = True
        self._save_geometry()
        self._shutdown()
        QApplication.quit()
