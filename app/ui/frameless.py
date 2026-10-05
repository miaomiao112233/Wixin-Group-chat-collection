# -*- coding: utf-8 -*-
"""无边框窗口框架：QQ / 微信 风格的自绘标题栏。

Windows 原生标题栏（DWM）会在左上角固定绘制窗口小图标、外观也无法定制，
所以这里整窗自绘。但"透明无边框"有个硬代价：``WA_TranslucentBackground``
会把窗口变成**分层窗口**（逐像素 alpha），Windows 就不再给它做 DWM 过渡
动画（开/关/最小化/还原/最大化缩放），观感反而比原生差。因此本模块提供
两种模式，Windows 上默认走前者：

1. **原生模式（Windows，默认）**：窗口本身不透明，把 Qt 去掉的
   ``WS_THICKFRAME / WS_CAPTION / WS_SYSMENU / WS_MIN/MAXBOX`` 加回来，
   并自己处理 ``WM_NCCALCSIZE``（整窗都是客户区 -> 系统边框不可见）与
   ``WM_NCHITTEST``（四周按系统厚度给缩放命中，其余给 HTCLIENT 让 Qt 收
   鼠标事件）。于是：DWM 的原生动画、贴边吸附、投影、Win11 圆角、双击/
   Win+方向键、任务栏预览全部保留，而标题栏仍然完全自绘。
2. **透明自绘模式（非 Windows，或原生改造失败时）**：顶层窗口开
   ``WA_TranslucentBackground``，靠 12px 留白 + ``QGraphicsDropShadowEffect``
   自绘圆角投影；拖动/缩放走 ``QWindow.startSystemMove/startSystemResize``。

两种模式下标题栏、按钮、正文布局完全一致，只是"窗口外壳"由谁画不同。

用法：

    class MyWindow(FramelessWindow):
        def __init__(self):
            super().__init__(title="标题", subtitle="副标题")
            self.titlebar.add_extra(QPushButton("自定义按钮"))
            self.body_layout.addWidget(...)      # 往卡片里塞正文
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QDialog, QFrame, QGraphicsDropShadowEffect,
                               QHBoxLayout, QLabel, QToolButton, QVBoxLayout,
                               QWidget)

# ---------- 尺寸常量 ----------
SHADOW_MARGIN = 12          # 透明模式的投影留白；同时也是四周缩放热区宽度
CORNER_RADIUS = 10          # 透明模式的窗口圆角半径（原生模式用系统圆角）
TITLE_BAR_HEIGHT = 56       # 自绘标题栏高度
WIN_BTN_W, WIN_BTN_H = 40, 30
CARD_BG = "#F3F5F4"         # 窗口底色（与卡片/内容区同色，视觉上是一整块）
ICON_COLOR = "#3A4642"
ICON_COLOR_HOVER = "#1F2D2A"


# ---------- 图标（feather 风格线性图标，颜色/尺寸可替换） ----------
_SVG_BODY = ('<svg xmlns="http://www.w3.org/2000/svg" width="{s}" height="{s}" '
             'viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" '
             'stroke-linecap="round" stroke-linejoin="round">{d}</svg>')
_SVG = {
    "min": _SVG_BODY.format(d='<path d="M5 12h14"/>', s="{s}", c="{c}"),
    "max": _SVG_BODY.format(
        d='<rect x="5" y="5" width="14" height="14" rx="2.5"/>', s="{s}", c="{c}"),
    "restore": _SVG_BODY.format(
        d='<rect x="4.5" y="8.5" width="11" height="11" rx="2.5"/>'
          '<path d="M8.5 5.5h8.5a2 2 0 0 1 2 2v8.5"/>', s="{s}", c="{c}"),
    "close": _SVG_BODY.format(d='<path d="M6 6l12 12M18 6L6 18"/>', s="{s}", c="{c}"),
}


def svg_pixmap(svg: str, color: str = ICON_COLOR, size: int = 16,
               scale: int = 2) -> QPixmap:
    """把 SVG 字符串渲染成位图（按 scale 放大渲染，HiDPI 下不糊）。"""
    px = int(round(size * scale))
    pm = QPixmap()
    pm.loadFromData(svg.format(c=color, s=px).encode("utf-8"), "SVG")
    pm.setDevicePixelRatio(scale)
    return pm


def svg_icon(svg: str, color: str = ICON_COLOR, size: int = 16) -> QIcon:
    return QIcon(svg_pixmap(svg, color, size))


# ---------- Windows 原生边框（保住 DWM 动画 / 圆角 / 投影） ----------
class _WinApi:
    """Win32 + DWM 的一小撮接口，用来把无边框窗口"改装"成原生窗口。

    这是本模块能在保留自绘标题栏的同时拿到系统动画的关键：
    窗口样式里必须有 WS_THICKFRAME/WS_CAPTION（否则 Windows 把它当
    WS_POPUP，不给阴影和过渡动画），再用 WM_NCCALCSIZE 把不可见的系统
    边框吃掉，用 WM_NCHITTEST 自己回答缩放命中。
    """

    # 窗口样式位
    GWL_STYLE, GWL_EXSTYLE = -16, -20
    WS_CAPTION = 0x00C00000
    WS_THICKFRAME = 0x00040000
    WS_SYSMENU = 0x00080000
    WS_MINIMIZEBOX = 0x00020000
    WS_MAXIMIZEBOX = 0x00010000
    WS_POPUP = 0x80000000
    WS_EX_LAYERED = 0x00080000
    # 消息与命中码
    WM_NCCALCSIZE = 0x0083
    WM_NCHITTEST = 0x0084
    HTCLIENT = 1
    HTLEFT, HTRIGHT, HTTOP = 10, 11, 12
    HTTOPLEFT, HTTOPRIGHT = 13, 14
    HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17
    ISZOOMED = 2
    # DWM 属性
    DWMWA_TRANSITIONS_FORCEDISABLED = 3
    DWMWA_WINDOW_CORNER_PREFERENCE = 33
    DWMWA_BORDER_COLOR = 34
    DWMWCP_ROUND = 2
    DWMWA_COLOR_NONE = 0xFFFFFFFE
    # 系统度量 / SetWindowPos
    SM_CXSIZEFRAME, SM_CYSIZEFRAME, SM_CXPADDEDBORDER = 32, 33, 92
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_FRAMECHANGED = 1, 2, 4, 0x20

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.c = ctypes
        self.w = wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        u = self.user32
        # 64 位下必须声明 argtypes/restype，否则 HWND/LPARAM 会被截断
        u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int,
                                        ctypes.c_ssize_t]
        u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.GetWindowRect.argtypes = [wintypes.HWND,
                                    ctypes.POINTER(wintypes.RECT)]
        u.GetSystemMetrics.argtypes = [ctypes.c_int]
        u.IsZoomed.argtypes = [wintypes.HWND]
        u.IsIconic.argtypes = [wintypes.HWND]
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_uint]

        class NCCALCSIZE_PARAMS(ctypes.Structure):
            _fields_ = [("rgrc", wintypes.RECT * 3),
                        ("lppos", ctypes.c_void_p)]

        self.NCCALCSIZE_PARAMS = NCCALCSIZE_PARAMS
        self.build = getattr(sys, "getwindowsversion", lambda: None)()
        self.build = getattr(self.build, "build", 0)

    # ---------- 工具 ----------
    def msg(self, message):
        """把 Qt 给的 nativeEvent message 指针转成 MSG。"""
        return self.w.MSG.from_address(int(message))

    def _border(self):
        u = self.user32
        fx = (u.GetSystemMetrics(self.SM_CXSIZEFRAME)
              + u.GetSystemMetrics(self.SM_CXPADDEDBORDER))
        fy = (u.GetSystemMetrics(self.SM_CYSIZEFRAME)
              + u.GetSystemMetrics(self.SM_CXPADDEDBORDER))
        return fx, fy

    # ---------- 改装 ----------
    def apply(self, hwnd: int, *, resizable: bool, rounded: bool = True) -> bool:
        """给窗口加回原生边框样式并设置 DWM 圆角；成功与否都会返回。"""
        u, c = self.user32, self.c
        style = u.GetWindowLongPtrW(hwnd, self.GWL_STYLE)
        style &= ~self.WS_POPUP
        style |= (self.WS_CAPTION | self.WS_SYSMENU
                  | self.WS_MINIMIZEBOX | self.WS_MAXIMIZEBOX)
        if resizable:
            style |= self.WS_THICKFRAME
        else:
            style &= ~self.WS_THICKFRAME
        u.SetWindowLongPtrW(hwnd, self.GWL_STYLE, style)
        ex = u.GetWindowLongPtrW(hwnd, self.GWL_EXSTYLE) & ~self.WS_EX_LAYERED
        u.SetWindowLongPtrW(hwnd, self.GWL_EXSTYLE, ex)

        # 显式允许过渡动画（某些主题/策略会关掉）
        off = c.c_int(0)
        self.dwmapi.DwmSetWindowAttribute(
            hwnd, self.DWMWA_TRANSITIONS_FORCEDISABLED,
            c.byref(off), c.sizeof(off))
        if rounded and self.build >= 22000:            # Windows 11
            pref = c.c_int(self.DWMWCP_ROUND)
            self.dwmapi.DwmSetWindowAttribute(
                hwnd, self.DWMWA_WINDOW_CORNER_PREFERENCE,
                c.byref(pref), c.sizeof(pref))
            none = c.c_uint(self.DWMWA_COLOR_NONE)     # 去掉 1px 描边
            self.dwmapi.DwmSetWindowAttribute(
                hwnd, self.DWMWA_BORDER_COLOR, c.byref(none), c.sizeof(none))
        u.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                       self.SWP_NOSIZE | self.SWP_NOMOVE
                       | self.SWP_NOZORDER | self.SWP_FRAMECHANGED)
        # 回读确认改装生效（否则退回透明自绘模式）
        style = u.GetWindowLongPtrW(hwnd, self.GWL_STYLE)
        ex = u.GetWindowLongPtrW(hwnd, self.GWL_EXSTYLE)
        return bool(style & self.WS_CAPTION) and not (ex & self.WS_EX_LAYERED)

    # ---------- 消息处理 ----------
    def nccalcsize(self, msg):
        """WM_NCCALCSIZE：客户区 = 整个窗口（系统边框不可见）。

        最大化时窗口会比工作区各多出边框厚度，这里按厚度内缩，避免内容
        盖住任务栏。
        """
        if msg.wParam and self.user32.IsZoomed(msg.hWnd):
            params = self.NCCALCSIZE_PARAMS.from_address(msg.lParam)
            fx, fy = self._border()
            rect = params.rgrc[0]
            rect.left += fx
            rect.top += fy
            rect.right -= fx
            rect.bottom -= fy
        return True, 0

    def nchittest(self, msg):
        """WM_NCHITTEST：四周给缩放命中，其余交给 Qt 当客户区。"""
        u = self.user32
        style = u.GetWindowLongPtrW(msg.hWnd, self.GWL_STYLE)
        if not (style & self.WS_THICKFRAME):
            return True, self.HTCLIENT            # 固定尺寸对话框
        if u.IsZoomed(msg.hWnd) or u.IsIconic(msg.hWnd):
            return True, self.HTCLIENT            # 最大化/最小化时不能拉伸
        lparam = int(msg.lParam)
        x = self.c.c_short(lparam & 0xFFFF).value
        y = self.c.c_short((lparam >> 16) & 0xFFFF).value
        rect = self.w.RECT()
        u.GetWindowRect(msg.hWnd, self.c.byref(rect))
        fx, fy = self._border()
        left = x < rect.left + fx
        right = x >= rect.right - fx
        top = y < rect.top + fy
        bottom = y >= rect.bottom - fy
        if top and left:
            return True, self.HTTOPLEFT
        if top and right:
            return True, self.HTTOPRIGHT
        if bottom and left:
            return True, self.HTBOTTOMLEFT
        if bottom and right:
            return True, self.HTBOTTOMRIGHT
        if left:
            return True, self.HTLEFT
        if right:
            return True, self.HTRIGHT
        if top:
            return True, self.HTTOP
        if bottom:
            return True, self.HTBOTTOM
        return True, self.HTCLIENT


_WIN_API = None
_WIN_API_READY = False


def _win_api():
    """惰性加载原生接口；非 Windows 或加载失败返回 None。"""
    global _WIN_API, _WIN_API_READY
    if not _WIN_API_READY:
        _WIN_API_READY = True
        if sys.platform == "win32":
            try:
                _WIN_API = _WinApi()
            except Exception:                       # noqa: BLE001
                _WIN_API = None
    return _WIN_API


class WindowButton(QToolButton):
    """标题栏右侧的最小化 / 最大化 / 关闭按钮（自绘图标 + 悬停高亮）。"""

    _NAMES = {"min": "winMin", "max": "winMax", "close": "winClose"}

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._icon_name = kind
        self._hover = False
        self.setObjectName(self._NAMES[kind])
        self.setFixedSize(WIN_BTN_W, WIN_BTN_H)
        self.setCursor(Qt.ArrowCursor)          # 与 Windows 原生一致
        self.setFocusPolicy(Qt.NoFocus)
        self.setIconSize(QSize(14, 14))
        self._apply_icon()

    # ---------- 外观 ----------
    def set_icon_name(self, name: str) -> None:
        """最大化 <-> 还原图标切换。"""
        if name == self._icon_name:
            return
        self._icon_name = name
        self._apply_icon()

    def _apply_icon(self) -> None:
        if self._kind == "close" and self._hover:
            color = "#FFFFFF"                   # 关闭键悬停变红底白叉
        elif self._hover:
            color = ICON_COLOR_HOVER
        else:
            color = ICON_COLOR
        self.setIcon(svg_icon(_SVG[self._icon_name], color, 14))

    def enterEvent(self, ev):
        self._hover = True
        self._apply_icon()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hover = False
        self._apply_icon()
        super().leaveEvent(ev)


class TitleBar(QWidget):
    """自绘标题栏：标题（+ 副标题）在左，自定义按钮与窗口按钮在右。

    交互：拖动任意空白 = 移动窗口（系统级，保留吸附）；双击 =
    最大化 / 还原（仅在有最大化按钮时）；三个按钮分别最小化 / 最大化 /
    关闭（关闭走 ``window().close()``，所以"关闭=隐藏到托盘"照旧生效）。
    """

    _QSS = """
    QWidget#titleBar { background: transparent; }
    QLabel#tbTitle { font-size: 14px; font-weight: 600; color: #1F2D2A; }
    QLabel#tbSub { font-size: 11px; color: #8A9491; }
    QToolButton#winMin, QToolButton#winMax {
        background: transparent; border: none; border-radius: 6px;
    }
    QToolButton#winMin:hover, QToolButton#winMax:hover { background: #E2E7E5; }
    QToolButton#winMin:pressed, QToolButton#winMax:pressed { background: #D3DBD8; }
    QToolButton#winClose { background: transparent; border: none; border-radius: 6px; }
    QToolButton#winClose:hover { background: #E81123; }
    QToolButton#winClose:pressed { background: #C50F1F; }
    """

    def __init__(self, title: str = "", subtitle: str = "",
                 maximizable: bool = True, minimizable: bool = True,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setStyleSheet(self._QSS)
        self.setFixedHeight(TITLE_BAR_HEIGHT)
        self._maximizable = bool(maximizable)
        self._drag_origin: QPoint | None = None
        self._drag_geo: QRect | None = None

        row = QHBoxLayout(self)
        row.setContentsMargins(18, 4, 10, 4)
        row.setSpacing(8)

        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        text_box.setContentsMargins(0, 0, 0, 0)
        self.lb_title = QLabel(title)
        self.lb_title.setObjectName("tbTitle")
        self.lb_sub = QLabel(subtitle)
        self.lb_sub.setObjectName("tbSub")
        text_box.addWidget(self.lb_title)
        if subtitle:
            text_box.addWidget(self.lb_sub)
        else:
            self.lb_sub.hide()
        row.addLayout(text_box)
        row.addStretch(1)

        self._extra = QHBoxLayout()
        self._extra.setSpacing(8)
        row.addLayout(self._extra)

        self.btn_min: WindowButton | None = None
        if minimizable:
            self.btn_min = WindowButton("min")
            self.btn_min.setToolTip("最小化")
            self.btn_min.clicked.connect(lambda: self.window().showMinimized())
            row.addWidget(self.btn_min)

        self.btn_max: WindowButton | None = None
        if self._maximizable:
            self.btn_max = WindowButton("max")
            self.btn_max.setToolTip("最大化")
            self.btn_max.clicked.connect(self._toggle_max)
            row.addWidget(self.btn_max)

        self.btn_close = WindowButton("close")
        self.btn_close.setToolTip("关闭")
        self.btn_close.clicked.connect(lambda: self.window().close())
        row.addWidget(self.btn_close)

    # ---------- 对外 ----------
    def add_extra(self, widget: QWidget) -> None:
        """往标题栏右侧（窗口按钮左边）追加自定义控件。"""
        self._extra.addWidget(widget)

    def set_title(self, text: str) -> None:
        self.lb_title.setText(text)

    def set_subtitle(self, text: str) -> None:
        self.lb_sub.setText(text)
        self.lb_sub.setVisible(bool(text))

    def sync_state(self) -> None:
        """窗口最大化状态变化后刷新按钮图标/提示。"""
        if self.btn_max is None:
            return
        maxed = self.window().isMaximized()
        self.btn_max.set_icon_name("restore" if maxed else "max")
        self.btn_max.setToolTip("向下还原" if maxed else "最大化")

    # ---------- 交互 ----------
    def _toggle_max(self) -> None:
        win = self.window()
        toggle = getattr(win, "toggle_maximize", None)
        if callable(toggle):
            toggle()
        elif win.isMaximized():
            win.showNormal()
        else:
            win.showMaximized()

    def _begin_system_move(self) -> bool:
        wh = self.window().windowHandle()
        if wh is None:
            return False
        try:
            return bool(wh.startSystemMove())
        except Exception:                       # noqa: BLE001
            return False

    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            super().mousePressEvent(ev)
            return
        if self._begin_system_move():
            ev.accept()
            return
        # 兜底：个别平台 startSystemMove 不可用时，自己跟着鼠标挪
        self._drag_origin = ev.globalPosition().toPoint()
        self._drag_geo = self.window().geometry()
        ev.accept()

    def mouseMoveEvent(self, ev):
        if self._drag_origin is not None and self._drag_geo is not None:
            delta = ev.globalPosition().toPoint() - self._drag_origin
            self.window().move(self._drag_geo.topLeft() + delta)
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        self._drag_origin = None
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.LeftButton and self._maximizable:
            self._toggle_max()
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)


class _FramelessMixin:
    """无边框窗口的公共实现（QWidget / QDialog 共用，请配合 mixin 使用）。"""

    # ---------- 初始化 ----------
    def _init_frameless(self, *, title: str = "", subtitle: str = "",
                        title_bar: bool = True, maximizable: bool = True,
                        minimizable: bool = True, resizable: bool = True,
                        radius: int = CORNER_RADIUS, shadow: bool = True,
                        background: str = CARD_BG,
                        native: bool | None = None) -> None:
        # native=None 表示自动：Windows 上用原生边框（动画/圆角/投影都是
        # 系统的），其它平台或改装失败时退回透明自绘
        self._fs_api = _win_api() if native is not False else None
        self._fs_native = self._fs_api is not None
        self._fs_hwnd = 0
        self._fs_resizable = bool(resizable)
        if self._fs_native:
            self._fs_outer = 0                    # 不留白，投影由 DWM 画
            self._fs_radius = 0                   # 圆角由 DWM 画
            self._fs_use_shadow = False
        else:
            self._fs_outer = SHADOW_MARGIN if shadow else 0
            self._fs_radius = int(radius)
            self._fs_use_shadow = bool(shadow)
        self._fs_bg = background
        self._fs_resize_edges = None
        self._fs_resize_origin: QPoint | None = None
        self._fs_resize_geo: QRect | None = None

        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        if not self._fs_native:
            self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(self._fs_outer, self._fs_outer,
                                 self._fs_outer, self._fs_outer)
        outer.setSpacing(0)

        self._fs_card = QFrame(self)
        self._fs_card.setObjectName("winCard")
        # 卡片内显式给箭头光标：否则卡片空白继承顶层的缩放光标，
        # 鼠标离开边缘后箭头不会恢复
        self._fs_card.setCursor(Qt.ArrowCursor)
        outer.addWidget(self._fs_card)

        self._fs_shadow: QGraphicsDropShadowEffect | None = None
        if self._fs_use_shadow:
            eff = QGraphicsDropShadowEffect(self._fs_card)
            eff.setBlurRadius(32)
            eff.setOffset(0, 5)
            eff.setColor(QColor(15, 30, 25, 70))
            self._fs_card.setGraphicsEffect(eff)
            self._fs_shadow = eff

        body = QVBoxLayout(self._fs_card)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.body_layout = body                 # 子类把正文塞这里
        self.titlebar: TitleBar | None = None
        if title_bar:
            self.titlebar = TitleBar(title, subtitle,
                                     maximizable=maximizable,
                                     minimizable=minimizable,
                                     parent=self._fs_card)
            body.addWidget(self.titlebar)
        self._apply_card_style()
        if self._fs_native:
            self._apply_native_frame()

    # ---------- 原生边框 ----------
    def _apply_native_frame(self) -> bool:
        """把窗口改装成"有原生边框但看不见"的窗口；失败则退回透明自绘。"""
        api = self._fs_api
        if api is None:
            return False
        try:
            hwnd = int(self.winId())            # 触发原生窗口创建
            ok = api.apply(hwnd, resizable=self._fs_resizable)
        except Exception:                       # noqa: BLE001
            ok = False
        self._fs_hwnd = int(self.winId()) if ok else 0
        if not ok:
            # 改装失败（例如非 Windows 平台插件）：退回不透明方角窗口，
            # 功能不受影响，只是没有系统圆角/投影
            self._fs_native = False
            self._fs_api = None
        return ok

    # ---------- 对外 ----------
    @property
    def card(self) -> QFrame:
        """圆角卡片本体（需要给整窗设背景/样式时用）。"""
        return self._fs_card

    @property
    def outer_margin(self) -> int:
        """投影留白宽度（窗口尺寸 - 内容区尺寸）。"""
        return self._fs_outer

    def resize_content(self, width: int, height: int) -> None:
        """按"内容区尺寸"设置窗口大小（自动加上投影留白）。"""
        self.resize(int(width) + 2 * self._fs_outer,
                    int(height) + 2 * self._fs_outer)

    def size_content(self):
        return self._fs_card.size()

    def set_card_background(self, color: str) -> None:
        self._fs_bg = color
        self._apply_card_style()

    def toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    # ---------- 绘制 / 状态 ----------
    def _apply_card_style(self) -> None:
        radius = 0 if (self.isMaximized() or self.isFullScreen()) else self._fs_radius
        self._fs_card.setStyleSheet(
            f"QFrame#winCard {{ background: {self._fs_bg}; "
            f"border-radius: {radius}px; }}")

    def _sync_frame_state(self) -> None:
        """最大化/还原后：去掉或恢复投影留白、圆角与投影。"""
        maxed = self.isMaximized() or self.isFullScreen()
        margin = 0 if maxed else self._fs_outer
        lay = self.layout()
        if lay is not None:
            lay.setContentsMargins(margin, margin, margin, margin)
        if self._fs_shadow is not None:
            self._fs_shadow.setEnabled(self._fs_use_shadow and not maxed)
        self._apply_card_style()
        if maxed:
            self.setCursor(Qt.ArrowCursor)
        if self.titlebar is not None:
            self.titlebar.sync_state()

    def changeEvent(self, ev):
        if ev.type() == QEvent.WindowStateChange:
            self._sync_frame_state()
        super().changeEvent(ev)

    # ---------- 原生消息（原生模式才有意义） ----------
    def nativeEvent(self, ev_type, message):
        api = self._fs_api
        if api is not None:
            try:
                msg = api.msg(message)
            except Exception:                       # noqa: BLE001
                msg = None
            if msg is not None:
                if msg.message == api.WM_NCCALCSIZE:
                    return api.nccalcsize(msg)
                if msg.message == api.WM_NCHITTEST:
                    return api.nchittest(msg)
        return super().nativeEvent(ev_type, message)

    # ---------- 缩放（四周留白热区） ----------
    def _edges_at(self, pos: QPoint):
        if not self._fs_resizable or self._fs_outer <= 0:
            return None
        if self.isMaximized() or self.isFullScreen():
            return None
        band = self._fs_outer + 2                # 略超出留白，边缘更好抓
        rect = self.rect()
        edges = []
        if pos.x() <= band:
            edges.append(Qt.LeftEdge)
        elif pos.x() >= rect.width() - band:
            edges.append(Qt.RightEdge)
        if pos.y() <= band:
            edges.append(Qt.TopEdge)
        elif pos.y() >= rect.height() - band:
            edges.append(Qt.BottomEdge)
        if not edges:
            return None
        flag = Qt.Edge(0)
        for e in edges:
            flag |= e
        return flag

    @staticmethod
    def _cursor_for(edges):
        left = bool(edges & Qt.LeftEdge)
        right = bool(edges & Qt.RightEdge)
        top = bool(edges & Qt.TopEdge)
        bottom = bool(edges & Qt.BottomEdge)
        if (left and top) or (right and bottom):
            return Qt.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return Qt.SizeBDiagCursor
        if left or right:
            return Qt.SizeHorCursor
        return Qt.SizeVerCursor

    def mouseMoveEvent(self, ev):
        edges = self._edges_at(ev.position().toPoint())
        self.setCursor(self._cursor_for(edges) if edges else Qt.ArrowCursor)
        if self._fs_resize_edges is not None and self._fs_resize_origin is not None:
            self._manual_resize(ev.globalPosition().toPoint())
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            edges = self._edges_at(ev.position().toPoint())
            if edges is not None:
                wh = self.windowHandle()
                if wh is not None:
                    try:
                        if wh.startSystemResize(edges):
                            ev.accept()
                            return
                    except Exception:           # noqa: BLE001
                        pass
                # 兜底：自己算几何
                self._fs_resize_edges = edges
                self._fs_resize_origin = ev.globalPosition().toPoint()
                self._fs_resize_geo = self.geometry()
                ev.accept()
                return
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        self._fs_resize_edges = None
        self._fs_resize_origin = None
        self._fs_resize_geo = None
        super().mouseReleaseEvent(ev)

    def leaveEvent(self, ev):
        if self._fs_resize_edges is None:
            self.setCursor(Qt.ArrowCursor)
        super().leaveEvent(ev)

    def _manual_resize(self, gpos: QPoint) -> None:
        if self._fs_resize_geo is None or self._fs_resize_origin is None:
            return
        delta = gpos - self._fs_resize_origin
        geo = QRect(self._fs_resize_geo)
        min_w = max(self.minimumWidth(), 240)
        min_h = max(self.minimumHeight(), 160)
        edges = self._fs_resize_edges
        if edges & Qt.LeftEdge:
            geo.setLeft(min(geo.left() + delta.x(), geo.right() - min_w + 1))
        if edges & Qt.TopEdge:
            geo.setTop(min(geo.top() + delta.y(), geo.bottom() - min_h + 1))
        if edges & Qt.RightEdge:
            geo.setRight(max(geo.right() + delta.x(), geo.left() + min_w - 1))
        if edges & Qt.BottomEdge:
            geo.setBottom(max(geo.bottom() + delta.y(), geo.top() + min_h - 1))
        self.setGeometry(geo)

    # ---------- 最大化贴齐工作区 ----------
    def _fix_max_geometry(self) -> None:
        """个别环境下无边框最大化会盖住任务栏，这里按工作区校正一次。"""
        if not self.isMaximized():
            return
        screen = self.screen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        geo = self.geometry()
        if geo.width() > avail.width() + 2 or geo.height() > avail.height() + 2:
            self.setGeometry(avail)

    def showEvent(self, ev):
        super().showEvent(ev)
        # 原生窗口句柄若被 Qt 重建（改过 flags 等），重新改装一次
        if self._fs_native and self._fs_api is not None:
            try:
                if int(self.winId()) != self._fs_hwnd:
                    self._apply_native_frame()
            except Exception:                       # noqa: BLE001
                pass
        if self.isMaximized():
            self._fix_max_geometry()


class FramelessWindow(_FramelessMixin, QWidget):
    """无边框主窗口：自绘标题栏 + 系统边框/投影/圆角，可拖动 / 缩放 / 最大化。"""

    def __init__(self, parent=None, *, title: str = "", subtitle: str = "",
                 maximizable: bool = True, minimizable: bool = True,
                 resizable: bool = True, radius: int = CORNER_RADIUS,
                 shadow: bool = True, background: str = CARD_BG,
                 title_bar: bool = True, native: bool | None = None):
        QWidget.__init__(self, parent)
        self._init_frameless(title=title, subtitle=subtitle, title_bar=title_bar,
                             maximizable=maximizable, minimizable=minimizable,
                             resizable=resizable, radius=radius,
                             shadow=shadow, background=background,
                             native=native)


class FramelessDialog(_FramelessMixin, QDialog):
    """无边框对话框：标题栏只保留关闭按钮，默认不缩放、居中于父窗口。"""

    def __init__(self, parent=None, *, title: str = "", subtitle: str = "",
                 resizable: bool = False, radius: int = CORNER_RADIUS,
                 shadow: bool = True, background: str = CARD_BG,
                 native: bool | None = None):
        QDialog.__init__(self, parent)
        self._init_frameless(title=title, subtitle=subtitle, title_bar=True,
                             maximizable=False, minimizable=False,
                             resizable=resizable, radius=radius,
                             shadow=shadow, background=background,
                             native=native)
        self.setSizeGripEnabled(False)
        self._centered = False

    def showEvent(self, ev):
        super().showEvent(ev)
        # QDialog 自带居中，但无边框（含投影留白）下再校正一次更稳
        if not self._centered:
            self._centered = True
            parent = self.parentWidget()
            if parent is not None:
                host = parent.window()
                center = host.frameGeometry().center()
                self.move(center.x() - self.width() // 2,
                          center.y() - self.height() // 2)
