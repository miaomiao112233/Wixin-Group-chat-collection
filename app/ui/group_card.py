# -*- coding: utf-8 -*-
"""群卡片：大格子色块布局的单个监控群单元。"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QTextLayout, QTextOption
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QToolButton, QVBoxLayout)

from app.ui.frameless import ICON_COLOR, svg_icon

_GREEN = "#07C160"
_GRAY = "#9AA4AE"
CARD_WIDTH, CARD_HEIGHT = 300, 180      # 卡片固定尺寸（主窗口据此算列数）
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
QToolButton#cardIcon {
    background: transparent; border: none; border-radius: 6px;
}
QToolButton#cardIcon:hover { background: #E4F6EC; }
QToolButton#cardIcon:disabled { background: transparent; }
"""

# 卡片上的两个快捷入口图标（feather 风格，与标题栏窗口按钮同一套渲染）
_ICON_FOLDER = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="{s}" height="{s}" '
    'viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4L11 8.5h8.5A1.5 1.5 0 0 1 21 10v7.5a1.5 '
    '1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/></svg>')
_ICON_DOC = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="{s}" height="{s}" '
    'viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/>'
    '<path d="M14 3v5h5"/></svg>')


class ElidedLabel(QLabel):
    """按当前宽度自动省略的文本标签（原文放 tooltip）。

    替代 wordWrap + maxHeight 的组合：卡片是固定尺寸，折行的长群名/
    长预览会把行数撑多，布局放不下时各控件被竖向压缩、文字被裁掉。
    这里把文本截到限定的行数并加 …，每行标签高度恒定；仍由 QLabel
    自绘，QSS 样式不受影响。
    """

    def __init__(self, text: str = "", max_lines: int = 1, parent=None):
        super().__init__(parent)
        self._raw = ""
        self._max_lines = max(1, int(max_lines))
        self.setRawText(text)

    # ---------- 对外 ----------
    def setRawText(self, text: str) -> None:
        """设置任意长度的原文；显示时按当前宽度省略。"""
        raw = (text or "").replace("\r", " ").replace("\n", " ").strip()
        if raw == self._raw:
            return
        self._raw = raw
        self.setToolTip(raw)
        self._apply()

    def rawText(self) -> str:
        return self._raw

    # ---------- 内部 ----------
    def _apply(self) -> None:
        super().setText(self._elided())

    def _elided(self) -> str:
        raw = self._raw
        if not raw:
            return ""
        width = self.width() - 2        # 临界宽度留 1px 余量，防裁字
        if width <= 20:                 # 布局未确定：先放原文，resize 后重算
            return raw
        fm = self.fontMetrics()
        if self._max_lines == 1:
            return fm.elidedText(raw, Qt.ElideRight, width)
        # 多行：按当前宽度折行，超过 max_lines 时保留前 max_lines-1
        # 整行，其余文本在最后一行内横向省略。
        # QLabel 未开 wordWrap，须在各折行点插入真实换行符才生效。
        tl = QTextLayout(raw, self.font())
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        tl.setTextOption(opt)
        tl.beginLayout()
        line_starts: list[int] = []
        while True:
            line = tl.createLine()
            # 文本耗尽时返回无效行，必须先判 isValid 再调用其它方法
            #（对无效行 setLineWidth 会段错误）
            if not line.isValid():
                break
            line.setLineWidth(width)
            line.setPosition(QPointF(0.0, len(line_starts) * fm.lineSpacing()))
            line_starts.append(line.textStart())
        tl.endLayout()
        if len(line_starts) <= 1:
            return raw
        starts = line_starts[:self._max_lines]
        segs: list[str] = []
        for i, st in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(raw)
            segs.append(raw[st:end])
        if len(line_starts) > self._max_lines:
            # 可见行放不下剩余文本：最后一行内横向省略
            rest = raw[starts[-1]:]
            segs[-1] = fm.elidedText(rest, Qt.ElideRight, width)
        return "\n".join(segs)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._apply()

    def minimumSizeHint(self):
        # 完整文本的宽度不参与布局约束（由本类负责省略），
        # 否则超长文本会把水平布局撑爆、挤走同行控件
        hint = super().minimumSizeHint()
        return QSize(1, hint.height())


