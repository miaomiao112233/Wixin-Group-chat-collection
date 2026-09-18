# -*- coding: utf-8 -*-
"""更新对话框 + 检查/下载后台线程。

- CheckThread：请求 GitHub API（供启动静默检查、托盘手动检查复用）
- DownloadThread：流式下载 zip，带进度，可取消
- UpdateDialog：版本信息 / Release 说明 / 立即更新（进度条）/ 跳过 / 稍后
"""
from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import QThread, Signal, Qt, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QTextEdit, QVBoxLayout,
                               QMessageBox)

from app.config import APP_VERSION, save_skip_version
from app.core import updater
from app.core.updater import ReleaseInfo


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
    ok = Signal(str)                   # 下载好的文件路径
    error = Signal(str)

    def __init__(self, url: str, dest: str, parent=None):
        super().__init__(parent)
        self._url = url
        self._dest = dest
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


# ---------- 对话框 ----------
_DLG_QSS = """
QDialog { background: #F3F5F4; }
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


class UpdateDialog(QDialog):
    def __init__(self, release: ReleaseInfo, parent=None):
        super().__init__(parent)
        self._release = release
        self._dl_thread: DownloadThread | None = None
        self.setWindowTitle("软件更新")
        self.setStyleSheet(_DLG_QSS)
        self.setMinimumWidth(480)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)

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
                QUrl("https://github.com/miaomiao112233/"
                     "Wixin-Group-chat-collection/releases/latest"))
            self.reject()
            return
        self._enter_download_mode()
        fd, dest = tempfile.mkstemp(prefix="WxSum-update-", suffix=".zip")
        os.close(fd)
        os.remove(dest)             # mkstemp 生成空文件，下载流程自己写
        self._dl_thread = DownloadThread(self._release.url, dest, self)
        self._dl_thread.progress.connect(self._on_progress)
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

    def _on_cancel_download(self):
        if self._dl_thread and self._dl_thread.isRunning():
            self._dl_thread.request_cancel()
            self.btn_cancel_dl.setEnabled(False)

    def _on_downloaded(self, path: str):
        self.lb_pct.setText("下载完成，正在准备更新…")
        try:
            updater.apply_and_restart(path)
        except Exception as e:                    # noqa: BLE001
            QMessageBox.warning(self, "更新失败", str(e))
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
        if msg != "已取消":
            QMessageBox.warning(self, "下载失败", msg)

    def closeEvent(self, ev):
        if self._dl_thread and self._dl_thread.isRunning():
            self._on_cancel_download()
        super().closeEvent(ev)
