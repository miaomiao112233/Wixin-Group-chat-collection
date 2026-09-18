# -*- coding: utf-8 -*-
"""WeChatDB 实例工厂：统一缓存目录与密钥文件分配。

不同角色（采集线程 / 总结线程 / UI 临时查询）必须使用**独立**的解密副本
目录，否则并发 WAL 合并会互相重写缓存文件，导致 SQLite
"database disk image is malformed"。密钥文件跨角色共享，只需提取一次。
"""
from __future__ import annotations

from app.config import (WECHAT_KEYS_FILE, get_wechat_data_dir,
                        wechat_workdir)


def open_wechat_db(role: str):
    """role: 'monitor' / 'summary' / 'ui'。"""
    from wechatauto import WeChatDB
    return WeChatDB(db_dir=get_wechat_data_dir() or None,
                    workdir=wechat_workdir(role),
                    keys_file=WECHAT_KEYS_FILE)
