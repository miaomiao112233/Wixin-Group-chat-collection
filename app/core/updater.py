# -*- coding: utf-8 -*-
"""自动更新核心：检查 GitHub Release → 下载 zip → 外部 bat 退出替换重启。

为什么用外部 bat：Windows 下运行中的 exe（及 onedir 内被加载的 dll）
被文件锁占用，进程存活时无法覆盖。因此流程是——主程序把 zip 下好，
生成 update.bat 并以分离进程启动，随后主程序退出；bat 轮询等待进程
消失，用 PowerShell Expand-Archive 覆盖程序目录，再重启 exe，最后
自删除。

仅依赖标准库（urllib / zipfile / subprocess），不增加打包体积。
用户数据（config.json / state.db / API Key）在 %LOCALAPPDATA%\\WxSum，
不在程序目录内，覆盖更新不丢配置。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.config import APP_VERSION, GITHUB_REPO

log = logging.getLogger("app.update")

_API_URL = (f"https://api.github.com/repos/{GITHUB_REPO}"
            "/releases/latest")
_UA = "WxSum-Updater"
# DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
_DETACH_FLAGS = 0x00000008 | 0x00000200


@dataclass
class ReleaseInfo:
    version: str                 # 去掉 v 前缀，如 "1.2.0"
    tag: str                     # 原始 tag，如 "v1.2.0"
    notes: str                   # Release 说明（Markdown）
    url: str | None              # zip 下载地址（无匹配 asset 时为 None）
    size: int                    # zip 字节数
    prerelease: bool

    @property
    def size_mb(self) -> str:
        return f"{self.size / 1048576:.1f} MB" if self.size else "未知"


# ---------- 版本比较 ----------
def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.0' / '1.2' → (1, 2, 0)；无法解析返回 ()。"""
    text = (text or "").strip().lstrip("vV")
    parts: list[int] = []
    for p in text.split("."):
        num = ""
        for ch in p:
            if ch.isdigit():
                num += ch
            else:
                break
        if num:
            parts.append(int(num))
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    r, l = parse_version(remote), parse_version(local)
    if not r:
        return False
    n = max(len(r), len(l))
    return r + (0,) * (n - len(r)) > l + (0,) * (n - len(l))


# ---------- 检查更新 ----------
def check_latest(timeout: int = 10) -> ReleaseInfo:
    """请求 GitHub API 获取最新 Release；网络/解析失败抛异常。"""
    req = urllib.request.Request(_API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": _UA,
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    tag = str(data.get("tag_name") or "")
    assets = data.get("assets") or []
    zip_url, zip_size = _pick_zip(assets)
    return ReleaseInfo(
        version=tag.lstrip("vV"),
        tag=tag,
        notes=str(data.get("body") or "").strip(),
        url=zip_url,
        size=zip_size,
        prerelease=bool(data.get("prerelease")),
    )


def _pick_zip(assets: list[dict]) -> tuple[str | None, int]:
    """从 assets 中挑选更新包 zip：优先 windows x64，其次任意 zip。"""
    candidates: list[tuple[int, str, int]] = []
    for a in assets:
        name = str(a.get("name") or "").lower()
        url = str(a.get("browser_download_url") or "")
        if not name.endswith(".zip") or not url:
            continue
        score = 0
        if "wxsum" in name:
            score += 4
        if "windows" in name or "win" in name:
            score += 2
        if "x64" in name:
            score += 1
        candidates.append((score, url, int(a.get("size") or 0)))
    if not candidates:
        return None, 0
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[0][1], candidates[0][2]


# ---------- 下载 ----------
def download(url: str, dest: str,
             progress_cb=None,
             cancel_event: threading.Event | None = None) -> str:
    """流式下载到 dest；progress_cb(done, total)；取消抛 RuntimeError。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, total)
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("用户取消下载")
    _verify_zip(dest)
    return dest


def _verify_zip(path: str) -> None:
    """轻量校验：能打开、非空、根含 WxSum.exe（防止下到损坏/错误文件）。"""
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if not names:
            raise ValueError("更新包为空")
        if "WxSum.exe" not in names:
            raise ValueError("更新包内容不符（缺少 WxSum.exe）")


# ---------- 应用更新 ----------
def apply_and_restart(zip_path: str) -> None:
    """生成 update.bat 并以分离进程启动；随后调用方负责退出主程序。

    仅打包版可调用（源码运行时 sys.executable 是 python.exe，无意义）。
    """
    if not getattr(sys, "frozen", False):
        raise RuntimeError("自动更新仅支持打包版")

    exe = Path(sys.executable).resolve()
    app_dir = exe.parent
    pid = os.getpid()

    bat_dir = Path(tempfile.mkdtemp(prefix="wxsum_upd_"))
    bat_path = bat_dir / "update.bat"
    bat_path.write_text(_build_bat(zip_path, app_dir, exe, pid),
                        encoding="gbk", errors="replace")

    import subprocess
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat_path)],
        creationflags=_DETACH_FLAGS,
        close_fds=True,
        cwd=str(bat_dir),
    )
    log.info("更新器已启动，等待主进程（pid=%s）退出", pid)


def _build_bat(zip_path: str, app_dir: Path, exe: Path, pid: int) -> str:
    """"""
    fail_popup = (
        "powershell -NoProfile -Command \""
        "Add-Type -AssemblyName PresentationFramework;"
        "[System.Windows.MessageBox]::Show('更新失败，请手动到 GitHub "
        "下载最新版本。','WxSum 更新')\""
    )
    return f"""@echo off
chcp 65001 >nul
set "ZIP={zip_path}"
set "APPDIR={app_dir}"
set "EXE={exe}"
set "PID={pid}"

:wait
tasklist /FI "PID eq %PID%" /NH 2>nul | find "%PID%" >nul 2>nul
if not errorlevel 1 (
    ping -n 2 127.0.0.1 >nul
    goto wait
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "try {{ Expand-Archive -LiteralPath $env:ZIP -DestinationPath $env:APPDIR -Force }} catch {{ exit 1 }}"
if errorlevel 1 goto fail

start "" "%EXE%"
del /f /q "%ZIP%" 2>nul
(goto) 2>nul & del "%~f0"
exit /b 0

:fail
{fail_popup}
del /f /q "%ZIP%" 2>nul
(goto) 2>nul & del "%~f0"
exit /b 1
"""
