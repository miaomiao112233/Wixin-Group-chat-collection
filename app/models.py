# -*- coding: utf-8 -*-
"""数据模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Message:
    """清洗后的一条群消息。"""

    chatroom_id: str
    sort_seq: int
    local_id: int
    msg_type: str            # 文本/图片/语音/视频/动画表情/文件/链接/系统消息...
    sender_wxid: str
    sender_name: str
    content: str             # 清洗后的可读文本
    create_time: datetime
    raw_content: str = ""
    file_name: str = ""      # 文件消息的原文件名（归档用）
    svr_id: str = ""         # newmsgid/svrid，归档关联用

    @property
    def is_file(self) -> bool:
        return self.file_name != ""

    def line(self) -> str:
        """喂给模型的行格式：[HH:MM:SS] 昵称(wxid): 内容"""
        return (
            f"[{self.create_time:%H:%M:%S}] {self.sender_name}"
            f"({self.sender_wxid or '系统'}): {self.content}"
        )


@dataclass
class Group:
    chatroom_id: str
    name: str
    enabled: bool = True


@dataclass
class FileInfo:
    """归档文件记录。"""

    chatroom_id: str = ""
    date: str = ""
    orig_name: str = ""
    saved_path: str = ""
    size: int = 0
    svr_id: str = ""
    status: str = "pending"   # pending/archived/missing(未下载)/skipped(不归档类型)


@dataclass
class Topic:
    title: str
    summary: str


@dataclass
class ActiveUser:
    name: str
    msg_count: int
    note: str = ""


@dataclass
class NotableQuote:
    sender: str
    quote: str
    time: str = ""


@dataclass
class ActionItem:
    item: str
    owner: str = ""
    deadline: str = ""


@dataclass
class SummaryResult:
    """DeepSeek map-reduce 产出的四模块结果（统计概览由代码计算）。"""

    topics: list[Topic] = field(default_factory=list)
    active_users: list[ActiveUser] = field(default_factory=list)
    notable_quotes: list[NotableQuote] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)
    partial_failed: bool = False   # JSON 解析降级时标注
