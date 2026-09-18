# -*- coding: utf-8 -*-
"""Word 文档生成：python-docx 四模块排版。"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

from app.config import DOCX_FONT
from app.models import Message, SummaryResult
from app.summary.pipeline import Stats

_STATUS_LABEL = {"archived": "已归档", "missing": "未下载",
                 "pending": "下载中", "skipped": "不归档类型"}


def _set_font(run, size: int = 11, bold: bool = False):
    run.font.name = DOCX_FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), DOCX_FONT)


def _h1(doc: Document, text: str):
    p = doc.add_heading("", level=0)
    run = p.add_run(text)
    _set_font(run, 20, True)


def _h2(doc: Document, text: str):
    p = doc.add_heading("", level=1)
    run = p.add_run(text)
    _set_font(run, 14, True)


def _table(doc: Document, headers: list[str],
           rows: list[list[str]], widths: list[int] | None = None):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    for i, h in enumerate(headers):
        cell = t.rows[0].cells[i]
        cell.text = ""
        _set_font(cell.paragraphs[0].add_run(h), 10.5, True)
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            _set_font(cells[i].paragraphs[0].add_run(str(v)), 10.5)
    return t


def build_docx(group_name: str, date: str, stats: Stats,
               result: SummaryResult, files: list[dict], out_path: Path,
               messages: list[Message] | None = None) -> Path:
    doc = Document()

    _h1(doc, f"{group_name} 群聊总结")
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_font(sub.add_run(f"{date}  {stats.range_text}"), 10)

    # 一、统计概览
    _h2(doc, "一、统计概览")
    _table(doc, ["项目", "数值"], stats.to_rows())

    # 二、话题分节摘要
    _h2(doc, "二、话题分节摘要")
    if result.topics:
        for t in result.topics:
            p = doc.add_paragraph()
            _set_font(p.add_run(f"◆ {t.title}"), 12, True)
            p2 = doc.add_paragraph()
            _set_font(p2.add_run(t.summary), 11)
    else:
        _set_font(doc.add_paragraph().add_run(
            "今日无实质性话题讨论。" + ("（AI 摘要部分失败，仅供参考）"
                                     if result.partial_failed else "")), 11)

    # 三、活跃人物与关键发言
    _h2(doc, "三、活跃人物与关键发言")
    if stats.top_users:
        _table(doc, ["排名", "成员", "消息数"],
               [[str(i + 1), n, str(c)]
                for i, (n, c) in enumerate(stats.top_users[:8])])
    if result.active_users:
        doc.add_paragraph()
        _table(doc, ["成员", "参与情况"],
               [[u.name, u.note or "-"] for u in result.active_users[:8]])
    if result.notable_quotes:
        doc.add_paragraph()
        for q in result.notable_quotes:
            p = doc.add_paragraph()
            who = f"{q.sender}" + (f" {q.time}" if q.time else "")
            _set_font(p.add_run(f"「{q.quote}」 —— {who}"), 10.5)

    # 四、重要事项待办
    _h2(doc, "四、重要事项待办")
    if result.action_items:
        _table(doc, ["事项", "负责人", "期限"],
               [[a.item, a.owner or "-", a.deadline or "-"]
                for a in result.action_items])
    else:
        _set_font(doc.add_paragraph().add_run("今日无明显待办事项。"), 11)

    # 五、文件清单
    _h2(doc, "五、文件清单")
    if files:
        _table(doc, ["文件名", "大小", "状态"],
               [[f["orig_name"], _fmt_size(f.get("size") or 0),
                 _STATUS_LABEL.get(f.get("status"), f.get("status"))]
                for f in files])
    else:
        _set_font(doc.add_paragraph().add_run("今日无文件消息。"), 11)

    if result.partial_failed:
        p = doc.add_paragraph()
        _set_font(p.add_run("注：AI 摘要部分失败，本文档由可用块拼接生成。"), 9)

    # 六、群聊原话记录（完整保留当天消息原文，便于核对）
    if messages:
        doc.add_page_break()
        _h2(doc, "六、群聊原话记录")
        for m in messages:
            p = doc.add_paragraph()
            _set_font(
                p.add_run(
                    f"[{m.create_time:%H:%M:%S}] {m.sender_name}: {m.content}"),
                9.5)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    return out_path


def _fmt_size(n: int) -> str:
    if n <= 0:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return f"{n:.1f}TB"
