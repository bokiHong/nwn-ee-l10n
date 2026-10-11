#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""txt 导入导出的**唯一 IO 处**（从 `tlk tool.py` 原样搬来，行为不变）。

**搬运原则：一个字节都不变。** 这些函数里藏着不少细节，重写就等着踩坑：

  * 导出 txt 用 **`encoding="utf-8-sig"`（带 BOM）+ `newline=""`**；
  * 条目首行要把换行符拼回去（`parse_txt:128`），否则多行条目会被撕开；
  * `#//` 头里 `TLKCount` 与 `Total Item = [N/M]` 互相兜底（后者只在前者缺失时生效）；
  * 标点转换是 `to_txt(fn(clean(t)))` 这个顺序，不是别的顺序。

格式（NWN MultiLang / TLK Tool 导出）：

    #// Exported by TLK Tool
    #// File Path: '...'
    #// Language: 0, Total Item = [N/M]
    #// TLKCount: M
    #// LanguageID: 0
    #//
    1 := 文本第一行\\r\\n
        文本后续行……
"""
import os
import re

_ITEM_RE = re.compile(r"^(\d+) := (.*)$")


def parse(data):
    """解析导出 txt -> (meta, [(id, text)])。与 `tlk tool.py: parse_txt` 逐字一致。"""
    meta, entries, cur, parts = {}, [], None, []

    def flush():
        if cur is not None:
            entries.append((cur, "".join(parts)))

    for line in data.splitlines(keepends=True):
        cc = line[:-2] if line.endswith("\r\n") else line
        if cc.startswith("#//"):
            m = re.search(r"TLKCount:\s*(\d+)", cc)
            if m:
                meta["count"] = int(m.group(1))
            m = re.search(r"LanguageID:\s*(\d+)", cc)
            if m:
                meta["lang"] = int(m.group(1))
            m = re.search(r"Total Item = \[(\d+)/(\d+)\]", cc)
            if m and "count" not in meta:
                meta["count"] = int(m.group(2))
            continue
        m = _ITEM_RE.match(cc)
        if m:
            flush()
            cur, parts = m.group(1), [m.group(2) + ("\r\n" if line.endswith("\r\n") else "")]
        elif cur is not None:
            parts.append(line)
    flush()
    return meta, entries


def clean(s):
    """去尾部换行、统一成 \\n。"""
    if s.endswith("\r\n"):
        s = s[:-2]
    elif s.endswith("\n"):
        s = s[:-1]
    return s.replace("\r\n", "\n")


def to_txt(s):
    """\\n -> \\r\\n。"""
    return s.replace("\r\n", "\n").replace("\n", "\r\n")


def build(meta, entries, src_path):
    """拼出导出 txt 全文。与 `tlk tool.py: build_txt` 逐字一致。"""
    lines = ["#// Exported by TLK Tool\n",
             "#// File Path: '%s'\n" % src_path,
             "#// Language: %d, Total Item = [%d/%d]\n"
             % (meta.get("lang", 0), len(entries), meta.get("count", 0)),
             "#// TLKCount: %d\n" % meta.get("count", 0),
             "#// LanguageID: %d\n" % meta.get("lang", 0),
             "#// \n"]
    for eid, text in entries:
        lines.append("%s := %s\r\n" % (eid, to_txt(text)))
    return "".join(lines)


def read(path, encoding="utf-8-sig"):
    """读导出 txt -> (meta, entries)。"""
    with open(path, "r", encoding=encoding, newline="") as f:
        return parse(f.read())


def write(path, meta, entries, src_path):
    """写导出 txt，**utf-8-sig（带 BOM）+ newline=""**，返回字节数。"""
    text = build(meta, entries, src_path)
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(text)
    return len(open(path, "rb").read())


def leading_comments(raw):
    """取开头连续的 `#//` 行。"""
    out = []
    for ln in raw.splitlines(keepends=True):
        if ln.startswith("#//"):
            out.append(ln)
        else:
            break
    return out


def rewrite_transform(path, transform):
    """就地改写 txt：每条 `to_txt(transform(clean(t)))`（= `cli_punct` 的语义）。

    返回条目数。
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        raw = f.read()
    _meta, entries = parse(raw)
    head = leading_comments(raw)
    body = "".join("%s := %s\r\n" % (i, to_txt(transform(clean(t)))) for i, t in entries)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write("".join(head) + body)
    return len(entries)


def rewrite_texts(path, raw, entries, texts):
    """用**内存里的** `texts` 重写 txt（= `cli_translate` 的语义：保留原头）。

    `texts` 是 `{id(str): 文本}`，`entries` 是 `parse()` 给的 [(id, text)]（决定顺序与
    哪些 id 要写）。
    """
    head = leading_comments(raw)
    body = "".join("%s := %s\r\n" % (i, to_txt(texts[i])) for i, _ in entries)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write("".join(head) + body)
    return len(entries)
