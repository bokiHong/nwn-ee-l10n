#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DiffService —— 算出"如果现在要翻译，需要动哪些"。**只读，不写任何表。**

这是增量汉化的核心（流水线第 3 步）。它回答四个问题：

    哪些已经有译文？       translated
    哪些完全没有译文？     missing      ← 要花钱的
    哪些上次回退了原文？   fallback     ← 要重试的（`tm.status='fallback'`）
    哪些源文被改过了？     src_changed  ← 译文可能作废，要复核

**★ 双 key 兜底**：判断"有没有译文"时一律走 `keys_for()`（norm 优先、raw 兜底），
不是直接算一个 key 去查。Sprint 2 实测：Ch1 单用 norm key 会漏 51 条已译文本。
"""
from collections import Counter

from ..keys import keys_for


def _rows(db, project=None, container_id=None, include_gone=False):
    where, params = [], []
    if project:
        where.append("u.project=?"); params.append(project)
    if container_id:
        where.append("u.container_id=?"); params.append(container_id)
    if not include_gone:
        where.append("u.status!='gone'")
    w = (" WHERE " + " AND ".join(where)) if where else ""
    return db.execute(
        "SELECT u.uid,u.container_id,u.resref,u.ext,u.kind,u.field_path,u.src_text,"
        "u.src_key,u.status, t.key AS tm_key, t.dst AS dst, t.status AS tm_status "
        "FROM unit u LEFT JOIN tm t ON t.key=u.src_key AND t.status!='rejected'" + w,
        params).fetchall()


def _raw_fallback(db, rows):
    """对"用主 key 没查到"的单元，再用 raw key 查一遍。返回 {uid: (dst, status)}。"""
    need = {}
    for r in rows:
        if r["tm_key"] is None and r["src_text"]:
            ks = keys_for(r["src_text"])
            if len(ks) > 1:                     # 该文本的两种算法结果不同，才需要兜底
                need.setdefault(ks[1], r["uid"])
    if not need:
        return {}, 0
    got = {}
    ks = list(need)
    for i in range(0, len(ks), 500):
        chunk = ks[i:i + 500]
        q = ("SELECT key,dst,status FROM tm WHERE status!='rejected' AND key IN (%s)"
             % ",".join("?" * len(chunk)))
        got.update({r[0]: (r[1], r[2]) for r in db.execute(q, chunk)})
    out = {}
    for k, uid in need.items():
        if k in got:
            out[uid] = got[k]
    return out, len(out)


def diff(db, project=None, container_id=None, sample=20, include_gone=False):
    """返回变更集统计（详见模块 docstring）。不写库。"""
    rows = _rows(db, project, container_id, include_gone)
    fallback_map, n_fallback_hit = _raw_fallback(db, rows)

    units = Counter()
    uniq_seen = {}                    # src_key -> 该文本的归类
    missing_detail = []
    by_res = Counter()
    containers_todo = set()           # 待译内容出现在哪些容器（影响面）
    chars_missing = 0
    n_gone = 0
    by_kind = Counter()

    for r in rows:
        text = r["src_text"] or ""
        dst, tm_status = r["dst"], r["tm_status"]
        if dst is None and r["uid"] in fallback_map:       # ★ raw key 兜底命中
            dst, tm_status = fallback_map[r["uid"]]
        if r["status"] == "gone":
            n_gone += 1
            if not include_gone:
                continue
            units["gone"] += 1
            continue
        if tm_status == "fallback":
            cat = "fallback"
        elif dst is not None:
            cat = "translated"
        else:
            cat = "missing"
        units[cat] += 1
        if r["status"] == "src_changed":
            units["src_changed"] += 1
        by_kind[r["kind"]] += 1
        # 按内容去重：同一句英文只算一次钱
        if r["src_key"] not in uniq_seen:
            uniq_seen[r["src_key"]] = cat
            if cat in ("missing", "fallback"):
                chars_missing += len(text)
                by_res[(r["resref"], r["ext"])] += 1
                containers_todo.add(r["container_id"])
                if len(missing_detail) < sample * 20:
                    missing_detail.append({
                        "uid": r["uid"], "resref": r["resref"], "ext": r["ext"],
                        "kind": r["kind"], "field_path": r["field_path"],
                        "src_text": text, "cat": cat})

    n_uniq = {"translated": 0, "missing": 0, "fallback": 0}
    for cat in uniq_seen.values():
        n_uniq[cat] += 1
    return {
        "units": sum(units.values()) - units["src_changed"],   # src_changed 是交叉标记
        "units_all": len(rows),
        "gone": n_gone,
        "unique": len(uniq_seen),
        "translated": units["translated"],
        "missing": units["missing"],
        "fallback": units["fallback"],
        "src_changed": units["src_changed"],
        "uniq_translated": n_uniq["translated"],
        "uniq_missing": n_uniq["missing"],
        "uniq_fallback": n_uniq["fallback"],
        "uniq_todo": n_uniq["missing"] + n_uniq["fallback"],
        "chars_todo": chars_missing,
        "raw_fallback_hit": n_fallback_hit,
        "by_kind": dict(by_kind),
        "by_res": sorted(by_res.items(), key=lambda kv: -kv[1]),
        "containers_todo": len(containers_todo),
        "samples": missing_detail[:sample],
    }


def summarize(d, indent="  "):
    """把 diff 结果打印成一段人话。"""
    lines = [
        "%s单元 %d 个（含已消失 %d）；去重后 %d 条不同文本"
        % (indent, d["units_all"], d["gone"], d["unique"]),
        "%s  已有译文 %d 条 / 待译 %d 条 / 待重试 %d 条 / 源文变了 %d 个单元"
        % (indent, d["uniq_translated"], d["uniq_missing"], d["uniq_fallback"],
           d["src_changed"]),
    ]
    if d["raw_fallback_hit"]:
        lines.append("%s  ★ 其中 %d 条是靠 raw key 兜底查到的（不用重译，省下这笔钱）"
                     % (indent, d["raw_fallback_hit"]))
    if d["by_kind"]:
        lines.append("%s  按类型：%s" % (indent,
                     "、".join("%s %d" % (k, v) for k, v in d["by_kind"].items())))
    return "\n".join(lines)
