# -*- coding: utf-8 -*-
"""更新对话框 + 检查/下载后台线程。

- CheckThread：请求 GitHub API（供启动静默检查、托盘手动检查复用）
- DownloadThread：流式下载 zip，带进度，可取消，下完校验 SHA-256
- UpdateDialog：版本信息 / Release 说明 / 立即更新（进度条）/ 跳过 / 稍后
  （无边框自绘标题栏，与主窗口风格一致）
"""
from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import QThread, Signal, Qt, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QTextEdit, QVBoxLayout)

from app.config import APP_VERSION, GITHUB_REPO, save_skip_version
from app.core import updater
from app.core.updater import ReleaseInfo
from app.ui import msgbox
from app.ui.frameless import FramelessDialog


# ---------- 后台线程 ----------
class CheckThread(QThread):
    ok = Signal(object)
    error = Signal(str)

    def run(self):
        try:
            self.ok.emit(updater.check_latest())
        except Exception as e:                       # noqa: BLE001
            self.error.emit(f"{type(e).__name__}: {e}")


class DownloadThread(QThread):
    progress = Signal(int, int)        # done, total (bytes)
    status = Signal(str)               # 校验中 / 校验通过 / 跳过校验
    ok = Signal(str)                   # 下载好的文件路径
    error = Signal(str)

    def __init__(self, url: str, dest: str,
                 release: ReleaseInfo | None = None, parent=None):
        super().__init__(parent)
        self._url = url
        self._dest = dest
        self._release = release        # ReleaseInfo：提供 SHA-256 校验地址
        import threading
        self._cancel = threading.Event()

    def request_cancel(self):
        self._cancel.set()

    def run(self):
        try:
            updater.download(
                self._url, self._dest,
                progress_cb=lambda d, t: self.progress.emit(d, t),
                cancel_event=self._cancel)
            # 下载完成 → SHA-256 校验（无校验文件时只是记日志并放行）
            self._verify()
            self.ok.emit(self._dest)
        except Exception as e:                       # noqa: BLE001
            try:
                if os.path.exists(self._dest):
                    os.remove(self._dest)
            except OSError:
                pass
            if self._cancel.is_set():
                self.error.emit("已取消")
            else:
                self.error.emit(f"{type(e).__name__}: {e}")

    def _verify(self):
        """比对官方 SHA-256；不一致抛 RuntimeError（由 run 统一清理）。"""
        if self._cancel.is_set():
            raise RuntimeError("用户取消下载")
        self.status.emit("校验中…（正在获取官方 SHA-256）")
        try:
            text = updater.verify_release_checksum(
                self._dest, release=self._release,
                cancel_event=self._cancel)
        except RuntimeError as e:
            if "取消" in str(e):
                raise
            self.status.emit("校验失败")
            raise
        self.status.emit(text)


# ---------- 对话框 ----------
_DLG_QSS = """
QLabel#upTitle { font-size: 17px; font-weight: 700; color: #1F2D2A; }
QLabel#upMeta { font-size: 12px; color: #7A8683; }
QTextEdit {
    background: #FFFFFF; border: 1px solid #E6EAE8; border-radius: 10px;
    color: #3A4642; font-size: 13px; padding: 6px;
}
QPushButton {
    background: #FFFFFF; color: #2B3A35; border: 1px solid #E2E7E5;
    border-radius: 10px; padding: 7px 16px; font-size: 13px;
}
QPushButton:hover { border-color: #07C160; color: #07C160; }
QPushButton#accent {
    background: #07C160; color: white; border: none; font-weight: 600;
}
QPushButton#accent:hover { background: #06AD56; color: white; }
QProgressBar {
    border: 1px solid #E2E7E5; border-radius: 8px;
    background: #FFFFFF; height: 18px; text-align: center;
    font-size: 12px; color: #2B3A35;
}
QProgressBar::chunk { background: #07C160; border-radius: 7px; }
"""

# 进度文字配色：默认灰 / 通过绿 / 失败红
_GRAY = "#7A8683"
_GREEN = "#07C160"
_RED = "#D93025"


def _label_qss(color: str) -> str:
    return f"color:{color}; font-size:12px;"


