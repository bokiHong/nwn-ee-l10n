#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CostService —— **翻译花费账本**（用户很在意"怎样让翻译花钱更少" ✓）。

为什么不复用 `run` 表：它只有 `(id, project, kind, started, finished, args)`，
**没有 token / 金额列** ✗。这里另建一张（动态建，无需迁移 ✓）：

    cost_log(id, ts, project, kind, items, tokens_in, tokens_out,
             price_in, price_out, cost, note)

用法：
    from l10n.services import cost_service
    cost_service.record(db, "prc8", "tlk-translate", items=1200,
                        tokens_in=320000, tokens_out=180000,
                        price_in=1.0, price_out=2.0, note="Ch3 前 1200 条")
    print(cost_service.summary(db))          # 累计 + 按天 + 按项目
"""
import time

_DDL = ("CREATE TABLE IF NOT EXISTS cost_log("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, project TEXT, kind TEXT, "
        "items INTEGER, tokens_in INTEGER, tokens_out INTEGER, "
        "price_in REAL, price_out REAL, cost REAL, note TEXT)")


def _ensure(db):
    db.execute(_DDL)
    db.commit()


def record(db, project, kind, items=0, tokens_in=0, tokens_out=0,
           price_in=0.0, price_out=0.0, note=None, dry=False):
    """记一笔（单价单位：元/百万 token ✓）。`dry=True` 只算不写（用于"预估"）✓"""
    cost = (tokens_in or 0) / 1e6 * (price_in or 0) + (tokens_out or 0) / 1e6 * (price_out or 0)
    if dry:
        return {"cost": cost, "items": items, "written": False}
    _ensure(db)
    cur = db.execute("INSERT INTO cost_log(ts, project, kind, items, tokens_in, tokens_out, "
                     "price_in, price_out, cost, note) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (time.strftime("%Y-%m-%d %H:%M:%S"), project, kind, items, tokens_in,
                      tokens_out, price_in, price_out, cost, note))
    db.commit()
    return {"cost": cost, "items": items, "written": True, "id": cur.lastrowid}


def summary(db, project=None, limit=12):
    """累计 + 按天 + 按项目（给界面用）。"""
    _ensure(db)
    where, params = "", []
    if project:
        where, params = " WHERE project=?", [project]
    tot = db.execute("SELECT COUNT(*), COALESCE(SUM(items),0), COALESCE(SUM(cost),0) "
                     "FROM cost_log" + where, params).fetchone()
    days = list(db.execute("SELECT substr(ts,1,10) d, COUNT(*), COALESCE(SUM(items),0), "
                           "COALESCE(SUM(cost),0) FROM cost_log" + where +
                           " GROUP BY d ORDER BY d DESC LIMIT ?", params + [limit]))
    projs = list(db.execute("SELECT project, COUNT(*), COALESCE(SUM(items),0), "
                            "COALESCE(SUM(cost),0) FROM cost_log GROUP BY project "
                            "ORDER BY 4 DESC LIMIT ?", [limit]))
    return {"runs": tot[0], "items": tot[1], "cost": tot[2],
            "by_day": [tuple(d) for d in days], "by_project": [tuple(p) for p in projs]}


def human(sum_):
    """一句话人话。"""
    return ("花费账本：%d 次翻译、共 %d 条、累计 %.2f 元"
            % (sum_["runs"], sum_["items"], sum_["cost"]))
