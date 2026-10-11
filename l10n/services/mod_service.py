#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ModService —— `.mod` 汉化编辑的业务逻辑（**零 tk 引用**）。

从 `mod tool.py` 搬出来的：原来 `ModDoc` 就写在 GUI 文件里，还自带一个
`TM(tm_path)` 连接。现在：

  * 连接由调用方注入（`db=`）或按 `tm_path` 自建 —— **整个 App 只用一个连接**
    （多连接同时跑迁移会撞 `view v_unit_todo already exists`，tlk tool 那边踩过）；
  * 写库走 `TmRepo`（本连接写，不开第二个），并且**逐条记失败、失败的行不清空**。

⚠ **key 语义必须保持 raw**：`ModDoc` 的 key 一直用 `modl10n.tm.key_of()`
（= `sha256(text)[:32]`，**不做 norm**），而 `l10n.keys.src_key` 默认 `normalize=True`。
两者对"首尾有空白的文本"会算出不同 key —— 换错就会"从 raw 行读出、往 norm 行写入"。
所以这里显式用 `src_key(text, normalize=False)`，且**读写都用同一个 key**。
"""
import collections
import os

from ..db import DEF_DB, connect as db_connect
from ..keys import src_key
from ..repo import TmRepo

try:
    from modl10n.core import sfrules as R
    from modl10n.extract import extract_units
except ImportError:                       # pragma: no cover - modl10n 不在路径时
    R = None
    extract_units = None


def nl_of(text):
    """把各种换行统一成 `\\n`，避免从 Windows 编辑器粘贴带进 `\\r`。

    ⚠ 不能用现成的两个替代：`txtio.clean` 会**删掉末尾一个换行**（语义不同）；
    `textops.normalize` 是"写库前的译文清洗"（全角→半角 + NFKD），更不是一回事。
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def raw_key(text):
    """与 `modl10n.tm.key_of` **完全一致**的 raw key（不 norm）。"""
    return src_key(text, normalize=False)


