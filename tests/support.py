# -*- coding: utf-8 -*-
"""测试公共辅助：独立临时目录。

本模块只做一件事——给每个测试一个干净的临时目录，并保证结束后能删干净。

为什么不用 ``tempfile.TemporaryDirectory`` / ``tempfile.mkdtemp``：它们
在 Windows 上以 0700 权限创建目录，并因此写入一份不含沙箱授权的访问
控制项；在受限沙箱会话（如本次开发的 workspace-write 会话）里，创建者
自己都会失去写入和删除权限，实测表现为目录内 ``PermissionError``、
``shutil.rmtree`` 报 WinError 5，测试既无法落盘也留一堆删不掉的空目录。
这里改为在 ``tempfile.gettempdir()`` 之下用 uuid 命名的唯一目录（权限
继承父目录），同样满足"每个测试独立临时目录 + 结束清理 + 不碰项目
data/ 输出/ dbcache/"，在权限正常的普通环境与受限沙箱里都能跑通。
"""
from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path


def make_temp_dir(prefix: str = "wxsum-test-") -> Path:
    """创建并返回一个空的独立临时目录（清理由调用方负责）。"""
    path = Path(tempfile.gettempdir()) / f"{prefix}{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def cleanup_dir(path: Path) -> None:
    """静默递归删除临时目录（清理失败不影响测试结论）。"""
    shutil.rmtree(path, ignore_errors=True)
