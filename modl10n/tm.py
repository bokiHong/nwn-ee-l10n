#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""翻译记忆（Translation Memory）：SQLite 存储，跨 mod 复用。

为什么从 JSON 换成 SQLite：
  * 多个 mod 共用一个库，天然复用；逐条写入不用重写整个文件（JSON 每次全量落盘，条目上万后很慢）。
  * 可以标记 manual（人工修订）并让机器翻译永不覆盖它。
  * 可以记录来源 mod、时间，便于溯源与冲突排查。

key = sha256(英文原文 UTF-8) 前 32 位 —— 同一句英文永远映射到同一句中文。
"""
import os
import re
import json
import sqlite3
import hashlib
import datetime
import threading

KEY_RE = re.compile(r"^[0-9a-f]{32}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tm (
    key      TEXT PRIMARY KEY,
    src      TEXT NOT NULL,
    dst      TEXT NOT NULL,
    src_mod  TEXT,
    status   TEXT NOT NULL DEFAULT 'auto',   -- auto | manual | rejected
    note     TEXT,
    updated  TEXT
);
CREATE INDEX IF NOT EXISTS idx_tm_src ON tm(src);
CREATE TABLE IF NOT EXISTS mods (
    name     TEXT PRIMARY KEY,
    path     TEXT,
    units    INTEGER,
    new_tr   INTEGER,
    reused   INTEGER,
    updated  TEXT
);
"""


def key_of(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class TM(object):
    def __init__(self, path, create=True):
        self.path = path
        if not create and not os.path.exists(path):
            raise SystemExit("翻译记忆不存在：%s" % path)
        # check_same_thread=False + 自带锁：翻译是多线程的，sqlite 连接默认不允许跨线程使用
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self._lk = threading.Lock()
        self.db.executescript(SCHEMA)
        self.db.commit()
        self._pending = 0

    # ---------------------------------------------------------------- 读写
    def get(self, key):
        row = self.db.execute("SELECT dst FROM tm WHERE key=? AND status!='rejected'",
                              (key,)).fetchone()
        return row[0] if row else None

    def get_many(self, keys):
        """返回 {key: 译文}（不含 rejected）。注意：包含 status='fallback' 的条目。"""
        out = {}
        keys = list(keys)
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            q = "SELECT key,dst FROM tm WHERE status!='rejected' AND key IN (%s)" \
                % ",".join("?" * len(chunk))
            for k, d in self.db.execute(q, chunk):
                out[k] = d
        return out

    def put(self, key, src, dst, src_mod=None, status="auto", overwrite=False):
        """写入。manual 条目不会被 auto 覆盖。线程安全。"""
        with self._lk:
            row = self.db.execute("SELECT status, dst FROM tm WHERE key=?", (key,)).fetchone()
            if row:
                if row[0] == "manual" and status == "auto":
                    return False
                if not overwrite and row[0] == status == "auto" and row[1] == dst:
                    return False
            self.db.execute(
                "INSERT INTO tm(key,src,dst,src_mod,status,updated) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET src=excluded.src, dst=excluded.dst, "
                "src_mod=COALESCE(tm.src_mod, excluded.src_mod), status=excluded.status, "
                "updated=excluded.updated",
                (key, src, dst, src_mod, status, _now()))
            self._pending += 1
            if self._pending >= 50:
                self.db.commit()
                self._pending = 0
            return True

    def put_many(self, rows, status="auto"):
        n = 0
        for key, src, dst, mod in rows:
            if self.put(key, src, dst, mod, status):
                n += 1
        self.commit()
        return n

    def commit(self):
        self.db.commit()
        self._pending = 0

    def mark_manual(self, key, dst=None):
        if dst is not None:
            self.db.execute("UPDATE tm SET dst=?, status='manual', updated=? WHERE key=?",
                            (dst, _now(), key))
        else:
            self.db.execute("UPDATE tm SET status='manual', updated=? WHERE key=?", (_now(), key))
        self.db.commit()

    def reject(self, key):
        self.db.execute("UPDATE tm SET status='rejected', updated=? WHERE key=?", (_now(), key))
        self.db.commit()

    # ---------------------------------------------------------------- 统计/导入导出
    def fallback_keys(self, keys=None):
        """返回所有"回退原文"的 key（这些需要下次重试；而正确的空操作不该重试）。"""
        if keys is None:
            return {k for (k,) in self.db.execute("SELECT key FROM tm WHERE status='fallback'")}
        out = set()
        keys = list(keys)
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            q = "SELECT key FROM tm WHERE status='fallback' AND key IN (%s)" % ",".join("?" * len(chunk))
            out |= {k for (k,) in self.db.execute(q, chunk)}
        return out

    def stats(self):
        c = self.db.execute("SELECT status, COUNT(*) FROM tm GROUP BY status").fetchall()
        return dict(c)

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM tm").fetchone()[0]

    def record_mod(self, name, path, units, new_tr, reused):
        self.db.execute(
            "INSERT INTO mods(name,path,units,new_tr,reused,updated) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET units=excluded.units, new_tr=excluded.new_tr, "
            "reused=excluded.reused, updated=excluded.updated",
            (name, path, units, new_tr, reused, _now()))
        self.db.commit()

    def import_json(self, json_path, src_lookup=None, src_mod=None):
        """导入旧的 sf_cache.json。src_lookup: {key: 英文原文}；缺省时 src 存空串。"""
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        rows = []
        for k, v in data.items():
            if not KEY_RE.match(k):
                continue
            src = (src_lookup or {}).get(k, "")
            rows.append((k, src, v, src_mod))
        return self.put_many(rows)

    def export_json(self, path):
        data = {k: d for k, d in self.db.execute("SELECT key,dst FROM tm WHERE status!='rejected'")}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return len(data)

    def iter_all(self, status=None):
        q = "SELECT key,src,dst,status FROM tm"
        if status:
            q += " WHERE status=?"
            yield from self.db.execute(q, (status,))
        else:
            yield from self.db.execute(q)
