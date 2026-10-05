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

仅依赖标准库（urllib / zipfile / subprocess / hashlib），不增加打包体积。
用户数据（config.json / state.db / API Key）在 %LOCALAPPDATA%\WxSum，
不在程序目录内，覆盖更新不丢配置。

更新包完整性：发版流水线会为 zip 附一个 `<zip 名>.sha256`（或
`checksums.txt`）。若 Release 提供，客户端下载后比对 SHA-256——不一致
立即删除已下载文件并报错；未提供则只记日志并跳过校验，保证老 Release
（v1.2.x 及更早，没有校验文件）仍能正常更新。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
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

# 校验文件名固定后缀 / 通用校验清单名；均不区分大小写
_SHA256_SUFFIX = ".sha256"
_CHECKSUM_TXT = "checksums.txt"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass
class ReleaseInfo:
    version: str                 # 去掉 v 前缀，如 "1.2.0"
    tag: str                     # 原始 tag，如 "v1.2.0"
    notes: str                   # Release 说明（Markdown）
    url: str | None              # zip 下载地址（无匹配 asset 时为 None）
    size: int                    # zip 字节数
    prerelease: bool
    # --- 以下为增量字段，全部带默认值以保证向后兼容 ---
    name: str = ""               # zip 资产文件名，如 WxSum-v1.3.0-windows-x64.zip
    checksum_url: str | None = None      # 校验文件下载地址（没有则为 None）
    checksum_name: str = ""      # 校验文件名，如 <zip 名>.sha256 / checksums.txt

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
    zip_url, zip_size, zip_name = _pick_zip(assets)
    sum_url, sum_name = _pick_checksum(assets, zip_name)
    if zip_name:
        log.info("Release %s 更新包：%s（%s）；校验文件：%s",
                 tag or "?", zip_name, zip_size,
                 sum_name or "未提供（跳过校验）")
    return ReleaseInfo(
        version=tag.lstrip("vV"),
        tag=tag,
        notes=str(data.get("body") or "").strip(),
        url=zip_url,
        size=zip_size,
        prerelease=bool(data.get("prerelease")),
        name=zip_name,
        checksum_url=sum_url,
        checksum_name=sum_name,
    )


def _pick_zip(assets: list[dict]) -> tuple[str | None, int, str]:
    """从 assets 中挑选更新包 zip：优先 windows x64，其次任意 zip。

    返回 (下载地址, 字节数, 资产文件名)；没找到时 (None, 0, "")。
    """
    candidates: list[tuple[int, str, int, str]] = []
    for a in assets:
        name = str(a.get("name") or "")
        url = str(a.get("browser_download_url") or "")
        if not name.lower().endswith(".zip") or not url:
            continue
        low = name.lower()
        score = 0
        if "wxsum" in low:
            score += 4
        if "windows" in low or "win" in low:
            score += 2
        if "x64" in low:
            score += 1
        candidates.append((score, url, int(a.get("size") or 0), name))
    if not candidates:
        return None, 0, ""
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[0][1], candidates[0][2], candidates[0][3]


def _pick_checksum(assets: list[dict],
                   zip_name: str) -> tuple[str | None, str]:
    """找 zip 对应的校验文件：优先 `<zip 名>.sha256`，其次 checksums.txt。

    返回 (下载地址, 资产文件名)；没有则 (None, "")。老 Release 没有校验
    文件属于正常情况，调用方按"跳过校验"处理。
    """
    if not zip_name:
        return None, ""
    wanted = zip_name.lower() + _SHA256_SUFFIX    # <zip 名>.sha256
    exact: tuple[str, str] | None = None
    generic: tuple[str, str] | None = None
    for a in assets:
        name = str(a.get("name") or "")
        url = str(a.get("browser_download_url") or "")
        if not name or not url:
            continue
        low = name.lower()
        if low == wanted:
            exact = (url, name)
            break
        if low == _CHECKSUM_TXT and generic is None:
            generic = (url, name)
    return exact or generic or (None, "")


