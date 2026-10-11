#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SF01.txt 解析 / 重建 / 翻译单元提取。

格式（已逐字节验证）：
    #module <path>
    #file <name>.<ext> <keyidx>
    <空行>
    #fld <label> <ord>
     #str chsM: <text>
    <空行>
    ...
    #-----------------------------------------------------------------
    <空行>
    #file <name>.<ext> <keyidx>       <- .nss 文件块
    <空行>
     #line <n>: <code>
     #line <n>: <code>
    <空行>
    #-----------------------------------------------------------------
"""
import re
import hashlib

CRLF = "\r\n"

RE_MODULE = re.compile(r"^#module (.*)$")
RE_FILE = re.compile(r"^#file (\S+) (\d+)$")
RE_FLD = re.compile(r"^#fld (\S+) (\d+)$")
RE_STR = re.compile(r"^ #str (\S+): (.*)$")
RE_LINE = re.compile(r"^ #line (\d+): (.*)$")
RE_SEP = re.compile(r"^#-+$")


class Unit(object):
    """一个翻译单元。"""
    __slots__ = ("kind", "fileidx", "fname", "label", "ord", "lineno",
                 "text", "lit_index", "lit_start", "lit_end", "raw_line")

    def __init__(self, kind, text):
        self.kind = kind          # "fld" | "line"
        self.text = text          # 待翻译的英文（fld=整串；line=引号内内容）
        self.fileidx = None
        self.fname = None
        self.label = None
        self.ord = None
        self.lineno = None
        self.lit_index = None     # 该行第几个字符串字面量
        self.lit_start = None     # 在 raw_line 中的字符区间（含引号）
        self.lit_end = None
        self.raw_line = None

    @property
    def key(self):
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:32]

    def __repr__(self):
        return "<Unit %s %s %s %r>" % (self.kind, self.fname, self.label or self.lineno,
                                       self.text[:40])


class SfDoc(object):
    def __init__(self):
        self.lines = []      # 原始行（不含换行符）
        self.has_trailing_nl = True
        self.units = []      # Unit 列表
        self.files = []      # [(name, keyidx)]
        self.module = None
        # text -> [unit] 映射
        self.by_key = {}


def parse_nwscript_literals(code):
    """返回 [(start, end, body)]，区间含两侧引号，body 为未转义的原始内容。"""
    out = []
    i = 0
    n = len(code)
    while i < n:
        c = code[i]
        if c == '"':
            j = i + 1
            buf = []
            while j < n:
                if code[j] == "\\" and j + 1 < n:
                    buf.append(code[j:j + 2])
                    j += 2
                    continue
                if code[j] == '"':
                    break
                buf.append(code[j])
                j += 1
            if j >= n:
                break        # 未闭合：跳过
            out.append((i, j + 1, "".join(buf)))
            i = j + 1
        else:
            i += 1
    return out


def parse_sf(path):
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8")
    doc = SfDoc()
    doc.has_trailing_nl = text.endswith("\n")
    lines = text.split("\r\n") if "\r\n" in text else text.split("\n")
    # split 后若文件以换行结尾，最后会多一个空串
    if lines and lines[-1] == "":
        lines.pop()
    doc.lines = lines

    cur_file = None
    cur_fname = None
    cur_label = None
    # 逐行：为了把 unit 值与行号绑定，保存行索引
    doc.line_units = {}          # line_idx -> [Unit]
    doc.line_is_str = {}         # line_idx -> Unit
    for li, ln in enumerate(lines):
        m = RE_MODULE.match(ln)
        if m:
            doc.module = m.group(1)
            continue
        m = RE_FILE.match(ln)
        if m:
            cur_fname = m.group(1)
            cur_file = int(m.group(2))
            doc.files.append((cur_fname, cur_file))
            continue
        m = RE_FLD.match(ln)
        if m:
            cur_label = (m.group(1), int(m.group(2)))
            continue
        m = RE_STR.match(ln)
        if m:
            u = Unit("fld", m.group(2))
            u.fileidx = cur_file
            u.fname = cur_fname
            u.label, u.ord = cur_label
            u.raw_line = ln
            doc.units.append(u)
            doc.line_is_str[li] = u
            continue
        m = RE_LINE.match(ln)
        if m:
            lineno = int(m.group(1))
            code = m.group(2)
            us = []
            for k, (s, e, body) in enumerate(parse_nwscript_literals(code)):
                if body == "":
                    continue
                u = Unit("line", body)
                u.fileidx = cur_file
                u.fname = cur_fname
                u.lineno = lineno
                u.lit_index = k
                u.lit_start = s
                u.lit_end = e
                u.raw_line = ln
                doc.units.append(u)
                us.append(u)
            if us:
                doc.line_units[li] = us
            continue
    for u in doc.units:
        doc.by_key.setdefault(u.key, []).append(u)
    return doc


def build_sf(doc, translations=None, eol=CRLF):
    """按 translations {key: 中文} 重建文件内容，未命中则保留原文。"""
    translations = translations or {}
    lines = list(doc.lines)
    # 1) #str 行
    for li, u in doc.line_is_str.items():
        tr = translations.get(u.key)
        if tr is None:
            continue
        lines[li] = " #str chsM: " + tr
    # 2) #line 行：只替换字面量区间，按区间从后往前替换
    for li, us in doc.line_units.items():
        repls = []
        for u in us:
            tr = translations.get(u.key)
            if tr is None:
                continue
            repls.append((u.lit_start, u.lit_end, '"' + escape_nwscript(tr) + '"'))
        if not repls:
            continue
        m = RE_LINE.match(us[0].raw_line)
        code = m.group(2)
        for s, e, new in sorted(repls, key=lambda x: -x[0]):
            code = code[:s] + new + code[e:]
        lines[li] = " #line %d: " % us[0].lineno + code
    out = eol.join(lines)
    if doc.has_trailing_nl:
        out += eol
    return out


_ESC = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_ESC_RE = re.compile(r'[\\"\n\r\t]')


def escape_nwscript(s):
    return _ESC_RE.sub(lambda m: _ESC[m.group(0)], s)


def load_translations(cache):
    """cache: {sha256前缀: 译文} -> {key: 译文}（同一 key 即同一内容）。"""
    return dict(cache)
