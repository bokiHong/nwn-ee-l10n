#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把"内嵌在 GFF 里的译文"改道成"TLK + strref 引用"。

**为什么必须这样**（实测 + 社区确认）：
把译文直接写进会被存档序列化的 GFF（`.git` / `.uti` / `.utc` / `.are` / `.jrl` / `.ifo` …）
会让**存档必崩** —— 游戏在写 .sav 时重新序列化这些 GFF，崩在 nwmain.exe 的固定偏移上
（Windows 事件日志：异常代码 0xc0000409 / BEX64）。只翻译 `.dlg` 则完全没事。
Neverwinter Vault 论坛 "Automatic module translation" 里 uther78 踩到的是同一个坑
（"modifying the script to make it translate the other files as well causes the savegame to crash"），
标准解法即 neverwinter.nim 的 `nwn_erf_tlkify` 思路：文本集中放进模块的自定义 TLK，
GFF 里只留 `strref = 0x01000000 + 索引`。

用法：
    python -m modl10n.tlkroute --orig 原件.mod --zh 我的成品.mod \\
        --tlk-in "…\\tlk\\prc8_consortium.tlk" --out 新.mod --tlk-out "…\\tlk\\prc8_consortium.tlk"
"""
import os
import sys
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from modl10n.core.erf import Erf                      # noqa: E402
from modl10n.core.gff import Gff, LocString           # noqa: E402
from modl10n.core import gff as G                     # noqa: E402
from modl10n.extract import looks_like_gff            # noqa: E402
from modl10n import tlk as T                          # noqa: E402

STRREF_BASE = 0x01000000        # 模块自定义 TLK 的 strref 约定

# 保持内嵌文本的资源类型：对话文件不进存档，实测内嵌没问题（也省得 TLK 臃肿）
DEFAULT_INLINE = ("dlg",)


def route(orig_mod, zh_mod, base_tlk, out_mod, out_tlk,
          inline_ext=DEFAULT_INLINE, skip_ext=(), only_ext=(), quiet=False):
    """把 zh_mod 里的 GFF 译文改道成 TLK+strref。

    skip_ext / only_ext：用于**对照测试**——被排除的资源类型会**还原成原件的字节**，
    这样可以做出"只翻译某几类资源"的版本来二分定位崩溃源。
    """
    A, B = Erf.load(orig_mod), Erf.load(zh_mod)
    if len(A.entries) != len(B.entries):
        raise SystemExit("两个 .mod 的资源数不同（%d vs %d），无法一一对应"
                         % (len(A.entries), len(B.entries)))

    skip_ext = tuple(x.lower() for x in skip_ext)
    only_ext = tuple(x.lower() for x in only_ext)
    jobs = []          # (entry_index, field_index, 文本)
    texts, seen = [], {}
    n_restored = 0
    for ei, (ea, eb) in enumerate(zip(A.entries, B.entries)):
        ext = ea.ext.lower()
        if (only_ext and ext not in only_ext) or ext in skip_ext:
            eb.data = A.orig(ea)        # 还原原件字节（对照用）
            n_restored += 1
            continue
        if ext in inline_ext:
            continue
        da, db = A.orig(ea), B.read_data(eb)
        if da == db or not looks_like_gff(da):
            continue
        ga, gb = Gff(da), Gff(db)
        fa_list, fb_list = ga.locstring_fields(), gb.locstring_fields()
        if len(fa_list) != len(fb_list):
            continue
        for fa, fb in zip(fa_list, fb_list):
            ta = b"".join(x[1] for x in fa.value.entries)
            tb = b"".join(x[1] for x in fb.value.entries)
            if ta == tb:
                continue
            t = tb.decode("utf-8", "replace")
            if not t.strip():
                continue
            if t not in seen:
                seen[t] = len(texts)
                texts.append(t)
            jobs.append((ei, fb.index, t))

    if not quiet:
        print("  需改道字段 %d 处 → 去重后 %d 条文本" % (len(jobs), len(texts)))
    if not jobs:
        raise SystemExit("没有需要改道的字段（两个 .mod 的 GFF 文本一样？）")

    index, tinfo = T.append_tlk(base_tlk, out_tlk, texts)
    if not quiet:
        print("  TLK：%s（条目 %d → %d，新增 %d，%d 字节；校验不一致 %d）"
              % (os.path.basename(out_tlk), tinfo["count"] - tinfo["added"],
                 tinfo["count"], tinfo["added"], tinfo["bytes"], tinfo["mismatch"]))

    by_entry = {}
    for ei, fi, t in jobs:
        by_entry.setdefault(ei, []).append((fi, t))

    n_files = 0
    for ei, items in by_entry.items():
        eb = B.entries[ei]
        db = B.read_data(eb)
        edits = {fi: LocString(strref=STRREF_BASE + index[t], entries=[]) for fi, t in items}
        eb.data = G.patch_locstrings(db, edits)
        n_files += 1

    blob = B.build()
    with open(out_mod, "wb") as f:
        f.write(blob)

    # 复核：新 .mod 里不该再有"内嵌的非 dlg 译文"
    C = Erf.load(out_mod)
    left = 0
    for ei, (ea, ec) in enumerate(zip(A.entries, C.entries)):
        if ea.ext.lower() in inline_ext:
            continue
        da, dc = A.orig(ea), C.read_data(ec)
        if da == dc or not looks_like_gff(da):
            continue
        ga, gc = Gff(da), Gff(dc)
        for fa, fc in zip(ga.locstring_fields(), gc.locstring_fields()):
            tb = b"".join(x[1] for x in fc.value.entries)
            ta = b"".join(x[1] for x in fa.value.entries)
            if tb and tb != ta:      # 只算"仍然是译文"的，垃圾同名串不算
                left += 1
    info = {"fields": len(jobs), "texts": len(texts), "files": n_files,
            "bytes": len(blob), "tlk": tinfo, "inline_left": left,
            "restored": n_restored}
    if not quiet:
        print("  输出：%s（%d 字节，%d 个文件改动；还原原件 %d 个；残留内嵌译文 %d 处）"
              % (out_mod, len(blob), n_files, n_restored, left))
    return info


def main(argv=None):
    ap = argparse.ArgumentParser(description="把 GFF 内嵌译文改道为 TLK+strref")
    ap.add_argument("--orig", required=True, help="英文原件 .mod")
    ap.add_argument("--zh", required=True, help="已翻译的 .mod（内嵌文本版）")
    ap.add_argument("--tlk-in", required=True, help="模块的自定义 TLK（Mod_CustomTlk 指向的那个）")
    ap.add_argument("--out", required=True, help="输出的 .mod")
    ap.add_argument("--tlk-out", required=True, help="输出的 TLK（可写回原位）")
    ap.add_argument("--inline-ext", default=",".join(DEFAULT_INLINE),
                    help="保持内嵌的资源类型，逗号分隔（默认 dlg）")
    ap.add_argument("--skip-ext", default="",
                    help="这些类型还原成原件字节，逗号分隔（对照测试用）")
    ap.add_argument("--only-ext", default="",
                    help="只翻译这些类型，其余还原成原件字节（对照测试用）")
    a = ap.parse_args(argv)
    split = lambda s: tuple(x.strip().lower() for x in s.split(",") if x.strip())   # noqa: E731
    info = route(a.orig, a.zh, a.tlk_in, a.out, a.tlk_out,
                 inline_ext=split(a.inline_ext), skip_ext=split(a.skip_ext),
                 only_ext=split(a.only_ext))
    print("  完成：%s" % info)
    return 0


if __name__ == "__main__":
    sys.exit(main())
