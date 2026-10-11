#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""术语统一：把一个中文译名改成新译名，并让**所有已翻译资产**跟着走（0 次 API 调用）。

做四件事：
  1) **术语表**：把 `zh == 旧译名` 的条目改成新译名 —— 写进 `glossary_overrides.json`
     （`make_glossary.py` 会在生成的最后应用它，所以**重新生成也不会丢**），
     并顺手把该条目提升为 `required=true`（零调用替换工具要求目标必须是必守条目）。
  2) **替换表**：把 `旧 -> 新` 追加进 `modl10n/term_variants.json`，以后回订/新章节也能用上。
  3) **记忆库**：扫描 `tm.sqlite`，把「原文命中该英文术语 + 译文含旧译名 + 译文不含新译名」
     的条目就地替换并标 `status=manual`（机器永不覆盖）。
  4) **报告**：按 `src_mod` 统计受影响条目，并列出需要重建的产物（.mod / .tlk）。

用法：
    python -m modl10n.rename --old 剑翔 --new 剑飞 --dry-run     # 先看影响面
    python -m modl10n.rename --old 剑翔 --new 剑飞               # 执行（改术语表+替换表+记忆库）
    python -m modl10n.rename --file 改名表.tsv [--dry-run]        # 批量（每行：旧<TAB>新）
    python -m modl10n.rename --list                               # 只列当前术语表里的中英对照

