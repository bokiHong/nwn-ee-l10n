#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 .mod 抽取可译文本单元（带精确回写位置）。

两个入口：
  * `extract_units(mod)`            —— 自研抽取（路线 B，默认）。GFF 全类型覆盖 + .nss 显示文本判别。
  * `units_from_sf01(mod, sf01)`    —— 从 NWN MultiLang 导出的 SF01.txt 反推（路线 A，兜底）。

两条路都产出同一种 Unit（带 locator），所以下游的翻译/回写/校验完全共用：
    GFF 单元 locator = ("gff", keyidx, field_index)
    .nss 单元 locator = ("nss", keyidx, lineno, lit_index)
"""
import os
import re
import sys
import struct

from .core.erf import Erf
from .core.gff import Gff
from .core.sf import parse_sf, parse_nwscript_literals
from .core import sfrules as R
from .core.classify import classify_file

# 常见 NWN GFF 资源首 4 字节
GFF_MAGICS = {
    b"UTI ", b"UTC ", b"DLG ", b"ARE ", b"GIT ", b"IFO ", b"ITP ", b"UTT ",
    b"UTS ", b"FAC ", b"UTE ", b"UTD ", b"UTP ", b"GIC ", b"UTM ", b"JRL ",
    b"UTW ", b"BIC ", b"SET ", b"PTM ", b"UPE ",
}


class Unit(object):
    __slots__ = ("kind", "keyidx", "fname", "locator", "text", "label", "lineno",
                 "lit_index", "line", "strref", "decision")

    def __init__(self, kind, keyidx, fname, locator, text, **kw):
        self.kind = kind
        self.keyidx = keyidx
        self.fname = fname
        self.locator = locator
        self.text = text
        self.label = kw.get("label")
        self.lineno = kw.get("lineno")
        self.lit_index = kw.get("lit_index")
        self.line = kw.get("line")
        self.strref = kw.get("strref")
        self.decision = kw.get("decision", "translate")

    def __repr__(self):
        return "<%s %s %s %r>" % (self.kind, self.fname,
                                  self.label or "%s/%s" % (self.lineno, self.lit_index),
                                  self.text[:40])


def looks_like_gff(data):
    if len(data) < 56 or data[:4] not in GFF_MAGICS:
        return False
    try:
        _ft, _fv, so, sc, fo, fc, lo, lc, fdo, fdc, fio, fic, lio, lic = \
            struct.unpack_from("<4s4sIIIIIIIIIIII", data, 0)
    except struct.error:
        return False
    if not (0 < so <= len(data) and so + sc * 12 <= len(data)):
        return False
    if not (0 < fo <= len(data) and fo + fc * 12 <= len(data)):
        return False
    return fc <= 200000 and sc <= 200000


def txt_of(ls):
    if ls is None:
        return None
    b = ls.text(0)
    if b is None and ls.entries:
        b = ls.entries[0][1]
    if b is None:
        return None
    return b.decode("utf-8", "replace")


def extract_units(modpath, include_nss=True, var_assign=True, review=None):
    """路线 B：直接从 .mod 抽取。review 传 list 时，把"疑似显示文本（字符串变量赋值）"记进去。

    统一表示：GFF 文本用 `escape_gff()` 转成"真实换行 -> 两字符 \\n"（与 SF01 表示一致，
    便于校验换行结构，回写时再 unescape）；.nss 字面量保持源码原样（其中的 \\n 本身就是转义）。
    """
    erf = Erf.load(modpath)
    resrefs = frozenset(e.resref.lower() for e in erf.entries)
    units = []
    for i, ent in enumerate(erf.entries):
        data = erf.orig(ent)
        ext = ent.ext.lower()
        if ext == "nss":
            if not include_nss:
                continue
            try:
                s = data.decode("utf-8")
            except UnicodeDecodeError:
                s = data.decode("cp1252", "replace")
            for ln, li, body, fn, dec in classify_file(s, var_assign=var_assign):
                if not dec.startswith("translate"):
                    continue
                if R.is_nontext_literal(body, resrefs):
                    continue
                u = Unit("nss", i, "%s.%s" % (ent.resref, ext),
                         ("nss", i, ln, li), body,
                         lineno=ln, lit_index=li, decision=dec)
                units.append(u)
                if review is not None and dec.endswith("var-assign"):
                    review.append(u)
            continue
        if not looks_like_gff(data):
            continue
        try:
            g = Gff(data)
            fields = g.locstring_fields()
        except Exception:
            continue
        for f in fields:
            # ★ 绝不翻译模块名（`.ifo` 的 `Mod_Name`）：引擎把它当**标识符**用
            #   （日志里的 2da 缓存指纹就是 `模块名_UID`），写成非 ASCII 会让游戏在
            #   **保存时**硬崩（nwmain.exe `0xc0000409` / BEX64，固定偏移）。
            #   实测：只改这一个字段就必崩；而其余字段全部可以安全翻译
            #   （含 Mod_Description、任务日志 .jrl 的全部字段、所有物件/对话文本）。
            if ext == "ifo" and str(f.label) == "Mod_Name":
                continue
            t = txt_of(f.value)
            if not t:
                continue
            units.append(Unit("gff", i, "%s.%s" % (ent.resref, ext),
                              ("gff", i, f.index), R.escape_gff(t), label=f.label,
                              strref=f.value.strref))
    return erf, units


def units_from_sf01(modpath, sf01_path, review=None):
    """路线 A：按 MultiLang 导出的 SF01.txt 反推单元（与 Chapter One 那次完全一致的定位方式）。"""
    erf = Erf.load(modpath)
    resrefs = frozenset(e.resref.lower() for e in erf.entries)
    doc = parse_sf(sf01_path)
    units = []
    gcache = {}

    def gff_of(idx):
        if idx not in gcache:
            gcache[idx] = Gff(erf.orig(erf.entries[idx]))
        return gcache[idx]

    unresolved = []
    for u in doc.units:
        ent = erf.entries[u.fileidx]
        ext = ent.ext.lower()
        if u.kind == "fld":
            if ext == "nss":
                continue
            pool = {}
            for f in gff_of(u.fileidx).locstring_fields():
                t = txt_of(f.value)
                if t:
                    pool.setdefault((f.label, R.escape_gff(t)), []).append(f.index)
            key = (u.label, u.text)
            hit = pool.get(key)
            if not hit:
                # 兼容 MultiLang 只做 \n 转义、不转义反斜杠的写法
                alt = (u.label, R.escape_gff(R.unescape_sf(u.text)))
                hit = pool.get(alt)
                if hit:
                    key = alt
            if not hit:
                unresolved.append(u)
                continue
            for fi in hit:
                units.append(Unit("gff", u.fileidx, "%s.%s" % (ent.resref, ext),
                                  ("gff", u.fileidx, fi), key[1], label=u.label))
        else:
            if ext != "nss":
                continue
            if R.is_nontext_literal(u.text, resrefs):
                continue
            units.append(Unit("nss", u.fileidx, "%s.%s" % (ent.resref, ext),
                              ("nss", u.fileidx, u.lineno, u.lit_index), u.text,
                              lineno=u.lineno, lit_index=u.lit_index))
    if review is not None:
        review.extend(unresolved)
    return erf, units
