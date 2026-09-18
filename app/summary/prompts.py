# -*- coding: utf-8 -*-
"""Prompt 模板。模型输出统一为 JSON，schema 见 MAP_PROMPT。"""
from __future__ import annotations

MAP_PROMPT = """你是群聊记录整理助手。阅读下面一段带时间戳的群聊记录，提炼信息。

严格输出如下 JSON（不要输出任何其它文字、不要用 markdown 代码块包裹）：
{
  "topics": [{"title": "话题名(<=15字)", "summary": "该话题讨论内容摘要(80-150字，含关键结论)"}],
  "action_items": [{"item": "待办事项", "owner": "负责人或空", "deadline": "时间或空"}],
  "notable_quotes": [{"sender": "发言人", "quote": "原话(<=60字)", "time": "HH:MM"}],
  "active_users": [{"name": "昵称", "note": "主要参与话题/角色一句话"}]
}

要求：
1. topics 按讨论主题分节（1-5 个），没有实质讨论则给空数组；
2. action_items 收录明确安排/请求/约定的事，没有则空数组；
3. notable_quotes 挑 2-6 条有信息量或有趣的原话；
4. active_users 列 3-8 位活跃者；
5. 闲聊寒暄不必成 topic；金额、日期、人名、文件名务必保留原文；
6. 图片消息若带有"识别文字:"，其后内容是 OCR 从图中提取的文字，
   应视为该图片表达的信息参与总结，不要当作普通文本忽略。

群聊记录：
"""


REDUCE_PROMPT = """你是群聊记录整理助手。下面是同一天多个分块的 JSON 摘要，
请合并为一份最终 JSON，schema 与输入相同：
{"topics":[...],"action_items":[...],"notable_quotes":[...],"active_users":[...]}

要求：
1. topics 合并同类话题、按重要性排序，summary 融合各块信息；
2. action_items 去重合并，owner/deadline 保留更完整者；
3. notable_quotes 保留最有价值的 5-8 条；active_users 去重；
4. 严格只输出 JSON，不要 markdown 包裹、不要解释。

分块摘要：
"""
