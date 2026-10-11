#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量汉化多个 .mod：共用一个翻译记忆 + 一张术语表，顺序处理，逐章产出报告。

例：
    # 先干跑自检（不花 API 钱）
    python -m modl10n.batch --mods "..\\Swordflight - Chapter Two.mod" --dry-run

    # 冒烟 50 条
    python -m modl10n.batch --mods "Ch2.mod" --limit 50

    # 全量跑四章（顺序执行，越往后复用率越高）
    python -m modl10n.batch --mods "Ch2.mod" "Ch3.mod" "Ch4.mod" "Ch5.mod" --workers 5

设计要点：
  * 共用 --tm（默认 modl10n/tm.sqlite），同一句英文只翻一次；
  * 每章独立打包 + 独立读回校验，一章失败不影响其他章；
  * 自动跳过 .BAK/.bak/备份/已汉化(_zh) 文件。
"""
import os
import sys
import json
import glob
import time
import argparse

from . import pipeline as P
from .tm import TM
from .glossary import Glossary

SKIP_PAT = (".bak", ".prc.", "_zh", "备份", "backup")


def expand(paths):
    out = []
    for p in paths:
        if any(ch in p for ch in "*?"):
            out.extend(sorted(glob.glob(p)))
        else:
            out.append(p)
    seen, res = set(), []
    for p in out:
        ap = os.path.abspath(p)
        low = os.path.basename(ap).lower()
        if ap in seen:
            continue
        seen.add(ap)
        if not ap.lower().endswith(".mod"):
            print("  跳过（不是 .mod）：%s" % os.path.basename(ap))
            continue
        if any(s in low for s in SKIP_PAT):
            print("  跳过（备份/已汉化）：%s" % os.path.basename(ap))
            continue
        if not os.path.exists(ap):
            print("  跳过（不存在）：%s" % ap)
            continue
        res.append(ap)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(prog="modl10n.batch", description="批量汉化多个 NWN:EE .mod")
    ap.add_argument("--mods", nargs="+", required=True, help=".mod 路径，支持通配符")
    ap.add_argument("--tm", default=P.DEF_TM)
    ap.add_argument("--glossary", default=P.DEF_GLOSSARY)
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0, help="每个 mod 只翻 N 条（冒烟）")
    ap.add_argument("--punct", default=os.environ.get("SF_PUNCT", "full"), choices=["full", "half"])
    ap.add_argument("--sf01-dir", default=None, help="路线 A：每个 mod 对应的 SF01 文本所在目录")
    ap.add_argument("--extract-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-ncs", action="store_true")
    ap.add_argument("--no-nss", action="store_true")
    ap.add_argument("--no-var-assign", action="store_true")
    ap.add_argument("--nwnsc", default=None)
    ap.add_argument("--install-dir", default=None)
    ap.add_argument("--extra-args", default="")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)

    mods = expand(args.mods)
    if not mods:
        raise SystemExit("没有可处理的 .mod")
    if args.outdir is None:
        args.outdir = os.path.join(os.path.dirname(os.path.abspath(mods[0])), "汉化产物")
    os.makedirs(args.outdir, exist_ok=True)

    tm = TM(args.tm)
    gl = Glossary.load(args.glossary)
    from .translator import Translator, read_api_key
    key = read_api_key(args.api_key)
    tr = None
    if key and not args.extract_only and not args.dry_run:
        tr = Translator(key, glossary=gl, punct=args.punct)
    print("=" * 78)
    print("待处理 %d 个 .mod：" % len(mods))
    for m in mods:
        print("   - %s" % os.path.basename(m))
    print("翻译记忆：%s（%d 条 %s）" % (args.tm, tm.count(), tm.stats()))
    print("术语表：%s（%d 条）" % (args.glossary, len(gl)))
    if not args.yes and not (args.extract_only or args.dry_run) and args.limit == 0:
        try:
            input("\n按回车开始（Ctrl+C 取消）...")
        except EOFError:
            pass

    t0 = time.time()
    reports = []
    for i, m in enumerate(mods, 1):
        sub = argparse.Namespace(**vars(args))
        if args.sf01_dir:
            cand = os.path.join(args.sf01_dir, os.path.basename(m).rsplit(".", 1)[0] + ".txt")
            sub.sf01 = cand if os.path.exists(cand) else None
            if sub.sf01 is None:
                print("  [提示] %s 未找到对应 SF01，自动改用自研抽取" % os.path.basename(m))
        else:
            sub.sf01 = None
        try:
            rep = P.process(m, sub, tm=tm, glossary=gl, translator=tr)
        except Exception as e:
            print("  [失败] %s：%r" % (os.path.basename(m), e))
            rep = {"name": os.path.basename(m), "error": repr(e)}
        reports.append(rep)
        print("  (第 %d/%d 个完成，累计耗时 %.1f 分钟)" % (i, len(mods), (time.time() - t0) / 60))

    print("\n" + "=" * 78)
    print("汇总")
    for r in reports:
        if r.get("error"):
            print("  %-28s 失败: %s" % (r.get("name"), r["error"][:60]))
        else:
            print("  %-28s 单元 %-6s 去重 %-6s 复用 %-6s 新译 %-6s 校验不一致 %s"
                  % (r.get("name"), r.get("units", "-"), r.get("uniq", "-"),
                     r.get("reused", "-"), r.get("new", "-"),
                     len(r.get("verify_bad", [])) if "verify_bad" in r else "-"))
    with open(os.path.join(args.outdir, "batch_report.json"), "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)
    print("\n汇总报告：%s" % os.path.join(args.outdir, "batch_report.json"))
    print("翻译记忆现有 %d 条 %s" % (tm.count(), tm.stats()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
