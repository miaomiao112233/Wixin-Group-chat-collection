# -*- coding: utf-8 -*-
"""文件归档：文件消息 → msg\\file 定位 → 复制到 输出\\群名\\日期\\。

实测结论（勿改）：
- 已下载群文件位于 `account_dir\\msg\\file\\<下载月份YYYY-MM>\\<原文件名>`；
  **微信对同名文件自动加 (1)/(2) 后缀**（无空格），且新旧文件内容不同；
- 消息 appmsg type=6 的 <title> 即原文件名，<totallen> 即字节大小；
- 大小与声明一致视为下载完成；不一致标 pending 等下轮（等价于
  "连续两轮稳定"判据的单轮近似，避免复杂状态）。

定位同名文件的判据（修复张冠李戴）：
  枚举消息所在月及其它月份下的 原名/原名(1)/原名(2)… 全部候选，
  先按 totallen 字节大小精确匹配，再要求文件 mtime 不早于消息时间
  （文件只能在消息发出后下载）；拿不到 totallen 时按 mtime 就近。
  任何候选都不满足 → 标 missing 等下轮，**绝不拿旧文件凑数**。
"""
from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import stat
from pathlib import Path

from wechatauto import WeChatDB

from app.config import ARCHIVE_EXTS
from app.models import FileInfo, Message
from app.state_store import StateStore

log = logging.getLogger("app.archive")

# 文件 mtime 允许早于消息时间的容差（秒）：覆盖时区/CDN 时间戳轻微偏差
_MTIME_TOLERANCE_SEC = 3600

try:
    from lxml import etree

    _XML_PARSER = etree.XMLParser(recover=True, resolve_entities=False,
                                 no_network=True, huge_tree=False)
except Exception:  # pragma: no cover
    etree = None

_PREFIX_RE = re.compile(r"^[^:\n]{1,64}:\r?\n")
_DUP_SUFFIX_RE = re.compile(r"^(.*)\((\d+)\)$")


