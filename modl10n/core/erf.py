#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NWN ERF/MOD/HAK 归档读写库（BioWare ERF V1.0）。"""
import os
import struct

# 4s signature, 4s version, I langcount, I locsize, I entrycount,
# I ofs_loc, I ofs_key, I ofs_res, I year, I day, I strref, 116s reserved
HDR_FMT = "<4s4sIIIIIIIII116s"
HDR_SIZE = 160

NWN_RESTYPE = {
    2000: "PLT", 2001: "TGA", 2002: "WAV", 2003: "WAV", 2004: "PLT", 2005: "INI",
    2006: "BAM", 2007: "WED", 2008: "PLT", 2009: "NSS", 2010: "NSS",
    2011: "NSS", 2012: "ARE", 2013: "SET", 2014: "IFO", 2015: "BIC",
    2016: "WOK", 2017: "2DA", 2018: "TLK", 2019: "TXT", 2020: "NSS",
    2021: "NSS", 2022: "NSS", 2023: "NSS", 2024: "NSS", 2025: "NSS",
    2026: "NSS", 2027: "UTC", 2028: "DLG", 2029: "DLG", 2030: "DLG",
    2031: "DLG", 2032: "DLG", 2033: "NSS", 2034: "NSS", 2035: "NSS",
    2036: "NSS", 2037: "NSS", 2038: "NSS", 2039: "NSS", 2040: "NSS",
    2041: "NSS", 2042: "NSS", 2043: "NSS", 2044: "NSS", 2045: "NSS",
    2046: "NSS", 2047: "NSS", 2048: "NSS", 2049: "NSS", 2050: "NSS",
    2051: "NSS", 2052: "NSS", 2053: "NSS", 2054: "NSS", 2055: "NSS",
    2056: "NSS", 2057: "NSS", 2058: "NSS", 2059: "NSS", 2060: "NSS",
    2061: "NSS", 2062: "NSS", 2063: "NSS", 2064: "NSS", 2065: "NSS",
    2066: "NSS", 2067: "NSS", 2068: "NSS", 2069: "NSS", 2070: "NSS",
}
# 精确补充常用类型
NWN_RESTYPE.update({
    2009: "NSS", 2010: "NSS", 2011: "NSS", 2012: "ARE", 2013: "SET",
    2014: "IFO", 2015: "BIC", 2016: "WOK", 2017: "2DA", 2018: "TLK",
    2019: "TXT", 2020: "NSS", 2021: "NSS", 2022: "NSS", 2023: "NSS",
    2024: "NSS", 2025: "NSS", 2026: "NSS", 2027: "UTC", 2028: "DLG",
    2029: "DLG", 2030: "DLG", 2031: "DLG", 2032: "DLG",
    2033: "NSS", 2034: "NSS", 2035: "NSS", 2036: "NSS", 2037: "NSS",
    2038: "NSS", 2039: "NSS", 2040: "NSS", 2041: "NSS", 2042: "NSS",
    2043: "NSS", 2044: "NSS", 2045: "NSS", 2046: "NSS", 2047: "NSS",
    2048: "NSS", 2049: "NSS", 2050: "NSS", 2051: "NSS", 2052: "NSS",
    2053: "NSS", 2054: "NSS", 2055: "NSS", 2056: "NSS", 2057: "NSS",
    2058: "NSS", 2059: "NSS", 2060: "NSS", 2061: "NSS", 2062: "NSS",
    2063: "NSS", 2064: "NSS", 2065: "NSS", 2066: "NSS", 2067: "NSS",
    2068: "NSS", 2069: "NSS", 2070: "NSS",
    3000: "NSS", 3001: "NSS", 3002: "NSS", 3003: "NSS", 3004: "NSS",
    3005: "NSS", 3006: "NSS", 3007: "NSS",
    2016: "WOK",
})

