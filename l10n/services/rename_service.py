#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RenameService —— 术语统一（影响面预览 + 执行）。

★ 与旧 `modl10n.rename` 的三处**刻意不同**（用户 2026-10-08 拍板）：
  ① **术语来源改为中央库 `term` 表**（真源），不再读 `glossary.json`；
  ② **执行时不再写 `glossary_overrides.json`** —— 那会让"多个程序共写同一份 JSON"
     的老问题复活（2026-10-07 真的把用户新增的 `Swordsage` 整段抹掉过）；
  ③ 新译名直接写 `term` 表（旧行自动降级）+ `term_variant` 表。

**记忆库的正文替换仍复用 `modl10n.rename.rows_of_rename`** ——
预览与执行**必须同源**（同一个查询函数），否则会出现"预览 0 条 → 执行也 0 条 →
永远改不到"的鸡生蛋坑（我在 rename.py 上踩过）。
"""
import re

from modl10n.rename import rows_containing, rows_of_rename   # 内核：保持原样给 CLI 用

from ..model import Term

_PLURAL_TAIL = re.compile(r"(s|x|ch|sh)$", re.I)

# ---------------------------------------------------------------------------
# ★ 2026-10-10 加的两道护栏 —— 起因：一次「术语统一」炸了 2776 行 ✗
#
# 事故回顾：`apply()` 执行的是 `dst.replace(old, new)` = **中文全文子串替换**，
# 而护栏 `_tolerant_rx` 只检查**英文**里该术语是否整词出现 —— 它管不住中文里**哪个**
# 同名字符该被换。`term_variant` 里一条 `龙 → 龙类`（护栏 Dragon）就把
# 「龙脉术士」改成「龙类脉术士」、`次 → 时间`（护栏 Time）把「每日一次」改成
# 「每日一时间」，全库 2776 行 ✗（同类事故此前已发生 2 次：Archivist 2761 条、
# Elf→「卓尔，1 级武者」126 行）。
#
# 所以：① **旧译名过短就拒绝自动执行**（短串必然误伤）；
#       ② 执行前逐行**长术语优先**校验，若该行英文匹配的长术语译名被换没了，
#          **跳过该行**并记账 ✓（判据与 `tmp\_plan_restore_1009.py` 的 `r3_revoke` 同源）。
# ---------------------------------------------------------------------------
MIN_SAFE_OLD_LEN = 3


class RiskyRenameRefused(ValueError):
    """危险替换被拒绝执行（原因见 `plan()` 返回的 `risky`）。"""


def _long_terms_lost(terms, src, before, after):
    """替换前后对比：该行英文匹配的 active 术语译名若**从有变无** → 被误改。

    `terms` 由调用方一次性取好（避免逐行查库）。先用"译名是否出现在 before 里"
    做粗筛（字符串包含，快），再用英文护栏正则确认 ✓

    ★ **长术语优先**（2026-10-10 补）：`Justice of Weald and Woe`（林灾审判者）里
    含 `Justice`（正义）—— 把前者换成规范译名时，短术语 `Justice=正义` 必然"消失" ✗，
    于是护栏误报"会破坏长术语"、把 23 行全部跳过 ✗。
    判据与 `tmp\_plan_restore_1009.py` 的 `r3_revoke` 同源：**英文是更长命中术语子串的，不单独计** ✓
    """
    if not src:
        return []
    # ⚠ 顺序很关键：**先按"英文是否匹配"收候选**（含那些译名不在 before 里的长术语 ——
    #   它们正是本次替换的**目标**，若被前置判据滤掉，就无法"覆盖"自己的子串术语 ✗
    #   实测：`Justice of Weald and Woe`→林灾审判者 时，短术语 `Justice=正义` 被判丢失，
    #   23 行全被跳过 ✗）
    cands = []
    for t in terms:
        if t.en and _tolerant_rx(t.en).search(src):
            cands.append(t)
    cands.sort(key=lambda t: -len(t.en))
    covered, lost = [], []
    for t in cands:
        el = (t.en or "").lower()
        if any(el in c for c in covered):
            continue                       # 被更长术语覆盖 → 不算丢失 ✓
        covered.append(el)
        if t.zh and len(t.zh) >= 2 and t.zh in before and t.zh not in after:
            lost.append("%s=%s" % (t.en, t.zh))
    return lost


def _tolerant_rx(en):
    """护栏正则的**宽容版**：忽略大小写 + 容忍复数 + 拉丁第二变格（与 `term_service` 同一套）。

    ⚠ 为什么要与术语检查**同一套规则**：否则会出现"术语检查说该改 32 条、批量替换只改了
    31 条"——实测差的那 1 条就是复数 `Anima Mages`（护栏 `Anima Mage` 匹配不上）✗。
    另一处实测：`Blood Magi`（`Magus` 的拉丁复数）也不在 `s/es` 规则里 ✗。
    """
    esc = re.escape(en)
    if en.endswith("us"):
        # 拉丁第二变格 `Magus` → `Magi`：**替换词尾**，不是加后缀（见 term_service 的说明）
        return re.compile(r"(?<![A-Za-z])" + esc[:-2] + r"(?:us|i|es|s)?(?![A-Za-z])",
                          re.IGNORECASE)
    tail = r"(?:es)?" if _PLURAL_TAIL.search(en) else r"(?:s)?"
    return re.compile(r"(?<![A-Za-z])" + esc + tail + r"(?![A-Za-z])", re.IGNORECASE)


def rows_containing_tolerant(tm_repo, old, new, en=None, include_empty_src=True):
    """`modl10n.rename.rows_containing` 的宽容版。

    ⚠ **不动内核**：`modl10n.rename` 的行为要给 CLI 保持原样（用户手册里写着那条命令），
    所以这里另写一份，只服务 GUI 这条路径。
    """
    rx = _tolerant_rx(en) if en else None
    out = []
    for rid, src, dst, mod in tm_repo.db.execute(
            "SELECT rowid, src, dst, src_mod FROM tm WHERE status!='rejected'"):
        if not dst or old not in dst:
            continue
        if not (src or "").strip():
            if not include_empty_src:
                continue
        elif rx is not None and not rx.search(src):
            continue
        out.append((rid, src or "", dst, mod or ""))
    return out


def rows_of_rename_tolerant(tm_repo, old, new, guard=None, include_empty_src=True):
    """宽容版 `rows_of_rename` —— **预览与执行必须走同一个函数**（同源）。"""
    rows = rows_containing_tolerant(tm_repo, old, new, en=guard, include_empty_src=False)
    if include_empty_src:
        empty = [r for r in rows_containing_tolerant(tm_repo, old, new, en=None,
                                                     include_empty_src=True)
                 if not (r[1] or "").strip()]
        rows += empty
    return rows


def plan(tm_repo, term_repo, renames, en_override=None, include_empty_src=True):
    """影响面预览（**只读**，不写任何东西）。

    `renames` = `[(旧译名, 新译名)]`；`en_override` = 显式英文护栏
    （术语表里已经是新名、但正文还留着旧名时用）。

    返回 `[{old, new, guard, glossary_hits, tm_rows, loose_rows, empty_src_rows,
             mods, targets, unknown_mods}]`。
    """
    from modl10n.rename import TARGET_MAP

    report = []
    for old, new in renames:
        hits_all = term_repo.by_zh(old)
        guard = en_override or (hits_all[0].en if hits_all else None)
        if en_override:
            # ★ **显式护栏优先**：按"旧译名"反查可能查到**同名但不同英文**的术语 ——
            #   实测：`灵魂法师` 既是 `Anima Mage` 的**正确**译名，又是 `Soulcaster`
            #   的**错译**；不按护栏过滤，就会把 Anima Mage 也改成 Soulcaster 的译名 ✗。
            hits = [t for t in hits_all if t.en == en_override]
            if not hits:
                hits = [Term(en=en_override, zh=new, required=1,
                             note="术语表译名已是新名（仅扫正文）")]
        else:
            hits = hits_all
            if guard and not hits:
                # 术语表已是新名：只做"按英文护栏扫正文"的替换
                hits = [Term(en=guard, zh=new, required=1,
                             note="术语表译名已是新名（仅扫正文）")]
        rows = rows_of_rename_tolerant(tm_repo, old, new, guard, include_empty_src)
        empty = [r for r in rows if not (r[1] or "").strip()]
        # 没有英文护栏时退化成"全库替换"——必须明确告警
        loose = [] if guard else rows_containing_tolerant(tm_repo, old, new, en=None)
        # ★ 危险替换标记（2026-10-10）：短旧译名 / 无护栏 → `apply()` 会拒绝执行
        risky = []
        if len(old) < MIN_SAFE_OLD_LEN:
            risky.append("旧译名「%s」只有 %d 个字：中文子串替换会命中无关位置"
                         "（实测：「龙」→「龙类」把「龙脉术士」改成「龙类脉术士」✗）"
                         % (old, len(old)))
        if not guard:
            risky.append("没有英文护栏：会退化成全库替换，无法判断命中是否对位")
        mods = {}
        for _rid, _s, _d, m in (rows + loose):
            mods[m] = mods.get(m, 0) + 1
        report.append({
            "old": old, "new": new, "guard": guard,
            "glossary_hits": [{"en": h.en, "zh": h.zh, "required": bool(h.required)}
                              for h in hits],
            "tm_rows": len(rows), "loose_rows": len(loose), "empty_src_rows": len(empty),
            "mods": mods,
            "targets": sorted({TARGET_MAP.get(m, ("?", "?"))[1] for m in mods
                               if m in TARGET_MAP}),
            "unknown_mods": sorted({m for m in mods if m not in TARGET_MAP}),
            "risky": risky,
        })
    return report


def apply(tm_repo, term_repo, renames, report, include_empty_src=True, force=False):
    """执行：写 `term` 表（新译名 + 提升必守）+ `term_variant` + 改记忆库。

    **不写 `glossary_overrides.json`**（用户拍板：那是导出产物，不该由编辑器自动写）。

    ★ 两道护栏（2026-10-10，`force=True` 可绕过，仅供人工确认后使用）：
      ① 短旧译名 / 无护栏 → 抛 `RiskyRenameRefused`，**整批拒绝**；
      ② 逐行「长术语优先」校验 → 该行英文匹配的长术语译名会被换没的**跳过该行**并记账。

    返回 `{"tm_rows", "terms", "variants", "skipped_breaks"}`。
    """
    terms = term_repo.list(status="active")      # 一次取好，逐行校验用（性能）
    tm_rows = terms_written = variants_written = 0
    skipped = []
    for r, (old, new) in zip(report, renames):
        if r.get("risky") and not force:
            raise RiskyRenameRefused("；".join(r["risky"]))
        for h in r["glossary_hits"]:
            term_repo.set_zh(h["en"], new, required=True, src="rename",
                             note="术语统一：%s -> %s" % (old, new))
            terms_written += 1
        variants_written += term_repo.upsert_variants(
            [(old, new, r.get("guard"), "*", "术语统一：%s -> %s" % (old, new))])
        rows = rows_of_rename_tolerant(tm_repo, old, new, r["guard"], include_empty_src)
        n_skip = 0
        for rid, src, dst, _m in rows:
            fixed = dst.replace(old, new)
            lost = _long_terms_lost(terms, src, dst, fixed)
            if lost:
                skipped.append({"rowid": rid, "old": old, "new": new, "lost": lost,
                                "before": dst[:80], "after": fixed[:80]})
                n_skip += 1
                continue
            tm_repo.db.execute("UPDATE tm SET dst=?, status='manual' WHERE rowid=?",
                               (fixed, rid))
        tm_repo.db.commit()
        tm_rows += len(rows) - n_skip
    return {"tm_rows": tm_rows, "terms": terms_written, "variants": variants_written,
            "skipped_breaks": skipped}
