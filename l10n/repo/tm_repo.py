#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TmRepo —— 翻译记忆（`tm` 表）的访问。

Sprint 1 收编了 `tlk tool.py` 里两处绕过 TM 封装的裸 SQL；
**Sprint 6 补上 `search_texts` / `counterpart` / `put_manual`** —— 前两个彻底收编那两处
裸 SQL，第三个修掉"保存到记忆库"那个从来没成功过的按钮。

**关于写 `tm`**：`put_manual()` 用**本连接**直接写（不开第二个连接，避免 WAL 写锁竞争），
SQL 与 `modl10n.tm.TM.put` 的语义逐条对齐；**机器翻译的批量写入仍走 `TM`**，
不在这里重造第二套。
"""
from ..db import now
from ..keys import keys_for, src_key as _src_key


class TmRepo(object):
    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------ 统计
    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM tm").fetchone()[0]

    def stats(self):
        """{"auto": 41063, "manual": 2879, ...}"""
        return {r[0]: r[1] for r in
                self.db.execute("SELECT status, COUNT(*) FROM tm GROUP BY status")}

    def count_by_mod(self, limit=20):
        return self.db.execute(
            "SELECT src_mod, COUNT(*) FROM tm GROUP BY src_mod "
            "ORDER BY 2 DESC LIMIT ?", (limit,)).fetchall()

    # ------------------------------------------------------------ 查译文
    def get(self, key):
        r = self.db.execute(
            "SELECT dst FROM tm WHERE key=? AND status!='rejected'", (key,)).fetchone()
        return r[0] if r else None

    # ------------------------------------------------------------ ★ 双 key 兜底
    def lookup_one(self, text):
        """一句原文 -> 译文（两个 key 都试，norm 优先）。查不到返回 None。"""
        for k in keys_for(text):
            v = self.get(k)
            if v is not None:
                return v
        return None

    def lookup_many(self, texts):
        """{原文: 译文} —— 批量版，同样是双 key 兜底。

        只做 1 次批量 SQL（把所有候选 key 一起查），所以几千条也很快。
        查不到的原文不会出现在返回的 dict 里。
        """
        texts = list(dict.fromkeys(texts))          # 去重且保序
        cand = {}                                   # key -> 原文（norm 的 key 先入）
        for t in texts:
            for k in keys_for(t):
                cand.setdefault(k, t)
        if not cand:
            return {}
        got = self.get_many(cand.keys())            # {key: dst}
        out = {}
        for k, t in cand.items():                   # 按插入顺序 = norm 优先
            if k in got and t not in out:
                out[t] = got[k]
        return out

    def get_many(self, keys):
        """{key: 译文}（不含 rejected）。分批查询，避免 SQL 变量数上限。"""
        out, keys = {}, [k for k in keys]
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            q = ("SELECT key,dst FROM tm WHERE status!='rejected' AND key IN (%s)"
                 % ",".join("?" * len(chunk)))
            out.update(dict(self.db.execute(q, chunk).fetchall()))
        return out

    def fallback_keys(self, keys=None):
        """上次"回退原文"、需要重试的 key 集合。keys=None 时返回全部。"""
        if keys is None:
            return {r[0] for r in
                    self.db.execute("SELECT key FROM tm WHERE status='fallback'")}
        out, keys = set(), list(keys)
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            q = ("SELECT key FROM tm WHERE status='fallback' AND key IN (%s)"
                 % ",".join("?" * len(chunk)))
            out |= {r[0] for r in self.db.execute(q, chunk)}
        return out

    # ------------------------------------------------------------ 搜索（收编裸 SQL #1）
    def search(self, q, limit=500, only_dst=False, exclude_rejected=False):
        """按原文/译文模糊搜索。

        行为与 `tlk tool.py:798-800` 一致（默认**不**排除 rejected、原文与译文都搜），
        新增的两个开关只是给以后用，默认值保证与旧实现等价。
        """
        like = "%%%s%%" % q
        where, params = [], []
        if only_dst:
            where.append("dst LIKE ?"); params.append(like)
        else:
            where.append("(src LIKE ? OR dst LIKE ?)"); params += [like, like]
        if exclude_rejected:
            where.append("status!='rejected'")
        params.append(limit)
        sql = ("SELECT key,src,dst,src_mod,status FROM tm WHERE %s ORDER BY updated DESC LIMIT ?"
               % " AND ".join(where))
        return self.db.execute(sql, params).fetchall()

    def zh_to_en_map(self):
        """{中文译文: 英文原文} —— 收编 `tlk tool.py:832-833` 的反向缓存。

        同一条中文被多条英文译出时，后者覆盖前者（与旧实现一致）。
        ⚠ 这是**全表 44k 行**扫描，调用方建一次就缓存住，别每次调用重建。
        """
        out = {}
        for src, dst in self.db.execute("SELECT src,dst FROM tm WHERE status!='rejected'"):
            out[dst] = src
        return out

    # ------------------------------------------------------------ 收编裸 SQL（Sprint 6）
    def search_texts(self, q, limit=None):
        """搜"命中的文本集合"（原文命中或译文命中都算）—— 收编 `tlk tool.py:798-800`。

        ★ `limit=None` 表示**不截断**。旧裸 SQL 没有 LIMIT，而旁边的 `search()`
        默认 500 条 —— 界面搜索必须走这个，否则 44k 行的库会静默少显示。

        返回 `{被 strip 过的命中文本}`（旧实现也是 strip 后比对）。
        """
        like = "%%%s%%" % q
        sql = "SELECT src, dst FROM tm WHERE src LIKE ? OR dst LIKE ?"
        params = [like, like]
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        out = set()
        for src, dst in self.db.execute(sql, params):
            if src:
                out.add(src.strip())
            if dst:
                out.add(dst.strip())
        return out

    def counterpart(self, text, dst2src=None):
        """一句文本的"对照"：先按原文查译文（**双 key 兜底**），再按译文反查原文。

        收编 `tlk tool.py:857`（`tm.get(单key)`）+ `:861`（`_dst2src.get`）两处。
        比旧实现多一层收益：`lookup_one` 会两个 key 都试，能多查出一批旧实现查不到的。

        `dst2src` 请把缓存的 `zh_to_en_map()` 传进来（44k 行，别每次重建）。
        """
        v = self.lookup_one(text)
        if v is not None:
            return v
        if dst2src is None:
            dst2src = self.zh_to_en_map()
        return dst2src.get(text)

    def put_manual(self, rows, src_mod=None, note=None):
        """写**手工译文**（`status='manual'`，机器永不覆盖）。

        `rows` = `[(src, dst)]` 或 `[(src, dst, src_mod)]`；返回写入条数。

        **为什么不调 `modl10n.tm.TM`**：那要开第二个连接（WAL 下写写会争锁），
        而且 `TM.put` **不接受 `note`** —— 那正是一个历史 bug 的根因
        （`tlk tool.py:936` 传了 `note=`，异常被 except 吞掉，"保存到记忆库"从未成功）。
        这里用本连接写，SQL 与 `TM.put` 的语义逐条对齐。
        """
        ts = now()
        data = []
        for row in rows:
            src, dst = row[0], row[1]
            mod = row[2] if len(row) > 2 else src_mod
            data.append((_src_key(src), src, dst, mod, "manual", note, ts))
        if not data:
            return 0
        self.db.executemany(
            "INSERT INTO tm(key,src,dst,src_mod,status,note,updated) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
            "src=excluded.src, dst=excluded.dst, "
            "src_mod=COALESCE(tm.src_mod, excluded.src_mod), "
            "status='manual', note=excluded.note, updated=excluded.updated", data)
        self.db.commit()
        return len(data)

    # ------------------------------------------------------------ 按【预计算 key】写
    def mark_manual(self, key, dst, src=None):
        """把一条译文标成**手工译文**（`status='manual'`，机器永不覆盖）。

        接受**调用方算好的 key**（`ModDoc` 的 key 是 raw 的，不能在这里重新按 norm 算，
        否则会"从 raw 行读出、往 norm 行写入"）。

        ★ 与 `modl10n.tm.TM.mark_manual` 有一处**刻意不同**：`TM.mark_manual` 是纯
        `UPDATE`，库里没有这个 key 时**静默无效** —— 于是"给未译条目填上译文再点保存"
        会提示成功、实际什么都没存（2026-10-08 实测复现）。这里加了 upsert 分支：
        UPDATE 影响 0 行且给了 `src` 时，就 INSERT 一条新的 manual 行。
        """
        cur = self.db.execute(
            "UPDATE tm SET dst=?, status='manual', updated=? WHERE key=?", (dst, now(), key))
        if cur.rowcount == 0 and src is not None:
            self.db.execute(
                "INSERT INTO tm(key,src,dst,status,updated) VALUES(?,?,?,'manual',?) "
                "ON CONFLICT(key) DO UPDATE SET src=excluded.src, dst=excluded.dst, "
                "status='manual', updated=excluded.updated",
                (key, src, dst, now()))
        self.db.commit()
        return cur.rowcount if cur.rowcount else (1 if src is not None else 0)

    def put_fallback(self, key, src, dst):
        """写入并标 `status='fallback'`（下次打包允许时重译）。

        与 `TM.put(key, src, dst, status="fallback", overwrite=True)` 语义对齐。
        """
        self.db.execute(
            "INSERT INTO tm(key,src,dst,status,updated) VALUES(?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET src=excluded.src, dst=excluded.dst, "
            "status='fallback', updated=excluded.updated",
            (key, src, dst, "fallback", now()))
        self.db.commit()
        return True

    # ------------------------------------------------------------ key
    @staticmethod
    def key_of(text):
        """统一入口（见 `l10n\\keys.py`）——不要在别处另算一份。

        ⚠ `ModDoc` 刻意**不用**它：那边历史上用的是 `modl10n.tm.key_of`（**raw、不 norm**），
        换成它会让"从 raw 行读出、往 norm 行写入"。要用 `keys.src_key(t, normalize=False)`。
        """
        return _src_key(text)

    def rows_iter(self, status=None):
        """流式遍历 (key, src, dst, status)，避免一次性载入 44k 条。"""
        sql = "SELECT key,src,dst,status FROM tm"
        if status:
            yield from self.db.execute(sql + " WHERE status=?", (status,))
        else:
            yield from self.db.execute(sql)
