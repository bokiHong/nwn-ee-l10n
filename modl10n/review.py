#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""润色/审校流程：导出审校表(TSV) → 人工修改 → 回导翻译记忆 → 重新打包（0 次 API 调用）。

为什么需要这一步：译文存在翻译记忆库（tm.sqlite）里，打包时是从记忆库取译文的。
直接改 txt 而不回写记忆库，重新打包不会生效。

用法:
    # 1) 导出审校表（Excel 或记事本都能开；带 BOM，Excel 不会把中文显示成乱码）
    python -m modl10n.review export --mod "…\\Chapter One.mod" --out 审校表.tsv

    #    只要一部分？按关键词/文件/类型筛，或者分块（每块 200 条）
    python -m modl10n.review export --mod "…\\Chapter One.mod" --out 第1块.tsv --limit 200
    python -m modl10n.review export --mod "…\\Chapter One.mod" --out 对话.tsv --fname "*.dlg"

    # 2) 只改「现译」这一列（其余列别动），保存为 UTF-8

    # 3) 回导（先 --dry-run 看会改多少条）
    python -m modl10n.review apply --file 审校表.tsv --dry-run
    python -m modl10n.review apply --file 审校表.tsv

    # 4) 重新打包（0 次 API 调用）
    python -m modl10n.pipeline --mod "…\\Chapter One.mod" --outdir "…" --yes

