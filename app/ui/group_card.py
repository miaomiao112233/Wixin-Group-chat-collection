# -*- coding: utf-8 -*-
"""群卡片：大格子色块布局的单个监控群单元。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout)

_GREEN = "#07C160"
_GRAY = "#9AA4AE"
_CARD_QSS = """
QFrame#groupCard {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #FFFFFF, stop:1 #F7F9F8);
    border: 1px solid #E6EAE8;
    border-radius: 14px;
}
QFrame#groupCard:hover { border: 1px solid #07C160; }
QLabel#cardTitle { font-size: 15px; font-weight: 600; color: #1F2D2A; }
QLabel#cardMeta  { font-size: 12px; color: #7A8683; }
QPushButton#toggleOn {
    background: #07C160; color: white; border: none;
    border-radius: 12px; padding: 4px 12px; font-size: 12px; font-weight: 600;
}
QPushButton#toggleOn:hover { background: #06AD56; }
QPushButton#toggleOff {
    background: #EEF1F0; color: #7A8683; border: none;
    border-radius: 12px; padding: 4px 12px; font-size: 12px;
}
QPushButton#toggleOff:hover { background: #E2E7E5; }
QPushButton#cardBtn {
    background: #F1F5F3; color: #2B3A35; border: none;
    border-radius: 10px; padding: 5px 12px; font-size: 12px;
}
QPushButton#cardBtn:hover { background: #E2EAE6; }
QPushButton#cardBtnDanger {
    background: #FBF1F1; color: #C0564F; border: none;
    border-radius: 10px; padding: 5px 12px; font-size: 12px;
}
QPushButton#cardBtnDanger:hover { background: #F6E3E2; }
"""


class GroupCard(QFrame):
    toggled = Signal(str, str, bool)     # chatroom, name, enabled
    summarize = Signal(str, str)         # chatroom, name
    removed = Signal(str, str)           # chatroom, name

    def __init__(self, group: dict, status: dict | None = None,
                 parent=None):
        super().__init__(parent)
        self.chatroom = group["chatroom_id"]
        self.group_name = group["group_name"]
        self.setObjectName("groupCard")
        self.setStyleSheet(_CARD_QSS)
        self.setFixedSize(300, 168)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(8)

        # 行1：群名 + 开关
        top = QHBoxLayout()
        title = QLabel(self.group_name)
        title.setObjectName("cardTitle")
        title.setWordWrap(True)
        top.addWidget(title, 1)
        self.enabled = bool(group["enabled"])
        self.btn_toggle = QPushButton()
        self.btn_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_toggle.clicked.connect(self._on_toggle)
        top.addWidget(self.btn_toggle)
        v.addLayout(top)
        self._sync_toggle_btn()

        # 行2：今日新消息
        self.lb_new = QLabel()
        self.lb_new.setObjectName("cardMeta")
        v.addWidget(self.lb_new)

        # 行3：最近消息预览
        self.lb_last = QLabel()
        self.lb_last.setObjectName("cardMeta")
        self.lb_last.setWordWrap(True)
        self.lb_last.setMaximumHeight(34)
        v.addWidget(self.lb_last)

        # 行4：上次总结
        self.lb_summary = QLabel()
        self.lb_summary.setObjectName("cardMeta")
        v.addWidget(self.lb_summary)

        v.addStretch(1)

        # 行5：操作按钮
        btns = QHBoxLayout()
        btn_sum = QPushButton("生成总结")
        btn_sum.setObjectName("cardBtn")
        btn_sum.setCursor(Qt.PointingHandCursor)
        btn_sum.clicked.connect(
            lambda: self.summarize.emit(self.chatroom, self.group_name))
        btn_del = QPushButton("移除")
        btn_del.setObjectName("cardBtnDanger")
        btn_del.setCursor(Qt.PointingHandCursor)
        btn_del.clicked.connect(
            lambda: self.removed.emit(self.chatroom, self.group_name))
        btns.addWidget(btn_sum)
        btns.addWidget(btn_del)
        btns.addStretch(1)
        v.addLayout(btns)

        # 新群（游标待初始化）且尚无状态：显示"初始化中"，采集线程
        # 首轮轮询后 update_status 会替换为真实数据
        if status is None and int(group.get("sort_seq_cursor") or 0) < 0:
            self.lb_new.setText("正在初始化监控…")
            self.lb_last.setText("正在读取今日消息…")
            self.lb_summary.setText("")
        else:
            self.update_status(status or {})

    # ---------- 状态 ----------
    def update_status(self, status: dict):
        # 今日累计（每轮都刷新，启动首轮即正确显示当天总数）
        tc = status.get("today_count")
        if isinstance(tc, int):
            self.lb_new.setText(f"今日新消息  {tc} 条")
        else:
            n = status.get("new_count")
            self.lb_new.setText(
                f"今日新消息  {n} 条" if isinstance(n, int)
                else "今日新消息  -")
        preview = status.get("last_msg") or "暂无新消息"
        self.lb_last.setText(f"最近: {preview[:40]}")
        self.lb_summary.setText("今日已生成总结" if status.get("summaried")
                                else "今日尚未总结")

    def mark_summaried(self):
        self.lb_summary.setText("今日已生成总结")

    def _sync_toggle_btn(self):
        if self.enabled:
            self.btn_toggle.setText("● 监控中")
            self.btn_toggle.setObjectName("toggleOn")
        else:
            self.btn_toggle.setText("○ 已暂停")
            self.btn_toggle.setObjectName("toggleOff")
        self.btn_toggle.style().unpolish(self.btn_toggle)
        self.btn_toggle.style().polish(self.btn_toggle)

    def _on_toggle(self):
        self.enabled = not self.enabled
        self._sync_toggle_btn()
        self.toggled.emit(self.chatroom, self.group_name, self.enabled)
