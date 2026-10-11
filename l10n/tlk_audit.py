#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TLK ↔ 记忆库 对账（**只列不改**）。

把"当前已安装 TLK"与"翻译记忆库"逐条比对，导成 CSV 供人工拍板。四种状态：

    一致         当前译文 == 记忆库译文
    不一致       两边都有，但内容不同（记忆库过时 / TLK 被手工改过）
    未入库       记忆库里**没有**这条英文的译文  ← 手工翻译大概率落在这里
    无英文对照   英文源 TLK 该 strref 没有文本（例如那 89 个自加界面标签）

用法：
    cd scripts
    python -m l10n.tlk_audit                      # 默认对 PRC8 已安装 TLK，只导出"非一致"
    python -m l10n.tlk_audit --all                 # 连"一致"的也导出（约 3 万行）
    python -m l10n.tlk_audit --tlk X --en-tlk Y --out Z.csv

⚠ **本脚本只读库、只写那一份 CSV**，绝不修改 TLK 或记忆库。
"""
import csv
import os
import sys

from . import paths, tlkio
from .db import DEF_DB, connect
from .repo import TmRepo

COLS = ["strref", "状态", "英文原文", "当前TLK译文", "记忆库译文", "备注"]
STATES = ("一致", "不一致", "未入库", "无英文对照")

# TLK 目录：默认取 NWN:EE 用户目录下的 `tlk\`（可用环境变量 `NWN_HOME` 覆盖），
# 也可用 `--tlk` / `--en-tlk` 显式指定别的路径。
GAME_DIR = paths.nwn_sub("tlk")


def audit(tlk_path, en_tlk_path, tm_repo, include_same=False):
    """-> (rows, summary)。rows 已按 strref 升序。"""
    cur = tlkio.load(tlk_path)
    en = tlkio.load(en_tlk_path)
    cur_texts, en_texts = cur["texts"], en["texts"]

    # ★ 一次批量查（`lookup_many` 自带双 key 兜底）；别逐条 lookup_one —— 3 万条会慢到没法用
    pairs = {k: en_texts[k] for k in cur_texts if (en_texts.get(k) or "").strip()}
    got = tm_repo.lookup_many(pairs.values())

    rows, cnt = [], {s: 0 for s in STATES}
    for k in sorted(cur_texts, key=int):
        zh = cur_texts[k]
        e = en_texts.get(k)
        if not (e or "").strip():
            cnt["无英文对照"] += 1
            rows.append([k, "无英文对照", "", zh, "", "英文源该 strref 无文本"])
            continue
        zh_tm = got.get(e)
        if zh_tm is None:
            cnt["未入库"] += 1
            rows.append([k, "未入库", e, zh, "", "记忆库里没有这条英文的译文"])
        elif (zh_tm or "").strip() != (zh or "").strip():
            cnt["不一致"] += 1
            rows.append([k, "不一致", e, zh, zh_tm, ""])
        else:
            cnt["一致"] += 1
            if include_same:
                rows.append([k, "一致", e, zh, zh_tm, ""])
    if not include_same:
        rows = [r for r in rows if r[1] != "一致"]
    summary = dict(cnt)
    summary.update({"tlk": os.path.abspath(tlk_path),
                    "en_tlk": os.path.abspath(en_tlk_path),
                    "total": len(cur_texts), "rows": len(rows),
                    "encoding": cur.get("encoding")})
    return rows, summary


def write_csv(path, rows):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:   # BOM：Excel 不乱码
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(rows)
    return len(rows)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    tlk = opt("--tlk", os.path.join(GAME_DIR, "prc8_consortium.tlk"))
    en = opt("--en-tlk", os.path.join(GAME_DIR, "prc8_consortiumE.tlk"))
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = opt("--out", os.path.join(here, "tmp", "输出", "TLK对账_prc8.csv"))

    print("=" * 78)
    print("TLK ↔ 记忆库对账（只列不改）")
    print("  当前 TLK：%s" % tlk)
    print("  英文源  ：%s" % en)
    if not (os.path.exists(tlk) and os.path.exists(en)):
        print("  ✗ 文件不存在，无法对账")
        return 1
    db = connect(opt("--db", DEF_DB), migrate_now=False)
    try:
        rows, s = audit(tlk, en, TmRepo(db), include_same="--all" in argv)
    finally:
        db.close()
    print("  有文本条目 %d 条（编码判定：%s）：" % (s["total"], s["encoding"]))
    for k in STATES:
        print("    %-8s %6d" % (k, s[k]))
    n = write_csv(out, rows)
    print("  已写 CSV：%s（%d 行，utf-8-sig）" % (os.path.abspath(out), n))
    print()
    print("  ⚠ 本脚本没有改动任何 TLK 或记忆库 —— 改不改由你拍板。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
