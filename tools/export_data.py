#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把中央库 `tm.sqlite` 里的**可分发资产**导出成纯数据文件（JSON / JSONL）。

导出什么：
  * `term` 表 → `term_active.json`（正式术语）+ `term_candidate.json`（候选术语）
  * `tm`   表 → `tm.jsonl`（翻译记忆库，一行一条，UTF-8）

不导出什么：`unit` / `resource` / `container` 是**从源文件扫描出来的位置索引**，
  不同人的模组路径不一样，导出没有意义 —— 应该让对方自己跑 `scan` + `extract` 重建。

用法：
    python tools/export_data.py --db <tm.sqlite> --out <数据仓库目录> [--no-tm]

例：
    python tools/export_data.py --db ../nwn-ee-l10n/…  # 见 README
"""
import argparse
import json
import os
import sqlite3
import sys

TERM_COLS = ("en", "zh", "type", "required", "scope", "src", "note", "seq")


def export_terms(db, out, status, fname):
    rows = db.execute(
        "SELECT en, zh, type, required, scope, src, note, seq FROM term "
        "WHERE status=? ORDER BY seq, en COLLATE NOCASE", (status,)
    ).fetchall()
    data = [dict(zip(TERM_COLS, r)) for r in rows]
    # note/src/scope 为空的字段省掉，文件更小；导入时按默认值补
    for d in data:
        for k in ("note", "src"):
            if not d.get(k):
                d.pop(k, None)
        if d.get("scope") in (None, "*"):
            d.pop("scope", None)
    p = os.path.join(out, fname)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    print("  %-22s %6d 条  %7.1f KB" % (fname, len(data), os.path.getsize(p) / 1024))
    return len(data)


def export_tm(db, out, fname="tm.jsonl"):
    """导出翻译记忆库。

    ★ **必须带上原始 `key`**：库里有两种 key 算法写进来的行（norm / raw，见
    `l10n/keys.py` 的说明），导入时只能原样还原、不能重算 —— 重算会把 raw 那批
    的行算成另一个 key，同一句英文出现两条 ✗。

    ★ **孤儿译文（`src` 为空但 `key` + `dst` 有效）也要导出**：实测源库里有 64 条
    这种行，它们正是「模组里的文本能按 key 查到译文、但按 src 查不到」的原因。
    漏掉它们会让导入方看到「明明译过却报未译」的假缺口 ✗。
    """
    p = os.path.join(out, fname)
    n = 0
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        for key, src, dst, mod, status, note in db.execute(
                "SELECT key, src, dst, src_mod, status, note FROM tm "
                "WHERE (src IS NOT NULL AND src != '') "
                "   OR (dst IS NOT NULL AND dst != '') "
                "ORDER BY key"):
            rec = {"key": key, "src": src, "dst": dst, "mod": mod or "",
                   "status": status or "auto"}
            if note:
                rec["note"] = note
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    print("  %-22s %6d 条  %7.1f MB" % (fname, n, os.path.getsize(p) / 1048576))
    return n


def export_schema(db, out, fname="schema.sql"):
    p = os.path.join(out, fname)
    lines = []
    for name, sql in db.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type DESC, name"):
        lines.append(sql.strip() + ";\n")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(lines)
    print("  %-22s %6d 个对象" % (fname, len(lines)))
    return len(lines)


def main():
    ap = argparse.ArgumentParser(description="导出可分发资产")
    ap.add_argument("--db", required=True, help="中央库 tm.sqlite 路径")
    ap.add_argument("--out", required=True, help="输出目录（数据仓库的 data/）")
    ap.add_argument("--no-tm", action="store_true", help="只导术语，不导翻译记忆库")
    ap.add_argument("--schema-out", help="另外把建表 SQL 导出到这个目录")
    a = ap.parse_args()

    if not os.path.exists(a.db):
        sys.exit("找不到库：%s" % a.db)
    os.makedirs(a.out, exist_ok=True)

    print("从 %s 导出：" % a.db)
    db = sqlite3.connect(a.db)
    export_terms(db, a.out, "active", "term_active.json")
    export_terms(db, a.out, "candidate", "term_candidate.json")
    if not a.no_tm:
        export_tm(db, a.out)
    if a.schema_out:
        os.makedirs(a.schema_out, exist_ok=True)
        export_schema(db, a.schema_out)
    db.close()
    print("完成。")


if __name__ == "__main__":
    main()
