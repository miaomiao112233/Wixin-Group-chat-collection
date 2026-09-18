# -*- coding: utf-8 -*-
"""添加监控群对话框：列出全部群供选择。"""
from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                               QLabel, QVBoxLayout)


class GroupDialog(QDialog):
    def __init__(self, groups: list[dict], existing: set[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加监控群")
        self.resize(420, 120)
        self._groups = [g for g in groups if g["username"] not in existing]

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("选择要监控的群聊："))
        self.combo = QComboBox()
        for g in self._groups:
            self.combo.addItem(g.get("name") or g["username"], g["username"])
        layout.addWidget(self.combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
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
