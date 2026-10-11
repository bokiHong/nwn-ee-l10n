#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UnitRepo —— 单元（位置层）的访问。

Sprint 1 只实现**只读**查询：此刻 `unit` 表是空的（Sprint 2 才落库），
所以本类的验收标准是"空表返回 0、接口形状正确"。
写入（`upsert_many`）在 Sprint 2 随 `extract_service` 一起落地。

给 GUI 用的三个现成查询（对应界面上要能"看到 + 操作"的东西）：
    list_todo()      待办清单（未译 / 源文变了）
    stats()          按状态计数，用于顶部标签
    by_res()         按资源聚合，用于资源名筛选
"""
from dataclasses import fields

from ..db import now
from ..model import StringUnit

# 列名从数据类生成，保证"模型改了、SQL 跟着改"，不会漏字段
COLS = [f.name for f in fields(StringUnit)]
INSERT_SQL = ("INSERT INTO unit(%s) VALUES(%s) ON CONFLICT(uid) DO UPDATE SET %s" % (
    ",".join(COLS), ",".join("?" * len(COLS)),
    ",".join("%s=excluded.%s" % (c, c) for c in COLS if c != "uid")))


class UnitRepo(object):
    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------ 计数/统计
    def count(self, project=None, container_id=None):
        where, params = [], []
        if project:
            where.append("project=?"); params.append(project)
        if container_id:
            where.append("container_id=?"); params.append(container_id)
        sql = "SELECT COUNT(*) FROM unit"
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self.db.execute(sql, params).fetchone()[0]

    def stats(self, project=None, container_id=None):
        """{"new": n, "kept": n, ...}

        按容器过滤比按项目过滤更常用于验收 —— 同一个 project 下可以有多个
        容器（Ch1/Ch2/…），各自的进度不同，混在一起看不出问题。
        """
        where, params = [], []
        if project:
            where.append("project=?"); params.append(project)
        if container_id:
            where.append("container_id=?"); params.append(container_id)
        sql = "SELECT status, COUNT(*) FROM unit"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY status"
        return {r[0]: r[1] for r in self.db.execute(sql, params)}

    def unique_texts(self, container_id=None, project=None):
        """去重后的文本条数（按 norm 后的 src_key 计）。"""
        where, params = [], []
        if container_id:
            where.append("container_id=?"); params.append(container_id)
        if project:
            where.append("project=?"); params.append(project)
        sql = "SELECT COUNT(DISTINCT src_key) FROM unit"
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self.db.execute(sql, params).fetchone()[0]

    def by_res(self, project=None, limit=None):
        """[(resref, ext, 单元数, 已译数)] —— GUI 的资源筛选列表。"""
        sql = ("SELECT u.resref, u.ext, COUNT(*) AS total, "
               "SUM(CASE WHEN t.key IS NOT NULL THEN 1 ELSE 0 END) AS done "
               "FROM unit u LEFT JOIN tm t ON t.key=u.src_key AND t.status!='rejected'")
        params = []
        if project:
            sql += " WHERE u.project=?"
            params.append(project)
        sql += " GROUP BY u.resref, u.ext ORDER BY total DESC"
        if limit:
            sql += " LIMIT ?"; params.append(limit)
        return self.db.execute(sql, params).fetchall()

    # ------------------------------------------------------------ 列表
    def list(self, project=None, status=None, resref=None, kind=None,
             storage=None, limit=None, offset=0):
        where, params = [], []
        if project: where.append("project=?"); params.append(project)
        if status:  where.append("status=?");  params.append(status)
        if resref:  where.append("resref LIKE ?"); params.append("%%%s%%" % resref)
        if kind:    where.append("kind=?");    params.append(kind)
        if storage: where.append("storage=?"); params.append(storage)
        sql = "SELECT * FROM unit"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY resref, rowid"
        sql += " LIMIT ? OFFSET ?"
        params += [limit or -1, offset]
        return [StringUnit.from_row(r) for r in self.db.execute(sql, params)]

    def list_todo(self, project=None, limit=None, offset=0):
        """待办：还没译文的（视图 v_unit_todo）。"""
        sql = "SELECT * FROM unit WHERE uid IN (SELECT uid FROM v_unit_todo WHERE has_dst=0"
        params = []
        if project:
            sql += " AND project=?"; params.append(project)
        sql += ") ORDER BY resref, rowid"
        if limit:
            sql += " LIMIT ? OFFSET ?"; params += [limit, offset]
        return [StringUnit.from_row(r) for r in self.db.execute(sql, params)]

    def search(self, q, project=None, limit=500):
        where = ["(src_text LIKE ? OR field_path LIKE ? OR resref LIKE ?)"]
        params = ["%%%s%%" % q] * 3
        if project:
            where.append("project=?"); params.append(project)
        params.append(limit)
        return [StringUnit.from_row(r) for r in self.db.execute(
            "SELECT * FROM unit WHERE %s ORDER BY resref, rowid LIMIT ?" % " AND ".join(where),
            params)]

    def get(self, uid):
        r = self.db.execute("SELECT * FROM unit WHERE uid=?", (uid,)).fetchone()
        return StringUnit.from_row(r) if r else None

    def by_key(self, src_key, project=None):
        """同一句英文出现在哪些位置 —— 这就是"改一处、动多文件"的影响面。"""
        sql = "SELECT * FROM unit WHERE src_key=?"
        params = [src_key]
        if project:
            sql += " AND project=?"; params.append(project)
        return [StringUnit.from_row(r) for r in self.db.execute(sql, params)]

    def violations(self, project=None, limit=None):
        """术语违规清单（视图 v_term_violation）—— 一条 SQL 查 TLK 与 MOD 全库。"""
        sql = "SELECT * FROM v_term_violation"
        params = []
        if project:
            sql += " WHERE project=?"; params.append(project)
        if limit:
            sql += " LIMIT ?"; params.append(limit)
        return self.db.execute(sql, params).fetchall()

    # ============================================================ 写入
    def existing(self, container_id):
        """{uid: (src_key, first_seen)} —— 一次性取出，供 upsert 判状态。"""
        return {r[0]: (r[1], r[2]) for r in self.db.execute(
            "SELECT uid, src_key, first_seen FROM unit WHERE container_id=?", (container_id,))}

    def upsert_many(self, units, existing=None):
        """按 uid upsert，`status` 由 `src_key` 是否变化判定。

        这是 Sprint 3 `diff` 的基础，也是"第二次跑全都 kept"（幂等）的保证：
            new          库里没有这个 uid
            kept         有，且 src_key 没变
            src_changed  有，但源文变了（译文可能作废，需要重译/复核）
        `first_seen` 在冲突时保留旧值（不会被覆盖）。

        返回 {"new": n, "kept": n, "src_changed": n}。
        """
        units = list(units)
        if existing is None:
            existing = {}
            for cid in {u.container_id for u in units}:
                existing.update(self.existing(cid))
        ts = now()
        stats = {"new": 0, "kept": 0, "src_changed": 0}
        rows = []
        for u in units:
            prev = existing.get(u.uid)
            if prev is None:
                u.status, stats["new"] = "new", stats["new"] + 1
                u.first_seen = u.first_seen or ts
            elif prev[0] != u.src_key:
                u.status, stats["src_changed"] = "src_changed", stats["src_changed"] + 1
                u.first_seen = prev[1] or ts
            else:
                u.status, stats["kept"] = "kept", stats["kept"] + 1
                u.first_seen = prev[1] or ts
            u.last_seen = ts
            rows.append([getattr(u, c) for c in COLS])
        if rows:
            self.db.executemany(INSERT_SQL, rows)
        self.db.commit()
        return stats

    def mark_gone(self, container_id, keep_uids):
        """把本次没抽到的旧单元标记为 gone（不删行 —— 保留历史）。返回条数。"""
        keep = set(keep_uids)
        ts = now()
        old = [r[0] for r in self.db.execute(
            "SELECT uid FROM unit WHERE container_id=?", (container_id,))]
        gone = [u for u in old if u not in keep]
        if gone:
            self.db.executemany(
                "UPDATE unit SET status='gone', last_seen=? WHERE uid=?",
                [(ts, u) for u in gone])
            self.db.commit()
        return len(gone)

    def delete_container(self, container_id):
        """删掉某容器的全部单元（回滚/重建测试用）。"""
        n = self.db.execute("DELETE FROM unit WHERE container_id=?", (container_id,)).rowcount
        self.db.commit()
        return n
