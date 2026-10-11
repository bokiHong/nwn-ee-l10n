#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""术语表维护 CLI。

  # 1) 从翻译记忆里挖候选（每翻完一章都跑一次，把新出现的人名/地名补进术语表）
  python -m modl10n.terms mine --tm modl10n/tm.sqlite --out term_candidates.csv

  # 2) 体检：术语表里每个词在记忆库里的实际译法是否统一，列出漂移
  python -m modl10n.terms audit --tm modl10n/tm.sqlite --glossary modl10n/glossary.json

  # 3) 用术语表体检某个 .mod（不翻译，只看文本命中情况）
  python -m modl10n.terms scan --glossary modl10n/glossary.json --mod "X.mod"
"""
import os
import re
import sys
import csv
import json
import argparse
import collections

from .tm import TM
from .glossary import Glossary

ZH_RUN = re.compile(r"[\u4e00-\u9fff·]{2,6}")


def cmd_mine(args):
    tm = TM(args.tm)
    rows = [(s, d) for _k, s, d, st in tm.iter_all() if s and st != "rejected" and s != d]
    cands = Glossary.build_candidates(rows)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["en", "zh", "type_guess", "note"])
        for c in cands:
            zh = c["zh"]
            kind = "name" if 0 else "?"
            w.writerow([c["en"], zh, "", ""])
    print("从 %d 条记忆里挖出 %d 个候选 -> %s" % (len(rows), len(cands), args.out))
    print("提示：候选里混有物品/生物名，只把【人名/地名/神祇/法术名】填进 glossary.json。")
    return 0


def cmd_audit(args):
    tm = TM(args.tm)
    gl = Glossary.load(args.glossary)
    rows = [(s, d) for _k, s, d, st in tm.iter_all() if s and st != "rejected" and s != d]
    hard = gl.required_terms
    soft = [t for t in gl.terms if not t.get("required", True)]
    print("术语表 %d 条（必守 %d / 建议 %d）；记忆库对照 %d 条\n"
          % (len(gl), len(hard), len(soft), len(rows)))

    def scan(terms):
        out = []
        for t in terms:
            rx = re.compile(r"(?<![A-Za-z])" + re.escape(t["en"]) + r"(?![A-Za-z])")
            hits = [(s, d) for s, d in rows if rx.search(s)]
            if not hits:
                continue
            bad = [(s, d) for s, d in hits if t["zh"] not in d]
            if bad:
                out.append((t, len(hits), bad))
        return out

    drift = 0
    print("=== 【必守】未使用规范译名（需要修）===")
    hard_bad = scan(hard)
    if not hard_bad:
        print("   无 ✅")
    for t, n_hit, bad in hard_bad:
        drift += len(bad)
        print("⚠ %-22s -> %-10s 命中 %3d 条，其中 %3d 条未用规范译名"
              % (t["en"], t["zh"], n_hit, len(bad)))
        print("     例: %s" % bad[0][1][:90])
    print("\n=== 【建议】偏离（仅供参考，不算错；如同义词选择）===")
    soft_bad = scan(soft)
    if not soft_bad:
        print("   无")
    for t, n_hit, bad in soft_bad:
        print("· %-22s 建议=%-10s 偏离 %3d/%3d 条  例: %s"
              % (t["en"], t["zh"], len(bad), n_hit, bad[0][1][:55]))
    print("\n必守合计需回订：%d 条" % drift)
    print("提示：可直接 `python -m modl10n.terms fix` 零调用就地回订（用 term_variants.json 的替换表），")
    print("      或者 `audit --fix` 把它们标为 rejected 交给模型重译（会花钱且可能改动句子）。")
    if drift and args.fix:
        n = 0
        gl_map = {t["en"]: t["zh"] for t in gl.terms if t.get("required", True)}
        for t in gl.terms:
            if not t.get("required", True):
                continue
            rx = re.compile(r"(?<![A-Za-z])" + re.escape(t["en"]) + r"(?![A-Za-z])")
            for k, s, d, st in list(tm.iter_all()):
                if st == "rejected" or not s or s == d or not rx.search(s):
                    continue
                # 只在译文里找不到规范译名时标记为待重译（具体重译交给 pipeline --retry-*）
                if t["zh"] not in d:
                    tm.reject(k)
                    n += 1
        print("已把 %d 条漂移译文标记为 rejected，下次跑 pipeline 会自动重译。" % n)
    return 0


def cmd_fix(args):
    """零 API 调用回订：按替换表把已译内容里的译名漂移就地改掉。

    比"标记 rejected 后重译"更好的地方：
      * 0 次调用（重译要按句数花钱）
      * 只动译名，句子其余部分（语气、标点、术语外的用词）完全不变，
        不会因为重译把原本没问题的句子改坏
    安全约束：原文必须命中该术语、译文里必须完全没有规范译名、目标必须是 required 条目。
    """
    tm = TM(args.tm)
    gl = Glossary.load(args.glossary)
    with open(args.variants, "r", encoding="utf-8") as f:
        data = json.load(f)
    variants = data.get("variants", data)
    zh2en = {}
    for t in gl.required_terms:
        zh2en.setdefault(t["zh"], t["en"])

    rules = []
    for var, canon in variants.items():
        en = zh2en.get(canon)
        if not en:
            print("  [跳过] %r 的目标 %r 不在术语表 required 条目里" % (var, canon))
            continue
        rules.append((en, var, canon))
    # 长变体优先：否则 `谢卡尔` 会先把 `谢尔卡尔` 改成 `舍卡尔尔`
    rules.sort(key=lambda r: -len(r[1]))

    rows = [(k, s, d) for k, s, d, st in tm.iter_all() if s and st != "rejected" and s != d]
    print("待回订内容 %d 条；替换规则 %d 条\n" % (len(rows), len(rules)))
    # 关键：每条内容只写一次。若像早先那样"每条规则各写一次"，后面的规则会拿旧快照覆盖前面的修正
    # （实测把 谢尔卡尔->舍卡尔 的修正冲掉过）。
    rxs = {en: re.compile(r"(?<![A-Za-z])" + re.escape(en) + r"(?![A-Za-z])")
           for en, _v, _c in rules}
    per_rule = collections.Counter()
    updates = []
    preview = 0
    for k, s, d in rows:
        nd = d
        for en, var, canon in rules:
            if canon in nd or var not in nd or not rxs[en].search(s):
                continue
            nd = nd.replace(var, canon)
            per_rule[(en, var, canon)] += 1
        if nd != d:
            updates.append((k, nd))
            if args.dry_run and preview < 3:
                print("   [预览] %r -> %r" % (d[:60], nd[:60]))
                preview += 1
    for (en, var, canon), n in per_rule.most_common():
        print("   %-14s %-10s -> %-10s  %2d 处" % (en, var, canon, n))
    if args.dry_run:
        print("\n[dry-run] 可修正 %d 条内容（%d 处替换），未写入。"
              % (len(updates), sum(per_rule.values())))
    else:
        for k, nd in updates:
            tm.mark_manual(k, nd)      # 标为人工修订，机器翻译永不覆盖
        tm.commit()
        print("\n共修正 %d 条内容（%d 处替换），已标记为 manual 防止被机器覆盖。"
              % (len(updates), sum(per_rule.values())))
    return 0


def cmd_scan(args):
    from .extract import extract_units
    from .core import sfrules as R
    from .tm import key_of
    gl = Glossary.load(args.glossary)
    erf, units = extract_units(args.mod)
    seen = collections.Counter()
    for u in units:
        for en, zh in gl.hits(u.text):
            seen[(en, zh)] += 1
    print("模组 %s：%d 个可译单元" % (os.path.basename(args.mod), len(units)))
    print("术语命中：")
    for (en, zh), c in seen.most_common():
        print("   %-22s -> %-10s  %d 条" % (en, zh, c))
    uniq = {key_of(u.text) for u in units}
    print("内容去重后 %d 条" % len(uniq))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="modl10n.terms")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("mine"); p1.add_argument("--tm", required=True); p1.add_argument("--out", required=True)
    p2 = sub.add_parser("audit"); p2.add_argument("--tm", required=True)
    p2.add_argument("--glossary", required=True); p2.add_argument("--fix", action="store_true",
                                                                 help="把漂移译文标记为 rejected（下次自动重译）")
    p3 = sub.add_parser("scan"); p3.add_argument("--glossary", required=True); p3.add_argument("--mod", required=True)
    p4 = sub.add_parser("fix", help="按替换表零调用回订译名漂移")
    p4.add_argument("--tm", required=True)
    p4.add_argument("--glossary", required=True)
    p4.add_argument("--variants", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                       "term_variants.json"))
    p4.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    return {"mine": cmd_mine, "audit": cmd_audit, "scan": cmd_scan, "fix": cmd_fix}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