# 权威 NWN1 resource type 表（已在本项目 Swordflight .mod 上按 GFF magic 实测校正）
RESTYPE_FULL = {
    1: "BMP", 3: "TGA", 4: "WAV", 5: "PLT", 6: "INI", 7: "BAM", 8: "WED",
    2005: "INI", 2006: "BAM", 2007: "WED",
    2009: "NSS", 2010: "NCS", 2011: "NSS", 2012: "ARE", 2013: "SET",
    2014: "IFO", 2015: "BIC", 2016: "WOK", 2017: "2DA", 2018: "TLK",
    2019: "TXT", 2020: "NSS", 2021: "GIT", 2022: "NSS", 2023: "GIT",
    2024: "NSS", 2025: "UTI", 2026: "NSS", 2027: "UTC", 2028: "DLG",
    2029: "DLG", 2030: "ITP", 2031: "BTT", 2032: "UTT", 2033: "DDS",
    2034: "BMP", 2035: "UTS", 2036: "NSS", 2037: "NSS", 2038: "FAC",
    2039: "NSS", 2040: "UTE", 2041: "NSS", 2042: "UTD", 2043: "NSS",
    2044: "UTP", 2045: "NSS", 2046: "GIC", 2047: "GUI", 2048: "NSS",
    2049: "NSS", 2050: "NSS", 2051: "UTM", 2052: "NSS", 2053: "NSS",
    2054: "NSS", 2055: "NSS", 2056: "JRL", 2057: "NSS", 2058: "UTW",
    2059: "NSS", 2060: "NSS", 2061: "NSS", 2062: "NSS", 2063: "NSS",
    2064: "NSS", 2065: "NSS", 2066: "NSS", 2067: "NSS", 2068: "NSS",
    2069: "NSS", 2070: "NSS", 2071: "NSS", 2072: "NSS", 2073: "NSS",
    2074: "NSS", 2075: "NSS", 2076: "NSS", 2077: "NSS", 2078: "NSS",
    2079: "NSS", 2080: "NSS", 2081: "NSS", 2082: "NSS", 2083: "NSS",
    2084: "NSS", 2085: "NSS", 2086: "NSS", 2087: "NSS", 2088: "NSS",
    2089: "NSS", 2090: "NSS", 2091: "NSS", 2092: "NSS", 2093: "NSS",
    2094: "NSS", 2095: "NSS", 2096: "NSS", 2097: "NSS", 2098: "NSS",
    2099: "NSS",
}
RESTYPE_FULL.update({r: "NSS" for r in range(2000, 2100) if r not in RESTYPE_FULL})
RESTYPE_FULL.update({
    2009: "NSS", 2010: "NCS", 2012: "ARE", 2014: "IFO", 2023: "GIT",
    2025: "UTI", 2027: "UTC", 2029: "DLG", 2030: "ITP", 2032: "UTT",
    2035: "UTS", 2038: "FAC", 2040: "UTE", 2042: "UTD", 2044: "UTP",
    2046: "GIC", 2051: "UTM", 2056: "JRL", 2058: "UTW", 2017: "2DA",
    2018: "TLK", 2013: "SET", 2015: "BIC", 2016: "WOK", 2019: "TXT",
    2021: "GIT", 2031: "BTT", 2033: "DDS", 2034: "BMP", 2047: "GUI",
})
# 常见扩展名 -> restype（用于反查）
EXT_TO_RESTYPE = {
    "uti": 2016, "utc": 2027, "dlg": 2029, "nss": 2009, "ncs": 2010,
    "are": 2012, "git": 2021, "ifo": 2014, "fac": 2019, "2da": 2017,
}


def restype_name(rid):
    return RESTYPE_FULL.get(rid, "T%04d" % rid)


class ErfEntry(object):
    __slots__ = ("resref", "resid", "restype", "offset", "size", "data")

    def __init__(self, resref, resid, restype, offset, size):
        self.resref = resref
        self.resid = resid
        self.restype = restype
        self.offset = offset
        self.size = size
        self.data = None

    @property
    def ext(self):
        return restype_name(self.restype).lower()

    def __repr__(self):
        return "<ErfEntry %s.%s id=%d off=%d size=%d>" % (
            self.resref, self.ext, self.resid, self.offset, self.size)


