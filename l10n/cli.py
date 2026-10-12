#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一 CLI —— 汉化工作台的所有批量操作都从这里进。

    python -m l10n scan    --project swordflight --mod <文件>      # 只登记资源，不解析文本
    python -m l10n extract --project swordflight --mod <文件>      # 抽取可译单元并落库
    python -m l10n diff    --project swordflight                   # 还要翻哪些（只读，0 花费）
    python -m l10n plan    --project swordflight --price-in 2 --price-out 8   # 花费预估
    python -m l10n terms   --action import-legacy --dry-run        # 术语收编（先干跑）
    python -m l10n terms   --action export --out <文件>            # 导出术语表
    python -m l10n terms   --action fuzzy --query sword            # 模糊搜术语
    python -m l10n terms   --action check --src X --dst Y          # 守必校验（单条）
    python -m l10n status  [--project swordflight]                 # 看库里的进度

后续 Sprint 会加：tlk（槽位账本）/ translate / apply / pack
"""
import argparse
import os
import sys

from .db import connect, DEF_DB
from .repo import (ContainerRepo, ResourceRepo, RunRepo, TermRepo, TlkRepo,
                   TmRepo, UnitRepo)
from .tlkio import en_ids_from_export
from .services import diff as do_diff
from .services import extract as do_extract
from .services import plan as do_plan
from .services import scan as do_scan
from .services.diff_service import summarize as summarize_diff
from .services.plan_service import summarize as summarize_plan

# 术语资产的默认路径（收编用）
SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEF_GLOSSARY = os.path.join(SCRIPTS_DIR, "modl10n", "glossary.json")
DEF_OVERRIDES = os.path.join(SCRIPTS_DIR, "glossary_overrides.json")
DEF_DRAFT = os.path.join(SCRIPTS_DIR, "dnd3r_术语库_草案.json")
DEF_VARIANTS = os.path.join(SCRIPTS_DIR, "modl10n", "term_variants.json")


def cmd_scan(args, db):
    print("=" * 74)
    print("scan：%s" % args.mod)
    rep = do_scan(db, args.project, args.mod, role=args.role, fmt=args.fmt,
                  resource_hash=not args.no_hash)
    print("  ✅ 容器 #%d：%d 个资源已登记" % (rep["container_id"], rep["resources"]))
    return 0


def cmd_extract(args, db):
    print("=" * 74)
    print("extract：%s" % args.mod)
    rep = do_extract(db, args.project, args.mod, include_nss=not args.no_nss,
                     var_assign=not args.no_var_assign, decode=args.decode)
    print("  ✅ 单元 %d 个（去重 %d 条文本）已落库" % (rep["units"], rep["unique"]))
    return 0


def cmd_diff(args, db):
    print("=" * 74)
    print("diff：还要翻哪些（只读，不写 tm、不调 API）")
    d = do_diff(db, project=args.project, container_id=args.container_id,
                sample=args.sample, include_gone=args.include_gone)
    print(summarize_diff(d))
    if d["samples"]:
        print("  待译样例：")
        for s in d["samples"]:
            print("     [%s] %s:%s  %r" % (s["cat"], s["resref"], s["field_path"],
                                          (s["src_text"] or "")[:56]))
    top = d["by_res"][:8]
    if top:
        print("  待译最多的资源：")
        for (resref, ext), n in top:
            print("     %-28s .%-4s %d 条" % (resref, ext, n))
    runs = RunRepo(db)
    rid = runs.start(args.project, "diff", {"container_id": args.container_id})
    runs.finish(rid, units=d["units_all"], uniq=d["unique"],
                reused=d["uniq_translated"], new_tr=d["uniq_todo"],
                raw_fallback_hit=d["raw_fallback_hit"])
    return 0


def cmd_plan(args, db):
    print("=" * 74)
    print("plan：这次要花多少钱、会动哪些（只读，不调 API）")
    p = do_plan(db, project=args.project, container_id=args.container_id,
                price_in=args.price_in, price_out=args.price_out, sample=args.sample)
    print(summarize_plan(p))
    print(summarize_diff(p["diff"]))
    runs = RunRepo(db)
    rid = runs.start(args.project, "plan", {"price_in": args.price_in,
                                            "price_out": args.price_out})
    runs.finish(rid, units=p["diff"]["units_all"], uniq=p["diff"]["unique"],
                reused=p["diff"]["uniq_translated"], new_tr=p["items"],
                cost_est=p["cost"])
    return 0


def cmd_terms(args, db):
    """术语库：收编 / 导出 / 搜索 / 守必校验。"""
    tr = TermRepo(db)
    act = args.action
    print("=" * 74)

    if act == "import-legacy":
        print("术语收编：四份 JSON -> term / term_variant 表%s"
              % ("（dry-run，不写库）" if args.dry_run else ""))
        rep = tr.import_legacy(args.glossary, draft_path=args.draft,
                               variants_path=args.variants, overrides_path=args.overrides,
                               reset=args.reset, dry_run=args.dry_run)
        print("  生效术语 %d 条 · 候选译名 %d 条 · 替换规则 %d 条"
              % (rep["active"], rep["candidate"], rep["variants"]))
        chk = rep["override_check"] or {}
        if chk:
            print("  覆盖层校验：base=%s patched=%s added=%s total=%s"
                  % (chk.get("base"), chk.get("patched"), chk.get("added"),
                     chk.get("total")))
            if chk.get("dangling"):
                print("  ⚠ 悬空覆盖（覆盖层里有、术语表里没有）：%s" % chk["dangling"])
            else:
                print("  ✓ 覆盖层已全部体现在 glossary.json 里（无悬空覆盖）")
        if not args.dry_run:
            print("  落库后统计：%s" % tr.count_by_status())
        return 0

    if act == "export":
        out = args.out or args.glossary
        n = tr.export_json(out)
        print("已导出 %d 条 -> %s（%d 字节）" % (n, out, os.path.getsize(out)))
        print("  （与 make_glossary.py 用同一种写法：ensure_ascii=False / indent=2 / 文本模式）")
        return 0

    if act == "stats":
        print("术语总数 %d 条：%s" % (tr.count(), tr.count_by_status()))
        print("  必守（active）%d 条；替换规则 %d 条"
              % (tr.count_required(), len(tr.variants())))
        for r in db.execute("SELECT type, COUNT(*) FROM term WHERE status='active' "
                            "GROUP BY type ORDER BY 2 DESC"):
            print("    %-8s %d" % (r[0], r[1]))
        return 0

    if act == "fuzzy":
        if not args.query:
            raise SystemExit("需要 --query")
        print("模糊匹配：%s" % args.query)
        hits = tr.fuzzy(args.query, limit=args.limit,
                        only_required=args.only_required)
        if not hits:
            print("  （无匹配）")
        for t, score, why in hits:
            print("  %.3f  %-36s = %-18s [%s/%s] %s"
                  % (score, t.en, t.zh, t.type,
                     "必守" if t.required else "建议", why))
        return 0

    if act == "check":
        if not args.src or args.dst is None:
            raise SystemExit("需要 --src 与 --dst")
        bad = tr.check_required(args.src, args.dst)
        print("守必校验")
        print("  原文：%s" % args.src)
        print("  译文：%s" % args.dst)
        print("  命中术语：%s" % (tr.hits(args.src) or "（无）"))
        if bad:
            print("  ✗ 未遵守的必守术语 %d 个：" % len(bad))
            for en, zh in bad:
                print("     %s = %s" % (en, zh))
        else:
            print("  ✓ 命中的必守术语都已使用规范译名")
        return 0

    if act == "index-terms":
        # ★ 2026-10-10 补上：`unit_term` 一直是**空的孤儿表**（表与视图都建了、从没填充过 ✗），
        #   导致 `validate` 永远报 0 —— 这就是"术语违规检查"名存实亡的原因 ✓
        from .repo import TmRepo, UnitRepo
        from .services import term_service
        print("重建 unit_term（必守术语命中缓存）…")
        rep = term_service.rebuild_unit_terms(UnitRepo(db), TmRepo(db), tr)
        print("  必守术语 %d 条 · 命中源文 %d 条 · 写入 unit_term %d 行"
              % (rep["terms"], rep["texts"], rep["rows"]))
        return 0

    if act == "validate":
        from .repo import TmRepo, UnitRepo
        from .services import term_service
        rows = term_service.violations(UnitRepo(db), tr, TmRepo(db),
                                       project=getattr(args, "project", None))
        print("术语违规检查（命中必守术语、但译文没用规范译名）：全库 %d 处" % len(rows))
        if not rows:
            print("  （若一直为 0，先跑一次：python -m l10n terms --action index-terms）")
        from collections import Counter
        by_en = Counter(r[0] for r in rows)
        print("  按术语分布（前 20）：%s"
              % "、".join("%s×%d" % kv for kv in by_en.most_common(20)))
        print("  明细（前 %d）：" % min(args.limit, len(rows)))
        for en, zh, dst, _uid, proj, resref in rows[:args.limit]:
            print("    %-34s 应有「%s」｜%s:%s" % (en[:34], zh, proj, resref))
            print("        现：%s" % (dst or "").replace("\n", " ")[:72])
        return 0

    raise SystemExit("未知 action：%s" % act)


def cmd_tlk(args, db):
    """TLK 槽位账本：认领 / 查看 / 分配 / 基线 / 校验。"""
    tr = TlkRepo(db)
    act = args.action
    print("=" * 74)

    if act == "claim":
        if not args.tlk or args.start is None or args.end is None:
            raise SystemExit("认领需要 --tlk --start --end")
        en_ids = en_ids_from_export(args.en_txt) if args.en_txt else None
        rep = tr.claim_range(args.tlk, args.start, args.end, args.owner or "prc8",
                             purpose=args.purpose, note=args.note,
                             en_ids=en_ids, dry_run=args.dry_run)
        print("认领 %s 的 [%d, %d]" % (os.path.basename(args.tlk), args.start, args.end))
        print("  区间内有文本的槽位 %d 个；区间内空槽位 %d 个"
              % (rep["with_text"], len(rep["empty_in_range"])))
        if en_ids is not None:
            print("  交叉验证（与英文导出对比）：落在英文导出里的 %d 个 %s"
                  % (len(rep["in_en_export"]), rep["in_en_export"][:8]))
        print("  %s" % ("（dry-run，未写库）" if args.dry_run
                        else "已写入账本 %d 条" % rep["claimed"]))
        return 0

    if act == "list":
        slots = tr.slots(args.tlk, owner=args.owner, purpose=args.purpose)
        print("账本：%d 条" % len(slots))
        for o, p, n, prot in tr.by_owner(args.tlk):
            print("  %-14s %-14s %5d 条（受保护 %d）" % (o, p, n, prot))
        for s in slots[:args.limit]:
            print("   %6d  %-12s %-12s %s %s"
                  % (s["strref"], s["owner"], s["purpose"],
                     "保护" if s["protected"] else "    ", (s["note"] or "")[:28]))
        return 0

    if act == "allocate":
        got = tr.allocate(args.tlk, args.owner or "工作台", count=args.count,
                          purpose=args.purpose, start=args.start)
        print("已分配槽位：%s" % got)
        return 0

    if act == "baseline":
        rep = tr.baseline(args.tlk, note=args.note)
        print("TLK 版本基线（run #%d）" % rep["run_id"])
        print("  槽位总数 %d；有文本 %d；文件 %d 字节"
              % (rep["count"], rep["with_text"], rep["bytes"]))
        print("  文件 sha256  %s…" % rep["sha256"][:24])
        print("  内容指纹     %s…" % rep["fingerprint"][:24])
        print("  账本 %d 条（受保护 %d）" % (rep["ledger_slots"], rep["protected_slots"]))
        return 0

    if act == "verify":
        en_ids = en_ids_from_export(args.en_txt) if args.en_txt else None
        r = tr.verify(args.tlk, en_ids=en_ids)
        print("账本校验：%s" % os.path.basename(args.tlk))
        print("  账本 %d 条；问题 %d 个" % (r["ledger"], len(r["problems"])))
        for p in r["problems"][:10]:
            print("     ✗ #%s %s" % (p["strref"], p["why"]))
        if en_ids is not None:
            print("  启发式（英文导出里没有的）：%d 个" % r["heuristic_count"])
            print("  账本多认领 %d 个 / 启发式多认领 %d 个"
                  % (len(r["ledger_only"]), len(r["heuristic_only"])))
            if r["heuristic_only"]:
                print("     ⚠ 账本漏认领：%s" % r["heuristic_only"][:10])
            if r["ledger_only"]:
                print("     账本多出（启发式没发现的）：%s" % r["ledger_only"][:10])
        good = not r["problems"] and not r["heuristic_only"]
        print("  %s" % ("✅ 账本与 TLK 一致" if good else "✗ 存在不一致"))
        return 0 if good else 1

    if act == "guard":
        r = tr.ledger_guard(args.tlk, ledger_tlk_path=args.ledger_tlk)
        print("账本守门（fail-closed）：%s" % os.path.basename(args.tlk))
        print("  账本要求保护 %d 个槽位" % r["expected"])
        if r["missing"]:
            print("  ✗ 缺失 %d 个：%s" % (len(r["missing"]), r["missing"][:10]))
        if r["changed"]:
            print("  ✗ 内容不符 %d 个：%s" % (len(r["changed"]), r["changed"][:10]))
        print("  %s" % ("✅ 受保护槽位齐全 → 可以安装"
                        if r["ok"] else "⛔ 拒绝安装（受保护槽位不齐，宁可不装）"))
        return 0 if r["ok"] else 1

    raise SystemExit("未知 action：%s" % act)


def cmd_status(args, db):
    crepo, rrepo, urepo, tm = (ContainerRepo(db), ResourceRepo(db),
                               UnitRepo(db), TmRepo(db))
    print("=" * 74)
    print("中央库：%s（%.1f MB）" % (os.path.abspath(args.db),
                                    os.path.getsize(os.path.abspath(args.db)) / 1048576.0))
    print("  容器 %d 个；资源 %d 个；单元 %d 个" %
          (crepo.count(), rrepo.count(), urepo.count(args.project)))
    print("  翻译记忆 %d 条（%s）" % (tm.count(), tm.stats()))
    if args.project:
        print("\n  项目 %s：" % args.project)
        st = urepo.stats(args.project)
        print("    单元状态：%s" % (st or "（还没有单元）"))
        for c in crepo.list(args.project):
            n = rrepo.count(c["id"])
            print("    #%d %-6s %-60s 资源 %d" % (c["id"], c["role"],
                                                  os.path.basename(c["path"]), n))
        top = rrepo.by_ext(crepo.list(args.project)[0]["id"]) if crepo.list(args.project) else []
        if top:
            print("    资源类型：%s" % ", ".join("%s×%d" % (e, n) for e, n in top[:8]))
    return 0


def cmd_pack(args, db):
    """**重新打包**：把记忆库里的译文写回 `.mod`（复用 `pack_service.repack`，**0 次 API** ✓）。

    与 `mod tool.py` 的「重新打包」按钮同源 ✓（原子写 + 覆盖前自动备份 + 读回校验）。
    """
    from .services import pack_service
    print("=" * 74)
    print("pack：%s" % args.mod)
    r = pack_service.repack(args.mod, outdir=args.outdir, out=args.out,
                            inplace=args.inplace, db=db)
    print("  ✅ 产物：%s" % r.get("out"))
    print("     读回校验：%s（比对 %s 条）" % (r.get("verified"), r.get("compared")))
    runs = RunRepo(db)
    rid = runs.start(args.project or "default", "pack", {"mod": args.mod})
    runs.finish(rid, units=r.get("compared"), new_tr=0)
    return 0


def cmd_translate(args, db):
    """翻译 —— **默认只估价**（不调 API ✓）；加 `--run --txt <txt> --key <key> --yes` 才真跑（会花钱 ✗）。

    引擎复用 `tlk tool.py`（`translate_service` 用独立子进程调它 ✓ 不重写引擎）。
    """
    from .services import translate_service
    print("=" * 74)
    if not args.run:
        print("translate（估价，不调 API）：%s" % (args.project or args.container_id or "-"))
        p = translate_service.estimate(db, project=args.project,
                                       container_id=args.container_id,
                                       price_in=args.price_in, price_out=args.price_out,
                                       sample=args.sample)
        print("  " + p["summary"])
        print(summarize_diff(p["diff"]))
        print("  （估价完成：**没有调用任何 API** ✓）")
        print("  要真跑：l10n translate --run --txt <导出txt> --key sk-xxx --tm <记忆库> --yes")
        return 0
    print("translate（真跑，**会花钱**）：%s" % args.txt)
    r = translate_service.run(args.txt, key=args.key, tm_path=args.tm,
                              do_all=not args.only_new, model=args.model, base=args.base,
                              confirm=args.yes, progress=lambda s: print("  " + s))
    if not r["ok"]:
        print("  ✗ %s" % r["error"])
        return 2
    print("  ✅ 引擎跑完（返回码 %s）" % r["returncode"])
    tail = (r["output"] or "").strip().splitlines()[-25:]
    for ln in tail:
        print("     " + ln)
    translate_service.record_cost(db, args.project, items=0,
                                  note=os.path.basename(args.txt or ""))
    print("  （已记一笔到花费账本 ✓；跑完别忘了 `l10n pack` 打包、"
          "以及用术语违规检查复核 ✓）")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="l10n", description="无冬之夜汉化工作台")
    ap.add_argument("--db", default=DEF_DB, help="中央库路径（默认 modl10n\\tm.sqlite）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("scan", help="登记容器的资源清单（不解析文本）")
    p.add_argument("--project", required=True)
    p.add_argument("--mod", required=True, help=".mod / .tlk 文件路径")
    p.add_argument("--role", default="source", choices=["source", "output", "installed"])
    p.add_argument("--fmt", default=None, choices=["mod", "tlk", "hak", "erf"])
    p.add_argument("--no-hash", action="store_true", help="不计算资源 sha256（更快）")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("extract", help="抽取可译单元并落 unit 表")
    p.add_argument("--project", required=True)
    p.add_argument("--mod", required=True)
    p.add_argument("--no-nss", action="store_true", help="不抽 .nss 里的文本")
    p.add_argument("--no-var-assign", action="store_true", help="不抽字符串变量赋值")
    p.add_argument("--decode", default="replace", choices=["replace", "cp1252"],
                   help="TLK 解码方式：replace=与现有 tm 命中率最优（默认）；"
                        "cp1252=文本更干净但会制造 15 条不一致（详见方案 10.1）")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("diff", help="算出还要翻哪些（只读）")
    p.add_argument("--project", default=None)
    p.add_argument("--container-id", type=int, default=None)
    p.add_argument("--sample", type=int, default=8)
    p.add_argument("--include-gone", action="store_true")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("plan", help="花钱前的影响面预览（只读，不调 API）")
    p.add_argument("--project", default=None)
    p.add_argument("--container-id", type=int, default=None)
    p.add_argument("--price-in", type=float, default=0.0, help="输入单价（元/百万 token）")
    p.add_argument("--price-out", type=float, default=0.0, help="输出单价（元/百万 token）")
    p.add_argument("--sample", type=int, default=6)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("terms", help="术语库：收编 / 导出 / 搜索 / 守必校验")
    p.add_argument("--action", required=True,
                   choices=["import-legacy", "export", "stats", "fuzzy", "check",
                            "validate", "index-terms"])
    p.add_argument("--project", default=None, help="validate 时按项目过滤")
    p.add_argument("--glossary", default=DEF_GLOSSARY)
    p.add_argument("--overrides", default=DEF_OVERRIDES)
    p.add_argument("--draft", default=DEF_DRAFT)
    p.add_argument("--variants", default=DEF_VARIANTS)
    p.add_argument("--out", default=None, help="导出目标（默认覆盖 glossary.json）")
    p.add_argument("--query", default=None)
    p.add_argument("--src", default=None)
    p.add_argument("--dst", default=None)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--only-required", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--reset", action="store_true", help="导入前清空 term / term_variant")
    p.set_defaults(func=cmd_terms)

    p = sub.add_parser("tlk", help="TLK 槽位账本：认领 / 查看 / 分配 / 基线 / 校验")
    p.add_argument("--action", required=True,
                   choices=["claim", "list", "allocate", "baseline", "verify", "guard"])
    p.add_argument("--tlk", required=True, help="TLK 文件路径")
    p.add_argument("--ledger-tlk", default=None,
                   help="账本所属的 TLK（guard 时用：新产物还没入账本，就按源 TLK 的账本校验）")
    p.add_argument("--start", type=int, default=None)
    p.add_argument("--end", type=int, default=None)
    p.add_argument("--owner", default=None)
    p.add_argument("--purpose", default="engine_text",
                   help="engine_text（界面标签）| routed（内嵌改道）| reserved")
    p.add_argument("--note", default=None)
    p.add_argument("--en-txt", default=None, help="英文导出 txt（用于交叉验证认领是否完整）")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_tlk)

    p = sub.add_parser("status", help="查看库里的进度")
    p.add_argument("--project", default=None)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("pack", help="重新打包：把记忆库译文写回 .mod（0 次 API）")
    p.add_argument("--mod", required=True, help=".mod 文件路径")
    p.add_argument("--outdir", default=None, help="产物目录（默认 <模组目录>\\汉化产物）")
    p.add_argument("--out", default=None, help="产物文件名")
    p.add_argument("--inplace", action="store_true", help="覆盖原件（自动备份）")
    p.add_argument("--project", default=None)
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("translate", help="翻译：默认只估价；加 --run 才真跑（会花钱 ✗）")
    p.add_argument("--project", default=None)
    p.add_argument("--container-id", type=int, default=None)
    p.add_argument("--price-in", type=float, default=0.0, help="输入单价（元/百万 token）")
    p.add_argument("--price-out", type=float, default=0.0, help="输出单价（元/百万 token）")
    p.add_argument("--sample", type=int, default=6)
    p.add_argument("--estimate-only", action="store_true", help="（默认行为，保留兼容 ✓）")
    p.add_argument("--run", action="store_true", help="★ 真跑翻译（会调用 API、会花钱 ✗）")
    p.add_argument("--txt", default=None, help="导出的 txt（--run 时必填）")
    p.add_argument("--key", default=None, help="API key")
    p.add_argument("--tm", default=None, help="记忆库路径（默认 modl10n\\tm.sqlite）")
    p.add_argument("--model", default=None)
    p.add_argument("--base", default=None)
    p.add_argument("--only-new", action="store_true", help="只翻未翻译的（默认全量 --all）")
    p.add_argument("--yes", action="store_true", help="★ 确认花钱（不加会被拒绝 ✗）")
    p.set_defaults(func=cmd_translate)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 0
    db = connect(args.db)
    return args.func(args, db)


if __name__ == "__main__":
    sys.exit(main())
