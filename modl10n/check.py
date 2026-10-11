#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`.mod` 体检：专治 0 字节、半损坏、结构异常的归档。

用法:
    python -m modl10n.check "C:\\...\\Neverwinter Nights\\modules"
    python -m modl10n.check "某文件.mod" --verbose

检查项：
  * 文件大小是否为 0（第三方工具"先截断再写"崩溃的典型后果）
  * 是否真的是 ERF/MOD 归档（签名 MOD / ERF / HAK / NWM / SAV）
  * 头部各块偏移是否落在文件内、资源区间是否越界/重叠、键表是否可读
  * 每个 GFF 资源能否解析（坏掉的 GFF 会在游戏里表现为异常或崩溃）
  * 是否已经含中文（判断这个 mod 有没有汉化过）
  * 遗留的 `.tmp-writing` / `.orig-backup` 文件
"""
import os
import sys
import struct
import argparse
import collections

from .core.erf import Erf, HDR_FMT, HDR_SIZE
from .core.gff import Gff
from .extract import looks_like_gff, txt_of


def check_one(path, verbose=False):
    name = os.path.basename(path)
    size = os.path.getsize(path)
    res = {"path": path, "name": name, "size": size, "problems": [], "notes": []}

    if size == 0:
        res["problems"].append("文件为 0 字节——多半是某个工具"
                               "「先截断再写」时崩溃留下的（原始内容已丢失，需从备份恢复）")
        return res
    if size < HDR_SIZE:
        res["problems"].append("文件只有 %d 字节，小于 ERF 头长度，肯定损坏" % size)
        return res

    with open(path, "rb") as f:
        head = f.read(HDR_SIZE)
    sig = head[:4]
    res["sig"] = sig
    if sig not in (b"MOD ", b"ERF ", b"HAK ", b"NWM ", b"SAV ", b"NWX "):
        res["problems"].append("签名 %r 不是已知的 ERF 归档类型" % sig)
        return res

    try:
        (s, ver, langcount, locsize, entrycount, ofs_loc, ofs_key, ofs_res,
         year, day, strref, _r) = struct.unpack(HDR_FMT, head)
    except struct.error as e:
        res["problems"].append("头部解析失败：%r" % e)
        return res
    res.update(version=ver.decode("latin-1", "replace"), entries=entrycount,
               langcount=langcount, locale=locsize)

    # 各块是否在文件内
    for label, off, need in (("localized", ofs_loc, locsize),
                             ("key list", ofs_key, entrycount * 24),
                             ("resource list", ofs_res, entrycount * 8)):
        if off == 0 and need == 0:
            continue
        if off < HDR_SIZE or off + need > size:
            res["problems"].append("%s 块越界：offset=%d size=%d 文件=%d"
                                   % (label, off, need, size))
    if res["problems"]:
        return res

    try:
        erf = Erf.load(path)
    except Exception as e:
        res["problems"].append("归档读取失败：%r" % e)
        return res

    # 资源区间越界/重叠
    spans = []
    oor = 0
    for e in erf.entries:
        if e.offset + e.size > size or e.offset < 0:
            oor += 1
        spans.append((e.offset, e.offset + e.size))
    if oor:
        res["problems"].append("%d 个资源的偏移越界" % oor)
    spans.sort()
    overlap = sum(1 for i in range(1, len(spans)) if spans[i][0] < spans[i - 1][1])
    if overlap:
        res["problems"].append("资源区间重叠 %d 处" % overlap)
    if len(erf.entries) != entrycount:
        res["problems"].append("键表条目数不一致：头说 %d，实读 %d"
                               % (entrycount, len(erf.entries)))

    # GFF 解析 + 中文统计
    bad_gff = 0
    cjk = 0
    types = collections.Counter()
    for e in erf.entries:
        types[e.ext] += 1
        try:
            data = erf.read_data(e)
        except Exception:
            bad_gff += 1
            continue
        if not looks_like_gff(data):
            if e.ext == "nss" and any(0x4E00 <= ord(c) <= 0x9FFF
                                      for c in data.decode("utf-8", "ignore")):
                cjk += 1
            elif e.ext == "ncs" and any(0x4E00 <= ord(c) <= 0x9FFF
                                        for c in data.decode("utf-8", "ignore")):
                cjk += 1
            continue
        try:
            g = Gff(data)
            for f in g.locstring_fields():
                t = txt_of(f.value)
                if t and any(0x4E00 <= ord(c) <= 0x9FFF for c in t):
                    cjk += 1
        except Exception:
            bad_gff += 1
            if verbose:
                res["notes"].append("GFF 解析失败：%s.%s" % (e.resref, e.ext))
    if bad_gff:
        res["problems"].append("%d 个 GFF 资源解析失败" % bad_gff)
    res["cjk"] = cjk
    res["types"] = types
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(prog="modl10n.check", description=".mod 归档体检")
    ap.add_argument("target", help=".mod 文件或包含 .mod 的目录")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    if os.path.isdir(args.target):
        files = sorted(os.path.join(args.target, f) for f in os.listdir(args.target)
                       if f.lower().endswith(".mod"))
    else:
        files = [args.target]
    if not files:
        raise SystemExit("没有找到 .mod 文件")

    print("体检 %d 个文件\n" % len(files))
    bad = 0
    for p in files:
        r = check_one(p, args.verbose)
        hz = "【已汉化 %d 处】" % r.get("cjk", 0) if r.get("cjk") else "【未检出中文】"
        if r["problems"]:
            bad += 1
            print("❌ %-46s %10d 字节" % (r["name"], r["size"]))
            for x in r["problems"]:
                print("      · %s" % x)
        else:
            print("✅ %-46s %10d 字节  条目 %-6s %s"
                  % (r["name"], r["size"], r.get("entries", "-"), hz))
        for x in r.get("notes", []):
            print("      · %s" % x)
    # 遗留文件
    d = os.path.dirname(os.path.abspath(files[0]))
    leftovers = [f for f in os.listdir(d) if f.endswith(".tmp-writing")]
    if leftovers:
        print("\n⚠ 发现遗留的临时文件（说明某次写出没完成）：%s" % ", ".join(leftovers))
    baks = [f for f in os.listdir(d) if f.endswith(".orig-backup")]
    if baks:
        print("\nℹ 原件备份：%s" % ", ".join(baks))
    print("\n结论：%d 个文件有问题，%d 个正常" % (bad, len(files) - bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
