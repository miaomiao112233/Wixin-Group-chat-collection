# -*- coding: utf-8 -*-
"""总结 Worker：串行消费总结队列，调用 DeepSeek 生成 docx（工作线程）。"""
from __future__ import annotations

import logging
import queue

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from app.config import OUTPUT_DIR, get_ai_config
from app.core.archiver import Archiver
from app.core.message_poller import MessagePoller
from app.core.summary_service import summarize_group
from app.state_store import StateStore


class _SignalLogHandler(logging.Handler):
    """把 logging 消息转发到 Qt 信号，让 OCR 等日志在 UI 可见。"""

    def __init__(self, signal: Signal):
        super().__init__(level=logging.INFO)
        self._signal = signal

    def emit(self, record: logging.LogRecord):
        try:
            self._signal.emit(self.format(record))
        except Exception:                          # noqa: BLE001
            pass


def _build_client(key: str, base_url: str, model: str,
                  disable_thinking: bool = False):
    """按配置构造客户端；云端服务无 key 或地址/模型缺失时返回 None。"""
    if not base_url or not model:
        return None
    local = ("://localhost" in base_url) or ("://127.0.0.1" in base_url)
    if not key and not local:
        return None
    from app.summary.deepseek_client import DeepSeekClient
    return DeepSeekClient(key, base_url, model,
                          disable_thinking=disable_thinking)


class SummaryWorker(QObject):
    log = Signal(str)
    done = Signal(str, str)      # chatroom_id, docx_path
    failed = Signal(str, str)    # chatroom_id, error
    ai_config_set = Signal(str, str, str, bool)   # UI 保存设置后触发热更新

    def __init__(self, store: StateStore):
        super().__init__()
        self._store = store
        self._q: queue.Queue[tuple[str, str, str] | None] = queue.Queue()
        self._db = None
        self._poller: MessagePoller | None = None
        self._archiver: Archiver | None = None
        self._client = None
        # UI 线程触达本线程必须走信号：直接方法调用会在 UI 线程执行
        self.ai_config_set.connect(self.set_ai_config)

    @Slot()
    def start_work(self):
        try:
            from app.core.db_factory import open_wechat_db
            self._db = open_wechat_db("summary")
            self._poller = MessagePoller(self._db)
            self._archiver = Archiver(self._db, self._store)
        except Exception as e:                          # noqa: BLE001
            self.log.emit(f"[错误] 总结线程连接微信失败: {e}")

        self._client = _build_client(*get_ai_config())
        if self._client is None:
            self.log.emit("[提示] 未配置 AI 服务，总结将只含统计与文件清单"
                          "（在「设置」中选择服务商并填入 API Key）")

        # 把 app.ocr 等日志转发到 UI 日志框
        handler = _SignalLogHandler(self.log)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger("app.ocr").addHandler(handler)
        logging.getLogger("app.ocr").setLevel(logging.INFO)

        timer = QTimer(self)
        timer.setInterval(500)
        timer.timeout.connect(self._drain)
        timer.start()

    def stop(self):
        self._q.put(None)

    @Slot(str, str, str)
    def submit(self, chatroom_id: str, group_name: str, day_str: str = ""):
        """入队一次总结；day_str 非空（YYYY-MM-DD）时补发指定日，空=今天。"""
        self._q.put((chatroom_id, group_name, day_str))

    @Slot(str, str, str, bool)
    def set_ai_config(self, key: str, base_url: str, model: str,
                      disable_thinking: bool = False):
        """设置界面保存后热更新 AI 配置（经信号排队到本线程执行）。"""
        client = _build_client(key, base_url, model, disable_thinking)
        if client is not None:
            self._client = client
            local = ("://localhost" in base_url) or ("://127.0.0.1" in base_url)
            self.log.emit("AI 总结已启用（本地服务）" if local and not key
                          else "AI 总结已启用")
        else:
            self._client = None
            self.log.emit("AI 总结已停用（未配置 Key 或接口信息不完整）")

    # ---------- 内部 ----------
    def _drain(self):
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                return
            if item is None:
                return
            chatroom, name, day_str = item
            self._summarize_one(chatroom, name, day_str)

    @staticmethod
    def _parse_day(day_str: str):
        """"YYYY-MM-DD" → date；空/非法返回 None（=今天）。"""
        if not day_str:
            return None
        try:
            from datetime import datetime as _dt
            return _dt.strptime(day_str, "%Y-%m-%d").date()
        except ValueError:
            return None

    def _summarize_one(self, chatroom: str, name: str, day_str: str = ""):
        if self._poller is None:
            return
        try:
            self.log.emit(f"[{name}] 开始生成总结…")
            out = summarize_group(self._db, self._poller, self._archiver,
                                  self._store, self._client,
                                  chatroom, name, self._parse_day(day_str),
                                  OUTPUT_DIR)
            if out is None:
                self.log.emit(f"[{name}] 当日无消息，跳过总结")
                return
            self.done.emit(chatroom, str(out))
            self.log.emit(f"[{name}] 总结完成: {out}")
        except Exception as e:                          # noqa: BLE001
            try:
                self._store.log_error("ERROR", "summary", f"{name}: {e}")
            except Exception:                           # noqa: BLE001
                pass
            self.failed.emit(chatroom, str(e))
            self.log.emit(f"[错误] 总结 {name} 失败: {e}")
