# -*- coding: utf-8 -*-
"""可折叠分组：把长表单收纳成"点开才展开"的卡片。

设置页用它把 AI / 归档 / 提醒 / 维护 四组选项折起来；标题行右侧能显示
当前值摘要，收起时也能一眼看到配置状态。展开/收起带 150ms 高度动画，
动画每一帧都发 ``reflow``，外层对话框据此跟着调整高度。

用法::

    sec = CollapsibleSection("消息提醒", expanded=False)
    sec.set_summary("已开启 · 8 个关键词")
    sec.body_layout.addWidget(...)      # 内容塞这里
    layout.addWidget(sec)
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QVBoxLayout,
                               QWidget)

from app.ui.frameless import ICON_COLOR, svg_pixmap

_EXPANDED_MAX = 16777215        # QWIDGETSIZE_MAX
_ANIM_MS = 150

_SVG_BODY = ('<svg xmlns="http://www.w3.org/2000/svg" width="{s}" height="{s}" '
             'viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" '
             'stroke-linecap="round" stroke-linejoin="round">{d}</svg>')
_ARROW = {
    "down": _SVG_BODY.format(d='<path d="M6 9l6 6 6-6"/>', s="{s}", c="{c}"),
    "right": _SVG_BODY.format(d='<path d="M9 6l6 6-6 6"/>', s="{s}", c="{c}"),
}

_QSS = """
QFrame#section {
    background: #FFFFFF; border: 1px solid #E6EAE8; border-radius: 10px;
}
QWidget#secHeader {
    background: transparent; border: none;
    border-top-left-radius: 9px; border-top-right-radius: 9px;
}
QWidget#secHeader:hover { background: #F7F9F8; }
QLabel#secTitle { font-size: 13px; font-weight: 600; color: #1F2D2A; }
QLabel#secSummary { font-size: 12px; color: #8A9491; }
QFrame#secDivider { background: #EEF1F0; border: none; }
"""


class _Header(QWidget):
    """分组标题行：箭头 + 标题 + 右侧摘要，整行可点。"""

    clicked = Signal()

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("secHeader")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(38)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 14, 0)
        row.setSpacing(8)
        self.lb_arrow = QLabel(self)
        self.lb_arrow.setFixedSize(14, 14)
        row.addWidget(self.lb_arrow)
        self.lb_title = QLabel(title, self)
        self.lb_title.setObjectName("secTitle")
        row.addWidget(self.lb_title)
        row.addStretch(1)
        self.lb_summary = QLabel("", self)
        self.lb_summary.setObjectName("secSummary")
        row.addWidget(self.lb_summary)
        self.set_arrow("down")

    def set_arrow(self, name: str) -> None:
        self.lb_arrow.setPixmap(svg_pixmap(_ARROW[name], ICON_COLOR, 14))

    def set_summary(self, text: str) -> None:
        self.lb_summary.setText(text or "")

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()
            ev.accept()
            return
        super().mousePressEvent(ev)


class CollapsibleSection(QFrame):
    """一个可折叠分组卡片：标题行 + 分隔线 + 内容区。"""

    toggled = Signal(bool)      # 用户点击标题行后的新状态
    reflow = Signal()           # 展开/收起过程中需要外层重排高度

    def __init__(self, title: str, expanded: bool = False,
                 content_spacing: int = 8, parent=None):
        super().__init__(parent)
        self.setObjectName("section")
        self.setStyleSheet(_QSS)
        self._expanded = bool(expanded)
        self._anim: QPropertyAnimation | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.header = _Header(title, self)
        self.header.clicked.connect(self.toggle)
        lay.addWidget(self.header)

        self._divider = QFrame(self)
        self._divider.setObjectName("secDivider")
        self._divider.setFixedHeight(1)
        lay.addWidget(self._divider)

        self.body = QWidget(self)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(14, 10, 14, 12)
        self.body_layout.setSpacing(content_spacing)
        lay.addWidget(self.body)

        self._apply(self._expanded, animate=False)

    # ---------- 对外 ----------
    def is_expanded(self) -> bool:
        return self._expanded

    def set_summary(self, text: str) -> None:
        """标题行右侧的当前值摘要（收起时也能看到状态）。"""
        self.header.set_summary(text)

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool, animate: bool = True) -> None:
        expanded = bool(expanded)
        if expanded == self._expanded and animate:
            return
        self._expanded = expanded
        self._apply(expanded, animate)
        self.toggled.emit(expanded)

    # ---------- 内部 ----------
    def _apply(self, expanded: bool, animate: bool) -> None:
        self.header.set_arrow("down" if expanded else "right")
        self._divider.setVisible(expanded)
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        if not animate:
            self.body.setMaximumHeight(_EXPANDED_MAX)
            self.body.setVisible(expanded)
            self.reflow.emit()
            return

        # 先解除高度限制才能量到真实内容高度（否则量到的是 0）
        self.body.setMaximumHeight(_EXPANDED_MAX)
        target = self.body.sizeHint().height() if expanded else 0
        # 展开一律从 0 长起来；收起从当前高度缩回去
        start = 0 if expanded else self.body.height()
        self.body.setVisible(True)
        anim = QPropertyAnimation(self.body, b"maximumHeight", self)
        anim.setDuration(_ANIM_MS)
        anim.setEasingCurve(QEasingCurve.InOutQuad)
        anim.setStartValue(start)
        anim.setEndValue(target)
        anim.valueChanged.connect(lambda _v: self.reflow.emit())
        anim.finished.connect(lambda: self._on_anim_done(expanded))
        self._anim = anim
        anim.start()

    def _on_anim_done(self, expanded: bool) -> None:
        self._anim = None
        if expanded:
            self.body.setMaximumHeight(_EXPANDED_MAX)
        else:
            self.body.setVisible(False)
        self.reflow.emit()
