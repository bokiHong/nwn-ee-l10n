#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NWN1 GFF 读写（实测类型表）。

类型编号（在 Swordflight .mod 上扫描实测）：
    0  BYTE          内联
    1  CHAR          内联（未出现）
    2  WORD          内联
    3  SHORT         内联
    4  DWORD         内联
    5  INT           内联
    6  DWORD64       FieldData 偏移（未出现）
    7  INT64         FieldData 偏移（未出现）
    8  FLOAT         内联（4 字节 IEEE754 位模式）
    10 CExoString    FieldData 偏移: [u32 len][bytes]
    11 CResRef       FieldData 偏移: [u8 len][bytes]
    12 CExoLocString FieldData 偏移: [u32 size][u32 strref][u32 cnt][(u32 strid,u32 len,bytes)*]
   13 VOID          FieldData 偏移: [u32 len][bytes]
    14 STRUCT        data = struct 数组下标
    15 LIST          data = ListIndices 数组下标（见 list_members 自适应）

回写策略：只把新的 LocString 追加到 FieldData 末尾，改该字段的 data 指针，
并平移 FieldIndices / ListIndices 两块的绝对偏移（其余字节原样保留）。
"""
import struct

T_BYTE, T_CHAR, T_WORD, T_SHORT, T_DWORD, T_INT = 0, 1, 2, 3, 4, 5
T_DWORD64, T_INT64, T_FLOAT = 6, 7, 8
T_CSTRING, T_RESREF, T_LOCSTRING, T_VOID = 10, 11, 12, 13
T_STRUCT, T_LIST, T_ORIENT = 14, 15, 16

TYPE_NAME = {0: "BYTE", 1: "CHAR", 2: "WORD", 3: "SHORT", 4: "DWORD", 5: "INT",
             6: "DWORD64", 7: "INT64", 8: "FLOAT", 9: "?9",
             10: "CExoString", 11: "CResRef", 12: "CExoLocString", 13: "VOID",
             14: "Struct", 15: "List"}

# 内联存放（<=4 字节）的类型
INLINE_TYPES = {T_BYTE, T_CHAR, T_WORD, T_SHORT, T_DWORD, T_INT, T_FLOAT}
# 定长但在 FieldData 里的类型
OFFSET_FIXED = {T_DWORD64: 8, T_INT64: 8}
# 变长（4 字节长度前缀 + 数据）的类型
VARLEN32 = {T_CSTRING, T_VOID}
# 变长（1 字节长度前缀）的类型
VARLEN8 = {T_RESREF}

HDR = struct.Struct("<4s4sIIIIIIIIIIII")
HDR_SIZE = HDR.size          # 56
STRUCT_SIZE = 12
FIELD_SIZE = 12
LABEL_SIZE = 16
assert HDR_SIZE == 56


class GffField(object):
    __slots__ = ("ftype", "label", "data", "index", "value", "fdata_off",
                 "src_off")

    def __init__(self, ftype, label, data, index):
        self.ftype = ftype
        self.label = label
        self.data = data
        self.index = index
        self.value = None
        self.fdata_off = None      # FieldData 内偏移（变长/8 字节类型）
        self.src_off = None        # 原始 blob 的绝对偏移（仅变长类型）

    def __repr__(self):
        return "<F%d %s %s>" % (self.index, self.label, TYPE_NAME.get(self.ftype, "?"))


class GffStruct(object):
    __slots__ = ("type", "data", "fields", "index")

    def __init__(self, stype, data, index):
        self.type = stype
        self.data = data
        self.index = index
        self.fields = []

    def field(self, name):
        for f in self.fields:
            if f.label == name:
                return f
        return None

    def __repr__(self):
        return "<Struct[%d] type=%d n=%d>" % (self.index, self.type, len(self.fields))


class LocString(object):
    __slots__ = ("strref", "entries", "size")

    def __init__(self, strref=0xFFFFFFFF, entries=None, size=0):
        self.strref = strref
        self.entries = entries if entries is not None else []
        self.size = size

    def text(self, strid=0):
        for s, b in self.entries:
            if s == strid:
                return b
        return None

    def set_text(self, strid, data):
        """就地设置；不存在则追加。返回是否新增了条目。"""
        for i, (s, _b) in enumerate(self.entries):
            if s == strid:
                self.entries[i] = (s, data)
                return False
        self.entries.append((strid, data))
        return True

    def serialize(self):
        """size = 其后字节数（不含 size 自身），与实测一致。"""
        body = struct.pack("<II", self.strref, len(self.entries))
        for s, b in self.entries:
            body += struct.pack("<II", s, len(b)) + b
        return struct.pack("<I", len(body)) + body

    def __repr__(self):
        return "<LocString strref=%d %s>" % (self.strref, [(s, len(b)) for s, b in self.entries])


class Gff(object):
    def __init__(self, raw):
        self.raw = raw
        (self.ftype, self.fver, self.struct_off, self.struct_cnt,
         self.field_off, self.field_cnt, self.label_off, self.label_cnt,
         self.fdata_off, self.fdata_cnt, self.findex_off, self.findex_cnt,
         self.lindex_off, self.lindex_cnt) = HDR.unpack_from(raw, 0)

    # ------------------------------------------------------------------ 惰性解析
    def _ensure(self):
        if getattr(self, "_done", False):
            return
        self._done = True
        raw = self.raw
        # labels
        self.labels = []
        for i in range(self.label_cnt):
            off = self.label_off + i * LABEL_SIZE
            self.labels.append(raw[off:off + LABEL_SIZE].split(b"\x00")[0].decode("latin-1"))
        # fields
        self.fields = []
        for i in range(self.field_cnt):
            off = self.field_off + i * FIELD_SIZE
            ftype, flabel, fdata = struct.unpack_from("<III", raw, off)
            lab = self.labels[flabel] if flabel < len(self.labels) else "#%d" % flabel
            self.fields.append(GffField(ftype, lab, fdata, i))
        # FieldIndices：字段槽位 -> 全局字段下标
        self.findex = list(struct.unpack_from("<%dI" % self.field_cnt, raw, self.findex_off)) \
            if self.field_cnt else []
        # structs（用 FieldIndices 解析其字段列表）
        self.structs = []
        for i in range(self.struct_cnt):
            off = self.struct_off + i * STRUCT_SIZE
            stype, sdata, scount = struct.unpack_from("<III", raw, off)
            st = GffStruct(stype, sdata, i)
            self.structs.append(st)
        for st in self.structs:
            off = self.struct_off + st.index * STRUCT_SIZE
            _t, sdata, scount = struct.unpack_from("<III", raw, off)
            base = 0 if st.type == 0xFFFFFFFF else sdata // 4
            idxs = self.findex[base:base + scount]
            st.fields = [self.fields[k] for k in idxs if 0 <= k < len(self.fields)]
        self.root = self.structs[0] if self.structs else None
        # 值
        fo = self.fdata_off
        for f in self.fields:
            t = f.ftype
            if t in INLINE_TYPES:
                n = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 8: 4}[t]
                v = f.data & ((1 << (8 * n)) - 1)
                if t in (T_SHORT, T_INT) and v >= (1 << (8 * n - 1)):
                    v -= (1 << (8 * n))
                f.value = v
                f.fdata_off = None
            elif t in OFFSET_FIXED:
                n = OFFSET_FIXED[t]
                f.fdata_off = f.data
                f.value = raw[fo + f.data: fo + f.data + n]
            elif t in VARLEN32:
                f.fdata_off = f.data
                ln = struct.unpack_from("<I", raw, fo + f.data)[0]
                f.src_off = fo + f.data
                f.value = raw[fo + f.data + 4: fo + f.data + 4 + ln]
            elif t in VARLEN8:
                f.fdata_off = f.data
                ln = raw[fo + f.data]
                f.src_off = fo + f.data
                f.value = raw[fo + f.data + 1: fo + f.data + 1 + ln]
            elif t == T_LOCSTRING:
                f.fdata_off = f.data
                f.src_off = fo + f.data
                f.value = self._parse_loc(f.src_off)
            else:
                f.value = None      # Struct / List：data 是索引

    def locstring_fields(self):
        """所有 CExoLocString 字段（不依赖 struct/list 遍历，含列表内嵌套字段）。"""
        self._ensure()
        return [f for f in self.fields if f.ftype == T_LOCSTRING]

    def _parse_loc(self, off):
        raw = self.raw
        size = struct.unpack_from("<I", raw, off)[0]
        ls = LocString(size=size)
        p = off + 4
        ls.strref = struct.unpack_from("<I", raw, p)[0]
        p += 4
        cnt = struct.unpack_from("<I", raw, p)[0]
        p += 4
        for _ in range(cnt):
            strid, ln = struct.unpack_from("<II", raw, p)
            p += 8
            ls.entries.append((strid, raw[p:p + ln]))
            p += ln
        return ls

    # ------------------------------------------------------------------ 遍历
    def list_info(self, f):
        """返回 (count, [struct_index...], 用字节偏移还是元素下标)。"""
        raw = self.raw
        lio = self.lindex_off
        # 元素下标约定
        off = lio + f.data * 4
        if off + 4 <= len(raw):
            cnt = struct.unpack_from("<I", raw, off)[0]
            if cnt == 0:
                return 0, []
            if off + 4 + cnt * 4 <= len(raw):
                members = list(struct.unpack_from("<%dI" % cnt, raw, off + 4))
                if all(m < self.struct_cnt for m in members):
                    return cnt, members
        # 字节偏移约定
        off = lio + f.data
        cnt = struct.unpack_from("<I", raw, off)[0]
        members = list(struct.unpack_from("<%dI" % cnt, raw, off + 4))
        return cnt, members

    def list_members(self, f):
        cnt, members = self.list_info(f)
        return [self.structs[m] for m in members]

    def walk(self, st=None, path="", depth=0):
        """深度优先，字段声明顺序，进入 Struct/List 子节点。仅供查看，回写不依赖它。"""
        self._ensure()
        if depth > 24:
            return
        if st is None:
            st = self.root
        for f in st.fields:
            p = path + "/" + f.label
            yield st, f, p, depth
            if f.ftype == T_STRUCT:
                if 0 <= f.data < self.struct_cnt:
                    for x in self.walk(self.structs[f.data], p, depth + 1):
                        yield x
            elif f.ftype == T_LIST:
                for k, sub in enumerate(self.list_members(f)):
                    for x in self.walk(sub, "%s[%d]" % (p, k), depth + 1):
                        yield x


# ---------------------------------------------------------------- 最小改动回写
def patch_locstrings(raw, edits):
    """edits: {field_index: LocString}。返回新的 GFF 字节。

    ★ 这里做**紧凑重排**：只把"被字段引用到的数据"按原偏移顺序重排进 FieldData，
      不保留任何死数据。

    为什么必须紧凑：NWN 工具产出的 GFF 是 **100% 紧凑** 的（实测原件 666 个 GFF 的
    FieldData 冗余 0 字节）。而"复制整块旧 FieldData + 追加新译文"的做法会留下大量
    不再被引用的旧字符串 —— 实测第一版成品冗余 693,442 字节（占 36.3%），module.jrl
    一个文件就有 46KB 死数据。游戏**读取**时按指针取数据所以看不出来，但**保存**时
    要重新序列化这些 GFF，于是崩在 nwmain.exe 的固定偏移上（实测 0xc0000409
    栈缓冲越界 / BEX64，每次偏移都相同）。
    """
    if not edits:
        return raw
    g = Gff(raw)
    g._ensure()
    fs, fe = g.fdata_off, g.fdata_off + g.fdata_cnt
    old = bytes(raw[fs:fe])

    # 收集每个字段在 FieldData 里的 (原偏移, 长度)；遇到未知类型就退回保守做法
    items = []
    for fi, f in enumerate(g.fields):
        t = f.ftype
        if t in INLINE_TYPES or t in (T_STRUCT, T_LIST):
            continue
        try:
            if t in OFFSET_FIXED:
                ln = OFFSET_FIXED[t]
            elif t in VARLEN32:
                ln = 4 + struct.unpack_from("<I", old, f.data)[0]
            elif t in VARLEN8:
                ln = 1 + old[f.data]
            elif t == T_LOCSTRING:
                ln = 4 + struct.unpack_from("<I", old, f.data)[0]
            else:
                return _patch_append(raw, edits)
            if ln < 0 or f.data < 0 or f.data + ln > len(old):
                return _patch_append(raw, edits)
        except (struct.error, IndexError):
            return _patch_append(raw, edits)
        items.append((f.data, ln, fi))

    buf = bytearray(raw)
    new = bytearray()
    memo = {}                      # 原偏移 -> 新偏移（保留字段之间共享同一块数据的别名）
    for off, ln, fi in sorted(items, key=lambda x: (x[0], x[2])):
        if fi in edits:
            blob = edits[fi].serialize()
        else:
            if off in memo:
                struct.pack_into("<I", buf, g.field_off + fi * FIELD_SIZE + 8, memo[off])
                continue
            blob = old[off:off + ln]
        noff = len(new)
        new += blob
        memo.setdefault(off, noff)
        struct.pack_into("<I", buf, g.field_off + fi * FIELD_SIZE + 8, noff)

    head = bytes(buf[:fs])
    gap = raw[fe:g.findex_off]
    findex = raw[g.findex_off:g.findex_off + g.findex_cnt]
    mid = raw[g.findex_off + g.findex_cnt:g.lindex_off]
    lindex = raw[g.lindex_off:g.lindex_off + g.lindex_cnt]
    tail = raw[g.lindex_off + g.lindex_cnt:]

    out = bytearray()
    out += head
    out += bytes(new)
    out += gap
    new_findex_off = len(out)
    out += findex
    out += mid
    new_lindex_off = len(out)
    out += lindex
    out += tail

    h = list(HDR.unpack_from(out, 0))
    h[9] = len(new)             # FieldDataCount
    h[10] = new_findex_off      # FieldIndicesOffset
    h[12] = new_lindex_off      # ListIndicesOffset
    HDR.pack_into(out, 0, *h)
    return bytes(out)


def _patch_append(raw, edits):
    """保守退路：复制整块旧 FieldData 再把新译文追加在后面（会留下死数据，仅供兜底）。"""
    g = Gff(raw)
    g._ensure()
    fdata_start = g.fdata_off
    fdata_end = g.fdata_off + g.fdata_cnt
    new_fdata = bytearray(raw[fdata_start:fdata_end])
    buf = bytearray(raw)

    for fi, newls in sorted(edits.items()):
        f = g.fields[fi]
        assert f.ftype == T_LOCSTRING, "field %d 不是 CExoLocString" % fi
        blob = newls.serialize()
        newoff = len(new_fdata)
        new_fdata += blob
        # 只改字段条目里的 data 指针（FieldIndices 存的是字段下标，不是数据偏移，不能动）
        foff = g.field_off + fi * FIELD_SIZE
        struct.pack_into("<I", buf, foff + 8, newoff)

    head = bytes(buf[:fdata_start])
    gap = raw[fdata_end:g.findex_off]
    findex = raw[g.findex_off:g.findex_off + g.findex_cnt]
    mid = raw[g.findex_off + g.findex_cnt:g.lindex_off]
    lindex = raw[g.lindex_off:g.lindex_off + g.lindex_cnt]
    tail = raw[g.lindex_off + g.lindex_cnt:]

    out = bytearray()
    out += head
    out += bytes(new_fdata)
    out += gap
    new_findex_off = len(out)
    out += findex
    out += mid
    new_lindex_off = len(out)
    out += lindex
    out += tail

    h = list(HDR.unpack_from(out, 0))
    h[9] = len(new_fdata)       # FieldDataCount
    h[10] = new_findex_off      # FieldIndicesOffset
    h[12] = new_lindex_off      # ListIndicesOffset
    HDR.pack_into(out, 0, *h)
    return bytes(out)
