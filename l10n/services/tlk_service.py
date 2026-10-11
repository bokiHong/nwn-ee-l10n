#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TlkService —— TLK 编辑的业务逻辑（**零 tk 引用**，GUI 只负责刷控件）。

核心是 `TlkDoc`：把旧 `tlk tool.py` 里散在 `App` 上的 `self.tlk` / `self.modified` /
`self.orig_texts` 三件状态，连同"改一条、存盘、导出、写记忆库"这些动作，收成一个
**不依赖界面**的文档对象。这样一来：

  * GUI 侧只剩"控件 → 调 doc → 刷控件"，没有一行 IO/SQL；
  * 同一个 `TlkDoc` 能被无头测试直接驱动（`l10n/selftest_gui.py` 就是靠它）。

行为对齐（照搬旧实现，未改语义）：
    from_path   ← `load_tlk(path)`（per-item 解码）
    save        ← `save_tlk(path, lang, count, texts)`
    export_txt  ← `build_txt` 那套（**但导出内存态**，修掉旧 `do_export` 走磁盘重读的 bug）
    from_txt    ← `do_import` 的语义（缺 TLKCount 时用 `fallback_count` 兜底）
    set_punct   ← `do_punct` 的语义（逐条比对、变了才标脏）
    dirty_rows  ← `save_to_memory` 的过滤逻辑（原文空/译文空/未变 → 跳过）
