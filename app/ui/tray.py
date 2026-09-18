# -*- coding: utf-8 -*-
"""托盘图标：双击显示主窗口，菜单操作，关闭窗口=隐藏。"""
from __future__ import annotations

from PySide6.QtGui import QAction, QColor, QIcon, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


def _make_icon() -> QIcon:
    """程序化生成 64x64 圆角图标（避免外部资源文件）。"""
    pm = QPixmap(64, 64)
    pm.fill(QColor(0, 0, 0, 0))
    from PySide6.QtGui import QPainter, QBrush
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor("#07C160")))
    p.setPen(QColor("#07C160"))
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setPen(QColor("white"))
    f = p.font()
    f.setPixelSize(30)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), 0x0084, "总")   # AlignCenter
    p.end()
    return QIcon(pm)


class TrayIcon(QSystemTrayIcon):
    def __init__(self, main_window, parent=None):
        super().__init__(_make_icon(), parent)
        self._win = main_window
        self.setToolTip("微信群消息监控总结工具")
        menu = QMenu()
        act_show = QAction("显示主窗口", menu)
        act_sum = QAction("手动总结全部群", menu)
        act_quit = QAction("退出程序", menu)
        menu.addAction(act_show)
        menu.addAction(act_sum)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.setContextMenu(menu)

        act_show.triggered.connect(self._win.show_and_raise)
        act_sum.triggered.connect(self._win.manual_summary_all)
        act_quit.triggered.connect(self._win.really_quit)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._win.show_and_raise()
