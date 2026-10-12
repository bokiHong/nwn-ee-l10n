#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UndoService —— **批量写库的可撤销快照**（只存被改的行，不复制整个库 ✓）。

为什么需要它：三个工具都是"直接写库"，回滚只能靠翻备份文件（一份 80 MB ✗）。
而批量改写最容易出错（2026-10-11 一天就误伤过 3 次：`抗抗解除缓冲`、`鳞族人人`、
`Suppress` 吃掉动词句 ✗），所以每次批量写之前要把**被改行的旧值**留下来 ✓。

用法（配合 `rename_service.apply`）：

    bid = undo_service.begin(db, "术语统一：幽影 -> 阴影")
    ... 逐行改：undo_service.record(db, bid, rowid, old_dst) ...
    undo_service.finish(db, bid)          # 落盘
    ...
    undo_service.undo_last(db)            # 撤销最近一次

表（动态建，无需迁移）：
    undo_batch(id INTEGER PK, ts TEXT, label TEXT, n INTEGER, payload TEXT)
    payload = [[rowid, old_dst], ...]
"""
import json
import time

_DDL = ("CREATE TABLE IF NOT EXISTS undo_batch("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, label TEXT, n INTEGER, payload TEXT)")


def _ensure(db):
    db.execute(_DDL)
    db.commit()


def begin(db, label):
    """开一批（返回 batch id）。旧值先攒在内存里，`finish()` 才落盘 ✓"""
    _ensure(db)
    cur = db.execute("INSERT INTO undo_batch(ts, label, n, payload) VALUES(?,?,?,?)",
                     (time.strftime("%Y-%m-%d %H:%M:%S"), label, 0, None))
    db.commit()
    _MEM[cur.lastrowid] = []
    return cur.lastrowid


_MEM = {}


def record(db, batch_id, rowid, old_dst):
    """记一行的旧值（同一行只记第一次 ✓ —— 撤销要的是"原始状态"）。"""
    rows = _MEM.setdefault(batch_id, [])
    for r in rows:
        if r[0] == rowid:
            return
    rows.append([rowid, old_dst])


def finish(db, batch_id):
    """落盘；返回这批改了多少行。空批次直接删掉（不留垃圾 ✓）。"""
    rows = _MEM.pop(batch_id, [])
    if not rows:
        db.execute("DELETE FROM undo_batch WHERE id=?", (batch_id,))
        db.commit()
        return 0
    db.execute("UPDATE undo_batch SET n=?, payload=? WHERE id=?",
               (len(rows), json.dumps(rows, ensure_ascii=False), batch_id))
    db.commit()
    return len(rows)


def list_batches(db, limit=10):
    """最近几批（给界面挑）。返回 `[(id, ts, label, n)]`。"""
    _ensure(db)
    return list(db.execute("SELECT id, ts, label, n FROM undo_batch "
                           "WHERE payload IS NOT NULL ORDER BY id DESC LIMIT ?", (limit,)))


def undo(db, batch_id):
    """撤销指定批次：只把 `tm.dst` 写回旧值 ✓（并把这批标记为已撤销，防重复点）。"""
    row = db.execute("SELECT payload, label FROM undo_batch WHERE id=?", (batch_id,)).fetchone()
    if not row or not row[0]:
        return {"ok": False, "n": 0, "error": "这批没有可撤销的快照"}
    rows = json.loads(row[0])
    n = 0
    for rid, old in rows:
        db.execute("UPDATE tm SET dst=?, status='manual' WHERE rowid=?", (old, rid))
        n += 1
    db.execute("UPDATE undo_batch SET payload=NULL, label=label||'（已撤销）' WHERE id=?",
               (batch_id,))
    db.commit()
    return {"ok": True, "n": n, "label": row[1]}


def undo_last(db):
    """撤销最近一批。"""
    b = list_batches(db, 1)
    if not b:
        return {"ok": False, "n": 0, "error": "没有可撤销的批次"}
    return undo(db, b[0][0])
