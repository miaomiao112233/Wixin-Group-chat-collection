# -*- coding: utf-8 -*-
"""添加监控群对话框：列出全部群供选择（无边框，与主窗口风格一致）。"""
from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDialogButtonBox, QLabel,
                               QVBoxLayout)

from app.ui.frameless import FramelessDialog

_QSS = """
QLabel { color: #2B3A35; font-size: 13px; }
QComboBox {
    border: 1px solid #DCE2E0; border-radius: 8px; padding: 6px 10px;
    font-size: 13px; background: #FFFFFF; color: #2B3A35;
}
QComboBox:focus { border-color: #07C160; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: #FFFFFF; border: 1px solid #DCE2E0;
    selection-background-color: #E4F6EC; selection-color: #1F2D2A;
}
QPushButton {
    background: #F1F5F3; color: #2B3A35; border: none;
    border-radius: 8px; padding: 6px 14px; font-size: 12px;
}
QPushButton:hover { background: #E2EAE6; }
"""


class GroupDialog(FramelessDialog):
    def __init__(self, groups: list[dict], existing: set[str], parent=None):
        super().__init__(parent, title="添加监控群")
        self.setStyleSheet(_QSS)
        self.resize_content(440, 160)
        self._groups = [g for g in groups if g["username"] not in existing]

        body = QVBoxLayout()
        body.setContentsMargins(20, 6, 20, 16)
        body.setSpacing(12)
        self.body_layout.addLayout(body)

        body.addWidget(QLabel("选择要监控的群聊："))
        self.combo = QComboBox()
        for g in self._groups:
            self.combo.addItem(g.get("name") or g["username"], g["username"])
        body.addWidget(self.combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        body.addWidget(buttons)
        self._sync_ok(buttons)
        self.combo.currentIndexChanged.connect(lambda _: self._sync_ok(buttons))

    def _sync_ok(self, buttons: QDialogButtonBox):
        buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self._groups))

    def selected(self) -> tuple[str, str] | None:
        """返回 (chatroom_id, 群名)；未选返回 None。"""
        idx = self.combo.currentIndex()
        if idx < 0 or not self._groups:
            return None
        g = self._groups[idx]
        return g["username"], (g.get("name") or g["username"])
