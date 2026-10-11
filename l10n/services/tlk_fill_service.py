#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TlkFillService —— 把**记忆库译文回填到 TLK**。

用户 2026-10-08 需求："将记忆库/术语库的改动，**安全地**回填到当前打开的 .tlk 文件
（要求复用 `rebuild_tlk_safe.py` 保留自加槽位的逻辑，并且必须做备份）"。

**与 `rebuild_tlk_safe.py` 的关系**：那个是"从**英文原版**重建整份 TLK"（会重排、
且路径为 PRC8 写死）；这里是"**就地增量回填**"（保留原文件结构，只改文本）。但**继承
了它最关键的三条保护**：

  ① **英文源里没有的 strref（自加槽位，例如那 89 个中文界面标签）→ 原样保留**；
  ② 写前**自动备份**（`<tlk>.回填前_<时间戳>`）；
  ③ 写后**读回逐条校验**：应改的都改了、**不该动的一字未动**。

**四条规则**（安全性的来源）：
  1. 逐条 strref：英文源有文本 → 用 `tm.lookup`（双 key 兜底）取译文；**取不到就保持原样**；
  2. 英文源里没有的 strref → **原样保留**；
  3. 只有内容**真的变了**才写；
  4. 写前备份 + 写后校验（见上）。
"""
import os
import shutil
import time

from .. import tlkio


def plan_fill(tlk_path, en_tlk_path, tm_repo, max_preview=50):
    """只读预览：算出会改哪些条目。**不写任何东西。**

    返回 `{tlk, en_tlk, total, changeable, same, no_tm, custom_slots, changes, preview}`。
    """
    cur = tlkio.load(tlk_path)
    en_texts = {}
    if en_tlk_path and os.path.exists(en_tlk_path):
        en_texts = (tlkio.load(en_tlk_path).get("texts") or {})
    pairs = {k: en_texts[k] for k in cur["texts"] if (en_texts.get(k) or "").strip()}
    got = tm_repo.lookup_many(pairs.values()) if pairs else {}

    changes, same, no_tm = [], 0, 0
    for k, zh in cur["texts"].items():
        e = en_texts.get(k)
        if not (e or "").strip():
            continue                                  # 自加槽位：不参与
        nz = got.get(e)
        if nz is None:
            no_tm += 1                                # 记忆库没译文 → 保持原样
        elif (nz or "").strip() != (zh or "").strip():
            changes.append({"strref": k, "en": e, "old": zh or "", "new": nz or ""})
        else:
            same += 1
    changes.sort(key=lambda c: int(c["strref"]))
    custom = [k for k in cur["texts"] if not (en_texts.get(k) or "").strip()]
    return {"tlk": os.path.abspath(tlk_path),
            "en_tlk": os.path.abspath(en_tlk_path) if en_tlk_path else None,
            "count": cur["count"], "lang": cur["lang"],
            "total": len(cur["texts"]),
            "changeable": len(changes), "same": same, "no_tm": no_tm,
            "custom_slots": len(custom), "custom_ids": sorted(custom, key=int),
            "changes": changes, "preview": changes[:max_preview]}


def fill_tlk(tlk_path, en_tlk_path, tm_repo, out_path=None, backup=True):
    """执行回填，返回结果 dict。

    `out_path=None` 表示**就地写回**（会先备份）；给了 `out_path` 则写到别处（不覆盖原件）。
    """
    rep = plan_fill(tlk_path, en_tlk_path, tm_repo)
    target = os.path.abspath(out_path or tlk_path)
    if not rep["changeable"]:
        return dict(rep, written=0, verified=True, bad=[], untouched_bad=[],
                    backup=None, out=target, note="没有需要回填的条目")

    cur = tlkio.load(tlk_path)
    changed_ids = {c["strref"] for c in rep["changes"]}
    for c in rep["changes"]:
        cur["texts"][c["strref"]] = c["new"]

    bk = None
    if backup and target == os.path.abspath(tlk_path):
        bk = target + ".回填前_" + time.strftime("%Y%m%d_%H%M%S")
        shutil.copy(target, bk)

    tlkio.write_texts(target, cur["lang"], cur["count"], cur["texts"])

    # ---- 写后读回校验：应改的都改了 + 不该动的一字未动 ----
    chk = tlkio.load(target)
    bad = [c["strref"] for c in rep["changes"]
           if (chk["texts"].get(c["strref"]) or "") != c["new"]]
    untouched_bad = [k for k, v in cur["texts"].items()
                     if k not in changed_ids and (chk["texts"].get(k) or "") != (v or "")]
    return dict(rep, written=len(rep["changes"]), verified=(not bad and not untouched_bad),
                bad=bad, untouched_bad=untouched_bad, backup=bk, out=target,
                after_total=len(chk["texts"]), after_count=chk["count"])