class UpdateDialog(FramelessDialog):
    def __init__(self, release: ReleaseInfo, parent=None):
        super().__init__(parent, title="软件更新")
        self._release = release
        self._dl_thread: DownloadThread | None = None
        self.setStyleSheet(_DLG_QSS)
        self.resize_content(520, 470)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout()
        root.setContentsMargins(22, 6, 22, 18)
        root.setSpacing(12)
        self.body_layout.addLayout(root)

        title = QLabel(f"发现新版本 {self._release.tag}")
        title.setObjectName("upTitle")
        meta = QLabel(f"当前版本 v{APP_VERSION}"
                      f" · 更新包 {self._release.size_mb}"
                      + (" · 预发布" if self._release.prerelease else ""))
        meta.setObjectName("upMeta")
        root.addWidget(title)
        root.addWidget(meta)

        notes = QTextEdit()
        notes.setReadOnly(True)
        notes.setMinimumHeight(200)
        if self._release.notes:
            notes.setMarkdown(self._release.notes)
        else:
            notes.setPlainText("（作者未填写更新说明）")
        root.addWidget(notes, 1)

        # 按钮行
        self._btn_row = QHBoxLayout()
        self.btn_skip = QPushButton("跳过此版本")
        self.btn_later = QPushButton("以后再说")
        self.btn_update = QPushButton("立即更新")
        self.btn_update.setObjectName("accent")
        for b in (self.btn_skip, self.btn_later):
            b.setCursor(Qt.PointingHandCursor)
        self.btn_update.setCursor(Qt.PointingHandCursor)
        self._btn_row.addWidget(self.btn_skip)
        self._btn_row.addStretch(1)
        self._btn_row.addWidget(self.btn_later)
        self._btn_row.addWidget(self.btn_update)
        root.addLayout(self._btn_row)

        # 进度区（默认隐藏）
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        self.lb_pct = QLabel("")
        self.lb_pct.setStyleSheet("color:#7A8683; font-size:12px;")
        self.lb_pct.hide()
        self.btn_cancel_dl = QPushButton("取消下载")
        self.btn_cancel_dl.setCursor(Qt.PointingHandCursor)
        self.btn_cancel_dl.hide()
        prog_row = QHBoxLayout()
        prog_row.addWidget(self.progress, 1)
        prog_row.addWidget(self.lb_pct)
        prog_row.addWidget(self.btn_cancel_dl)
        root.addLayout(prog_row)

        self.btn_skip.clicked.connect(self._on_skip)
        self.btn_later.clicked.connect(self.reject)
        self.btn_update.clicked.connect(self._on_update)
        self.btn_cancel_dl.clicked.connect(self._on_cancel_download)

    # ---------- 交互 ----------
    def _on_skip(self):
        save_skip_version(self._release.version)
        self.accept()

    def _on_update(self):
        if not self._release.url:
            # Release 未挂 zip：引导到下载页
            QDesktopServices.openUrl(
                QUrl(f"https://github.com/{GITHUB_REPO}/releases/latest"))
            self.reject()
            return
        self._enter_download_mode()
        fd, dest = tempfile.mkstemp(prefix="WxSum-update-", suffix=".zip")
        os.close(fd)
        os.remove(dest)             # mkstemp 生成空文件，下载流程自己写
        self._dl_thread = DownloadThread(self._release.url, dest,
                                         self._release, self)
        self._dl_thread.progress.connect(self._on_progress)
        self._dl_thread.status.connect(self._on_status)
        self._dl_thread.ok.connect(self._on_downloaded)
        self._dl_thread.error.connect(self._on_download_error)
        self._dl_thread.start()

    def _enter_download_mode(self):
        for b in (self.btn_skip, self.btn_later, self.btn_update):
            b.hide()
        self.progress.show()
        self.lb_pct.show()
        self.btn_cancel_dl.show()

    def _exit_download_mode(self):
        for b in (self.btn_skip, self.btn_later, self.btn_update):
            b.show()
        self.progress.hide()
        self.lb_pct.hide()
        self.btn_cancel_dl.hide()

    @staticmethod
    def _mb(n: int) -> str:
        return f"{n / 1048576:.1f} MB"

    def _on_progress(self, done: int, total: int):
        if total > 0:
            pct = int(done * 100 / total)
            self.progress.setRange(0, 100)
            self.progress.setValue(pct)
            self.lb_pct.setText(f"{pct}%  {self._mb(done)} / {self._mb(total)}")
        else:
            self.progress.setRange(0, 0)          # 忙碌指示
            self.lb_pct.setText(f"已下载 {self._mb(done)}")

    def _on_status(self, text: str):
        """SHA-256 校验阶段的状态文字（校验中/校验通过/校验失败/跳过）。"""
        color = _GRAY
        if "失败" in text or "不一致" in text:
            color = _RED
        elif "通过" in text or "跳过" in text:
            color = _GREEN
        self.lb_pct.setStyleSheet(_label_qss(color))
        self.lb_pct.setText(text)

    def _on_cancel_download(self):
        if self._dl_thread and self._dl_thread.isRunning():
            self._dl_thread.request_cancel()
            self.btn_cancel_dl.setEnabled(False)

    def _on_downloaded(self, path: str):
        self.lb_pct.setText("下载完成，正在准备更新…")
        try:
            updater.apply_and_restart(path)
        except Exception as e:                    # noqa: BLE001
            msgbox.warning(self, "更新失败", str(e))
            self._exit_download_mode()
            return
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.lb_pct.setText("更新就绪，程序将关闭并自动重启")
        self.btn_cancel_dl.hide()
        # 给分离的更新器一点时间确认启动，然后退出主程序
        from app.ui.main_window import MainWindow
        win = self.parent()
        if not isinstance(win, MainWindow):
            win = None
        QTimer.singleShot(900, lambda: win and win.really_quit())

    def _on_download_error(self, msg: str):
        self._exit_download_mode()
        self.btn_cancel_dl.setEnabled(True)
        if "校验失败" in msg:
            # 失败原因（含 SHA-256）直接留在进度文字上，不只弹一次对话框
            self.lb_pct.setStyleSheet(_label_qss(_RED))
            self.lb_pct.setText("校验失败：已删除下载文件，未安装")
            self.lb_pct.show()
        if msg != "已取消":
            msgbox.warning(self, "下载失败", msg)
        else:
            self.lb_pct.setStyleSheet(_label_qss(_GRAY))

    def closeEvent(self, ev):
        if self._dl_thread and self._dl_thread.isRunning():
            self._on_cancel_download()
        super().closeEvent(ev)
