#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键建库 + 导入公开数据包 —— 新克隆本仓库后跑这一个脚本就能开工。

做四件事（**幂等**，可以反复跑）：

  1) **建库**：`modl10n.tm.TM` 建 `tm` / `mods` 表 → `l10n.db.connect()` 迁移出
     索引层（container / resource / unit / term / tlk_slot …）与两个视图；
  2) **导入术语**：数据包里的 `term_active.json` / `term_candidate.json` → `term` 表；
  3) **导入翻译记忆库**：`tm.jsonl` → `tm` 表（★ **用文件里的原始 key**，不重算 ——
     库里有 norm / raw 两种 key 算法写进来的行，重算会让同一句英文变成两条）；
  4) **导入译名替换规则**：`modl10n/term_variants.json` —— 这个在代码仓库里，不用数据包。

用法：
    python tools/bootstrap.py --data ../nwn-ee-l10n-data/data
    python tools/bootstrap.py --data <数据包>/data --db <自定义库路径>
    python tools/bootstrap.py --data <数据包>/data --dry-run     # 只数不写

`unit` / `resource` / `container` 三张表**故意不导入**：它们记录的是
「某台机器上某个模组的某个位置」，路径因人而异，必须由你自己跑
`python -m l10n scan` + `python -m l10n extract` 从源文件重建。
"""
import argparse
import datetime
import json
import os
import sys
from dataclasses import fields

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from l10n.db import DEF_DB, connect                      # noqa: E402
from l10n.model import Term                              # noqa: E402
from l10n.repo import TermRepo                           # noqa: E402

TERM_FIELDS = {f.name for f in fields(Term)}


def build_db(db_path):
    """建一个空的中央库；已存在则只做幂等迁移。返回打开的连接。"""
    from modl10n.tm import TM
    h = TM(db_path)              # 建 tm / mods 表（modl10n 是这两张表的真源）
    h.db.close()
    return connect(db_path)      # 迁移索引层 + 视图


def import_terms(db, data_dir, dry_run=False):
    """导入术语表。返回 {status: 条数}。"""
    tr = TermRepo(db)
    got = {}
    for status, fname in (("active", "term_active.json"),
                          ("candidate", "term_candidate.json")):
        p = os.path.join(data_dir, fname)
        if not os.path.exists(p):
            print("    ⚠ 缺文件，跳过：%s" % fname)
            continue
        raw = json.load(open(p, encoding="utf-8"))
        terms = []
        for i, d in enumerate(raw):
            d = {k: v for k, v in d.items() if k in TERM_FIELDS}
            d.setdefault("type", "name")
            d.setdefault("required", 1)
            d.setdefault("scope", "*")
            d["status"] = status
            if d.get("seq") is None:
                d["seq"] = i + 1
            terms.append(Term(**d))
        if not dry_run:
            tr.upsert_many(terms)
        got[status] = len(terms)
        print("    %-20s %6d 条%s" % (fname, len(terms), "（dry-run，未写）" if dry_run else ""))
    return got


def import_tm(db, data_dir, dry_run=False):
    """导入翻译记忆库。返回条数。"""
    p = os.path.join(data_dir, "tm.jsonl")
    if not os.path.exists(p):
        print("    ⚠ 缺文件，跳过：tm.jsonl")
        return 0
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sql = ("INSERT INTO tm(key,src,dst,src_mod,status,note,updated) "
           "VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
           "src=excluded.src, dst=excluded.dst, src_mod=excluded.src_mod, "
           "status=excluded.status, note=excluded.note, updated=excluded.updated")
    n, buf = 0, []
    for ln in open(p, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        r = json.loads(ln)
        buf.append((r["key"], r["src"], r["dst"], r.get("mod") or None,
                    r.get("status") or "auto", r.get("note"), ts))
        n += 1
        if len(buf) >= 5000 and not dry_run:
            db.executemany(sql, buf)
            buf = []
    if not dry_run:
        if buf:
            db.executemany(sql, buf)
        db.commit()
    print("    %-20s %6d 条%s" % ("tm.jsonl", n, "（dry-run，未写）" if dry_run else ""))
    return n


def import_variants(db, dry_run=False):
    """导入译名替换规则（来自代码仓库，不需要数据包）。"""
    p = os.path.join(ROOT, "modl10n", "term_variants.json")
    if not os.path.exists(p):
        print("    ⚠ 找不到 term_variants.json，跳过")
        return 0
    rows = TermRepo.load_legacy_variants(p)
    if not dry_run:
        TermRepo(db).upsert_variants(rows)
    print("    %-20s %6d 条%s" % ("term_variants.json", len(rows),
                                  "（dry-run，未写）" if dry_run else ""))
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="建库 + 导入公开数据包")
    ap.add_argument("--data", default=os.path.join(ROOT, "..", "nwn-ee-l10n-data", "data"),
                    help="数据包里的 data/ 目录（默认猜同级的 nwn-ee-l10n-data/data）")
    ap.add_argument("--db", default=DEF_DB, help="中央库路径（默认 <仓库>/modl10n/tm.sqlite）")
    ap.add_argument("--dry-run", action="store_true", help="只数条数、不写库")
    a = ap.parse_args()

    data_dir = os.path.abspath(a.data)
    if not os.path.isdir(data_dir):
        sys.exit("找不到数据目录：%s\n（先克隆 nwn-ee-l10n-data，或用 --data 指定）" % data_dir)

    print("=" * 70)
    print("建库 + 导入公开数据")
    print("  数据包：%s" % data_dir)
    print("  目标库：%s%s" % (a.db, "（已存在，做幂等升级）" if os.path.exists(a.db) else "（新建）"))
    print("=" * 70)

    db = build_db(a.db)
    try:
        print("\n[1/3] 术语表 → term 表")
        t = import_terms(db, data_dir, a.dry_run)
        print("\n[2/3] 翻译记忆库 → tm 表")
        n = import_tm(db, data_dir, a.dry_run)
        print("\n[3/3] 译名替换规则 → term_variant 表")
        v = import_variants(db, a.dry_run)

        print("\n" + "=" * 70)
        if a.dry_run:
            print("dry-run 结束，**未写入任何内容**。")
        else:
            print("库现状：term %d 条（active %d / candidate %d）、tm %d 条、term_variant %d 条"
                  % (db.execute("SELECT COUNT(*) FROM term").fetchone()[0],
                     (t.get("active") or 0), (t.get("candidate") or 0), n, v))
            print("库文件：%s（%.1f MB）"
                  % (os.path.abspath(a.db), os.path.getsize(a.db) / 1048576.0))
            print("\n下一步：python -m l10n.selftest      # 跑验收，确认库可用")
    finally:
        db.close()


if __name__ == "__main__":
    main()