约定：改名必须**先预览后执行**（见记忆 rules：改一处、动多文件）。
"""
import os
import re
import sys
import json
import argparse

HERE_PKG = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE_PKG)
sys.path.insert(0, ROOT)

from modl10n.tm import TM                     # noqa: E402

DEF_TM = os.path.join(HERE_PKG, "tm.sqlite")
DEF_GLOSSARY = os.path.join(HERE_PKG, "glossary.json")
DEF_VARIANTS = os.path.join(HERE_PKG, "term_variants.json")
DEF_OVERRIDES = os.path.join(ROOT, "glossary_overrides.json")

# 已知产物：src_mod 标签 -> (类型, 路径模板)。重建由 pipeline / tlk 工具完成。
TARGET_MAP = {
    "Chapter One": ("mod", r"modules\Swordflight - Chapter One.mod"),
    "Swordflight - Chapter One": ("mod", r"modules\Swordflight - Chapter One.mod"),
    "Swordflight - Chapter Two": ("mod", r"modules\Swordflight - Chapter Two.mod"),
    "PRC8": ("tlk", r"tlk\prc8_consortium.tlk"),
}


def load_glossary(path=DEF_GLOSSARY):
    return json.load(open(path, encoding="utf-8"))["terms"]


def entries_by_zh(terms, zh):
    """术语表里 zh 完全等于给定中文的条目（可能不止一个：不同英文名共用同一译名）。"""
    return [t for t in terms if t["zh"] == zh]


def rows_containing(tm, old, new, en=None, require_absent=True, include_empty_src=False):
    """返回 [(rowid, src, dst, src_mod)]：译文含 old；给了 en 还要求原文命中该英文词。

    require_absent：是否要求"译文里不含新名"才改。
      * **改名场景要传 False** —— 同一行里可能既有旧名又有新名（实测：剑飞第一章的模组描述
        里"剑翔"和别处的"剑飞"同时存在 ✗），按"整行不含新名"过滤会把该改的行漏掉。
    include_empty_src：是否连"原文为空"的条目一起改。老数据里有一批这样的行（早期路线 A
      从 SF01.txt 导入时源语言槽是空的，实测 Chapter One 有 64 条 ✗），剑飞第一章的
      **模组描述**就存在这种行里 —— 不打开这个开关，英文护栏永远匹配不上它。
    """
    rx = re.compile(r"(?<![A-Za-z])" + re.escape(en) + r"(?![A-Za-z])", re.I) if en else None
    out = []
    for rid, src, dst, mod in tm.db.execute(
            "SELECT rowid, src, dst, src_mod FROM tm WHERE status!='rejected'").fetchall():
        if not dst or old not in dst:
            continue
        if require_absent and new in dst:
            continue
        if not (src or "").strip():
            if not include_empty_src:
                continue          # 原文为空：没有英文护栏，默认不碰
        elif rx is not None and not rx.search(src):
            continue
        out.append((rid, src or "", dst, mod or ""))
    return out


def rows_of_rename(tm, old, new, guard=None, include_empty_src=True):
    """改名要用到的全部行 = 有英文护栏的 + （可选）原文为空的老数据行。

    ⚠ 预览与执行必须走**同一个函数**：我曾让预览用默认参数、执行又拿预览的结果当开关，
    结果两次查询条件不一致，永远改不到那一行 ✗。
    """
    rows = rows_containing(tm, old, new, en=guard, require_absent=False,
                           include_empty_src=False)
    if include_empty_src:
        empty = [r for r in rows_containing(tm, old, new, en=None, require_absent=False,
                                            include_empty_src=True)
                 if not (r[1] or "").strip()]
        rows += empty
    return rows


def plan(renames, tm_path=DEF_TM, glossary_path=DEF_GLOSSARY, en_override=None,
         include_empty_src=True):
    """影响面预览。renames: [(old, new)]；en_override: 显式指定的英文护栏（术语表里
    的译名已经是新名、但正文里还留着旧名时用，例如 Swordflight 的 剑翔->剑飞）。"""
    terms = load_glossary(glossary_path)
    tm = TM(tm_path)
    report = []
    for old, new in renames:
        hits = entries_by_zh(terms, old)
        guard = en_override or (hits[0]["en"] if hits else None)
        if guard and not hits:
            # 术语表已是新名：只做"按英文护栏扫正文"的替换
            hits = [{"en": guard, "zh": new, "required": True,
                     "note": "术语表译名已是新名（仅扫正文）"}]
        rows = rows_of_rename(tm, old, new, guard, include_empty_src)
        empty = [r for r in rows if not (r[1] or "").strip()]
        # 没有英文护栏时退化成"全库替换"——必须明确告警
        loose = [] if guard else rows_containing(tm, old, new, en=None, require_absent=False)
        mods = {}
        for _rid, _s, _d, m in (rows + loose):
            mods[m] = mods.get(m, 0) + 1
        report.append({
            "old": old, "new": new, "guard": guard,
            "glossary_hits": [{"en": h["en"], "zh": h.get("zh", old), "required": h.get("required", True)}
                              for h in hits],
            "tm_rows": len(rows), "loose_rows": len(loose), "empty_src_rows": len(empty),
            "mods": mods,
            "targets": sorted({TARGET_MAP.get(m, ("?", "?"))[1] for m in mods if m in TARGET_MAP}),
            "unknown_mods": sorted({m for m in mods if m not in TARGET_MAP}),
        })
    return report


def apply(renames, report, tm_path=DEF_TM, glossary_path=DEF_GLOSSARY,
          variants_path=DEF_VARIANTS, overrides_path=DEF_OVERRIDES):
    """执行：写覆盖层 + 追加替换表 + 改记忆库。"""
    terms = load_glossary(glossary_path)
    by_en = {t["en"]: t for t in terms}
    # ⚠ 必须"整份读进来、只改自己管的键" ✗→✓：第一版是**从空字典重建**、只搬 zh/required，
    #    结果把术语库编辑器写进去的 `add` 段（新增术语）和 `type` 段整段丢掉 ✗✗——
    #    实测症状：界面上新增的 Swordsage 词条"隔一轮就消失" ✗（用户亲自发现）。
    ovr = {}
    if os.path.exists(overrides_path):
        try:
            ovr = json.load(open(overrides_path, encoding="utf-8"))
            if not isinstance(ovr, dict):
                ovr = {}
        except Exception as ex:
            print("   ⚠ 覆盖层读取失败（为免丢数据，中止写入）：%r" % ex)
            raise
    ovr.setdefault("zh", {})
    ovr.setdefault("required", {})
    ovr["note"] = "由 modl10n.rename / 术语库编辑器写入：译名·等级·分类·新增术语的覆盖层"

    tm = TM(tm_path)
    vars_ = json.load(open(variants_path, encoding="utf-8"))
    V = vars_.setdefault("variants", {})
    changed_total = 0
    for r, (old, new) in zip(report, renames):
        # 1) 术语表覆盖层
        if not r["glossary_hits"]:
            print("   ⚠ 「%s」在术语表里找不到对应条目：只做记忆库替换（无英文护栏），"
                  "请人工复核！" % old)
        for e in r["glossary_hits"]:
            ovr["zh"][e["en"]] = new
            ovr["required"][e["en"]] = True
            print("   [术语表] %s：保证译名为 %s（并提升为必守）" % (e["en"], new))
        # 2) 替换表
        if V.get(old) != new:
            V[old] = new
            print("   [替换表] 追加规则 %s -> %s" % (old, new))
        # 3) 记忆库
        rows = rows_of_rename(tm, old, new, r["guard"], include_empty_src=True)
        for rid, _s, dst, _m in rows:
            tm.db.execute("UPDATE tm SET dst=?, status='manual' WHERE rowid=?",
                          (dst.replace(old, new), rid))
        tm.db.commit()
        changed_total += len(rows)
        print("   [记忆库] %s -> %s：修改 %d 条（已标 manual）" % (old, new, len(rows)))

    json.dump(ovr, open(overrides_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(vars_, open(variants_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return changed_total


def show_report(report):
    print("=== 影响面预览 ===")
    for r in report:
        print("  「%s」→「%s」" % (r["old"], r["new"]))
        if r["glossary_hits"]:
            for g in r["glossary_hits"]:
                print("     术语表：%s（%s，必守=%s）" % (g["en"], g["zh"], g["required"]))
        else:
            print("     术语表：找不到 zh 等于该中文的条目 ⚠（将无英文护栏地全库替换）")
        print("     记忆库：命中 %d 条可改；无护栏的 %d 条；其中原文为空的行 %d 条"
              % (r["tm_rows"], r["loose_rows"], r.get("empty_src_rows", 0)))
        if r["mods"]:
            print("     受影响来源：%s" % ", ".join("%s×%d" % (k or "(未标)", v) for k, v in
                                              sorted(r["mods"].items(), key=lambda kv: -kv[1])))
        if r["targets"]:
            print("     需重建：%s" % "、".join(r["targets"]))
        if r["unknown_mods"]:
            print("     未知来源（需人工判断要不要重建）：%s" % "、".join(r["unknown_mods"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description="术语统一：改一个译名，波及的资产一起改（0 调用）")
    ap.add_argument("--old", help="旧译名（中文）")
    ap.add_argument("--new", help="新译名（中文）")
    ap.add_argument("--en", dest="en", help="英文护栏（术语表里已是新名、只扫正文时必填，如 Swordflight）")
    ap.add_argument("--file", help="批量改名表（TSV：旧译名<TAB>新译名）")
    ap.add_argument("--dry-run", action="store_true", help="只出影响面预览")
    ap.add_argument("--list", action="store_true", help="列出术语表当前的中英对照")
    ap.add_argument("--tm", default=DEF_TM)
    ap.add_argument("--glossary", default=DEF_GLOSSARY)
    a = ap.parse_args(argv)

    if a.list:
        terms = load_glossary(a.glossary)
        print("=== 术语表 %d 条 ===" % len(terms))
        for t in sorted(terms, key=lambda x: x["type"]):
            print("   %-6s %-30s -> %s%s" % (t["type"], t["en"], t["zh"], "（必守）" if t["required"] else ""))
        return 0

    renames = []
    if a.file:
        rows = open(a.file, encoding="utf-8-sig").read().splitlines()
        for ln in rows:
            if not ln.strip() or ln.startswith("#"):
                continue
            parts = re.split(r"\t+", ln.strip())
            if len(parts) >= 2:
                renames.append((parts[0].strip(), parts[1].strip()))
    if a.old and a.new:
        renames.append((a.old.strip(), a.new.strip()))
    if not renames:
        ap.print_help()
        return 1

    rep = plan(renames, a.tm, a.glossary, en_override=a.en)
    show_report(rep)
    if a.dry_run:
        print("\n  --dry-run：未做任何修改。确认无误后去掉该参数执行。")
        return 0
    print("\n=== 执行 ===")
    n = apply(renames, rep, a.tm, a.glossary)
    print("  记忆库共修改 %d 条" % n)
    print("\n  下一步：重建受影响的产物（0 次 API 调用），例如")
    for r in rep:
        for t in r["targets"]:
            if t.endswith(".mod"):
                print("     python -m modl10n.pipeline --mod \"%s\" --outdir .\\out_rename --yes" % t)
            else:
                print("     python -m modl10n.tlk rebuild <英文TLK> prc8.txt \"%s\"" % t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
