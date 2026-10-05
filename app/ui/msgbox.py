# -*- coding: utf-8 -*-
"""无边框消息框：替代 QMessageBox，保持 QQ / 微信 风格的窗口外观。

原生 ``QMessageBox`` 会带上系统标题栏（左上角固定小图标）且不能被 QSS
定制，与本项目的无边框窗口不一致，所以用 :class:`FramelessDialog` 自绘
一个轻量版本，并保留 QMessageBox 的调用方式（信息 / 警告 / 确认）。

用法::

    from app.ui import msgbox
    msgbox.info(self, "已保存", "设置已保存。")
    if msgbox.question(self, "确认", f"移除监控群「{name}」？"):
        ...
    idx = msgbox.choose(self, "无法连接微信", reason,
                        [("重试连接", True), ("打开设置", False)])
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton

from app.ui.frameless import FramelessDialog, svg_pixmap

_SVG_BODY = ('<svg xmlns="http://www.w3.org/2000/svg" width="{s}" height="{s}" '
             'viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" '
             'stroke-linecap="round" stroke-linejoin="round">{d}</svg>')
_GLYPHS = {
    "info": _SVG_BODY.format(
        s="{s}", c="{c}",
        d='<circle cx="12" cy="12" r="9.5"/><path d="M12 11v6"/>'
          '<path d="M12 7.6h.01"/>'),
    "warning": _SVG_BODY.format(
        s="{s}", c="{c}",
        d='<path d="M10.3 3.9 2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3'
          'L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4.5"/>'
          '<path d="M12 17.2h.01"/>'),
    "error": _SVG_BODY.format(
        s="{s}", c="{c}",
        d='<circle cx="12" cy="12" r="9.5"/><path d="M15 9l-6 6"/>'
          '<path d="M9 9l6 6"/>'),
    "question": _SVG_BODY.format(
        s="{s}", c="{c}",
        d='<circle cx="12" cy="12" r="9.5"/><path d="M9.2 9.2a3 3 0 0 1 5.9 1'
          'c0 2-3 2.6-3 4.4"/><path d="M12 17.6h.01"/>'),
}
_KIND_COLOR = {
    "info": "#07C160",
    "warning": "#E6A23C",
    "error": "#E0524A",
    "question": "#07C160",
}

_QSS = """
QLabel#msgText { font-size: 13px; color: #2B3A35; }
QPushButton {
    background: #FFFFFF; color: #2B3A35; border: 1px solid #E2E7E5;
    border-radius: 10px; padding: 7px 20px; font-size: 13px;
}
QPushButton:hover { border-color: #07C160; color: #07C160; }
QPushButton#msgAccent {
    background: #07C160; color: #FFFFFF; border: none; font-weight: 600;
}
QPushButton#msgAccent:hover { background: #06AD56; color: #FFFFFF; }
"""


class MessageDialog(FramelessDialog):
    """自绘消息框；一般不用直接实例化，用下面的 info/warning/question。"""

    def __init__(self, parent, title: str, text: str, kind: str = "info",
                 options: list[tuple[str, bool]] | None = None):
        super().__init__(parent, title=title, background="#F3F5F4")
        self._result = -1
        self.setStyleSheet(_QSS)
        options = options or [("确定", True)]

        body = self.body_layout
        body.setContentsMargins(24, 8, 24, 18)
        body.setSpacing(18)

        row = QHBoxLayout()
        row.setSpacing(14)
        icon = QLabel()
        icon.setPixmap(svg_pixmap(_GLYPHS.get(kind, _GLYPHS["info"]),
                                 _KIND_COLOR.get(kind, _KIND_COLOR["info"]), 26))
        icon.setFixedSize(27, 27)
        row.addWidget(icon, 0, Qt.AlignTop)

        self.lb_text = QLabel(text)
        self.lb_text.setObjectName("msgText")
        self.lb_text.setWordWrap(True)
        self.lb_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lb_text.setMaximumWidth(340)       # 控制窗口宽度，超长文本折行
        row.addWidget(self.lb_text, 1)
        body.addLayout(row)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        btns.addStretch(1)
        for i, (label, primary) in enumerate(options):
            btn = QPushButton(label)
            btn.setObjectName("msgAccent" if primary else "msgPlain")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, i=i: self._pick(i))
            if primary:
                btn.setDefault(True)
            btns.addWidget(btn)
        body.addLayout(btns)

        self.adjustSize()
        if self.width() < 400 + 2 * self.outer_margin:
            self.resize_content(400, self.size_content().height())

    # ---------- 结果 ----------
    def _pick(self, index: int) -> None:
        self._result = index
        self.accept()

    def result_index(self) -> int:
        """被点按钮的下标；直接关闭（Esc / 叉）返回 -1。"""
        return self._result


# ---------- 便捷入口（对齐 QMessageBox 的用法） ----------
def info(parent, title: str, text: str, ok: str = "确定") -> None:
    MessageDialog(parent, title, text, "info", [(ok, True)]).exec()


def warning(parent, title: str, text: str, ok: str = "确定") -> None:
    MessageDialog(parent, title, text, "warning", [(ok, True)]).exec()


def error(parent, title: str, text: str, ok: str = "确定") -> None:
    MessageDialog(parent, title, text, "error", [(ok, True)]).exec()


def question(parent, title: str, text: str, yes: str = "确定",
             no: str = "取消") -> bool:
    """确认框；点 yes 返回 True，点 no / 直接关闭返回 False。"""
    dlg = MessageDialog(parent, title, text, "question",
                        [(no, False), (yes, True)])
    dlg.exec()
    return dlg.result_index() == 1


def choose(parent, title: str, text: str,
           options: list[tuple[str, bool]], kind: str = "question") -> int:
    """多按钮选择框（options 为 (文案, 是否主按钮)）；返回下标，关闭返回 -1。"""
    dlg = MessageDialog(parent, title, text, kind, options)
    dlg.exec()
    return dlg.result_index()
