#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TlkRepo —— TLK 槽位账本（`tlk_slot` 表）。

**为什么需要它**：`rebuild_tlk_safe.py` 只能用启发式反推"哪些槽位是自加的"——
判据是"英文导出 `prc8.txt` 里没有的、且有文本的"。这条启发式一旦遇到
"某自加槽位与英文导出重合"或"英文导出某条是空文本"就会误判，而且它连
"自加槽位到底在 90001~90089 还是 219901~219989"都说不清（历史记录里两个值互相矛盾）。
账本把这件事从"猜"变成**事实查询**。

三条能力：
  * `claim_range` / `claim_many`  认领：把现存槽位登记下来（归谁、是否受保护、内容指纹）
  * `allocate`                    分配：新建槽位前先查占用，**冲突就报错，不静默覆盖**
  * `baseline` / `verify`         基线：记录并校验 TLK 的版本状态
"""
import json
import os
from collections import Counter

from ..db import now
from ..keys import raw_hash
from ..tlkio import fingerprint, info, range_texts, read_texts
from .run_repo import RunRepo


class TlkRepo(object):
    def __init__(self, db):
        self.db = db

    # ============================================================ 读
    def slots(self, tlk_path=None, owner=None, purpose=None, protected_only=False):
        where, params = [], []
        if tlk_path:
            where.append("tlk_path=?"); params.append(os.path.abspath(tlk_path))
        if owner:
            where.append("owner=?"); params.append(owner)
        if purpose:
            where.append("purpose=?"); params.append(purpose)
        if protected_only:
            where.append("protected=1")
        sql = "SELECT * FROM tlk_slot"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY tlk_path, strref"
        return [dict(r) for r in self.db.execute(sql, params)]

    def count(self, tlk_path=None, protected_only=False):
        return len(self.slots(tlk_path, protected_only=protected_only))

    def strrefs(self, tlk_path, protected_only=True):
        return {s["strref"] for s in self.slots(tlk_path, protected_only=protected_only)}

    def by_owner(self, tlk_path=None):
        """[(owner, purpose, 条数, 受保护数)] —— 看"谁占了多少槽位"。"""
        rows = self.slots(tlk_path)
        c = Counter((s["owner"], s["purpose"]) for s in rows)
        prot = Counter((s["owner"], s["purpose"]) for s in rows if s["protected"])
        return [(o, p, n, prot[(o, p)]) for (o, p), n in
                sorted(c.items(), key=lambda kv: -kv[1])]

    def get(self, tlk_path, strref):
        r = self.db.execute("SELECT * FROM tlk_slot WHERE tlk_path=? AND strref=?",
                            (os.path.abspath(tlk_path), int(strref))).fetchone()
        return dict(r) if r else None

    # ============================================================ 写
    def claim_many(self, tlk_path, rows):
        """rows = [(strref, owner, purpose, text_key, protected, note)]。返回写入条数。"""
        p = os.path.abspath(tlk_path)
        ts = now()
        data = [(p, int(s), o, pu, tk, 1 if pr else 0, nt, ts)
                for (s, o, pu, tk, pr, nt) in rows]
        if not data:
            return 0
        self.db.executemany(
            "INSERT INTO tlk_slot(tlk_path,strref,owner,purpose,text_key,protected,"
            "note,updated) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(tlk_path,strref) DO UPDATE SET owner=excluded.owner, "
            "purpose=excluded.purpose, text_key=excluded.text_key, "
            "protected=excluded.protected, note=excluded.note, updated=excluded.updated",
            data)
        self.db.commit()
        return len(data)

    def release(self, tlk_path, strrefs):
        p = os.path.abspath(tlk_path)
        n = 0
        for s in strrefs:
            n += self.db.execute("DELETE FROM tlk_slot WHERE tlk_path=? AND strref=?",
                                 (p, int(s))).rowcount
        self.db.commit()
        return n

    def allocate(self, tlk_path, owner, count=1, purpose="reserved", start=None, note=None):
        """分配空槽位，**先查占用**。

        · 指定 `start`：要求 [start, start+count-1] 全空，否则抛 ValueError（不静默偏移）；
        · 不指定：从"账本里最大 strref + 1"往后找连续空位。
        """
        used = {s["strref"] for s in self.slots(tlk_path)}
        if start is not None:
            want = list(range(int(start), int(start) + int(count)))
            clash = sorted(used & set(want))
            if clash:
                raise ValueError("槽位被占用，拒绝分配（不静默覆盖）：%s" % clash)
            picked = want
        else:
            picked, n = [], (max(used) + 1 if used else 1)
            while len(picked) < count:
                if n not in used:
                    picked.append(n)
                n += 1
        self.claim_many(tlk_path, [(s, owner, purpose, None, 1, note) for s in picked])
        return picked

    # ============================================================ 认领（从 TLK 实测）
    def claim_range(self, tlk_path, start, end, owner, purpose="engine_text",
                    note=None, en_ids=None, dry_run=False):
        """认领 [start, end] 里有文本的槽位。

        `en_ids` 传英文导出里的 strref 集合时，会**交叉验证**"这些槽位确实不在
        英文导出里"，并写进 report —— 认领不能只靠喊区间，要有证据。
        """
        texts = range_texts(tlk_path, start, end)
        rows, empty = [], []
        for i in range(int(start), int(end) + 1):
            t = texts.get(str(i))
            if not (t or "").strip():
                empty.append(i)
                continue
            rows.append((i, owner, purpose, raw_hash(t), 1, note))
        rep = {"tlk_path": os.path.abspath(tlk_path), "range": [start, end],
               "with_text": len(rows), "empty_in_range": empty,
               "in_en_export": [], "claimed": 0, "dry_run": bool(dry_run)}
        if en_ids is not None:
            rep["in_en_export"] = [r[0] for r in rows if str(r[0]) in en_ids]
        if not dry_run:
            rep["claimed"] = self.claim_many(tlk_path, rows)
        return rep

    # ============================================================ 基线 / 校验
    def baseline(self, tlk_path, note=None, decode="replace"):
        """建立 TLK 版本基线（写进 `run` 表，`kind='tlk-baseline'`）。"""
        inf = info(tlk_path)
        slots = self.slots(tlk_path)
        rep = {"tlk_path": inf["path"], "bytes": inf["bytes"], "count": inf["count"],
               "with_text": inf["with_text"], "sha256": inf["sha256"],
               "fingerprint": fingerprint(tlk_path, decode=decode),
               "ledger_slots": len(slots),
               "protected_slots": sum(1 for s in slots if s["protected"]),
               "owners": [[o, p, n] for o, p, n, _ in self.by_owner(tlk_path)],
               "note": note}
        runs = RunRepo(self.db)
        rid = runs.start("prc8", "tlk-baseline", {"tlk_path": inf["path"], "note": note})
        runs.finish(rid, ok=True, units=inf["count"], uniq=inf["with_text"],
                    baseline=rep)
        rep["run_id"] = rid
        return rep

    def last_baseline(self, tlk_path=None):
        """最近一次基线（可选按路径过滤）。返回 dict 或 None。"""
        want = os.path.abspath(tlk_path) if tlk_path else None
        for r in self.db.execute(
                "SELECT * FROM run WHERE kind='tlk-baseline' ORDER BY id DESC LIMIT 20"):
            b = (json.loads(r["summary"] or "{}").get("baseline") or {})
            if not want or os.path.abspath(b.get("tlk_path", "")) == want:
                return {"run_id": r["id"], "started": r["started"], "baseline": b}
        return None

    def verify(self, tlk_path, en_ids=None, decode="replace"):
        """校验账本与 TLK 是否一致；给 `en_ids` 时再与启发式交叉验证。

        返回 {"problems": [...], "ledger_only": [...], "heuristic_only": [...]}
        —— 三者都空才算"账本与 TLK 完全一致，且不比旧启发式少认领任何槽位"。
        """
        texts = read_texts(tlk_path, decode=decode)
        slots = self.slots(tlk_path)
        problems = []
        for s in slots:
            t = texts.get(str(s["strref"]))
            if not (t or "").strip():
                problems.append({"strref": s["strref"], "why": "槽位无文本"})
            elif s["protected"] and s["text_key"] and raw_hash(t) != s["text_key"]:
                problems.append({"strref": s["strref"], "why": "内容与账本记录不符"})
        heuristic = []
        if en_ids is not None:
            heuristic = sorted(int(k) for k, v in texts.items()
                               if k not in en_ids and (v or "").strip())
        ledger = sorted(s["strref"] for s in slots)
        return {"tlk_path": os.path.abspath(tlk_path), "ledger": len(ledger),
                "problems": problems, "heuristic_count": len(heuristic),
                "heuristic": heuristic,
                "ledger_only": sorted(set(ledger) - set(heuristic)),
                "heuristic_only": sorted(set(heuristic) - set(ledger))}

    def ledger_guard(self, new_tlk_path, ledger_tlk_path=None, decode="replace"):
        """**重建后的守门**：账本里 `protected=1` 的槽位，在新 TLK 里必须都在、内容一致。

        这是账本取代 `rebuild_tlk_safe.py` 那条启发式的**关键一步** —— 旧做法是
        "英文导出里没有的就算自加"，新做法是"账本说受保护的，少一条就不许安装"。

        **fail-closed**：任何缺失/内容不符都返回 `ok=False`，调用方据此拒绝安装。
        （沿用已有教训：拿不到数据、少了一条，都绝不能被当成"通过"。）

        `ledger_tlk_path` 用于"新 TLK 还没入账本"的场景 —— 此时用**源 TLK 的账本**
        来校验这个新产物。
        """
        src = ledger_tlk_path or new_tlk_path
        slots = [s for s in self.slots(src) if s["protected"]]
        texts = read_texts(new_tlk_path, decode=decode)
        missing, changed = [], []
        for s in slots:
            t = texts.get(str(s["strref"]))
            if not (t or "").strip():
                missing.append(s["strref"])
            elif s["text_key"] and raw_hash(t) != s["text_key"]:
                changed.append(s["strref"])
        return {"new_tlk": os.path.abspath(new_tlk_path),
                "ledger_tlk": os.path.abspath(src),
                "expected": len(slots), "missing": missing, "changed": changed,
                "ok": not missing and not changed}