# ---------- SHA-256 校验 ----------
def parse_checksum(text: str, expected_name: str) -> str | None:
    r"""从 sha256sum 文本里取出 `expected_name` 的摘要（小写 hex）。

    兼容 `sha256sum` 的两种行格式（"<hex>  <文件名>" 与 "<hex> *<文件名>"），
    忽略空行与 # / ; 开头的注释；文件名可带 ./ 或 * 前缀，只比 basename。
    找不到或格式不合法返回 None。
    """
    if not expected_name:
        return None
    target = expected_name.strip().lower()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts[0].strip().lower(), parts[1].strip()
        if not _HEX64_RE.match(digest):
            continue
        name = name.lstrip("*").replace("\\", "/")
        base = name.rsplit("/", 1)[-1].lower()
        if base == target:
            return digest
    return None


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    """返回文件 SHA-256 的 64 位小写十六进制串（分块读取，省内存）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def fetch_checksum(url: str, expected_name: str,
                   timeout: int = 15) -> str | None:
    """下载校验文件并取出 expected_name 的摘要；网络失败抛异常。

    地址取不到（HTTP 错误）时抛异常，由调用方决定是重试还是报错；
    能取到但内容里没有该文件名时返回 None（视为校验失败）。
    """
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    return parse_checksum(text, expected_name)


def fetch_checksum_safe(url: str, expected_name: str) -> str | None:
    """fetch_checksum 的包装：网络异常统一转成中文 RuntimeError。"""
    try:
        return fetch_checksum(url, expected_name)
    except RuntimeError:
        raise
    except Exception as e:                          # noqa: BLE001
        log.warning("校验文件下载失败：%s: %s", type(e).__name__, e)
        raise RuntimeError(
            f"校验失败：无法获取校验文件（{type(e).__name__}）。"
            "请稍后重试，或到 GitHub 手动下载更新包。") from e


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    """取消事件已置位时抛用户取消（供下载/校验各阶段调用）。"""
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("用户取消下载")


def _checksum_spec(checksum_url: str | None,
                   release: ReleaseInfo | None, dest: str,
                   expected_name: str | None) -> tuple[str, str] | None:
    """整理校验所需信息：(校验文件地址, zip 资产文件名)。

    没有任何校验地址时返回 None（调用方跳过校验）。
    """
    if not checksum_url and release is not None:
        checksum_url = release.checksum_url
    if not checksum_url:
        return None
    if not expected_name:
        if release is not None:
            expected_name = release.name or None
        if not expected_name:
            # ReleaseInfo 里也没有（老数据/手工构造）就退回本地文件名。
            # 下载目录用的是 mkstemp 随机名，此时大概率取不到摘要，
            # 会走"校验文件异常"分支而不是静默放行。
            expected_name = Path(dest).name
    return checksum_url, expected_name


def verify_release_checksum(path: str, release: ReleaseInfo | None = None,
                            checksum_url: str | None = None,
                            expected_name: str | None = None,
                            cancel_event: threading.Event | None = None,
                            ) -> str:
    """校验 zip 的 SHA-256，返回中文结果文案。

    - 无校验地址：返回"未提供校验文件，已跳过校验"，不抛异常（兼容老 Release）
    - 摘要缺失 / 不一致：抛 RuntimeError（中文，含"校验失败"），调用方须删除文件
    - 取消：抛 RuntimeError("用户取消下载")
    - 网络异常：抛 RuntimeError（中文），不会逃逸成未捕获异常

    调用前会先确保 dest 已写完（下载流程结束）。
    """
    spec = _checksum_spec(checksum_url, release, path, expected_name)
    if spec is None:
        log.info("Release 未提供 SHA-256 校验文件，跳过校验")
        return "未提供校验文件，已跳过校验"
    url, name = spec

    _raise_if_cancelled(cancel_event)
    digest = fetch_checksum_safe(url, name)
    if not digest:
        raise RuntimeError(
            f"校验失败：校验文件里没有 {name} 对应记录，"
            "可能被篡改或下载损坏，已删除已下载文件。")
    _raise_if_cancelled(cancel_event)

    actual = sha256_file(path)
    if actual != digest:
        log.warning("SHA-256 不一致：期望 %s，实际 %s", digest, actual)
        raise RuntimeError(
            "校验失败：更新包 SHA-256 与官方校验值不一致，"
            "文件可能被篡改或下载已损坏，已删除已下载文件。\n"
            f"官方：{digest}\n实际：{actual}")
    log.info("SHA-256 校验通过：%s", actual)
    return "校验通过"


# ---------- 下载 ----------
def download(url: str, dest: str,
             progress_cb=None,
             cancel_event: threading.Event | None = None) -> str:
    """流式下载到 dest；progress_cb(done, total)；取消抛 RuntimeError。

    只负责下载与 zip 结构自检；SHA-256 比对由 verify_release_checksum
    在下载完成后调用（这样进度条不会被校验请求打断）。网络失败统一转成
    中文 RuntimeError，避免 UI 直接吃到 URLError。
    """
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        resp_ctx = urllib.request.urlopen(req, timeout=30)
    except Exception as e:                          # noqa: BLE001
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("用户取消下载") from e
        log.warning("更新包下载失败：%s: %s", type(e).__name__, e)
        raise RuntimeError(
            f"下载失败：无法连接更新服务器（{type(e).__name__}）。"
            "请检查网络后重试，或到 GitHub 手动下载更新包。") from e
    with resp_ctx as resp:
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
