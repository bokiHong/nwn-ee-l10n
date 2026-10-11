#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RunRepo —— 流水线运行记录（`run` 表）。

取代散落在各处的 `*.report.json`：每次 scan / extract / diff / plan /
translate / apply / pack 都留一行，事后能回答"上次跑是什么时候、结论是什么"。
"""
import json

from ..db import now


class RunRepo(object):
    def __init__(self, db):
        self.db = db

    def start(self, project, kind, args=None):
        cur = self.db.execute(
            "INSERT INTO run(project,kind,started,args) VALUES(?,?,?,?)",
            (project, kind, now(),
             json.dumps(args, ensure_ascii=False) if args else None))
        self.db.commit()
        return cur.lastrowid

    def finish(self, run_id, ok=True, **stats):
        """写入统计。认识 `run` 表里有的列，其余塞进 summary(JSON)。"""
        known = {"units", "uniq", "reused", "new_tr", "api_calls", "cost_est"}
        cols = {k: v for k, v in stats.items() if k in known}
        rest = {k: v for k, v in stats.items() if k not in known}
        sets, params = ["finished=?", "ok=?"], [now(), 1 if ok else 0]
        for k, v in cols.items():
            sets.append("%s=?" % k); params.append(v)
        if rest:
            sets.append("summary=?"); params.append(json.dumps(rest, ensure_ascii=False))
        params.append(run_id)
        self.db.execute("UPDATE run SET %s WHERE id=?" % ",".join(sets), params)
        self.db.commit()

    def recent(self, project=None, kind=None, limit=10):
        where, params = [], []
        if project:
            where.append("project=?"); params.append(project)
        if kind:
            where.append("kind=?"); params.append(kind)
        sql = "SELECT * FROM run"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.db.execute(sql, params)]

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM run").fetchone()[0]

    # ------------------------------------------------------------ 遗留任务
    def todo(self, project, title, detail=None, **extra):
        """把一件事记成"遗留任务"（`kind='todo'`），暂不处理但留痕。

        用途：发现的数据问题 / 待办的技术债 —— 先记下来，别让它只存在于对话里。
        `extra` 里的已知列（units / cost_est / …）进对应字段，其余进 `summary`(JSON)。
        """
        rid = self.start(project, "todo", {"title": title})
        self.finish(rid, ok=True, title=title, detail=detail, **extra)
        return rid

    def todos(self, project=None):
        return self.recent(project=project, kind="todo", limit=100)
