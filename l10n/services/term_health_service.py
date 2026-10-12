#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TermHealthService —— **术语表体检**（只读）。

判据全部来自 2026-10-11 这一轮实际踩到的坑：

    dup_en   同一英文有多条 active      → 歧义；也是"被写脏"的第一信号 ✗
                 （实测被 `apply` 的虚拟术语写过 `哪种龙脉术士`、`仅限1级或龙类进化且3级`）
    suffix   译名带「（…）」后缀         → 如 `星质形态（灵能）`；"一键采用"会把括号一起搬进正文 ✗
    ascii    译名里混英文/数字          → 如 `Electric Jolt`、`驱逐术，灵能` 这类残留 ✗
    long     译名过长（>12 字）         → 多半是句子片段被当成术语写进去了 ✗
    empty    译名为空

用法：
    from l10n.services import term_health_service
    groups = term_health_service.health(term_repo)     # {"dup_en": [...], ...}
    term_health_service.summary(groups)                # 一句人话
"""
import re

_ASCII = re.compile(r"[A-Za-z]")
_SUFFIX = re.compile(r"[（(][^）)]{1,10}[）)]")
# ★ 2026-10-11 调细：**原文自带括号**时，译文里的括号是「对应结构」而不是脏后缀 ✓
#   例：`Armor Proficiency (Heavy)` = 擅长盔甲（重型）、`Deformity(eyes)` = 畸变(眼)、
#       `Spell Focus (Good)` = 法术专注（善良） —— 这些原本被误报 ✗
_PAREN_IN_EN = re.compile(r"[（(][^）)]{1,60}[）)]")


def _derived(a, b):
    """两个英文是否只是**词性/单复数/变音符号/限定词**的派生关系。

    这类共用同一个中文译名是**合理**的（用户 2026-10-11 已裁决"词性派生合理、不动" ✓）：
        Dead / Death、Demon / Demonic、Faerun / Faerûn、Moonshae / Moonshae Isles、
        Ability Damage / Ability Damaged、Kalashtar / Kalashtars、
        Justice of Weald and Woe / Justices of Weald and Woe
    → 只有**互不为派生关系**的同译名才算真冲突（如 `Dragon Hunter` / `Dragonstalker`）。
    """
    import unicodedata

    def norm(x):
        x = unicodedata.normalize("NFKD", (x or "").lower())
        return "".join(c for c in x if c.isalnum())

    na, nb = norm(a), norm(b)
    if not na or not nb:
        return True
    if na == nb or na in nb or nb in na:
        return True
    return False


def health(term_repo, limit=300):
    """返回 `{"dup_en": [...], "suffix": [...], "ascii": [...], "long": [...], "empty": [...]}`。

    每项是 `(en, zh, 说明)` 三元组，方便界面直接列。
    """
    rows = list(term_repo.db.execute(
        "SELECT en, zh, type, required FROM term WHERE status='active' ORDER BY en"))
    out = {"dup_en": [], "dup_zh": [], "suffix": [], "ascii": [], "long": [], "empty": []}
    seen = {}
    zseen = {}
    for en, zh, ty, req in rows:
        en, zh = en or "", zh or ""
        seen.setdefault(en.lower(), []).append(zh)
        if not zh.strip():
            out["empty"].append((en, zh, "空译名"))
        # 括号后缀：只报"原文没有括号、译文自己加括号"的（消歧后缀会污染正文 ✗）；
        # 另：`Lammasu, Golden Protector Celestial Half-D…` 这类**生物模板名**（en 带逗号）
        # 译名带括号是合理对应 ✓ 跳过。
        if _SUFFIX.search(zh) and not _PAREN_IN_EN.search(en) and "," not in en:
            out["suffix"].append((en, zh, "译名带括号后缀"))
        if _ASCII.search(zh):
            out["ascii"].append((en, zh, "译名里混英文"))
        # 过长：阈值 12 → 16 字；且**原文自带参数括号**的（`Spell Focus(chaos, Evil, …)`）
        # 译文长是结构对应 ✓ 跳过。
        if len(zh) > 16 and not _PAREN_IN_EN.search(en):
            out["long"].append((en, zh, "译名过长（%d 字）" % len(zh)))
        # ★ 新增：**同一译名对应多个不同英文** —— 做"一键采用/统一替换"时容易改错行 ✗
        zseen.setdefault(zh.strip(), []).append(en)
    for lo, zhs in seen.items():
        uniq = sorted(set(zhs))
        if len(uniq) > 1:
            out["dup_en"].append((lo, " / ".join(uniq),
                                  "同一英文有 %d 种 active 译名" % len(uniq)))
    for zh, ens in zseen.items():
        uniq = sorted(set(ens))
        # 过滤掉"词性/单复数派生"的同译名（那是合理的 ✓，用户已裁决过）
        if len(uniq) > 1 and len(zh) >= 2 and not any(
                _derived(x, y) for i, x in enumerate(uniq) for y in uniq[i + 1:]):
            out["dup_zh"].append((zh, " / ".join(uniq),
                                  "同一译名对应 %d 个不同英文" % len(uniq)))
    for k in out:
        out[k] = out[k][:limit]
    return out


def summary(groups):
    """一句话人话（给日志区/弹窗标题用）。"""
    parts = [("%s %d 条" % (name, len(groups.get(key) or [])))
             for key, name in (("dup_en", "同英多译"), ("dup_zh", "同译名多英文"),
                               ("suffix", "带括号后缀"), ("ascii", "混英文"),
                               ("long", "过长"), ("empty", "空译名"))]
    n = sum(len(groups.get(k) or []) for k in groups)
    return "术语表体检：共 %d 处问题（%s）" % (n, "；".join(parts))