class GroupCard(QFrame):
    toggled = Signal(str, str, bool)     # chatroom, name, enabled
    summarize = Signal(str, str)         # chatroom, name
    removed = Signal(str, str)           # chatroom, name
    open_folder = Signal(str, str)       # chatroom, name
    open_today = Signal(str, str)        # chatroom, name

    def __init__(self, group: dict, status: dict | None = None,
                 has_doc: bool = False, parent=None):
        super().__init__(parent)
        self.chatroom = group["chatroom_id"]
        self.group_name = group["group_name"]
        self.setObjectName("groupCard")
        self.setStyleSheet(_CARD_QSS)
        # 高度 ≥ 布局最小需求（实测 174）：留 6px 余量给行间弹性，
        # 否则固定尺寸下布局反向压缩控件、文字被竖向裁掉
        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(8)

        # 行1：群名（单行省略）+ 快捷入口 + 开关
        top = QHBoxLayout()
        top.setSpacing(4)
        self.lb_title = ElidedLabel(self.group_name, 1)
        self.lb_title.setObjectName("cardTitle")
        top.addWidget(self.lb_title, 1)
        self.btn_folder = self._icon_button(
            _ICON_FOLDER, "打开该群的输出文件夹",
            lambda: self.open_folder.emit(self.chatroom, self.group_name))
        top.addWidget(self.btn_folder)
        self.btn_doc = self._icon_button(
            _ICON_DOC, "打开今日 Word 总结",
            lambda: self.open_today.emit(self.chatroom, self.group_name))
        self.btn_doc.setEnabled(bool(has_doc))
        top.addWidget(self.btn_doc)
        self.enabled = bool(group["enabled"])
        self.btn_toggle = QPushButton()
        self.btn_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_toggle.clicked.connect(self._on_toggle)
        top.addWidget(self.btn_toggle)
        v.addLayout(top)
        self._sync_toggle_btn()

        # 行2：今日新消息
        self.lb_new = ElidedLabel("", 1)
        self.lb_new.setObjectName("cardMeta")
        v.addWidget(self.lb_new)

        # 行3：最近消息预览（最多两行，超出省略）
        self.lb_last = ElidedLabel("", 2)
        self.lb_last.setObjectName("cardMeta")
        self.lb_last.setFixedHeight(34)
        v.addWidget(self.lb_last)

        # 行4：上次总结
        self.lb_summary = ElidedLabel("", 1)
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
            self.lb_new.setRawText("正在初始化监控…")
            self.lb_last.setRawText("正在读取今日消息…")
            self.lb_summary.setRawText("")
        else:
            self.update_status(status or {})

    # ---------- 快捷入口 ----------
    @staticmethod
    def _icon_button(svg: str, tip: str, on_click) -> QToolButton:
        btn = QToolButton()
        btn.setObjectName("cardIcon")
        btn.setFixedSize(24, 24)
        btn.setIconSize(QSize(16, 16))
        btn.setIcon(svg_icon(svg, ICON_COLOR, 16))
        btn.setToolTip(tip)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.clicked.connect(on_click)
        return btn

    def set_has_doc(self, has_doc: bool) -> None:
        """今日是否已有 Word 总结（决定「当日文档」图标是否可点）。"""
        self.btn_doc.setEnabled(bool(has_doc))

    # ---------- 状态 ----------
    def update_status(self, status: dict):
        # 今日累计（每轮都刷新，启动首轮即正确显示当天总数）
        tc = status.get("today_count")
        if isinstance(tc, int):
            self.lb_new.setRawText(f"今日新消息  {tc} 条")
        else:
            n = status.get("new_count")
            self.lb_new.setRawText(
                f"今日新消息  {n} 条" if isinstance(n, int)
                else "今日新消息  -")
        preview = status.get("last_msg") or "暂无新消息"
        self.lb_last.setRawText(f"最近: {preview[:40]}")
        self.lb_summary.setRawText("今日已生成总结" if status.get("summaried")
                                   else "今日尚未总结")

    def mark_summaried(self):
        self.lb_summary.setRawText("今日已生成总结")

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
