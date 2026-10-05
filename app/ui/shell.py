# -*- coding: utf-8 -*-
"""用系统默认程序打开文件/文件夹的小工具（主窗口与记录窗口共用）。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def open_path(path) -> bool:
    """用系统默认程序打开文件或文件夹；失败返回 False。"""
    try:
        target = Path(path)
        if not target.exists():
            return False
        os.startfile(str(target))                       # noqa: S606
        return True
    except Exception:                                   # noqa: BLE001
        return False


def reveal_path(path) -> bool:
    """在资源管理器中定位文件（Windows：explorer /select,<path>）。"""
    try:
        target = Path(path)
        if os.name == "nt":
            subprocess.Popen(
                ["explorer", f"/select,{os.path.normpath(str(target))}"])
            return True
        return open_path(target.parent)
    except Exception:                                   # noqa: BLE001
        return False
