# -*- coding: utf-8 -*-
"""运行自检 / 解密缓存清理 / 诊断包导出。

三件事都只碰本工具自己的目录，不写微信目录：

- ``check_*`` / ``collect_checks()``：界面"自检"页用的一组体检项；
- ``scan_dbcache()`` / ``prune_dbcache()``：解密副本（dbcache）会随微信库
  大小无限增长，这里做"保留天数 + 总量上限"的清理；
- ``export_diagnostics()``：把日志、state.db 快照、脱敏配置、自检结果打成
  zip 方便反馈问题。**绝不包含微信密钥**（wechat_keys.json）。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

from app.config import (APP_VERSION, DATA_DIR, LOGS_DIR, OUTPUT_DIR,
                        PROJECT_ROOT, STATE_DB, WECHAT_KEYS_FILE,
                        get_ai_config, get_dbcache_config,
                        get_wechat_data_dir, wechat_workdir)

_DBCACHE_ROOT = DATA_DIR.parent / "dbcache"


# ---------- 小工具 ----------
def format_size(num: float) -> str:
    """字节 -> 人类可读（用于界面与诊断包）。"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024 or unit == "TB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024.0
    return f"{num:.1f} TB"


def dir_size(path: Path) -> int:
    """目录总字节数（不递归符号链接；出错按 0 计）。"""
    total = 0
    if not path.is_dir():
        return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def dir_writable(path: Path) -> bool:
    """真正写一个临时文件来判断可写（比 os.access 可靠）。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".wxsum_write_test_{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:                                  # noqa: BLE001
        return False


def disk_free(path: Path) -> int:
    try:
        return shutil.disk_usage(str(path)).free
    except Exception:                                  # noqa: BLE001
        return 0


def check_wechat_running() -> bool:
    """微信 4.x 进程通常是 Weixin.exe（兼容旧版 WeChat.exe）。

    tasklist 在中文系统输出 GBK，故用字节模式匹配避免解码异常。
    """
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=10,
            creationflags=0x08000000,                  # CREATE_NO_WINDOW
        ).stdout.lower()
        return b"weixin.exe" in out or b"wechat.exe" in out
    except Exception:                                  # noqa: BLE001
        return True            # 探测本身失败时不误报，交给库报错


# ---------- dbcache（解密副本）----------
def _dbcache_files(root: Path | None = None
                   ) -> list[tuple[Path, os.stat_result]]:
    root = Path(root) if root is not None else _DBCACHE_ROOT
    out: list[tuple[Path, os.stat_result]] = []
    if not root.is_dir():
        return out
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = Path(dirpath) / name
            try:
                out.append((p, p.stat()))
            except OSError:
                continue
    return out


def scan_dbcache(root: Path | None = None) -> dict:
    """缓存占用概览：总量、文件数、最旧文件时间、各角色占用。"""
    base = Path(root) if root is not None else _DBCACHE_ROOT
    files = _dbcache_files(base)
    total = sum(st.st_size for _p, st in files)
    oldest = min((st.st_mtime for _p, st in files), default=0.0)
    per_role: dict[str, int] = {}
    for p, st in files:
        try:
            role = p.relative_to(base).parts[0]
        except ValueError:
            role = "?"
        per_role[role] = per_role.get(role, 0) + st.st_size
    return {"root": base, "total": total, "files": len(files),
            "oldest": oldest, "per_role": per_role}


def prune_dbcache(keep_days: int | None = None, max_mb: int | None = None,
                  root: Path | None = None) -> tuple[int, int]:
    """清理解密副本：先删超过保留天数的，再超容量时从最旧删到限额内。

    只删文件、保留目录（采集/总结线程可能正在用某个副本，直接删目录会
    让正在进行的查询失败）；这些文件都是可再生的，下次轮询会自动重建。
    返回 (释放字节数, 删除文件数)。``root`` 仅供测试注入。
    """
    cfg_days, cfg_mb = get_dbcache_config()
    keep_days = cfg_days if keep_days is None else int(keep_days)
    max_mb = cfg_mb if max_mb is None else int(max_mb)
    files = _dbcache_files(root)
    if not files:
        return 0, 0
    now = time.time()
    freed = removed = 0
    keep: list[tuple[Path, os.stat_result]] = []
    for p, st in files:
        age_days = (now - st.st_mtime) / 86400.0
        if keep_days > 0 and age_days > keep_days:
            if _unlink(p):
                freed += st.st_size
                removed += 1
        else:
            keep.append((p, st))
    limit = max(0, int(max_mb)) * 1024 * 1024
    total = sum(st.st_size for _p, st in keep)
    if limit and total > limit:
        for p, st in sorted(keep, key=lambda item: item[1].st_mtime):
            if total <= limit:
                break
            if _unlink(p):
                freed += st.st_size
                removed += 1
                total -= st.st_size
    return freed, removed


def _unlink(path: Path) -> bool:
    """删除单个缓存文件；被占用（Windows 上很常见）时静默跳过。"""
    try:
        path.unlink()
        return True
    except OSError:
        return False


def build_size_hint() -> str:
    """给设置页/自检页用的一行占用说明。"""
    info = scan_dbcache()
    days, max_mb = get_dbcache_config()
    oldest = (datetime.fromtimestamp(info["oldest"]).strftime("%Y-%m-%d")
              if info["oldest"] else "—")
    return (f"解密缓存 {format_size(info['total'])} / {info['files']} 个文件"
            f"（最旧 {oldest}，策略：保留 {days} 天、上限 {max_mb} MB）")


# ---------- state.db ----------
def state_stats() -> dict:
    """state.db 体积与各表行数（只读打开，失败返回空）。"""
    out: dict = {"size": 0, "rows": {}}
    try:
        out["size"] = STATE_DB.stat().st_size if STATE_DB.exists() else 0
    except OSError:
        pass
    if not STATE_DB.exists():
        return out
    try:
        conn = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True,
                               timeout=2)
        try:
            for table in ("monitor_group", "summary_run", "archived_file",
                          "error_log"):
                try:
                    row = conn.execute(
                        f"SELECT COUNT(*) FROM {table}").fetchone()
                    out["rows"][table] = int(row[0]) if row else 0
                except sqlite3.Error:
                    continue
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    return out


# ---------- 自检 ----------
def collect_checks() -> list[dict]:
    """一组体检项；level ∈ ok/warn/error/info。"""
    checks: list[dict] = []

    def add(name: str, level: str, detail: str):
        checks.append({"name": name, "level": level, "detail": detail})

    add("程序版本", "info",
        f"v{APP_VERSION} · Python {sys.version.split()[0]} · "
        f"{'打包版' if getattr(sys, 'frozen', False) else '源码运行'}")

    # 微信进程与目录
    if check_wechat_running():
        add("微信进程", "ok", "已检测到 Weixin.exe 在运行")
    else:
        add("微信进程", "error",
            "未检测到微信运行；读取群消息需要微信 4.0+ 已登录")

    keys_path = Path(WECHAT_KEYS_FILE)
    if keys_path.exists():
        age_h = (time.time() - keys_path.stat().st_mtime) / 3600.0
        add("解密密钥缓存", "ok" if age_h < 24 else "warn",
            f"{keys_path}（{age_h:.1f} 小时前更新"
            "，微信退出后需重新提取）")
    else:
        add("解密密钥缓存", "warn", "尚未生成，首次连接微信时自动提取")

    manual_dir = get_wechat_data_dir()
    if manual_dir:
        ok = Path(manual_dir).is_dir()
        add("微信数据目录", "ok" if ok else "error",
            f"手动指定：{manual_dir}" if ok else f"手动指定但不存在：{manual_dir}")
    else:
        add("微信数据目录", "info", "自动检测（未手动指定）")

    # 目录可写 / 磁盘
    for label, path in (("输出目录", Path(OUTPUT_DIR)),
                        ("数据目录", Path(DATA_DIR))):
        if dir_writable(path):
            add(f"{label}可写", "ok", str(path))
        else:
            add(f"{label}可写", "error", f"不可写：{path}")

    free = disk_free(Path(OUTPUT_DIR))
    if free <= 0:
        add("磁盘剩余空间", "warn", "无法获取磁盘信息")
    else:
        add("磁盘剩余空间", "warn" if free < 1024 ** 3 else "ok",
            f"{format_size(free)}（输出盘）")

    # state.db
    stats = state_stats()
    rows = stats.get("rows", {})
    add("state.db", "ok",
        f"{format_size(stats['size'])} · 监控群 {rows.get('monitor_group', 0)}"
        f" · 总结记录 {rows.get('summary_run', 0)}"
        f" · 归档记录 {rows.get('archived_file', 0)}"
        f" · 错误 {rows.get('error_log', 0)}")

    # dbcache
    cache = scan_dbcache()
    _days, max_mb = get_dbcache_config()
    over = max_mb > 0 and cache["total"] > max_mb * 1024 * 1024
    add("解密缓存", "warn" if over else "ok", build_size_hint())

    # 日志体积
    add("日志目录", "info",
        f"{format_size(dir_size(Path(LOGS_DIR)))} · {LOGS_DIR}")

    # AI 配置
    key, url, model, _think = get_ai_config()
    if not url or not model:
        add("AI 配置", "error", "未配置接口地址或模型名（设置 → AI 服务商）")
    elif not key and "localhost" not in url and "127.0.0.1" not in url:
        add("AI 配置", "warn",
            f"{model} @ {url}，但未填写 API Key（本地服务可忽略）")
    else:
        add("AI 配置", "ok", f"{model} @ {url}")

    # 缓存目录归属（诊断 cache 是否落在程序目录）
    add("缓存目录", "info", str(cache["root"]))
    add("角色缓存目录", "info",
        " / ".join(f"{r}={wechat_workdir(r)}"
                   for r in ("monitor", "summary", "ui")))
    return checks


# ---------- 诊断包 ----------
def _redacted_config() -> str:
    """config.json 文本（API Key 打码；微信目录保留）。"""
    path = Path(DATA_DIR) / "config.json"
    if not path.exists():
        return "（无 config.json）"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:                             # noqa: BLE001
        return f"（config.json 解析失败：{e}）"
    for k in list(data):
        if "key" in k.lower() and isinstance(data[k], str):
            data[k] = f"<已脱敏 len={len(data[k])}>"
    return json.dumps(data, ensure_ascii=False, indent=2)


def _recent_errors(limit: int = 300) -> str:
    """error_log 最近若干条，导出成 CSV 文本。"""
    if not STATE_DB.exists():
        return "ts,level,module,message\n"
    lines = ["ts,level,module,message"]
    try:
        conn = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True,
                               timeout=2)
        try:
            rows = conn.execute(
                "SELECT ts,level,module,message FROM error_log "
                "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        return f"ts,level,module,message\n(读取失败: {e})\n"
    for ts, level, module, message in rows:
        msg = str(message).replace('"', '""').replace("\n", " ")
        lines.append(f'{ts},{level},{module},"{msg}"')
    return "\n".join(lines) + "\n"


def export_diagnostics(dest: Path) -> Path:
    """打包诊断 zip（日志 + state.db 快照 + 脱敏配置 + 自检结果）。

    明确不含 wechat_keys.json（微信解密密钥）与明文 API Key。
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cache = scan_dbcache()
    stats = state_stats()
    lines = [
        f"生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"程序版本: v{APP_VERSION}",
        f"运行方式: {'打包版' if getattr(sys, 'frozen', False) else '源码'}",
        f"Python: {sys.version}",
        f"平台: {sys.platform}",
        f"程序目录: {PROJECT_ROOT}",
        f"数据目录: {DATA_DIR}",
        f"输出目录: {OUTPUT_DIR}",
        f"state.db: {format_size(stats['size'])} {stats.get('rows', {})}",
        f"解密缓存: {format_size(cache['total'])} / {cache['files']} 个文件",
        "",
        "== 自检结果 ==",
    ]
    for item in collect_checks():
        lines.append(f"[{item['level'].upper():5}] {item['name']}: "
                     f"{item['detail']}")
    lines.append("")
    lines.append("（本包不含 wechat_keys.json 与明文 API Key）")

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("system.txt", "\n".join(lines) + "\n")
        zf.writestr("config.redacted.json", _redacted_config())
        zf.writestr("errors.csv", _recent_errors())
        for name in ("app.log", "gui_stderr.log"):
            p = Path(LOGS_DIR) / name
            if p.exists():
                try:
                    zf.write(p, f"logs/{name}")
                except OSError:
                    continue
        # state.db 用 sqlite 备份 API 取一致快照（直接拷可能撞上写入）
        if STATE_DB.exists():
            tmp = dest.parent / f".state_snapshot_{os.getpid()}.db"
            try:
                src = sqlite3.connect(str(STATE_DB), timeout=3)
                try:
                    dst = sqlite3.connect(str(tmp))
                    try:
                        src.backup(dst)
                    finally:
                        dst.close()
                finally:
                    src.close()
                zf.write(tmp, "state.db")
            except sqlite3.Error:
                pass
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
    return dest