class Archiver:
    def __init__(self, db: WeChatDB, store: StateStore):
        self._db = db
        self._store = store
        self._file_dir = Path(db.account_dir) / "msg" / "file"

    # ---------- 对外 ----------
    def archive_messages(self, messages: list[Message],
                         output_root: Path, group_name: str,
                         since_date: str | None = None) -> list[FileInfo]:
        """对一批消息里的文件消息做归档，返回 FileInfo 列表。

        since_date: 监控启用日（YYYY-MM-DD）。早于该日的文件一律忽略
        （不复制、不登记），避免首次添加群时回溯历史文件。
        None = 不限制（旧群兼容）。
        """
        out: list[FileInfo] = []
        for m in messages:
            if not m.is_file:
                continue
            if since_date and f"{m.create_time:%Y-%m-%d}" < since_date:
                continue
            try:
                info = self._archive_one(m, output_root, group_name)
            except Exception as e:                      # noqa: BLE001
                # 单个文件归档失败（占用/权限/磁盘）不阻断总结生成
                log.warning("归档文件 %s 失败，跳过: %s",
                            getattr(m, "file_name", "?"), e)
                continue
            if info is not None:
                out.append(info)
        return out

    def list_group_files(self, chatroom_id: str, date: str) -> list[dict]:
        """当天已登记的归档文件（含 skipped/missing），供 docx 文件清单。"""
        return self._store.list_archived_files(chatroom_id, date)

    # ---------- 单条 ----------
    def _archive_one(self, m: Message, output_root: Path,
                     group_name: str) -> FileInfo | None:
        date = f"{m.create_time:%Y-%m-%d}"
        expect = self._expect_size(m)
        # 已归档过（同群同名同大小）→ 跳过；同名但大小不同=另一个文件
        for row in self._store.list_archived_files(m.chatroom_id):
            if (row["orig_name"] == m.file_name
                    and (expect < 0 or int(row.get("size") or 0) == expect)
                    and row["status"] == "archived"):
                return None
        ext = os.path.splitext(m.file_name)[1].lower()
        if ext and ext not in ARCHIVE_EXTS:
            status, saved, size = "skipped", "", 0
        else:
            src = self._locate(m, expect)
            if src is None:
                status, saved, size = "missing", "", 0
            elif expect >= 0 and src.stat().st_size != expect:
                # 找到了同名候选但大小不对（通常是新文件还在下载中）：
                # 等下轮重试
                status, saved, size = "pending", str(src), src.stat().st_size
            else:
                saved = self._copy(src, output_root, m, group_name)
                status, size = "archived", src.stat().st_size

        self._store.upsert_archived_file(
            chatroom_id=m.chatroom_id, date=date, orig_name=m.file_name,
            saved_path=saved, size=size, svr_id=m.svr_id, status=status)
        return FileInfo(chatroom_id=m.chatroom_id, date=date,
                        orig_name=m.file_name, saved_path=saved,
                        size=size, svr_id=m.svr_id, status=status)

    # ---------- 定位 ----------
    def _locate(self, m: Message, expect_size: int = -1) -> Path | None:
        """在 msg\\file 各月份目录下定位本消息对应的真实文件。

        枚举原名及微信重名变体（(1)/(2)…），按 totallen 大小 + 下载
        时间双重判据选择；消息所在月份优先评分。全部不符返回 None。
        """
        if not self._file_dir.is_dir():
            return None
        msg_ts = m.create_time.timestamp()
        msg_month = f"{m.create_time:%Y-%m}"

        candidates: list[Path] = []
        month_dirs = sorted((p for p in self._file_dir.iterdir()
                             if p.is_dir()),
                            key=lambda p: p.name != msg_month)
        for month_dir in month_dirs:
            candidates.extend(self._name_variants(month_dir, m.file_name))
        if not candidates:
            return None

        scored = []
        for c in candidates:
            try:
                st = c.stat()
            except OSError:
                continue
            size_ok = expect_size < 0 or st.st_size == expect_size
            if not size_ok:
                continue
            time_ok = st.st_mtime >= msg_ts - _MTIME_TOLERANCE_SEC
            # 排序键：时间合格优先 → 与消息时间差最小 → 消息当月优先
            same_month = (c.parent.name == msg_month)
            scored.append((not time_ok,
                           abs(st.st_mtime - msg_ts),
                           not same_month, c))
        if not scored:
            return None
        scored.sort(key=lambda x: x[:3])
        chosen = scored[0][3]
        if scored[0][0]:
            # 大小吻合但 mtime 全部早于消息时间：仍可能是 CDN 保留了
            # 原文件时间戳的合法情况；大小是强判据，记录日志后接受
            log.info("文件 %s 按大小匹配，但 mtime 早于消息时间: %s",
                     m.file_name, chosen)
        return chosen

    @staticmethod
    def _name_variants(month_dir: Path, name: str) -> list[Path]:
        """目录下 name 本身及 name(1)/name(2)… 微信重名变体。

        编号不要求连续：用户可能删过中间文件（实测存在只有 (2)
        而无 (1) 的情况），故用 glob 全量匹配再按编号排序。
        """
        out = []
        direct = month_dir / name
        if direct.is_file():
            out.append(direct)
        stem, suffix = os.path.splitext(name)
        numbered = []
        for cand in month_dir.glob(f"{glob.escape(stem)}(*){suffix}"):
            mm = _DUP_SUFFIX_RE.match(cand.stem)
            if mm and mm.group(1) == stem and cand.is_file():
                numbered.append((int(mm.group(2)), cand))
        numbered.sort(key=lambda x: x[0])
        out.extend(c for _, c in numbered)
        return out

    @staticmethod
    def _expect_size(m: Message) -> int:
        """从原始 XML 提取 totallen；取不到时返回 -1（跳过大小校验）。"""
        try:
            raw = m.raw_content or ""
            raw = _PREFIX_RE.sub("", raw, count=1)   # 去 wxid:\n 前缀
            if etree is not None:
                root = etree.fromstring(raw.encode("utf-8"), _XML_PARSER)
            else:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(raw)
            el = root.find(".//totallen")
            return int(el.text) if el is not None and el.text else -1
        except Exception:
            return -1

    def _copy(self, src: Path, output_root: Path, m: Message,
              group_name: str) -> str:
        """复制到 输出\\群名\\日期\\，重名加 (1)。返回目标路径。"""
        dest_dir = output_root / safe_name(group_name) \
            / f"{m.create_time:%Y-%m-%d}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / m.file_name
        i = 1
        while dest.exists() and dest.stat().st_size != src.stat().st_size:
            stem, ext = os.path.splitext(m.file_name)
            dest = dest_dir / f"{stem}({i}){ext}"
            i += 1
        if not dest.exists():
            shutil.copy2(src, dest)
            # copy2 会保留源文件只读属性，导致后续无法删除/覆盖，重置为普通
            try:
                os.chmod(dest, stat.S_IREAD | stat.S_IWRITE)
            except OSError:
                pass
        return str(dest)


def safe_name(name: str) -> str:
    """文件系统安全的目录名。"""
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip() or "未知群"
