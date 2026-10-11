#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ContainerRepo / ResourceRepo —— 容器（物理文件）与资源（resref+ext）的读写。

`scan` 步骤的落库目标。规则：

  * 容器按 `UNIQUE(project, role, path)` upsert，同一文件反复扫描不产生重复行；
  * 资源按 `rid = sha1(container_id|resref|ext)` upsert，`change_state`
    由 sha256 对比得出（new / unchanged / modified）；
  * 本次扫描里**消失**的资源标记为 `removed`（不删行 —— 保留历史，便于排查）。
"""
import hashlib
import os

from ..db import now
from ..model import Resource


class ContainerRepo(object):
    def __init__(self, db):
        self.db = db

    def upsert(self, project, role, path, fmt, size=None, sha256=None, mtime=None):
        """插入或更新一个容器，返回它的 id。"""
        path = os.path.abspath(path)
        self.db.execute(
            "INSERT INTO container(project,role,path,fmt,size,sha256,mtime,scanned) "
            "VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(project,role,path) DO UPDATE SET fmt=excluded.fmt, "
            "size=excluded.size, sha256=excluded.sha256, mtime=excluded.mtime, "
            "scanned=excluded.scanned",
            (project, role, path, fmt, size, sha256, mtime, now()))
        self.db.commit()
        return self.db.execute(
            "SELECT id FROM container WHERE project=? AND role=? AND path=?",
            (project, role, path)).fetchone()[0]

    def get(self, project, role, path):
        r = self.db.execute(
            "SELECT * FROM container WHERE project=? AND role=? AND path=?",
            (project, role, os.path.abspath(path))).fetchone()
        return dict(r) if r else None

    def by_id(self, cid):
        r = self.db.execute("SELECT * FROM container WHERE id=?", (cid,)).fetchone()
        return dict(r) if r else None

    def list(self, project=None):
        if project:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM container WHERE project=? ORDER BY id", (project,))]
        return [dict(r) for r in self.db.execute("SELECT * FROM container ORDER BY id")]

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM container").fetchone()[0]


class ResourceRepo(object):
    def __init__(self, db):
        self.db = db

    def upsert_many(self, container_id, rows):
        """rows = [(resref, ext, size, sha256, gff_type)]。

        返回 {"new": n, "unchanged": n, "modified": n, "removed": n}。
        """
        old = {(r["resref"], r["ext"]): r["sha256"] for r in self.db.execute(
            "SELECT resref,ext,sha256 FROM resource WHERE container_id=?", (container_id,))}
        ts = now()
        stats = {"new": 0, "unchanged": 0, "modified": 0, "removed": 0}
        seen = set()
        for resref, ext, size, sha256, gff_type in rows:
            resref, ext = (resref or "").lower(), (ext or "").lower()
            key = (resref, ext)
            seen.add(key)
            prev = old.get(key)
            if prev is None:
                state = "new"
            elif sha256 and prev != sha256:
                state = "modified"
            else:
                state = "unchanged"
            stats[state] += 1
            self.db.execute(
                "INSERT INTO resource(rid,container_id,resref,ext,size,sha256,gff_type,"
                "change_state,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(rid) DO UPDATE SET size=excluded.size, sha256=excluded.sha256, "
                "gff_type=excluded.gff_type, change_state=excluded.change_state, "
                "last_seen=excluded.last_seen",
                (Resource.make_rid(container_id, resref, ext), container_id, resref, ext,
                 size, sha256, gff_type, state, ts, ts))
        # 本次没出现的老资源 -> removed（保留行，便于排查）
        for (resref, ext) in old:
            if (resref, ext) not in seen:
                self.db.execute(
                    "UPDATE resource SET change_state='removed', last_seen=? "
                    "WHERE container_id=? AND resref=? AND ext=?",
                    (ts, container_id, resref, ext))
                stats["removed"] += 1
        self.db.commit()
        return stats

    def count(self, container_id=None):
        if container_id:
            return self.db.execute("SELECT COUNT(*) FROM resource WHERE container_id=?",
                                   (container_id,)).fetchone()[0]
        return self.db.execute("SELECT COUNT(*) FROM resource").fetchone()[0]

    def list(self, container_id, limit=None, offset=0):
        sql = ("SELECT * FROM resource WHERE container_id=? AND change_state!='removed' "
               "ORDER BY ext, resref LIMIT ? OFFSET ?")
        return [Resource.from_row(r) for r in
                self.db.execute(sql, (container_id, limit or -1, offset))]

    def by_ext(self, container_id):
        """[("nss", 4974), ("dlg", 210), ...] —— GUI 的资源类型筛选就靠它。"""
        return self.db.execute(
            "SELECT ext, COUNT(*) FROM resource WHERE container_id=? AND change_state!='removed' "
            "GROUP BY ext ORDER BY 2 DESC", (container_id,)).fetchall()

    def refresh_unit_counts(self, container_id=None):
        """把 `resource.units_total / units_done` 按 `unit` 表现状重算（一次性 SQL）。"""
        where, params = "", []
        if container_id:
            where = "WHERE container_id=?"
            params = [container_id]
        self.db.execute(
            "UPDATE resource SET units_total=0, units_done=0 %s" % where, params)
        self.db.execute(
            "UPDATE resource SET units_total = COALESCE(("
            "  SELECT COUNT(*) FROM unit u WHERE u.container_id=resource.container_id "
            "  AND u.resref=resource.resref AND u.ext=resource.ext),0), "
            "units_done = COALESCE(("
            "  SELECT COUNT(*) FROM unit u JOIN tm t ON t.key=u.src_key AND t.status!='rejected' "
            "  WHERE u.container_id=resource.container_id "
            "  AND u.resref=resource.resref AND u.ext=resource.ext),0) %s" % where, params)
        self.db.commit()


def sha256_of(data):
    """字节 -> 64 位十六进制（容器与资源都用它做"变没变"的判据）。"""
    return hashlib.sha256(data).hexdigest()