class Erf(object):
    def __init__(self, sig=b"MOD ", version=b"V1.0"):
        self.sig = sig
        self.version = version
        self.langcount = 0
        self.year = 0
        self.day = 0
        self.strref = 0xFFFFFFFF
        self.entries = []          # 保持 key list 顺序
        self.localized = b""

    # ---------------------------------------------------------- 读
    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            raw = f.read()
        e = cls()
        e._parse(raw)
        e._raw = raw
        return e

    def _parse(self, raw):
        (sig, ver, langcount, locsize, entrycount,
         ofs_loc, ofs_key, ofs_res, year, day, strref, _res) = struct.unpack(
            HDR_FMT, raw[:HDR_SIZE])
        self.sig, self.version = sig, ver
        self.langcount = langcount
        self.year, self.day, self.strref = year, day, strref
        self.localized = raw[ofs_loc:ofs_loc + locsize] if locsize else b""
        keys = []
        for i in range(entrycount):
            off = ofs_key + i * 24
            resref = raw[off:off + 16].split(b"\x00")[0].decode("latin-1")
            resid, restype, _unused = struct.unpack("<IHH", raw[off + 16:off + 24])
            keys.append((resref, resid, restype))
        for i, (resref, resid, restype) in enumerate(keys):
            off = ofs_res + i * 8
            rofs, rsize = struct.unpack("<II", raw[off:off + 8])
            self.entries.append(ErfEntry(resref, resid, restype, rofs, rsize))

    def read_data(self, ent):
        """读取资源内容（会缓存到 ent.data）。

        注意：一旦调用，ent.data 就不再是 None，所以"哪些资源被改过"必须由调用方显式记录，
        不能靠 `ent.data is not None` 判断。
        """
        if ent.data is None:
            ent.data = self._raw[ent.offset:ent.offset + ent.size]
        return ent.data

    def orig(self, ent):
        """读取**原始**资源内容（不缓存、不受 ent.data 影响）。"""
        return self._raw[ent.offset:ent.offset + ent.size]

    # ---------------------------------------------------------- 写
    def build(self):
        """按当前 entries（顺序即 key list 顺序）重新打包为 ERF 字节。"""
        entrycount = len(self.entries)
        hdr_size = HDR_SIZE
        # 布局：header | localized | key list | resource list | 资源数据
        key_off = hdr_size + len(self.localized)
        res_off = key_off + entrycount * 24
        data_off = res_off + entrycount * 8
        # 资源数据按 entries 顺序紧密排列
        blob = bytearray()
        res_list = []
        cur = data_off
        for ent in self.entries:
            # 未改动的资源必须回落到原始字节 —— 否则会被写成空资源（严重损坏）
            if ent.data is not None:
                data = ent.data
            elif getattr(self, "_raw", None) is not None:
                data = self._raw[ent.offset:ent.offset + ent.size]
            else:
                data = b""
            res_list.append((cur, len(data)))
            blob += data
            cur += len(data)
        out = bytearray()
        out += struct.pack(
            HDR_FMT, self.sig, self.version, self.langcount, len(self.localized),
            entrycount, hdr_size, key_off, res_off,
            self.year, self.day, self.strref, b"\x00" * 116)
        out += self.localized
        for ent in self.entries:
            rr = ent.resref.encode("latin-1")[:16]
            out += rr + b"\x00" * (16 - len(rr))
            out += struct.pack("<IHH", ent.resid, ent.restype, 0)
        for rofs, rsize in res_list:
            out += struct.pack("<II", rofs, rsize)
        out += blob
        return bytes(out)


def unpack(path, outdir, exts=None, verbose=False):
    e = Erf.load(path)
    os.makedirs(outdir, exist_ok=True)
    n = 0
    for ent in e.entries:
        ext = ent.ext
        if exts and ext not in exts:
            continue
        data = e.read_data(ent)
        fn = os.path.join(outdir, "%s.%s" % (ent.resref, ext))
        with open(fn, "wb") as f:
            f.write(data)
        n += 1
        if verbose:
            print(fn, len(data))
    return e, n


if __name__ == "__main__":
    import sys
    mod = sys.argv[1]
    outdir = sys.argv[2] if len(sys.argv) > 2 else "unpacked"
    e, n = unpack(mod, outdir, verbose=False)
    print("signature:", e.sig, "version:", e.version)
    print("entries:", len(e.entries), "dumped:", n)
    print("localized len:", len(e.localized))
    import collections
    c = collections.Counter(restype_name(x.restype) for x in e.entries)
    for k, v in c.most_common():
        print("  %-6s %d" % (k, v))