"""
import collections
import os

from .. import textops, tlkio, txtio


class TlkDoc(object):
    """TLK 文档（纯逻辑）。"""

    def __init__(self, path=None, lang=0, count=0, texts=None, encoding="UTF-8"):
        self.path = path
        self.lang = int(lang or 0)
        self.count = int(count or 0)
        self.texts = dict(texts or {})
        self.encoding = encoding
        # 原文快照：写记忆库时要用"英文原文 -> 当前中文"。浅拷贝即可
        # （下面所有改动都是"换整个 value"，不会就地改字符串）
        self.orig_texts = dict(self.texts)
        self.modified = set()

    # ============================================================ 构造
    @classmethod
    def from_load(cls, d):
        """从 `tlkio.load()` 的结果构造。"""
        return cls(path=d.get("path"), lang=d.get("lang", 0), count=d.get("count", 0),
                   texts=d.get("texts") or {}, encoding=d.get("encoding", "UTF-8"))

    @classmethod
    def from_path(cls, path, decode="per-item"):
        """读一个 TLK 文件。

        `decode="per-item"`（默认）与旧 `load_tlk` 逐字等价；别改成整体解码 ——
        PRC8 那种 UTF-8/ANSI 混合的 TLK 会把中文条目一起弄乱。
        """
        return cls.from_load(tlkio.load(path, decode=decode))

    @classmethod
    def from_txt(cls, txt_path, fallback_count=None):
        """从导出 txt 构造（不落盘）。

        缺 `TLKCount` 时：给了 `fallback_count` 就用它（GUI 的语义），
        没给就抛 `ValueError`（CLI 的语义）。
        """
        meta, entries = txtio.read(txt_path)
        n = meta.get("count") or fallback_count
        if not n:
            raise ValueError("txt 头部缺少 TLKCount")
        out = (txt_path[:-4] + ".tlk") if txt_path.lower().endswith(".txt") else (txt_path + ".tlk")
        doc = cls(path=out, lang=meta.get("lang", 0), count=int(n),
                  texts={str(i): txtio.clean(t) for i, t in entries})
        # 导入来源是 txt，没有"原 TLK 原文"可比 —— 于是 dirty_rows() 会全部跳过，
        # 不会把导入内容误当"手工编辑"写进记忆库。
        doc.orig_texts = {}
        # 导入的文档**整体未保存**（还没写盘），所以全量标脏 —— 这是旧语义，保留。
        doc.modified = set(doc.texts)
        return doc

    # ============================================================ 改
    def set_text(self, i, s):
        """改一条。返回是否真的变了（变了才标脏）。"""
        i = str(i)
        if self.texts.get(i) == s:
            return False
        self.texts[i] = s
        self.modified.add(i)
        return True

    def ensure_slot(self, i):
        """确保槽位存在（旧 `goto_id` 的"凭空占位"）。返回是否新建。"""
        i = str(i)
        if i in self.texts:
            return False
        self.texts[i] = ""
        self.modified.add(i)
        return True

    def set_punct(self, mode):
        """全量标点转换（mode: "cn" / "en"）。返回变化条数。"""
        fn = textops.to_cn_punct if mode == "cn" else textops.to_en_punct
        n = 0
        for k in list(self.texts):
            old = self.texts[k]
            new = fn(old)
            if new != old:
                self.texts[k] = new
                self.modified.add(k)
                n += 1
        return n

    def dirty(self):
        return bool(self.modified)

    def dirty_rows(self):
        """`[(src, dst)]` —— 只取"原文非空、译文非空、且真的不同"的已改条目。

        与旧 `save_to_memory` 的过滤条件逐条一致（`tlk tool.py:933`）。
        """
        rows = []
        for i in sorted(self.modified, key=lambda x: int(x) if str(x).isdigit() else 0):
            src = (self.orig_texts or {}).get(i, "")
            dst = self.texts.get(i, "")
            if not (src or "").strip() or not (dst or "").strip() or src == dst:
                continue
            rows.append((src, dst))
        return rows

    # ============================================================ 存
    def save(self, out_path=None, clear=True):
        """写回 TLK 文件（整体重写，与旧 `save_tlk` 字节级一致）。返回字节数。

        `clear=False` 时**不清脏标记** —— 界面保存后还要靠 `modified` 判断哪些是
        手工改过的（要写进记忆库），所以旧 `save_cur` 的顺序是
        "落盘 → 写记忆库 → 最后才清标记"。
        """
        p = out_path or self.path
        if not p:
            raise ValueError("没有输出路径")
        n = tlkio.write_texts(p, self.lang, self.count, self.texts)
        self.path = p
        if clear:
            self.modified.clear()
        return n

    def export_txt(self, out):
        """导出 txt —— **导出内存态**。

        旧 `do_export` 调 `cli_export(self.tlk["path"], p)`，那里会**重新从磁盘读**，
        于是"改了但还没保存"的内容不会出现在导出的 txt 里（隐性 bug）。这里直接用
        内存里的 `texts`，所见即所得。
        """
        meta = {"lang": self.lang, "count": self.count}
        entries = [(i, (t or "").replace("\n", "\r\n"))
                   for i, t in sorted(self.texts.items(), key=lambda kv: int(kv[0]))]
        return txtio.write(out, meta, entries, self.path or "")

    def stats(self):
        """概况（界面状态栏/信息框用）。"""
        ids = sorted(int(k) for k in self.texts)
        longest = (0, None)
        for k, v in self.texts.items():
            if len(v or "") > longest[0]:
                longest = (len(v or ""), k)
        return {"path": self.path, "lang": self.lang, "count": self.count,
                "with_text": len(self.texts), "encoding": self.encoding,
                "modified": len(self.modified),
                "min_id": ids[0] if ids else None, "max_id": ids[-1] if ids else None,
                "longest_len": longest[0], "longest_id": longest[1]}


# ================================================================ 门面函数
def open_tlk(path, decode="per-item"):
    """打开 TLK 文件 -> TlkDoc。"""
    return TlkDoc.from_path(path, decode=decode)


def import_txt(txt_path, fallback_count=None):
    """从 txt 构造 TlkDoc（不落盘）。"""
    return TlkDoc.from_txt(txt_path, fallback_count=fallback_count)


def save_to_memory(doc, tm_repo, src_mod=None, note=None):
    """把 `doc` 里**手工改过**的条目写进记忆库（`status='manual'`，机器永不覆盖）。

    这是修好那个坏按钮的落点：旧的 `App.save_to_memory` 有两个硬 bug
    （`load_tm()` 返回的第二个值是元组却当函数调；`TM.put` 根本没有 `note` 参数），
    异常被 except 吞成弹窗 —— 所以它**从来没成功过**（实测 `tm` 里 note 含
    "tlk tool" 的条目为 0 条）。

    返回 `{"written", "skipped", "rows"}`。
    """
    rows = doc.dirty_rows()
    n = tm_repo.put_manual(rows, src_mod=src_mod, note=note) if rows else 0
    return {"written": n, "skipped": len(doc.modified) - len(rows), "rows": rows}


def register(db, project, tlk_path, role="source", decode="per-item", extract=False):
    """把 TLK 登记进中央库：`scan` + （可选）`extract`。

    `extract=True` 时会把条目抽成 `unit`（30k 条，**请放后台线程**）。
    """
    from .scan_service import scan
    rep = {"scan": scan(db, project, tlk_path, role=role, verbose=False)}
    if extract:
        from .extract_service import extract_tlk
        rep["extract"] = extract_tlk(db, project, tlk_path, decode=decode, verbose=False)
    return rep


# ================================================================ 英文源查找（方案 A）
# 用户 2026-10-08 提醒：第 3 层"内容探测"**必须限流** —— 同目录几十个 TLK 时禁止全量
# 读进内存，要加缓存、只探最近修改的几个，避免 GUI 卡顿。所以：
#   * `_idx_of` **只读 20 字节头部 + n×40 的索引表**，不碰字符串数据区；
#   * LRU 缓存（键 = 路径 + mtime + 大小），同一个文件只解析一次；
#   * 内容探测默认只探**最近 5 个**（`probe_limit`）。
_IDX_CACHE = collections.OrderedDict()
_IDX_CACHE_MAX = 8


def _idx_of(path):
    """一个 TLK 里"有文本"的 strref 集合 —— **只读索引表**，不解析文本。带 LRU 缓存。"""
    path = os.path.abspath(path)
    key = (path, os.path.getmtime(path), os.path.getsize(path))
    if key in _IDX_CACHE:
        _IDX_CACHE.move_to_end(key)
        return _IDX_CACHE[key]
    with open(path, "rb") as f:
        head = f.read(20)
        if head[0:4] != b"TLK " or head[4:8] != b"V3.0":
            raise ValueError("不是 TLK V3.0：%s" % path)
        n = int.from_bytes(head[12:16], "little")
        f.seek(20)
        table = f.read(n * 40)
    ids = set()
    for i in range(n):
        e = i * 40
        if (int.from_bytes(table[e:e + 4], "little") == 1
                and int.from_bytes(table[e + 32:e + 36], "little") > 0):
            ids.add(i)
    ids = frozenset(ids)
    _IDX_CACHE[key] = ids
    while len(_IDX_CACHE) > _IDX_CACHE_MAX:
        _IDX_CACHE.popitem(last=False)
    return ids


def _ascii_ratio(path, ids, sample=20):
    """抽样算"非 ASCII 字节占比" —— 用来把**中文版**排除出"英文源"候选。

    英文源（哪怕是 CP1252 的智能引号）占比极低；中文版会超过 50%。
    **只读少量字节**（前 sample 条），不把整份文件读进内存。
    """
    ids = sorted(ids)[:sample]
    if not ids:
        return 1.0
    with open(path, "rb") as f:
        head = f.read(20)
        n = int.from_bytes(head[12:16], "little")
        data_off = int.from_bytes(head[16:20], "little")
        f.seek(20)
        table = f.read(n * 40)
        total = nonascii = 0
        for i in ids:
            e = i * 40
            off = int.from_bytes(table[e + 28:e + 32], "little")
            ln = int.from_bytes(table[e + 32:e + 36], "little")
            if ln <= 0:
                continue
            f.seek(data_off + off)
            raw = f.read(ln)
            total += len(raw)
            nonascii += sum(1 for b in raw if b > 127)
    return (float(nonascii) / total) if total else 1.0


def find_en_source(tlk_path, db=None, project=None, probe_limit=5):
    """给"已汉化的 TLK"找对应的**英文源 TLK**，返回 `(路径 或 None, 说明)`。

    四级降级（用户 2026-10-08 拍板的方案 A）：
      ① **中央库**：`role='source'` 的 tlk 容器里，选与被查文件共享 strref 最多的；
      ② **文件名启发**：`<stem>E.tlk` / `<stem>_en.tlk` / `<stem>_E.tlk`
         （反向也认：名字末尾带 E / _en 时去掉再找，例如
         `prc8_consortium` ↔ `prc8_consortiumE`）；
      ③ **内容探测**：同目录按 mtime 倒序**只探最近 `probe_limit` 个** `.tlk`，比共享 strref 数；
      ④ **优雅降级**：返回 `(None, 原因)`，调用方退回"按中文反查术语表"。
    """
    tlk_path = os.path.abspath(tlk_path)
    reasons = []
    try:
        cur = _idx_of(tlk_path)
    except Exception as e:                                   # noqa: BLE001
        return None, "被查 TLK 读不了：%r" % (e,)

    if db is not None:                                       # ① 中央库
        try:
            sql = "SELECT path FROM container WHERE role='source' AND path<>?"
            params = [tlk_path]
            if project:
                sql += " AND project=?"
                params.append(project)
            best, best_n = None, 0
            for (p,) in db.execute(sql, params):
                if not p or not os.path.exists(p):
                    continue
                try:
                    shared = len(cur & _idx_of(p))
                except Exception:                            # noqa: BLE001
                    continue
                if shared > best_n:
                    best, best_n = p, shared
            if best:
                return best, "中央库 role=source（共享 strref %d）" % best_n
            reasons.append("中央库里没有可用的 source 容器")
        except Exception as e:                               # noqa: BLE001
            reasons.append("中央库查询失败 %r" % (e,))

    d, base = os.path.split(tlk_path)                        # ② 文件名启发
    stem, ext = os.path.splitext(base)
    cands = []
    if stem.lower().endswith("_en"):
        cands.append(stem[:-3] + ext)
    if stem.endswith("E"):
        cands.append(stem[:-1] + ext)
    cands += [stem + "E" + ext, stem + "_en" + ext, stem + "_E" + ext]
    for c in cands:
        p = os.path.join(d, c)
        if os.path.exists(p) and os.path.abspath(p) != tlk_path:
            return p, "文件名启发（%s）" % c
    reasons.append("文件名启发没匹配上")

    try:                                                     # ③ 内容探测（限流）
        others = []
        for fn in os.listdir(d):
            if not fn.lower().endswith(".tlk"):
                continue
            p = os.path.join(d, fn)
            if os.path.abspath(p) == tlk_path:
                continue
            others.append((os.path.getmtime(p), p))
        others.sort(reverse=True)
        probe = others[:probe_limit]
        best, best_n = None, 0
        for _mt, p in probe:
            try:
                ids = _idx_of(p)
                # ★ 必须确认它**真的是英文源**：否则会把"另一个中文版"当英文源，
                #   于是拿中文去匹配英文术语，术语条永远显示"无命中"（实测踩过）。
                if _ascii_ratio(p, ids) > 0.30:
                    continue
                shared = len(cur & ids)
            except Exception:                                # noqa: BLE001
                continue
            if shared > best_n:
                best, best_n = p, shared
        if best:
            return best, "内容探测（共享 strref %d；只探了最近 %d 个）" % (best_n, len(probe))
        reasons.append("内容探测无候选（只看了最近 %d 个）" % len(probe))
    except Exception as e:                                   # noqa: BLE001
        reasons.append("内容探测失败 %r" % (e,))

    return None, "；".join(reasons)
