# -*- coding: utf-8 -*-
"""总结编排：当天消息全量重建 docx（幂等）+ 文件归档。

语义：每次总结都拉取目标日期的当天全量消息重新生成 docx，
覆盖旧版，避免滚动增量拼接丢话题；归档按 svr_id/文件名去重。
"""
from __future__ import annotations

from datetime import date as date_cls
from pathlib import Path

from app.config import OUTPUT_DIR
from app.core.archiver import Archiver, safe_name
from app.core.message_poller import MessagePoller
from app.models import Message, SummaryResult
from app.state_store import StateStore
from app.summary.deepseek_client import DeepSeekClient
from app.summary.docx_builder import build_docx
from app.summary.pipeline import compute_stats, summarize


def summarize_group(db, poller: MessagePoller, archiver: Archiver,
                    store: StateStore, client: DeepSeekClient | None,
                    chatroom_id: str, group_name: str,
                    day: date_cls | None = None,
                    output_root: Path = OUTPUT_DIR) -> Path | None:
    """对一个群做"当天总结"，返回 docx 路径；当天无消息返回 None。

    client 传 None 时跳过 AI，仅生成统计+文件清单骨架。
    """
    messages = poller.fetch_day(chatroom_id, day)
    if not messages:
        return None
    the_date = f"{messages[0].create_time:%Y-%m-%d}"

    # 1) 文件归档（去重，登记 state.db；启用日前的历史文件不归档）；
    #    用户在设置中整体关闭归档时跳过（但文件清单仍从历史登记里读）
    since_date = (store.get_group(chatroom_id) or {}).get(
        "monitor_start_date")
    from app.config import get_auto_archive
    if get_auto_archive():
        archiver.archive_messages(messages, output_root, group_name,
                                  since_date=since_date)
    files = archiver.list_group_files(chatroom_id, the_date)

    # 2) 统计概览（纯代码）
    stats = compute_stats(messages, group_name, the_date, files)

    # 2.5) 图片 OCR：把图片里的文字提取出来拼进 content，让 AI 能处理
    from app.config import get_ocr_enabled
    if get_ocr_enabled():
        try:
            from app.core.image_ocr import enrich_image_messages
            enrich_image_messages(db, messages)
        except Exception as e:                      # noqa: BLE001
            import logging
            logging.getLogger("app.ocr").warning(
                "图片 OCR 阶段异常，跳过: %s", e)

    # 3) AI 总结
    if client is None:
        result = SummaryResult()
    else:
        result = summarize(client, group_name, messages, stats)

    # 4) Word 排版
    out = (output_root / safe_name(group_name) / the_date / "群聊总结.docx")
    build_docx(group_name, the_date, stats, result, files, out,
               messages=messages)
    store.mark_summaried(chatroom_id, str(out))
    return out
