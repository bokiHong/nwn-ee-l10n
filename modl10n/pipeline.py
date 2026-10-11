#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单 mod 全流程：抽取 → 翻译 → 回写 → 重编译 .ncs → 打包 → 读回校验。

命令行：
    python -m modl10n.pipeline --mod "X.mod" [选项]

常用选项：
    --sf01 FILE       走路线 A（用 NWN MultiLang 导出的文本），默认路线 B（自研抽取）
    --tm FILE         翻译记忆 sqlite（默认 modl10n/tm.sqlite）
    --glossary FILE   术语表 json（默认 modl10n/glossary.json）
    --outdir DIR      产物目录（默认与 .mod 同目录）
    --workers N       并发（默认 5）
    --limit N         只翻 N 条（冒烟测试）
    --extract-only    只抽取并报告，不调 API、不写文件
    --dry-run         完整跑一遍但不写出 .mod（校验回写逻辑）
    --no-ncs          不重编译/注入 .ncs
    --yes             不交互确认
"""
import os
import shutil
import stat
import sys
import json
import time
import argparse
import collections

from .core.erf import Erf
from .core.gff import Gff, LocString, patch_locstrings
from .core import sfrules as R
from .extract import extract_units, units_from_sf01, txt_of
from .tm import TM, key_of
from .glossary import Glossary
from . import ncs as NCS

HERE = os.path.dirname(os.path.abspath(__file__))
DEF_TM = os.path.join(HERE, "tm.sqlite")
DEF_GLOSSARY = os.path.join(HERE, "glossary.json")


# ---------------------------------------------------------------- 回写

def _clear_readonly(path):
    """Windows 上只读属性会让 os.replace / 覆盖写失败，先去掉只读位。"""
    try:
        if os.path.exists(path):
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def apply_units(erf, units, trans, punct_loc="full"):
    """把译文按 locator 写回 erf 条目。返回 (统计, 失败列表, 被改动的 resref 集合)。"""
    stats = collections.Counter()
    fails = []
    changed = set()
    by_file = collections.defaultdict(list)
    for u in units:
        by_file[u.keyidx].append(u)

    for idx, us in sorted(by_file.items()):
        ent = erf.entries[idx]
        data = erf.orig(ent)          # 用原始字节做基准，避免 read_data 污染 ent.data
        ext = ent.ext.lower()
        if ext == "nss":
            try:
                s = data.decode("utf-8")
            except UnicodeDecodeError:
                s = data.decode("cp1252", "replace")
            crlf = "\r\n" in s
            lines = s.split("\r\n") if crlf else s.split("\n")
            dirty = False
            for u in us:
                t = trans.get(key_of(u.text))
                if t is None or t == u.text:
                    stats["nss_保留"] += 1
                    continue
                if not (0 <= u.lineno < len(lines)):
                    fails.append([ent.resref, "行号越界", u.lineno])
                    continue
                from .core.sf import parse_nwscript_literals
                lits = parse_nwscript_literals(lines[u.lineno])
                if u.lit_index >= len(lits):
                    fails.append([ent.resref, "字面量序号越界", u.lineno, u.lit_index])
                    continue
                s0, e0, body = lits[u.lit_index]
                if body != u.text:
                    fails.append([ent.resref, "原文不符（不覆盖）", u.lineno, body[:40]])
                    continue
                new = R.escape_nwscript(t)
                lines[u.lineno] = lines[u.lineno][:s0] + '"' + new + '"' + lines[u.lineno][e0:]
                dirty = True
                stats["nss_已替换"] += 1
            if dirty:
                ent.data = ("\r\n" if crlf else "\n").join(lines).encode("utf-8")
                changed.add(ent.resref)
                stats["nss_文件已改"] += 1
            continue

        # GFF
        try:
            g = Gff(data)
            fields = {f.index: f for f in g.locstring_fields()}
        except Exception as e:
            fails.append([ent.resref, "GFF 解析失败 %r" % e])
            continue
        edits = {}
        for u in us:
            if u.locator[0] != "gff":
                continue
            t = trans.get(key_of(u.text))
            if t is None or t == u.text:
                stats["gff_保留"] += 1
                continue
            f = fields.get(u.locator[2])
            if f is None:
                fails.append([ent.resref, "字段不存在", u.locator[2]])
                continue
            if R.escape_gff(txt_of(f.value) or "") != u.text:
                fails.append([ent.resref, "字段原文不符（不覆盖）", u.label, u.text[:40]])
                continue
            ls = edits.get(f.index)
            if ls is None:
                ls = LocString(f.value.strref, list(f.value.entries))
                edits[f.index] = ls
            ls.set_text(0, R.unescape_gff(t).encode("utf-8"))
            stats["gff_已替换"] += 1
        if edits:
            try:
                ent.data = patch_locstrings(data, edits)
                changed.add(ent.resref)
                stats["gff_文件已改"] += 1
            except Exception as e:
                fails.append([ent.resref, "回写失败 %r" % e])
    return stats, fails, changed


# ---------------------------------------------------------------- 读回校验
def verify_output(modpath, expected):
    """把产物当黑盒读回，逐条比对 expected={locator: 期望文本}。"""
    erf = Erf.load(modpath)
    got = {}
    for i, ent in enumerate(erf.entries):
        data = erf.read_data(ent)
        ext = ent.ext.lower()
        if ext == "nss":
            try:
                s = data.decode("utf-8")
            except UnicodeDecodeError:
                s = data.decode("cp1252", "replace")
            lines = s.split("\r\n") if "\r\n" in s else s.split("\n")
            from .core.sf import parse_nwscript_literals
            for ln, line in enumerate(lines):
                for li, (s0, e0, body) in enumerate(parse_nwscript_literals(line)):
                    got[("nss", i, ln, li)] = body
            continue
        try:
            if not _gff_ok(data):
                continue
            for f in Gff(data).locstring_fields():
                t = txt_of(f.value)
                if t:
                    got[("gff", i, f.index)] = t
        except Exception:
            continue
    bad = []
    for loc, want in expected.items():
        have = got.get(loc)
        if have != want:
            bad.append([str(loc), (want or "")[:50], (have or "<缺失>")[:50]])
    return erf, bad


def _gff_ok(data):
    from .extract import looks_like_gff
    return looks_like_gff(data)


def erf_struct_check(path):
    import struct as _s
    raw = open(path, "rb").read()
    from .core.erf import HDR_FMT
    h = _s.Struct(HDR_FMT).unpack_from(raw, 0)
    entrycount, ofs_loc, ofs_key, ofs_res = h[4], h[5], h[6], h[7]
    spans, bad = [], 0
    for i in range(entrycount):
        off = ofs_res + i * 8
        o, sz = _s.unpack_from("<II", raw, off)
        if o + sz > len(raw):
            bad += 1
        spans.append((o, o + sz))
    spans.sort()
    overlaps = sum(1 for i in range(1, len(spans)) if spans[i][0] < spans[i - 1][1])
    return {"entries": entrycount, "out_of_range": bad, "overlaps": overlaps,
            "bytes": len(raw)}


# ---------------------------------------------------------------- 主流程
def process(modpath, args, tm=None, glossary=None, translator=None):
    t0 = time.time()
    name = os.path.splitext(os.path.basename(modpath))[0]
    review_vars = []
    review_lines = []
    if args.sf01:
        erf, units = units_from_sf01(modpath, args.sf01, review=review_lines)
        route = "A (SF01: %s)" % os.path.basename(args.sf01)
    else:
        erf, units = extract_units(modpath, include_nss=not args.no_nss,
                                   var_assign=not args.no_var_assign, review=review_vars)
        route = "B (自研抽取)"
    uniq = {}
    for u in units:
        uniq.setdefault(key_of(u.text), u.text)
    print("=" * 78)
    print("模组: %s" % name)
    print("  抽取路线 %s" % route)
    print("  资源 %d 个；可译单元 %d 条；内容去重后 %d 条" % (len(erf.entries), len(units), len(uniq)))
    if review_vars:
        print("  其中【疑似显示文本·字符串变量赋值】%d 条（已翻译，建议抽查）：" % len(review_vars))
        for u in review_vars[:6]:
            print("      %-20s L%-4d %r" % (u.fname, u.lineno, u.text[:60]))
    if review_lines:
        print("  SF01 中无法定位到资源的条目 %d 条（已跳过）" % len(review_lines))
    if args.extract_only:
        out = os.path.join(args.outdir, name + ".units.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump([{"loc": list(u.locator), "fname": u.fname, "text": u.text,
                        "label": u.label} for u in units], f, ensure_ascii=False, indent=1)
        print("  已写出单元清单：%s" % out)
        return {"name": name, "mod": modpath, "units": len(units), "uniq": len(uniq),
                "extract_only": True}

    # ---- 翻译
    hit = tm.get_many(uniq.keys())
    # 待译 = 记忆里没有的 + 上次"回退原文"的（正确的空操作不重试，保证重复跑零花费）
    fallback = tm.fallback_keys(uniq.keys())
    all_pending = [(k, t) for k, t in uniq.items() if k not in hit or k in fallback]
    reused = len(uniq) - len(all_pending)
    pending = all_pending[:args.limit] if args.limit else all_pending
    print("  翻译记忆命中 %d 条（本模组去重后，其中待重试的回退项 %d 条）；本次待译 %d 条"
          % (reused, len(fallback), len(pending)))
    stats = {}
    if pending and not args.dry_run:
        if translator is None:
            raise SystemExit("需要 API（缺少翻译器实例）")
        n_before = tm.count()
        stats = translator.run(pending, tm, src_mod=name, workers=args.workers)
        tm.commit()
        n_after = tm.count()
        print("  翻译统计: %s（记忆 %d -> %d 条）" % (dict(stats), n_before, n_after))
        if n_after == n_before:
            print("  [严重警告] 翻译记忆没有增长，译文可能未落盘，请检查上面是否有异常！")
    elif pending:
        print("  --dry-run：跳过 API 调用")

    trans = tm.get_many(uniq.keys())
    # ---- 回写 + 重编译 .ncs + 打包
    astats, afails, changed_resrefs = apply_units(erf, units, trans, args.punct)
    print("  回写统计: %s" % dict(astats))
    if afails:
        print("  [警告] 回写失败 %d 处，前 5：" % len(afails))
        for x in afails[:5]:
            print("     ", x)

    ncs_info = {}
    if args.dry_run:
        print("  --dry-run：跳过 .ncs 重编译（本地编译很慢，干跑时没必要做）")
    elif not args.no_ncs:
        changed = sorted(changed_resrefs)
        if changed:
            workdir = os.path.join(args.outdir, "_work", name)
            orig = {e.resref: erf.orig(e) for e in erf.entries if e.ext.lower() == "ncs"}
            produced, errors, logpath, _ = NCS.recompile(
                erf, changed, workdir, nwnsc=args.nwnsc, install=args.install_dir,
                extra=args.extra_args.split() if args.extra_args else None)
            line_map = {}
            for u in units:
                if u.kind != "nss":
                    continue
                t = trans.get(key_of(u.text))
                if not t or t == u.text:
                    continue
                line_map[NCS._norm(R.unescape_sf(u.text))] = NCS._norm(R.unescape_sf(t))
                line_map[NCS._norm(u.text)] = NCS._norm(t)
            accepted, rejected = NCS.inject(
                erf, produced, line_map, lambda r: orig.get(r, b""), workdir,
                nwnsc=args.nwnsc, install=args.install_dir,
                extra=args.extra_args.split() if args.extra_args else None)
            n_inj = NCS.apply_accepted(erf, accepted)
            ncs_info = {"compiled": len(produced), "failed": len(errors),
                        "rejected": len(rejected), "injected": n_inj,
                        "log": logpath}
            print("  .ncs 重编译: 成功 %d / 失败 %d；硬校验否决 %d；注入 %d"
                  % (len(produced), len(errors), len(rejected), n_inj))
            for r in sorted(errors)[:4]:
                print("     [编译失败] %s: %s" % (r, errors[r]))

    if args.dry_run:
        print("  --dry-run：不写出 .mod")
        return {"name": name, "units": len(units), "uniq": len(uniq),
                "reused": len(uniq) - len(pending), "new": len(pending),
                "apply": dict(astats), "fails": len(afails), "ncs": ncs_info,
                "dry_run": True, "secs": round(time.time() - t0, 1)}

    # ---- 写出（原子写 + 可选覆盖原件并自动备份）
    orig_size = os.path.getsize(modpath)
    blob = erf.build()
    if args.inplace:
        outpath = os.path.abspath(modpath)
    elif args.out:
        outpath = os.path.abspath(args.out)
    else:
        outpath = os.path.join(args.outdir, name + "_zh.mod")

    if os.path.abspath(outpath) == os.path.abspath(modpath):
        # 覆盖原件前先落一份逐字节备份，并校验备份大小
        bak = modpath + ".orig-backup"
        if not os.path.exists(bak) or os.path.getsize(bak) != orig_size:
            _clear_readonly(bak)
            _clear_readonly(modpath)
            shutil.copy2(modpath, bak)
            if os.path.getsize(bak) != orig_size:
                raise SystemExit("备份校验失败，已中止（原件未被改动）：%s" % bak)
            print("  已备份原件：%s（%d 字节）" % (bak, os.path.getsize(bak)))
        else:
            print("  原件备份已存在且大小正确：%s" % bak)

    # 原子写：先写临时文件再替换。绝不"先截断再写"——
    # 那样一旦中途出错（例如第三方工具崩溃）就会留下 0 字节文件（MultiLang 就是这样坑的）。
    os.makedirs(os.path.dirname(outpath) or ".", exist_ok=True)
    tmp = outpath + ".tmp-writing"
    if os.path.exists(tmp):          # 清掉上次失败留下的残渣
        _clear_readonly(tmp)
        try:
            os.remove(tmp)
        except OSError:
            pass
    with open(tmp, "wb") as f:
        f.write(blob)
    if os.path.getsize(tmp) != len(blob):
        os.remove(tmp)
        raise SystemExit("写出校验失败，已中止（目标文件未被改动）")
    # Windows 上如果目标是只读属性，os.replace 会 WinError 5；先去掉只读位
    _clear_readonly(outpath)
    try:
        os.replace(tmp, outpath)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise SystemExit("替换目标文件失败：%s\n  临时文件已清理，原文件未被改动。"
                         "（若提示拒绝访问，请确认该 .mod 没有被游戏占用）" % e)
    print("  已生成：%s（%d 字节，原 %d 字节）" % (outpath, len(blob), orig_size))

    # ---- 读回校验
    expected = {}
    for u in units:
        t = trans.get(key_of(u.text))
        val = t if (t and t != u.text) else u.text
        expected[u.locator] = R.unescape_gff(val) if u.kind == "gff" else val
    _erf, bad = verify_output(outpath, expected)
    struct_info = erf_struct_check(outpath)
    print("  读回校验：比对 %d 条，不一致 %d 条" % (len(expected), len(bad)))
    for x in bad[:5]:
        print("     [不一致]", x)
    print("  结构自检：%s" % struct_info)
    n_cn = 0
    for e in _erf.entries:
        if e.ext.lower() == "ncs" and any(0x4E00 <= ord(c) <= 0x9FFF
                                          for c in _erf.read_data(e).decode("utf-8", "ignore")):
            n_cn += 1
    print("  含中文的 .ncs：%d 个" % n_cn)
    tm.record_mod(name, modpath, len(units), len(pending), len(uniq) - len(pending))
    rep = {"name": name, "mod": modpath, "out": outpath, "units": len(units),
           "uniq": len(uniq), "reused": len(uniq) - len(pending), "new": len(pending),
           "apply": dict(astats), "apply_fails": afails[:50], "ncs": ncs_info,
           "verify_bad": bad[:50], "struct": struct_info, "ncs_with_chinese": n_cn,
           "secs": round(time.time() - t0, 1)}
    with open(os.path.join(args.outdir, name + ".report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    return rep


def build_parser():
    ap = argparse.ArgumentParser(prog="modl10n.pipeline",
                                description="NWN:EE .mod 汉化：抽取→翻译→回写→重编译→打包→校验")
    ap.add_argument("--mod", required=True)
    ap.add_argument("--sf01", default=None, help="路线 A：NWN MultiLang 导出的文本文件")
    ap.add_argument("--tm", default=DEF_TM)
    ap.add_argument("--glossary", default=DEF_GLOSSARY)
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--out", default=None,
                    help="直接指定输出文件路径（例如写回原始 mod 的文件名）")
    ap.add_argument("--inplace", action="store_true",
                    help="覆盖输入的那个 .mod 本身（会先自动备份为 <名字>.orig-backup）")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--punct", default=os.environ.get("SF_PUNCT", "full"),
                    choices=["full", "half"])
    ap.add_argument("--extract-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-ncs", action="store_true")
    ap.add_argument("--no-nss", action="store_true",
                    help="完全不处理脚本字符串（只汉化 GFF：物品/生物/对话/区域等）")
    ap.add_argument("--no-var-assign", action="store_true",
                    help="不把「字符串变量赋值」当作显示文本（更保守）")
    ap.add_argument("--nwnsc", default=None)
    ap.add_argument("--install-dir", default=None)
    ap.add_argument("--extra-args", default="")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--yes", action="store_true")
    return ap


def make_context(args):
    os.makedirs(args.outdir or os.path.dirname(os.path.abspath(args.mod)), exist_ok=True)
    tm = TM(args.tm)
    gl = Glossary.load(args.glossary)
    from .translator import Translator, read_api_key
    key = read_api_key(args.api_key)
    tr = None
    if key and not args.extract_only and not args.dry_run:
        tr = Translator(key, glossary=gl, punct=args.punct)
    return tm, gl, tr, key


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.outdir is None:
        args.outdir = os.path.join(os.path.dirname(os.path.abspath(args.mod)), "汉化产物")
    if not args.mod or not os.path.exists(args.mod):
        raise SystemExit("找不到 .mod：%s" % args.mod)
    tm, gl, tr, key = make_context(args)
    print("翻译记忆 %s（已有 %d 条 %s）；术语表 %d 条"
          % (args.tm, tm.count(), tm.stats(), len(gl)))
    if tr is None and not (args.extract_only or args.dry_run):
        raise SystemExit("未找到 API Key（--api-key / 环境变量 DEEPSEEK_API_KEY / .api_key 文件）")
    rep = process(args.mod, args, tm=tm, glossary=gl, translator=tr)
    print("\n完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
