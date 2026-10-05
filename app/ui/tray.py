# -*- coding: utf-8 -*-
"""托盘图标：状态化图标/提示、菜单操作，关闭窗口=隐藏。

图标颜色表达运行状态：绿=监控中、灰=已暂停/未连接、蓝=正在总结、
橙=有错误待处理。悬停提示里有"下次总结时间 + 监控群数"。
"""
from __future__ import annotations

from PySide6.QtGui import QAction, QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

# 状态 -> (底色, 字)
_STATES = {
    "monitor": ("#07C160", "总"),
    "paused": ("#9AA4AE", "总"),
    "busy": ("#2F80ED", "总"),
    "error": ("#E0524A", "!"),
}
_DEFAULT_STATE = "monitor"


def _make_icon(state: str = _DEFAULT_STATE) -> QIcon:
    """程序化生成 64x64 圆角图标（避免外部资源文件）。"""
    color, glyph = _STATES.get(state, _STATES[_DEFAULT_STATE])
    pm = QPixmap(64, 64)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor(color)))
    p.setPen(QColor(color))
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setPen(QColor("white"))
    f = p.font()
    f.setPixelSize(30 if glyph != "!" else 40)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), 0x0084, glyph)   # AlignCenter
    p.end()
    return QIcon(pm)


class TrayIcon(QSystemTrayIcon):
    def __init__(self, main_window, parent=None):
        super().__init__(_make_icon(), parent)
        self._win = main_window
        self._state = _DEFAULT_STATE
        self._tip_extra = ""
        self.setToolTip("微信群消息监控总结工具")
        menu = QMenu()
        act_show = QAction("显示主窗口", menu)
        act_sum = QAction("手动总结全部群", menu)
        act_records = QAction("记录与诊断…", menu)
        act_output = QAction("打开输出文件夹", menu)
        act_update = QAction("检查更新", menu)
        act_quit = QAction("退出程序", menu)
        menu.addAction(act_show)
        menu.addAction(act_sum)
        menu.addSeparator()
        menu.addAction(act_records)
        menu.addAction(act_output)
        menu.addSeparator()
        menu.addAction(act_update)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.setContextMenu(menu)

        act_show.triggered.connect(self._win.show_and_raise)
        act_sum.triggered.connect(self._win.manual_summary_all)
        act_records.triggered.connect(self._win.show_records)
        act_output.triggered.connect(self._win.open_output_dir)
        act_update.triggered.connect(
            lambda: self._win.check_update(manual=True))
        act_quit.triggered.connect(self._win.really_quit)
        self.activated.connect(self._on_activated)
        # 点托盘气泡：直接打开被提醒/刚生成的那份 Word
        self.messageClicked.connect(self._win.on_tray_message_clicked)

    # ---------- 状态 ----------
    def set_state(self, state: str) -> None:
        """切换图标状态（monitor/paused/busy/error）。"""
        if state not in _STATES or state == self._state:
            return
        self._state = state
        self.setIcon(_make_icon(state))
        self._refresh_tooltip()

    def state(self) -> str:
        return self._state

    def set_tooltip(self, text: str) -> None:
        self._tip_extra = text or ""
        self._refresh_tooltip()

    def _refresh_tooltip(self) -> None:
        base = "微信群消息监控总结工具"
        self.setToolTip(f"{base}\n{self._tip_extra}" if self._tip_extra
                        else base)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._win.show_and_raise()
