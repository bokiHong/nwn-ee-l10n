#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AuditService —— **备份库逐行对账**（把今天反复临时写的那套固化下来 ✓）。

用法（一次批量改写之后）：

    from l10n.services import audit_service
    d = audit_service.diff_against_backup(db, "tmp/备份/tm_xxx.sqlite")
    print(audit_service.summary(d))                      # 人话摘要
    print(audit_service.unexplained(d, mappings))        # 无法用映射解释的行（必须 0 ✗）

为什么需要它：2026-10-11 一天里我临时写了五六次"ATTACH 备份 → 按 rowid 逐行比 → 断言
每一行差异都能被本批映射解释"，正是它挖出了 `抑制`/`抗抗` 那类误伤 ✗。
"""
import os


def diff_against_backup(db, backup_path, table="tm", src_col="dst"):
    """把备份库 `ATTACH` 进来，逐行比 `src_col`（默认 `dst`）→ 返回差异行。

    返回 `[{"rowid", "before", "after", "src", "mod"}]`（按 rowid 升序）。
    ⚠ 只用 `rowid` 对齐（同一个库的两份副本 ✓）。
    """
    if not os.path.exists(backup_path):
        raise FileNotFoundError(backup_path)
    db.execute("ATTACH DATABASE ? AS audit_old", (backup_path,))
    try:
        cols = [r[1] for r in db.execute("PRAGMA table_info(%s)" % table)]
        extra = [c for c in ("src", "src_mod") if c in cols]
        sel = ", ".join(["n.rowid", "o.%s" % src_col, "n.%s" % src_col]
                        + ["n.%s" % c for c in extra])
        sql = ("SELECT %s FROM %s n JOIN audit_old.%s o ON o.rowid = n.rowid "
               "WHERE o.%s IS NOT n.%s ORDER BY n.rowid" % (sel, table, table, src_col, src_col))
        out = []
        for r in db.execute(sql):
            d = {"rowid": r[0], "before": r[1], "after": r[2]}
            for i, c in enumerate(extra):
                d[c] = r[3 + i]
            out.append(d)
        return out
    finally:
        db.execute("DETACH DATABASE audit_old")


def classify(diffs, mappings):
    """按 `[(旧, 新), ...]` 把差异归类；返回 `(命中统计 dict, 未解释的列表)`。

    判据：**一次或多次**应用这些映射后能完全还原出 `after` → 算"可解释" ✓
    （一行里可能有多处替换，所以不能用单次 `replace` 判断 ✗ —— 我踩过这个误报）
    """
    hit, un = {}, []
    for d in diffs:
        t = d["before"] or ""
        for a, b in mappings:
            if a in t:
                t = t.replace(a, b)
        if t == (d["after"] or ""):
            key = "本批映射"
            for a, b in mappings:
                if a in (d["before"] or ""):
                    key = "%s -> %s" % (a, b)
                    break
            hit[key] = hit.get(key, 0) + 1
        else:
            un.append(d)
    return hit, un


def summary(diffs):
    """一句话人话。"""
    return "对账：备份与当前共 %d 行不同" % len(diffs)
