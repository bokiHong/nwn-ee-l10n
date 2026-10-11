#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""key 语义的**唯一入口** —— 翻译记忆的键怎么算，只在这里定义。

为什么需要这个模块（Sprint 1 实测发现的隐患）：

    现有两条流程算 key 的算法**并不完全一致**——
      * `.mod` 流程（`modl10n\\pipeline.py:214`）：`key_of(u.text)`        ← **不**规范化
      * TLK 流程（`modl10n\\tlk.py:38 key_tlk`） ：`key_of(norm(text))`   ← 先规范化

    两者只在"文本本身没有首尾空白、换行已统一"时才相等。GFF / .nss 的文本
    通常满足这个条件（所以共用一个库能命中 43,942 条），但边界情况会算出
    两个不同的 key → 同一句英文被当成两条，白花钱重译。

本模块把 key 计算收成一处，默认 `normalize=True`（与 TLK 流程一致），
并复用 modl10n 的 `norm` / `key_of` 实现，**不重写第二份**。
"""
import os
import sys

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from modl10n.tm import key_of                     # noqa: E402  内容哈希（唯一真源）
from modl10n.tlk import norm                      # noqa: E402  统一换行 + 去首尾空白

__all__ = ["norm", "key_of", "src_key", "raw_hash", "keys_for",
           "is_normalized", "key_variants"]


def src_key(text, normalize=True):
    """源文 -> 翻译记忆的 key。

    normalize=True（默认）与 TLK 流程一致，也是本工作台的标准；
    normalize=False 用于**复核** .mod 流程的历史 key。
    """
    s = text or ""
    return key_of(norm(s) if normalize else s)


def raw_hash(text):
    """源文的**原样**哈希（不 norm）—— 记录"字面内容有没有变过"，用于诊断。

    与 `src_key` 的区别：`src_key` 会先 norm（吞掉首尾空白与换行差异），
    `raw_hash` 不会。所以"只有空白变了"表现为 raw_hash 变、src_key 不变。
    """
    return key_of(text or "")


def keys_for(text):
    """查库时**该试的全部 key**（norm 优先，raw 兜底）。顺序有意义：先用 norm。

    ★ 这是 Sprint 2 实测结论的落地：Ch1 的 3,325 条去重文本里，只用 norm key
    会漏掉 51 条（它们当初是原 pipeline 用 raw key 写进 tm 的），**两个都试就全中**。
    所以任何"这句有没有译文"的判断都必须走这个函数，不许直接 `src_key()` 了事。
    """
    k1 = src_key(text, True)
    k2 = src_key(text, False)
    return [k1] if k1 == k2 else [k1, k2]


def is_normalized(text):
    """文本是否已经"规范"（过一遍 norm 不变）。用于诊断 key 分叉。"""
    s = text or ""
    return norm(s) == s


def key_variants(text):
    """两种算法各自的 key —— 诊断用。

    返回 {"norm": k1, "raw": k2, "same": bool}。
    当 same=False 时就存在"同一句英文两个 key"的风险，需要人工关注。
    """
    k1, k2 = src_key(text, True), src_key(text, False)
    return {"norm": k1, "raw": k2, "same": k1 == k2}