回导后这些条目会被标为 status=manual —— 机器翻译永远不会再覆盖你的润色。
"""
import os
import sys
import csv
import argparse
import collections

from .core import sfrules as R
from .tm import TM, key_of
from .extract import extract_units

COLS = ["key", "英文原文", "现译", "状态", "类型", "位置", "字段", "出现次数"]
KEEP = "请勿修改本列"          # 表头注释写在第 2 行之后不方便，改用下方 print 提示


def _load_rows(args, tm):
    """返回 [(key, src, dst, kind, loc, label, count)]；--mod 时只取该 mod 需要的条目。"""
    out = []
    if args.mod:
        erf, units = extract_units(args.mod, include_nss=not args.no_nss)
        first = {}
        for u in units:
            k = key_of(u.text)
            if k not in first:
                first[k] = (u.text, u.kind, u.fname,
                            u.label if u.label else ("L%d" % (u.lineno or 0)), 0)
            first[k] = (first[k][0], first[k][1], first[k][2], first[k][3], first[k][4] + 1)
        hits = tm.get_many(first.keys())
        for k, (src, kind, loc, label, n) in first.items():
            out.append((k, src, hits.get(k, ""), kind, loc, label, n))
    else:
        for k, s, d, st in tm.iter_all():
            if st == "rejected":
                continue
            out.append((k, s, d or "", "gff", "", "", 1))
    return out


def cmd_export(args):
    tm = TM(args.tm)
    rows = _load_rows(args, tm)

    def keep(r):
        k, src, dst, kind, loc, label, n = r
        if args.kind and kind != args.kind:
            return False
        if args.fname and args.fname.strip("*.") not in (loc or ""):
            return False
        if args.filter and args.filter not in (src or "") and args.filter not in (dst or ""):
            return False
        if args.min_len and len(src or "") < args.min_len:
            return False
        return True

    rows = [r for r in rows if keep(r)]
    rows.sort(key=lambda r: (r[4] or "", r[3], r[2] or ""))
    total = len(rows)
    if args.offset:
        rows = rows[args.offset:]
    if args.limit:
        rows = rows[:args.limit]

    n_trans = sum(1 for r in rows if r[2] and r[2] != r[1])
    n_same = sum(1 for r in rows if r[2] and r[2] == r[1])
    n_none = sum(1 for r in rows if not r[2])
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter="\t", quoting=csv.QUOTE_MINIMAL)
        w.writerow(COLS)
        for k, src, dst, kind, loc, label, n in rows:
            st = "已译" if (dst and dst != src) else ("同原文" if dst else "无译文")
            w.writerow([k, src, dst, st, kind, loc, label, n])
    print("已导出 %d 条 → %s" % (len(rows), args.out))
    print("  其中：已译 %d / 与原文相同 %d / 无译文 %d（筛掉 %d 条）"
          % (n_trans, n_same, n_none, total - len(rows)))
    print("  只改「现译」那一列，其余列不要动；保存为 UTF-8（Excel 直接另存为即可）。")
    print("  提示：文本里的 \\n 是两个字符的换行标记，请原样保留；")
    print("        例如原文的 <his/her>、%(NAME)、1d6 等标记也必须原样保留。")
    return 0


def cmd_apply(args):
    tm = TM(args.tm)
    n_upd = n_same = n_err = 0
    warn = []
    if not os.path.exists(args.file):
        raise SystemExit("找不到审校表：%s" % args.file)
    with open(args.file, "r", encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f, delimiter="\t")
        if not rd.fieldnames or "现译" not in rd.fieldnames:
            raise SystemExit("表头不对，缺少「现译」列。请用 review export 生成的表格。")
        for i, row in enumerate(rd, 2):
            key = (row.get("key") or "").strip()
            src = row.get("英文原文") or ""
            dst = row.get("现译") or ""
            if not key or not src:
                n_err += 1
                warn.append((i, "缺少 key 或「英文原文」为空"))
                continue
            if key_of(src) != key:
                n_err += 1
                warn.append((i, "「英文原文」列被改动了（此列不可修改），已跳过"))
                continue
            cur = tm.get(key)
            if cur == dst:
                n_same += 1
                continue
            # 一致性提醒（只提醒，不阻止人工润色）
            if dst.count(R.NL) != src.count(R.NL):
                warn.append((i, "换行标记 \\n 数量与原文不同（原文 %d / 译文 %d）"
                             % (src.count(R.NL), dst.count(R.NL))))
            miss = set(R.TOKEN_RE.findall(src)) - set(R.TOKEN_RE.findall(dst))
            if miss:
                warn.append((i, "丢失标记：%s" % ", ".join(sorted(miss))[:60]))
            if not args.dry_run:
                tm.mark_manual(key, dst)     # 标为人工修订，机器永不覆盖
            n_upd += 1
    if not args.dry_run:
        tm.commit()
    print("%s：将更新 %d 条 / 未变 %d 条 / 格式错误跳过 %d 条"
          % ("[dry-run] " if args.dry_run else "回导完成", n_upd, n_same, n_err))
    if warn:
        print("\n提醒 %d 条（不影响写入，但建议复查）：" % len(warn))
        for i, m in warn[:15]:
            print("   第 %d 行：%s" % (i, m))
        if len(warn) > 15:
            print("   …… 共 %d 条" % len(warn))
    if not args.dry_run and n_upd:
        print("\n下一步：重新打包（0 次 API 调用）")
        print("   python -m modl10n.pipeline --mod \"<目标.mod>\" --outdir \"<产物目录>\" --yes")
    return 0


def cmd_retranslate(args):
    """把指定条目标为 fallback，下次跑 pipeline 会重新调用模型翻译（会花少量钱）。"""
    tm = TM(args.tm)
    keys = []
    with open(args.file, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            k = (row.get("key") or "").strip()
            src = row.get("英文原文") or ""
            if k and src and key_of(src) == k:
                keys.append((k, src))
    if args.filter:
        keys = [x for x in keys if args.filter in x[1]]
    n = 0
    for k, s in keys:
        if not args.dry_run:
            tm.put(k, s, tm.get(k) or s, status="fallback", overwrite=True)
        n += 1
    if not args.dry_run:
        tm.commit()
    print("%s：已把 %d 条标为待重译（status=fallback）。下次跑 pipeline 会自动重翻这些句子。"
          % ("[dry-run] " if args.dry_run else "完成", n))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="modl10n.review",
                                 description="润色流程：导出审校表 → 人工改 → 回导记忆库 → 重新打包")
    ap.add_argument("--tm", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                 "tm.sqlite"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("export", help="导出审校表(TSV)")
    p1.add_argument("--mod", default=None, help="只导出这个 .mod 需要的条目；不填=导出整个记忆库")
    p1.add_argument("--out", required=True)
    p1.add_argument("--filter", default=None, help="只导出原文或译文含该关键词的行")
    p1.add_argument("--kind", choices=["gff", "nss"], default=None)
    p1.add_argument("--fname", default=None, help="只导出资源名含该字符串的行，如 .dlg")
    p1.add_argument("--min-len", type=int, default=0, dest="min_len")
    p1.add_argument("--limit", type=int, default=0)
    p1.add_argument("--offset", type=int, default=0)
    p1.add_argument("--no-nss", action="store_true")

    p2 = sub.add_parser("apply", help="把改过的审校表回导翻译记忆")
    p2.add_argument("--file", required=True)
    p2.add_argument("--dry-run", action="store_true")

    p3 = sub.add_parser("retranslate", help="把某些条目交给模型重译（会花钱）")
    p3.add_argument("--file", required=True)
    p3.add_argument("--filter", default=None)
    p3.add_argument("--dry-run", action="store_true")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return {"export": cmd_export, "apply": cmd_apply, "retranslate": cmd_retranslate}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
