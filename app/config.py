# -*- coding: utf-8 -*-
"""全局常量与路径配置。

数据源使用 wechatauto 库（自动检测微信 4.x 数据目录并解密读取），
本文件只集中工具自身的路径与参数。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# ---------- 路径（源码运行 / 打包 exe 运行 自动分流，均不写死盘符） ----------
def _runtime_paths() -> tuple[Path, Path, Path, Path]:
    """返回 (app根目录, 数据目录, 日志目录, 输出目录)。

    - 源码/批处理运行：全部在项目根下（开发态直观可见）；
    - PyInstaller 打包后：状态与日志放 %LOCALAPPDATA%\\WxSum（避免装在
      Program Files 只读、以及 onefile 临时目录被清理），输出放 exe 旁，
      普通用户能直接找到生成的 Word。
    """
    if getattr(sys, "frozen", False):          # 打包 exe
        app_dir = Path(sys.executable).resolve().parent
        local = os.environ.get("LOCALAPPDATA") or str(Path.home())
        data_dir = Path(local) / "WxSum" / "data"
        logs_dir = Path(local) / "WxSum" / "logs"
        output_dir = app_dir / "输出"
    else:                                      # 源码运行
        app_dir = Path(__file__).resolve().parent.parent
        data_dir = app_dir / "data"
        logs_dir = app_dir / "logs"
        output_dir = app_dir / "输出"
    return app_dir, data_dir, logs_dir, output_dir


PROJECT_ROOT, DATA_DIR, LOGS_DIR, OUTPUT_DIR = _runtime_paths()
STATE_DB = DATA_DIR / "state.db"

# ---------- 版本 / 自动更新 ----------
APP_VERSION = "1.2.0"          # 发版前维护；与 GitHub Release tag 对齐
GITHUB_REPO = "miaomiao112233/Wixin-Group-chat-collection"

# ---------- 微信解密缓存 ----------
# 采集线程与总结线程各持一个 WeChatDB 实例，必须使用各自独立的解密副本
# 目录：两者并发执行 WAL 增量合并/全量重建时，若共用同一批缓存文件，
# 一方会把另一方正在查询的 .db 重写成撕裂页，SQLite 即报
# "database disk image is malformed"。
_DB_CACHE_DIR = DATA_DIR.parent / "dbcache"
WECHAT_KEYS_FILE = str(DATA_DIR / "wechat_keys.json")  # 密钥两实例共享，只提取一次


def wechat_workdir(role: str) -> str:
    """role: 'monitor' / 'summary' / 'ui'，各自独立缓存目录。"""
    return str(_DB_CACHE_DIR / role)

# ---------- 采集 ----------
POLL_INTERVAL_SEC = 60          # 采集 QTimer 间隔（秒）
POLL_BATCH_LIMIT = 2000         # 单轮单群最多拉取条数

# ---------- 自适应总结间隔（秒） ----------
SUMMARY_MIN_INTERVAL_SEC = 15 * 60
SUMMARY_MAX_INTERVAL_SEC = 60 * 60
SUMMARY_GROW_IDLE = 1.5         # 无新消息 → 间隔放大
SUMMARY_GROW_FEW = 1.2          # 少量新消息(0<n<FEW) → 略放大
SUMMARY_SHRINK_BURST = 0.7      # 刷屏(n>=BURST) → 缩短
SUMMARY_FEW_THRESHOLD = 20
SUMMARY_BURST_THRESHOLD = 200
DAILY_FORCE_TIME = "23:30"      # 每日强制总结点

# ---------- 总结分块（map-reduce 输入） ----------
CHUNK_MAX_MESSAGES = 400
CHUNK_MAX_CHARS = 12000

# ---------- DeepSeek / OpenAI 兼容 AI 服务 ----------
# 内置服务商预设（均为 OpenAI 兼容 /chat/completions 协议）
AI_PROVIDERS = [
    {"id": "deepseek", "name": "DeepSeek",
     "base_url": "https://api.deepseek.com", "model": "deepseek-chat",
     "need_key": True},
    {"id": "zhipu", "name": "智谱 GLM-4.7-Flash（免费·200K长文本）",
     "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "model": "glm-4.7-flash", "need_key": True,
     "thinking_disabled": True},
    {"id": "kimi", "name": "Kimi 月之暗面",
     "base_url": "https://api.moonshot.cn/v1",
     "model": "moonshot-v1-8k", "need_key": True},
    {"id": "qwen", "name": "通义千问 Qwen",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "model": "qwen-plus", "need_key": True},
    {"id": "siliconflow", "name": "硅基流动 SiliconFlow",
     "base_url": "https://api.siliconflow.cn/v1",
     "model": "Qwen/Qwen2.5-7B-Instruct", "need_key": True},
    {"id": "ollama", "name": "本地 Ollama（免费离线）",
     "base_url": "http://localhost:11434/v1",
     "model": "qwen2.5:7b", "need_key": False},
    {"id": "custom", "name": "自定义（任意 OpenAI 兼容接口）",
     "base_url": "", "model": "", "need_key": True},
]
DEFAULT_AI_BASE_URL = "https://api.deepseek.com"
DEFAULT_AI_MODEL = "deepseek-chat"
DEEPSEEK_BASE_URL = DEFAULT_AI_BASE_URL   # 向后兼容旧引用
DEEPSEEK_MODEL = DEFAULT_AI_MODEL
DEEPSEEK_TIMEOUT_SEC = 180
DEEPSEEK_MAX_RETRIES = 3        # 指数退避重试次数（429 限流在客户端单独处理）

# ---------- 文件归档 ----------
# 只归档这些扩展名；图片/视频等仅进文件清单
ARCHIVE_EXTS = {
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".pdf", ".zip", ".rar", ".7z", ".txt", ".csv", ".md",
    ".et", ".dps", ".wps",
}

# ---------- Word 排版 ----------
DOCX_FONT = "微软雅黑"


def _load_config_json() -> dict:
    """data/config.json 可覆盖默认配置（不提交版本库）。"""
    p = DATA_DIR / "config.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


_CONFIG_JSON = _load_config_json()


def get_deepseek_api_key() -> str:
    """优先环境变量，其次 data/config.json 的 deepseek_api_key 字段。"""
    return (
        os.environ.get("DEEPSEEK_API_KEY")
        or _CONFIG_JSON.get("deepseek_api_key")
        or ""
    )


def get_ai_config() -> tuple[str, str, str, bool]:
    """返回 (api_key, base_url, model, disable_thinking)；默认 DeepSeek。"""
    key = get_deepseek_api_key()
    base_url = (_CONFIG_JSON.get("ai_base_url") or DEFAULT_AI_BASE_URL).strip()
    model = (_CONFIG_JSON.get("ai_model") or DEFAULT_AI_MODEL).strip()
    disable_thinking = bool(_CONFIG_JSON.get("ai_thinking_disabled", False))
    return key, base_url, model, disable_thinking


def save_ai_config(key: str, base_url: str, model: str,
                   disable_thinking: bool = False) -> None:
    """统一写入 AI 配置（key 字段名沿用 deepseek_api_key 以兼容旧配置）。"""
    global _CONFIG_JSON
    p = DATA_DIR / "config.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        data = {}
    key = (key or "").strip()
    base_url = (base_url or "").strip().rstrip("/")
    model = (model or "").strip()
    if key:
        data["deepseek_api_key"] = key
    else:
        data.pop("deepseek_api_key", None)
    data["ai_base_url"] = base_url
    data["ai_model"] = model
    data["ai_thinking_disabled"] = bool(disable_thinking)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    _CONFIG_JSON = data


def get_wechat_data_dir() -> str:
    """手动指定的微信数据目录（xwechat_files 所在目录）；空=自动检测。"""
    return (_CONFIG_JSON.get("wechat_data_dir") or "").strip()


def get_auto_archive() -> bool:
    """是否自动归档群文件到 输出\\群名\\日期\\ 下；默认关闭。"""
    return bool(_CONFIG_JSON.get("auto_archive", False))


def save_auto_archive(enabled: bool) -> None:
    """写入自动归档开关。"""
    global _CONFIG_JSON
    p = DATA_DIR / "config.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        data = {}
    data["auto_archive"] = bool(enabled)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    _CONFIG_JSON = data


def get_ocr_enabled() -> bool:
    """是否对图片消息做 OCR 提取文字后喂给 AI；默认开启。"""
    return bool(_CONFIG_JSON.get("ocr_enabled", True))


def save_ocr_enabled(enabled: bool) -> None:
    """写入图片 OCR 开关。"""
    global _CONFIG_JSON
    p = DATA_DIR / "config.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        data = {}
    data["ocr_enabled"] = bool(enabled)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    _CONFIG_JSON = data


def get_skip_version() -> str:
    """用户选择"跳过此版本"的版本号；空串=不跳过。"""
    return str(_CONFIG_JSON.get("skip_update_version") or "").strip()


def save_skip_version(version: str) -> None:
    """写入/清除跳过的更新版本号。"""
    global _CONFIG_JSON
    p = DATA_DIR / "config.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        data = {}
    if version:
        data["skip_update_version"] = version
    else:
        data.pop("skip_update_version", None)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    _CONFIG_JSON = data


def save_wechat_data_dir(path: str) -> None:
    """写入/清除微信数据目录覆盖（空串=恢复自动检测）。"""
    global _CONFIG_JSON
    p = DATA_DIR / "config.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        data = {}
    path = (path or "").strip()
    if path:
        data["wechat_data_dir"] = path
    else:
        data.pop("wechat_data_dir", None)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    _CONFIG_JSON = data


def normalize_wechat_dir(picked: str) -> str | None:
    """把用户选择的目录规范化为「账号目录的父目录」。

    兼容三种选择：
      - <账号目录 wxid_xxx>        （其下直接有 db_storage）→ 返回父目录
      - xwechat_files             （其下有 <账号>/db_storage）→ 原样返回
      - xwechat_files 的父目录    （Documents 等）→ 自动下钻定位
    找不到有效数据目录返回 None。
    """
    picked = (picked or "").strip().strip('"')
    if not picked:
        return None
    p = Path(picked)
    if (p / "db_storage").is_dir():
        return str(p.parent)
    try:
        from wechatauto.db import _locate_account_root
        return _locate_account_root(str(p))
    except Exception:
        return None


# 启动即确保目录存在
for _d in (DATA_DIR, LOGS_DIR, OUTPUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)
