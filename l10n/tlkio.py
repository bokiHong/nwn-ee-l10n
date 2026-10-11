#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TLK 底层读写 —— 供 repo 层与 service 层共用（避免 repo 反向依赖 services）。

`decode` 的取舍见方案文档 10.1（PRC8 那 22 条的根因）：

    'replace'（默认）：utf-8 + replace，与 `modl10n.tlk.load_texts` 完全一致。
        选它做默认，是因为它在"tm 不动"的前提下**命中率最优**。
    'cp1252'：UTF-8 整体失败时回退 CP1252，智能引号能保住、文本更干净，
        但会让 15 条原本能命中的变成待译 —— 只在做"编码修正"任务时才该用。
"""
import hashlib
import os


def read_raw(path):
    """-> (原始字节, lang, 槽位总数, 数据区偏移)。复用 modl10n 的解析（同一处）。"""
    from modl10n.tlk import read_raw as _read_raw
    return _read_raw(path)


def read_texts(path, decode="replace"):
    """-> {strref(str): 文本}（只取 `flags==1` 且长度 > 0 的条目）。"""
    d, _lang, n, data_off = read_raw(path)
    out = {}
    for i in range(n):
        e = 20 + i * 40
        flags = int.from_bytes(d[e:e + 4], "little")
        off = int.from_bytes(d[e + 28:e + 32], "little")
        ln = int.from_bytes(d[e + 32:e + 36], "little")
        if flags != 1 or ln <= 0 or data_off + off + ln > len(d):
            continue
        blob = d[data_off + off: data_off + off + ln]
        if decode == "replace":
            out[str(i)] = blob.decode("utf-8", "replace")
        else:
            try:
                out[str(i)] = blob.decode("utf-8")
            except UnicodeDecodeError:
                out[str(i)] = blob.decode("cp1252", "replace")
    return out


def info(path):
    """TLK 概况：字节数 / lang / 槽位总数 / 有文本槽位数 / 整文件 sha256。"""
    d, lang, n, data_off = read_raw(path)
    texts = read_texts(path)
    return {"path": os.path.abspath(path), "bytes": len(d), "lang": lang,
            "count": n, "data_offset": data_off, "with_text": len(texts),
            "sha256": hashlib.sha256(d).hexdigest()}


def fingerprint(path, decode="replace"):
    """内容指纹：所有有文本槽位（按 strref 升序）拼起来算 sha256。

    与整文件 sha256 的区别：这个**只看文本内容**，所以"The TLK 被重新排列过、
    但每条文本没变"会被视为指纹相同 —— 这正是版本基线想要的语义。
    """
    texts = read_texts(path, decode=decode)
    h = hashlib.sha256()
    for k in sorted(texts, key=int):
        h.update(k.encode("ascii"))
        h.update(b"\x00")
        h.update(texts[k].encode("utf-8", "replace"))
        h.update(b"\x01")
    return h.hexdigest()


def range_texts(path, start, end, decode="replace"):
    """只取 [start, end] 区间里"有文本"的槽位 -> {strref(str): 文本}。"""
    texts = read_texts(path, decode=decode)
    return {k: v for k, v in texts.items()
            if start <= int(k) <= end and (v or "").strip()}


def en_ids_from_export(txt_path):
    """读英文导出 txt，返回其中的 strref 集合（`rebuild_tlk_safe.py` 的同一套解析）。

    支持两种行首格式：`12345: 文本` / `12345\t文本` / `#str 12345`。
    """
    import re
    ids = set()
    with open(txt_path, encoding="utf-8-sig", errors="replace") as f:
        for ln in f:
            m = re.match(r"^\s*(\d+)\s*[:：\t ]", ln) or re.match(r"^#str\s+(\d+)", ln)
            if m:
                ids.add(m.group(1))
    return ids


# ============================================================ 收编 tlk tool.py 的读写
def read_raw_entries(path):
    """-> (原始字节, lang, 槽位总数, 数据区偏移, [(flags, off, ln)])

    与 `tlk tool.py: read_tlk_raw`（原 `:40-55`）**逐字等价**（含报错文案）——
    那个实现被 6 个外部脚本依赖（`rebuild_tlk_safe.py` / `_tlk_probe.py` / `_patch*.py`），
    行为不能变。
    """
    d = open(path, "rb").read()
    if d[0:4] != b"TLK " or d[4:8] != b"V3.0":
        raise ValueError("不是 TLK V3.0 文件: %s" % path)
    lang = int.from_bytes(d[8:12], "little")
    n = int.from_bytes(d[12:16], "little")
    data_off = int.from_bytes(d[16:20], "little")
    if data_off != 20 + n * 40:
        raise ValueError("索引表布局与预期不符 (dataOffset=%d, 期望 %d)"
                         % (data_off, 20 + n * 40))
    ents = []
    for i in range(n):
        e = 20 + i * 40
        ents.append((int.from_bytes(d[e:e + 4], "little"),
                     int.from_bytes(d[e + 28:e + 32], "little"),
                     int.from_bytes(d[e + 32:e + 36], "little")))
    return d, lang, n, data_off, ents


def load(path, decode="per-item"):
    """读整份 TLK -> {"path","lang","count","texts","encoding"}。

    ★ `decode="per-item"`（默认）：**逐条**先试 UTF-8、失败则该条回退 CP1252，并统计
      fallback 生成 encoding 标签 —— 与 `tlk tool.py: load_tlk` 逐字等价。
      PRC8 那种 UTF-8/ANSI **混合**的 TLK 必须用这个模式；用整体回退会把本来
      解得好好的中文条目也一起按 CP1252 弄乱。

    `decode="replace"` / `"cp1252"`：走 `read_texts` 的整体解码，不产生 encoding 标签。
    """
    if decode != "per-item":
        _d, lang, n, _off = read_raw(path)
        return {"path": path, "lang": lang, "count": n,
                "texts": read_texts(path, decode=decode),
                "encoding": "UTF-8" if decode == "replace" else "ANSI/CP1252"}
    d, lang, n, data_off, ents = read_raw_entries(path)
    texts, fallback = {}, 0
    for i, (flags, off, ln) in enumerate(ents):
        if flags == 1 and ln > 0:
            raw = d[data_off + off:data_off + off + ln]
            try:
                texts[str(i)] = raw.decode("utf-8")
            except UnicodeDecodeError:
                texts[str(i)] = raw.decode("cp1252", "replace")
                fallback += 1
    enc = ("UTF-8" if not fallback else
           ("ANSI/CP1252" if fallback > len(texts) * 0.05
            else "UTF-8（少量 ANSI 条目已兼容）"))
    return {"path": path, "lang": lang, "count": n, "texts": texts, "encoding": enc}


def write_texts(path, lang, count, texts):
    """整体重写 TLK（与 `tlk tool.py: save_tlk` 原 `:80-101` 逐字等价）。

    ⚠ **这不是** `modl10n.tlk.write_tlk`（那个是外科式、保留索引表 `rec[4:28]`）。
    本函数新建 40 字节记录，声音长度等未知字段会被清零 —— 保留这个行为，是因为
    `rebuild_tlk_safe.py`、`_tlk_probe.py`、`_patch*.py` 共 6 个脚本依赖它。
    """
    ids = sorted(int(k) for k, v in texts.items() if v != "")
    data, loc = bytearray(), {}
    for i in ids:
        b = texts[str(i)].encode("utf-8")
        loc[i] = (len(data), len(b))
        data += b
    buf = bytearray()
    buf += b"TLK " + b"V3.0" + int(lang).to_bytes(4, "little") \
        + int(count).to_bytes(4, "little") + (20 + int(count) * 40).to_bytes(4, "little")
    for i in range(int(count)):
        rec = bytearray(40)
        if i in loc:
            off, ln = loc[i]
            rec[0:4] = (1).to_bytes(4, "little")
            rec[28:32] = off.to_bytes(4, "little")
            rec[32:36] = ln.to_bytes(4, "little")
        buf += rec
    buf += data
    open(path, "wb").write(buf)
    return len(buf)


def stats(path):
    """TLK 概况（给 CLI 的 `info` 用）：字节 / lang / count / 有文本 / 最长 / ID 范围 / 编码。"""
    t = load(path)
    texts = t["texts"]
    ids = sorted(int(k) for k in texts)
    longest = (0, None)
    for k, v in texts.items():
        if len(v or "") > longest[0]:
            longest = (len(v or ""), k)
    return {"path": os.path.abspath(path), "bytes": os.path.getsize(path),
            "lang": t["lang"], "count": t["count"], "with_text": len(texts),
            "encoding": t["encoding"],
            "min_id": ids[0] if ids else None, "max_id": ids[-1] if ids else None,
            "longest_len": longest[0], "longest_id": longest[1]}