class ModDoc(object):
    """一个 .mod 的可编辑视图：把里面所有可译文本摊平成「不重复文本」列表。

    同一句话在 mod 里出现多次只占一行 —— 改一次，所有出现处一起生效。
    """

    def __init__(self, modpath, db=None, tm_path=None, include_nss=True, progress=None):
        self.modpath = os.path.abspath(modpath)
        self._own_db = False
        if db is None:
            db = db_connect(tm_path or DEF_DB)
            self._own_db = True
        self.db = db
        self.tm = TmRepo(db)
        # 保留旧属性名（界面上要用它显示"记忆库：路径"，也是 CLI 的 tm_path）
        self.tm_path = os.path.abspath(tm_path or DEF_DB)

        def say(msg):
            if progress:
                progress(msg)

        say("正在解析 %s …" % os.path.basename(self.modpath))
        self.erf, units = extract_units(self.modpath, include_nss=include_nss)
        say("正在读取翻译记忆库 …")

        first = {}
        for u in units:
            k = raw_key(u.text)                       # ★ raw，与旧实现逐字一致
            d = first.get(k)
            if d is None:
                label = u.label or ("L%d" % (u.lineno or 0))
                d = first[k] = {"key": k, "src": u.text, "kind": u.kind, "loc": u.fname,
                                "label": label, "count": 0,
                                "where": "%s · %s" % (u.fname, label)}
            d["count"] += 1
        hits = self.tm.get_many(list(first.keys()))
        for k, d in first.items():
            d["dst"] = hits.get(k) or ""
            d["was"] = d["dst"]
        self.items = sorted(first.values(),
                            key=lambda d: ((d["loc"] or "").lower(), d["label"] or ""))
        self.by_key = {d["key"]: d for d in self.items}

        self.n_units = len(units)
        self.n_res = len(self.erf.entries)
        self.n_ncs = sum(1 for e in self.erf.entries if e.ext.lower() == "ncs")
        self.edits = {}        # key -> 尚未保存的新译文
        self.retrans = set()   # key -> 标记待重译（打包时若允许才调 API；GUI 不允许）

    @classmethod
    def from_path(cls, modpath, db_path=None, include_nss=True, progress=None):
        """按路径打开（自建连接）。测试请传临时库路径，别碰真实 `tm.sqlite`。"""
        return cls(modpath, tm_path=db_path, include_nss=include_nss, progress=progress)

    def close(self):
        """只关**自己建的**连接（注入的连接由调用方负责）。"""
        if self._own_db and self.db is not None:
            try:
                self.db.close()
            finally:
                self.db = None

    # ---------- 文本形态转换 ----------
    @staticmethod
    def to_edit(kind, stored):
        """记忆库里的存储形态 -> 编辑器里给人看的形态。"""
        if kind == "gff":
            return R.unescape_gff(stored)
        return stored                     # .nss 是源码字符串字面量，\n 就是源码里的转义

    @staticmethod
    def to_store(kind, edited):
        """编辑器里的形态 -> 记忆库存储形态。"""
        t = nl_of(edited)
        if kind == "gff":
            return R.escape_gff(t)
        return t.replace("\n", R.NL)      # .nss 字面量里不能有真换行，否则编译不过

    def display_src(self, it):
        """英文原文（只读展示用）。"""
        return self.to_edit(it["kind"], it["src"])

    def dst_now(self, it):
        return self.edits.get(it["key"], it["dst"])

    def status_of(self, it):
        if it["key"] in self.edits:
            return "✎已改"
        if it["key"] in self.retrans:
            return "⟳重译"
        d = it["dst"]
        if not d:
            return "未译"
        return "同原文" if d == it["src"] else "已译"

    def set_edit(self, key, text):
        it = self.by_key[key]
        if text == it["dst"]:
            self.edits.pop(key, None)
        else:
            self.edits[key] = text
            self.retrans.discard(key)

    def mark_retrans(self, key, on=True):
        if on:
            self.retrans.add(key)
            self.edits.pop(key, None)
        else:
            self.retrans.discard(key)

    def revert(self, key):
        """撤掉这条的未保存改动。

        旧版是界面**直接** `doc.edits.pop()` / `doc.retrans.discard()`（越界动内部状态），
        现在收进文档自身。
        """
        self.edits.pop(key, None)
        self.retrans.discard(key)

    # ---------- 保存 / 打包 ----------
    def save(self):
        """把未保存的修改写进记忆库（标 `status=manual`，机器永不覆盖）。

        ★ 修掉旧实现的两个丢数据问题：
          ① 旧版不管成功失败都 `commit()` + `edits.clear()`/`retrans.clear()` ——
             写库失败的行在界面上"看起来已保存"、内存改动被丢弃 ✗；
          ② 现在**逐条记失败**，只有真正写成功的行才从 `edits`/`retrans` 移除，
             失败的行留着，用户可以重试（返回 `(成功数, [(key, 原因)])`）。

        ※ 边界：`edits` 与 `retrans` 都空时**直接返回、不做任何 commit**
          （用户特意叮嘱：剥出函数时别把旧有的"空列表直接返回"行为弄丢）。
        """
        if not self.edits and not self.retrans:
            return 0, []
        n, fail = 0, []
        for k, text in list(self.edits.items()):
            it = self.by_key.get(k)
            try:
                # 传 src：库里还没有这个 key 时（给"未译"条目**新填**译文）也能插进去。
                # 旧版 `TM.mark_manual` 是纯 UPDATE，这种情况会静默无效（提示成功却没存）。
                self.tm.mark_manual(k, text, it["src"] if it else None)
            except Exception as e:                      # noqa: BLE001
                fail.append((k, repr(e)))
                continue
            self.edits.pop(k, None)                     # ★ 只有成功才移除
            if it is not None:
                it["dst"] = it["was"] = text
            n += 1
        for k in list(self.retrans):
            it = self.by_key.get(k)
            if it is None:
                self.retrans.discard(k)
                continue
            try:
                self.tm.put_fallback(k, it["src"], it["dst"] or it["src"])
            except Exception as e:                      # noqa: BLE001
                fail.append((k, repr(e)))
                continue
            self.retrans.discard(k)                     # ★ 只有成功才移除
            n += 1
        return n, fail

    def pending(self):
        """下次打包若允许调 API，会去翻译的条数（未译 + 标记重译）。"""
        un = sum(1 for it in self.items if not self.dst_now(it))
        return un + len(self.retrans)

    def stat(self):
        s = collections.Counter(self.status_of(it) for it in self.items)
        return {"资源": self.n_res, "可译文本位置": self.n_units, "不重复文本": len(self.items),
                "已译": s.get("已译", 0), "同原文(保留英文)": s.get("同原文", 0),
                "未译": s.get("未译", 0), "本次已改未存": len(self.edits),
                "已标待重译": len(self.retrans), "记忆库条数": self.tm.count()}


def open_mod_doc(modpath, db=None, db_path=None, include_nss=True, progress=None):
    """门面：打开一个 .mod 得到 `ModDoc`。"""
    return ModDoc(modpath, db=db, tm_path=db_path,
                  include_nss=include_nss, progress=progress)
