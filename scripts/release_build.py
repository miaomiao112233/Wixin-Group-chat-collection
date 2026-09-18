# -*- coding: utf-8 -*-
r"""WxSum 发版构建（确定性）：升版本号 → PyInstaller 打包 → 校验产物 →
压成 GitHub Release 用的 zip。

用法：
    .\.venv\Scripts\python.exe scripts\release_build.py              # 用 config 中现有版本
    .\.venv\Scripts\python.exe scripts\release_build.py 1.3.0        # 先改版本号再构建
    .\.venv\Scripts\python.exe scripts\release_build.py 1.3.0 --zip-only  # 跳过构建，只重压缩

成功判定不依赖 PyInstaller 进程退出码（沙箱可能在构建完成后写系统 pyc
缓存受限而报非零码），以 "Build complete" + exe 刚刚刷新为准。

本脚本只负责构建产物；git 提交/推送与 GitHub Release 创建不在此内
（见 .trae/skills/wxsum-release/SKILL.md）。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import string
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PY = ROOT / "app" / "config.py"
SPEC = ROOT / "WxSum.spec"
ISS = ROOT / "installer.iss"
DIST_DIR = ROOT / "dist"
BUILD_DIR = DIST_DIR / "WxSum"
EXE = BUILD_DIR / "WxSum.exe"

INNO_DOWNLOAD_URL = "https://jrsoftware.org/isdl.php"

# 必须确认打进包内的关键模块（历史上靠手工 PYZ 检查）
REQUIRED_MODULES = (
    "app.core.updater",
    "app.ui.update_dialog",
    "app.core.image_ocr",
)

_VER_RE = re.compile(r'^APP_VERSION\s*=\s*"(\d+\.\d+\.\d+)"', re.M)


def fail(msg: str) -> "None":
    print(f"[失败] {msg}")
    sys.exit(1)


def read_version() -> str:
    m = _VER_RE.search(CONFIG_PY.read_text(encoding="utf-8"))
    if not m:
        fail("app/config.py 中找不到 APP_VERSION = \"x.y.z\"")
    return m.group(1)


def bump_version(new: str) -> None:
    text = CONFIG_PY.read_text(encoding="utf-8")
    if not _VER_RE.search(text):
        fail("无法写回 APP_VERSION（正则不匹配）")
    CONFIG_PY.write_text(
        _VER_RE.sub(f'APP_VERSION = "{new}"', text, count=1),
        encoding="utf-8")
    print(f"[1] APP_VERSION 已改为 {new}")


def run_build() -> None:
    print(f"[2] PyInstaller 构建中（约 80 秒）…")
    proc = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", str(SPEC)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tail = (proc.stdout or "").splitlines()[-3:]
    for line in tail:
        print(f"    {line}")
    if not EXE.exists():
        fail("构建后未找到 dist/WxSum/WxSum.exe")
    age = time.time() - EXE.stat().st_mtime
    if age > 300:
        fail(f"WxSum.exe 不是刚生成的（已存在 {int(age)} 秒），构建可能未生效")
    print(f"    exe 新鲜度校验通过（{int(age)} 秒前生成，"
          f"{EXE.stat().st_size / 1048576:.1f} MB）")


def verify_modules() -> None:
    """读 exe 内嵌 PYZ，确认关键模块没有被打包遗漏。"""
    print("[3] 校验关键模块已打入 PYZ …")
    try:
        from PyInstaller.archive.readers import (CArchiveReader,
                                                 ZlibArchiveReader)
        import tempfile
        c = CArchiveReader(str(EXE))
        pyz_name = next((n for n in c.toc
                         if str(n).endswith(("PYZ", ".pyz"))), None)
        if pyz_name is None:
            fail("exe 内找不到 PYZ 归档")
        data = c.extract(pyz_name)
        raw = data[1] if isinstance(data, tuple) else data
        fd, tmp = tempfile.mkstemp(suffix=".pyz")
        os.write(fd, raw)
        os.close(fd)
        try:
            toc = ZlibArchiveReader(tmp).toc
        finally:
            os.remove(tmp)
        for name in REQUIRED_MODULES:
            if name not in toc:
                fail(f"{name} 未打入 PYZ")
                return
        print(f"    {len(REQUIRED_MODULES)} 个关键模块全部在包内")
    except SystemExit:
        raise
    except Exception as e:                          # noqa: BLE001
        fail(f"PYZ 校验异常: {type(e).__name__}: {e}")


def make_zip(version: str) -> Path:
    print("[4] 压缩 Release zip …")
    if not BUILD_DIR.is_dir():
        fail("dist/WxSum 目录不存在，无法压缩")
    zip_path = DIST_DIR / f"WxSum-v{version}-windows-x64.zip"
    if zip_path.exists():
        zip_path.unlink()
    n = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for f in BUILD_DIR.rglob("*"):
            if f.is_file():
                # arcname 相对 BUILD_DIR → WxSum.exe 位于 zip 根，
                # 与 updater._verify_zip 的 "WxSum.exe" in names 对齐
                zf.write(f, f.relative_to(BUILD_DIR))
                n += 1
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    if "WxSum.exe" not in names:
        fail("zip 根目录缺少 WxSum.exe")
    size_mb = zip_path.stat().st_size / 1048576
    print(f"    {zip_path.name} | {size_mb:.1f} MB | {n} 个文件")
    return zip_path


def find_iscc() -> str | None:
    """定位 Inno Setup 编译器 ISCC.exe。

    顺序：PATH → INNO_SETUP_HOME 环境变量 → 注册表卸载信息（InstallLocation，
    装在任意盘符都可靠）→ 各固定盘常见安装目录。
    """
    on_path = shutil.which("ISCC")
    if on_path:
        return on_path

    env_home = os.environ.get("INNO_SETUP_HOME")
    if env_home:
        p = Path(env_home) / "ISCC.exe"
        if p.exists():
            return str(p)

    try:
        import winreg
        sub = (r"SOFTWARE\Microsoft\Windows\CurrentVersion"
               r"\Uninstall\Inno Setup 6_is1")
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for flags in (winreg.KEY_READ,
                          winreg.KEY_READ | winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(root, sub, 0, flags) as k:
                        loc, _ = winreg.QueryValueEx(k, "InstallLocation")
                    p = Path(loc) / "ISCC.exe"
                    if p.exists():
                        return str(p)
                except OSError:
                    continue
    except ImportError:
        pass

    rel_dirs = (
        "Inno Setup 6",
        r"Program Files\Inno Setup 6",
        r"Program Files (x86)\Inno Setup 6",
        r"Software\Inno Setup 6",
        r"Programs\Inno Setup 6",
    )
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:\\")
        if not drive.exists():
            continue
        for rel in rel_dirs:
            p = drive / rel / "ISCC.exe"
            if p.exists():
                return str(p)
    return None


def build_setup(version: str, required: bool = False) -> Path | None:
    r"""调用 ISCC 编译 installer.iss → dist\WxSum-v<ver>-Setup.exe。

    required=True 时未安装 Inno / 编译失败一律硬失败；否则未安装仅提示跳过。
    """
    print("[5] 编译 Setup 安装包 …")
    if not ISS.exists():
        if required:
            fail("installer.iss 不存在")
        print("    跳过：installer.iss 不存在")
        return None
    iscc = find_iscc()
    if not iscc:
        msg = ("未安装 Inno Setup 6，跳过 Setup（zip 仍可正常分发）。\n"
               f"    安装后重跑即可：{INNO_DOWNLOAD_URL}")
        if required:
            fail(msg)
        print(f"    {msg}")
        return None

    setup_exe = DIST_DIR / f"WxSum-v{version}-Setup.exe"
    if setup_exe.exists():
        setup_exe.unlink()
    proc = subprocess.run(
        [iscc, str(ISS), f"/DAppVersion={version}", "/Q"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if required:
            fail(f"ISCC 编译失败: {detail}")
        print(f"    ISCC 编译失败（跳过 Setup）: {detail[:300]}")
        return None
    if not setup_exe.exists():
        if required:
            fail("ISCC 报告成功但未找到 Setup.exe")
        print("    跳过：未找到编译产物")
        return None
    print(f"    {setup_exe.name} | "
          f"{setup_exe.stat().st_size / 1048576:.1f} MB")
    return setup_exe


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("version", nargs="?", help="新版本号 x.y.z（不填用现有）")
    ap.add_argument("--zip-only", action="store_true",
                    help="跳过构建与 Setup，只重新压缩现有 dist/WxSum")
    ap.add_argument("--setup-only", action="store_true",
                    help="跳过构建与 zip，只用现有 dist/WxSum 编译 Setup")
    ap.add_argument("--setup", action="store_true",
                    help="要求必须生成 Setup（未装 Inno 时硬失败而非跳过）")
    args = ap.parse_args()

    if args.version:
        if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
            fail(f"版本号格式错误: {args.version}（应为 x.y.z）")
        bump_version(args.version)

    version = read_version()
    print(f"发版版本: v{version}")

    sp = None
    if args.setup_only:
        print("[2] --setup-only，跳过构建与 zip")
        sp = build_setup(version, required=True)
    else:
        if args.zip_only:
            print("[2] --zip-only，跳过构建")
        else:
            run_build()
            verify_modules()
        zp = make_zip(version)
        if not args.zip_only:
            sp = build_setup(version, required=args.setup)

    print("\n[完成] 下一步：")
    print("  1. git add / commit / push")
    print("  2. GitHub Releases 新建 v" + version)
    if sp is not None:
        print("     附件上传：zip（自动更新通道）+ " + sp.name
              + "（新用户安装）")
    else:
        print("     附件上传：" + zp.name
              + "（自动更新通道；装 Inno 后可补 Setup）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
