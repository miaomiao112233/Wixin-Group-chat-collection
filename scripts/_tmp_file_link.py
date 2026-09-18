# -*- coding: utf-8 -*-
"""阶段3实测3：在磁盘上定位已下载的群文件。"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")
from wechatauto import WeChatDB

db = WeChatDB()
base = db.account_dir
TARGET_SIZE = 280765
TARGET_NAME = "江西建设职业技术学院2026年秋季老生心理普查任务通知.docx"

# ---- 1. msg\attach / msg\file 目录结构 ----
for sub in ("msg\\attach", "msg\\file", "msg\\video"):
    p = os.path.join(base, sub)
    if not os.path.isdir(p):
        print(f"{sub}/ 不存在")
        continue
    subs = os.listdir(p)
    print(f"{sub}/ 共{len(subs)}项, 样例: {subs[:6]}")
    # 深入一层
    for s in subs[:2]:
        d1 = os.path.join(p, s)
        if os.path.isdir(d1):
            inner = os.listdir(d1)[:6]
            print(f"   {s}/: {inner}")
            for s2 in inner[:3]:
                d2 = os.path.join(d1, s2)
                if os.path.isdir(d2):
                    print(f"     {s2}/: {os.listdir(d2)[:6]}")

# ---- 2. 按大小精确搜索目标文件 ----
print(f"\n[搜索] size={TARGET_SIZE} 的文件:")
found = []
for root, dirs, files in os.walk(os.path.join(base, "msg")):
    for f in files:
        fp = os.path.join(root, f)
        try:
            if os.path.getsize(fp) == TARGET_SIZE:
                found.append(fp)
        except OSError:
            pass
for fp in found[:10]:
    print("  命中:", fp, "名字匹配" if f == TARGET_NAME else "")
print(f"共 {len(found)} 个命中")
