# -*- coding: utf-8 -*-
"""主窗口：群卡片网格 + 设置 + 日志。

使用 Windows 原生窗口：原生标题栏、圆角、阴影、出现/最大化过渡动画、
边缘缩放全部由 DWM 自动处理。任务栏图标由 setWindowIcon 控制（绿色"总"字图标）。
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtWidgets import (QApplication, QGridLayout, QHBoxLayout,
                               QLabel, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

from app.config import APP_VERSION, get_skip_version
from app.state_store import StateStore
from app.ui.group_card import GroupCard
from app.ui.group_dialog import GroupDialog
from app.ui.settings_dialog import SettingsDialog
from app.ui.tray import TrayIcon
from app.ui.update_dialog import CheckThread, UpdateDialog
from app.workers.monitor_worker import MonitorWorker
from app.workers.summary_worker import SummaryWorker

_MAIN_QSS = """
QMainWindow, QWidget#central { background: #F3F5F4; }
QLabel#appTitle { font-size: 18px; font-weight: 700; color: #1F2D2A; }
QLabel#appSub { font-size: 12px; color: #7A8683; }
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


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        # Windows 原生窗口：标题栏/圆角/阴影/动画/缩放全部由 DWM 处理。
        # 标题文字空格让标题栏无文字；任务栏图标仍为绿色"总"。
        self.setWindowTitle(" ")
        from app.ui.tray import _make_icon
        self.setWindowIcon(_make_icon())
        self.resize(1020, 680)
        self.setStyleSheet(_MAIN_QSS)
        self._store = StateStore()
        self._really_quitting = False
        self._status_by_chatroom: dict[str, dict] = {}
        self._cards: dict[str, GroupCard] = {}
        self._empty_tip: QLabel | None = None
        self._connect_dlg_open = False

        self._build_ui()
        self._build_workers()
        self._tray = TrayIcon(self)
        self._tray.show()
        self.refresh_cards()
        # 启动 6 秒后静默检查更新（让主界面先就绪；网络失败不打扰）
        self._check_thread: CheckThread | None = None
        self._update_dlg: UpdateDialog | None = None
        QTimer.singleShot(6000, lambda: self.check_update(manual=False))

    # ---------- UI ----------
    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 18, 24, 16)
        root.setSpacing(12)

        # 顶栏
        top = QHBoxLayout()
        title_box = QVBoxLayout()
        t1 = QLabel("微信群消息监控总结")
        t1.setObjectName("appTitle")
        t2 = QLabel("自动滚动总结群聊 · 归档群文件 · 生成 Word 周报式文档")
        t2.setObjectName("appSub")
        title_box.addWidget(t1)
        title_box.addWidget(t2)
        top.addLayout(title_box)
        top.addStretch(1)
        self.btn_add = QPushButton("＋ 添加群")
        self.btn_add.setObjectName("accentBtn")
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_settings = QPushButton("设置")
        self.btn_settings.setObjectName("topBtn")
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        top.addWidget(self.btn_add)
        top.addWidget(self.btn_settings)
        root.addLayout(top)

        # 卡片区
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setSpacing(14)
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

        self.setCentralWidget(central)
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
        self._mon.ask_summary.connect(
            lambda c, n: self._sum.submit(c, n))

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
        for i, g in enumerate(groups):
            card = GroupCard(g, self._status_by_chatroom.get(g["chatroom_id"]))
            card.toggled.connect(self._on_group_toggle)
            card.summarize.connect(
                lambda c, n: self._sum.submit(c, n))
            card.removed.connect(self._on_remove_group)
            self.grid.addWidget(card, i // 3, i % 3)
            self._cards[g["chatroom_id"]] = card
        # 占位卡：空态提示
        if not groups:
            tip = QLabel("还没有监控群，点击右上角「＋ 添加群」开始\n"
                         "提示：需微信已登录")
            tip.setAlignment(Qt.AlignCenter)
            tip.setStyleSheet("color:#9AA4AE; font-size:14px;")
            self.grid.addWidget(tip, 0, 0, 1, 3)
            self._empty_tip = tip

    def _card(self, chatroom: str) -> GroupCard | None:
        return self._cards.get(chatroom)

    # ---------- 交互 ----------
    def _on_add_group(self):
        try:
            from app.core.db_factory import open_wechat_db
            db = open_wechat_db("ui")
            groups = db.get_groups()
        except Exception as e:                          # noqa: BLE001
            QMessageBox.warning(self, "错误",
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
        if QMessageBox.question(self, "确认", f"移除监控群「{name}」？") \
                == QMessageBox.Yes:
            self._store.remove_group(chatroom)
            self.refresh_cards()
            self.append_log(f"已移除监控群: {name}")

    def _on_group_toggle(self, chatroom: str, name: str, enabled: bool):
        self._store.set_group_enabled(chatroom, enabled)
        self.append_log(f"{'开启' if enabled else '暂停'}监控: {name}")

    def _on_settings(self) -> bool:
        """打开设置；返回微信目录是否发生变更。"""
        dlg = SettingsDialog(self)
        if dlg.exec() == SettingsDialog.Accepted:
            ai = dlg.ai_config()
            if ai is not None:
                self._sum.set_ai_config(*ai)
                self.append_log("AI 服务配置已更新")
            if dlg.wechat_dir_changed():
                self.append_log("微信数据目录设置已变更，正在重连…")
                return True
        return False

    # ---------- 微信连接状态 ----------
    def _on_connected(self):
        # 不覆盖状态栏：倒计时文字由随后到达的 next_summary_at 信号写入
        self.refresh_cards()

    def _on_connect_failed(self, reason: str):
        self.lb_status.setText("微信未连接（点击「设置」可重试）")
        if self._connect_dlg_open:
            return
        self._connect_dlg_open = True
        try:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("无法连接微信")
            box.setText(reason)
            btn_retry = box.addButton("重试连接", QMessageBox.AcceptRole)
            box.addButton("打开设置", QMessageBox.ActionRole)
            box.exec()
            if box.clickedButton() is btn_retry:
                self._mon.reconnect()
            else:
                if self._on_settings():
                    self._mon.reconnect()
        finally:
            self._connect_dlg_open = False

    def manual_summary_all(self):
        for g in self._store.list_groups(enabled_only=True):
            self._sum.submit(g["chatroom_id"], g["group_name"])
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
                QMessageBox.information(
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
            QMessageBox.warning(
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

    def _on_summary_done(self, chatroom: str, path: str):
        self._store.mark_summaried(chatroom, path)
        card = self._card(chatroom)
        if card:
            card.mark_summaried()
        self._tray.showMessage("总结完成", path,
                               QSystemTrayIcon.Information, 3000)
        # 刚总结完，重置自动总结倒计时（跨线程 Slot 自动排队）
        self._mon.restart_summary_schedule()

    def _on_summary_failed(self, chatroom: str, err: str):
        self._tray.showMessage("总结失败", err[:120],
                               QSystemTrayIcon.Warning, 4000)

    # ---------- 日志 / 窗口 ----------
    def append_log(self, text: str):
        self.log_view.appendPlainText(f"[{datetime.now():%H:%M:%S}] {text}")

    def show_and_raise(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, ev):
        if self._really_quitting:
            ev.accept()
            self._shutdown()
            QApplication.quit()          # 真正结束事件循环，进程才能退出
            return
        ev.ignore()
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
        self._shutdown()
        QApplication.quit()
