# -*- coding: utf-8 -*-
"""程序入口：python run.py"""
from __future__ import annotations

import io
import sys
from pathlib import Path

# pythonw 下 stdout/stderr 为 None：重定向到日志文件，否则 print/报错无声丢失
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.config import LOGS_DIR as _log_dir  # noqa: E402  (打包感知路径)
try:
    _log_dir.mkdir(parents=True, exist_ok=True)
    _crash = open(_log_dir / "gui_stderr.log", "a", buffering=1,
                  encoding="utf-8", errors="replace")
    if sys.stderr is None:
        sys.stderr = _crash
    if sys.stdout is None:
        sys.stdout = _crash
except Exception:
    pass


def _excepthook(t, v, tb):
    """全局崩溃钩子：写日志文件，pythonw 下也能事后排查。"""
    import traceback
    try:
        with open(_log_dir / "gui_stderr.log", "a", encoding="utf-8",
                  errors="replace") as f:
            f.write(f"\n==== CRASH "
                    f"{__import__('datetime').datetime.now()} ====\n")
            traceback.print_exception(t, v, tb, file=f)
    except Exception:
        pass
    sys.__stderr__ and traceback.print_exception(t, v, tb)


sys.excepthook = _excepthook


def _setup_file_logging() -> None:
    """调度/采集关键事件落 logs/app.log，pythonw 下也能事后排查。"""
    import logging
    try:
        fh = logging.FileHandler(_log_dir / "app.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"))
        root = logging.getLogger("app")
        root.setLevel(logging.INFO)
        root.addHandler(fh)
        # httpx 的 INFO 噪音（每次请求一行）不写文件
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
    except Exception:
        pass


_setup_file_logging()


def main() -> int:
    # 单实例保护：绑定固定端口失败 = 已有实例在跑
    import socket
    global _lock_sock
    try:
        _lock_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock_sock.bind(("127.0.0.1", 47777))
        _lock_sock.listen(1)
    except OSError:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, "工具已在运行中，请查看系统托盘图标（右下角绿色图标，"
               "双击可打开窗口）。",
            "微信监控", 0x40)
        return 0

    from PySide6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # 关窗=隐藏到托盘
    app.setApplicationName("")             # 清空，避免回填到窗口标题栏
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
