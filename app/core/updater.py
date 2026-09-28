# -*- coding: utf-8 -*-
r"""自动更新核心：检查 GitHub Release → 下载 zip → 外部脚本退出替换重启。

为什么用外部脚本：Windows 下运行中的 exe（及 onedir 内被加载的 dll）
被文件锁占用，进程存活时无法覆盖。因此流程是——主程序把 zip 下好，
生成 update.ps1 并以隐藏控制台的独立进程启动，随后主程序退出；
ps1 等待进程消失，用 Expand-Archive 覆盖程序目录，再重启 exe，
最后清理 zip 与临时目录。全程写 update.log，失败弹真实错误。

为什么是 PowerShell 而不是 bat：实测（Windows 11）DETACHED_PROCESS
（无控制台）启动的 cmd 一旦执行「外部程序|外部程序」管道就整批静默
崩溃——旧方案（tasklist|find 等待循环）第一轮就死，表现为"程序退出
不重启、zip 残留、无任何提示"。powershell.exe + CREATE_NO_WINDOW
无此问题，且支持 try/catch 与中文不乱码（utf-8-sig）。

仅依赖标准库（urllib / zipfile / subprocess），不增加打包体积。
用户数据（config.json / state.db / API Key）在 %LOCALAPPDATA%\WxSum，
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

from app.config import APP_VERSION, GITHUB_REPO, LOGS_DIR

log = logging.getLogger("app.update")

_API_URL = (f"https://api.github.com/repos/{GITHUB_REPO}"
            "/releases/latest")
_UA = "WxSum-Updater"
# CREATE_NO_WINDOW：给 powershell 一个隐藏控制台。
# 不能用 DETACHED_PROCESS：无控制台的 cmd 执行外部程序管道会整批
# 崩溃（见模块 docstring），导致更新脚本一步都跑不完。
_CREATE_NO_WINDOW = 0x08000000


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
    """生成 update.ps1 并以隐藏窗口的独立进程启动；调用方随后退出主程序。

    仅打包版可调用（源码运行时 sys.executable 是 python.exe，无意义）。
    """
    if not getattr(sys, "frozen", False):
        raise RuntimeError("自动更新仅支持打包版")

    exe = Path(sys.executable).resolve()
    app_dir = exe.parent
    pid = os.getpid()
    log_path = LOGS_DIR / "update.log"

    work_dir = Path(tempfile.mkdtemp(prefix="wxsum_upd_"))
    ps1_path = work_dir / "update.ps1"
    ps1_path.write_text(
        _build_ps1(zip_path, app_dir, exe, pid, log_path, work_dir),
        encoding="utf-8-sig")

    import subprocess
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(ps1_path)],
        creationflags=_CREATE_NO_WINDOW,
        close_fds=True,
    )
    log.info("更新器已启动，等待主进程（pid=%s）退出", pid)


def _q(text) -> str:
    """PowerShell 单引号字符串字面量（内部 ' 加倍转义）。"""
    return "'" + str(text).replace("'", "''") + "'"


def _build_ps1(zip_path: str, app_dir: Path, exe: Path, pid: int,
               log_path: Path, work_dir: Path) -> str:
    """生成替换+重启脚本：等退出 → 解压 → 校验 → 重启 → 清理。

    成功才删 zip；任何一步失败写日志并弹真实错误（zip 保留，
    用户可手动解压救急）。
    """
    return f"""$ErrorActionPreference = 'Stop'
function Log($m) {{
  Add-Content -LiteralPath {_q(log_path)} -Encoding UTF8 -Value (
    "[" + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + "] " + $m)
}}
try {{
  Log "等待主进程(pid={pid})退出，最多 120 秒"
  $deadline = (Get-Date).AddSeconds(120)
  while (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{
    if ((Get-Date) -gt $deadline) {{
      throw "主程序 120 秒内未退出，放弃本次更新"
    }}
    Start-Sleep -Milliseconds 500
  }}
  Log "主进程已退出，解压覆盖 {_q(app_dir)}"
  Expand-Archive -LiteralPath {_q(zip_path)} -DestinationPath {_q(app_dir)} -Force
  if (-not (Test-Path {_q(exe)})) {{
    throw "解压后未找到 WxSum.exe，更新包内容异常"
  }}
  Log "解压完成，重启应用"
  Start-Process -FilePath {_q(exe)} -WorkingDirectory {_q(app_dir)}
  Log "更新完成，已重启"
  Remove-Item -LiteralPath {_q(zip_path)} -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath {_q(work_dir)} -Recurse -Force -ErrorAction SilentlyContinue
}} catch {{
  Log ("更新失败: " + $_.Exception.Message)
  try {{
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
      ("更新失败，请到 GitHub 手动下载最新版本。`n`n" + $_.Exception.Message),
      'WxSum 更新') | Out-Null
  }} catch {{}}
  exit 1
}}
"""
