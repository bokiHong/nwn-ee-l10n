#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TermRepo —— 术语（`term` 表）的访问 + 旧 JSON 资产的**只读适配**。

Sprint 1 的验收对象：把现有的四份术语资产读成统一的 `Term` 对象，
并**在不落库的前提下**预演合并结果 —— 合并规则先验证对了，Sprint 4 才敢写库。

四份资产的形状（实测）：
  * `modl10n\\glossary.json`       `{version, terms:[{en,zh,type,note,required}]}`  213 条
  * `glossary_overrides.json`     `{zh:{}, required:{}, type:{}, add:{en:{...}}}`   5+2 条
  * `modl10n\\term_variants.json` `{_说明,_生成方式,variants:{old:{new,...}}}`      77 条规则
  * `dnd3r_术语库_草案.json`       `{en: [{zh, n, raw}, ...]}`                    5,953 条

Sprint 4 起本类还负责**写入**：`import_legacy`（洗入）/ `export_json`（导出，
要求与 `glossary.json` 逐字节等价）/ `fuzzy`（模糊匹配）/ `check_required`（强制守必校验）。
"""
import copy
import json
import os
import re

from ..db import now
from ..model import Term


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class TermRepo(object):
    def __init__(self, db):
        self.db = db

    # ============================================================ 读库
    def list(self, status="active", type=None, scope=None, q=None, limit=None):
        """列出术语。默认只列 active（生效的）。"""
        where, params = [], []
        if status and status != "all":
            where.append("status=?"); params.append(status)
        if type:
            where.append("type=?"); params.append(type)
        if scope and scope != "*":
            where.append("(scope='*' OR scope=?)"); params.append(scope)
        if q:
            where.append("(en LIKE ? OR zh LIKE ?)"); params += ["%%%s%%" % q, "%%%s%%" % q]
        sql = "SELECT * FROM term"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY en"
        if limit:
            sql += " LIMIT ?"; params.append(limit)
        return [Term.from_row(r) for r in self.db.execute(sql, params)]

    def by_en(self, en):
        return [Term.from_row(r) for r in
                self.db.execute("SELECT * FROM term WHERE en=? ORDER BY status", (en,))]

    def required(self, scope="*"):
        return [Term.from_row(r) for r in self.db.execute(
            "SELECT * FROM term WHERE status='active' AND required=1 "
            "AND (scope='*' OR scope=?) ORDER BY en", (scope,))]

    def count(self, status=None):
        if status:
            return self.db.execute("SELECT COUNT(*) FROM term WHERE status=?",
                                   (status,)).fetchone()[0]
        return self.db.execute("SELECT COUNT(*) FROM term").fetchone()[0]

    def count_by_status(self):
        return {r[0]: r[1] for r in
                self.db.execute("SELECT status, COUNT(*) FROM term GROUP BY status")}

    def variants(self):
        """译名替换规则（term_variant 表）。"""
        return self.db.execute("SELECT old,new,guard_en,scope,note FROM term_variant").fetchall()

    # ============================================================ 旧资产适配（只读）
    @staticmethod
    def load_legacy_glossary(path, src="glossary"):
        """`glossary.json` -> [Term]。字段一一对应，不做任何加工。"""
        data = _read_json(path)
        out = []
        for t in data.get("terms", []):
            out.append(Term(
                en=t["en"], zh=t["zh"],
                type=t.get("type", "name"),
                required=1 if t.get("required", True) else 0,
                status="active", scope="*",
                src=t.get("src", src), note=t.get("note") or None))
        return out

    @staticmethod
    def load_legacy_overrides(path):
        """`glossary_overrides.json` -> (补丁字典, 新增列表)。

        补丁字典：{en: {"zh"?, "required"?, "type"?}}
        新增列表：[Term]（`add` 段里 glossary 中**没有**的条目）
        """
        data = _read_json(path)
        patch = {}
        for en, zh in (data.get("zh") or {}).items():
            patch.setdefault(en, {})["zh"] = zh
        for en, req in (data.get("required") or {}).items():
            patch.setdefault(en, {})["required"] = 1 if req else 0
        for en, ty in (data.get("type") or {}).items():
            patch.setdefault(en, {})["type"] = ty
        adds = []
        for en, v in (data.get("add") or {}).items():
            adds.append(Term(en=en, zh=v.get("zh", ""), type=v.get("type", "name"),
                             required=1 if v.get("required", True) else 0,
                             status="active", scope="*", src="override",
                             note=v.get("note") or None))
        return patch, adds

    @staticmethod
    def load_legacy_variants(path):
        """`term_variants.json` -> [(old, new, guard_en, scope, note)]。"""
        data = _read_json(path)
        v = data.get("variants") or {}
        out = []
        for old, spec in v.items():
            if isinstance(spec, dict):
                new = spec.get("new") or spec.get("to") or ""
                guard = spec.get("guard") or spec.get("en")
                scope = spec.get("scope", "*")
                note = spec.get("note")
            else:                       # 容忍 "old": "new" 的简写
                new, guard, scope, note = spec, None, "*", None
            out.append((old, new, guard, scope, note))
        return out

    @staticmethod
    def load_legacy_draft(path, min_count=1, limit=None):
        """`dnd3r_术语库_草案.json` -> [Term(status='candidate')]。

        草案是"一个英文多个候选译名"的候选池，所以取词频最高的若干名。
        """
        data = _read_json(path)
        out = []
        for en, cands in data.items():
            if not isinstance(cands, list):
                continue
            cands = [c for c in cands if isinstance(c, dict) and c.get("zh")]
            cands.sort(key=lambda c: -(c.get("n") or 0))
            for c in cands[:3]:
                if (c.get("n") or 0) < min_count:
                    continue
                out.append(Term(en=en, zh=c["zh"], type="misc", required=0,
                                status="candidate", scope="*", src="draft",
                                note="n=%s raw=%s" % (c.get("n"), c.get("raw"))))
                if limit and len(out) >= limit:
                    return out
        return out

    # ============================================================ 预演合并（不落库）
    @staticmethod
    def merge_legacy(glossary_terms, patch=None, adds=None):
        """把"基准术语表 + 覆盖层 + 新增"合并成最终生效列表（纯函数，不碰库）。

        规则（与 `make_glossary.py` 现有语义一致）：
          1. 基准表每条术语，按 `patch` 覆盖 zh / required / type；
          2. `adds` 里若 en 已存在 → 视作**覆盖**（避免像 Swordsage 那样被丢）；
          3. `adds` 里 en 不存在的 → 追加为新术语；
          4. patch 里出现、但基准表与 adds 都没有的 en → 记为"悬空覆盖"并返回，供人工核对。

        返回 (terms, report)：report = {"base","patched","added","dangling":[...]}
        """
        patch = patch or {}
        adds = adds or []
        by_en = {}
        order = []
        patched = 0
        for t in glossary_terms:
            by_en[t.en] = t
            order.append(t.en)
        for en, p in patch.items():
            t = by_en.get(en)
            if t is None:
                continue
            if "zh" in p: t.zh = p["zh"]
            if "required" in p: t.required = p["required"]
            if "type" in p: t.type = p["type"]
            if t.src == "glossary":
                t.src = "override"
            patched += 1
        added = 0
        for t in adds:
            if t.en in by_en:
                cur = by_en[t.en]
                cur.zh, cur.type, cur.required = t.zh, t.type, t.required
                if t.note: cur.note = t.note
                cur.src = "override"
                patched += 1
            else:
                by_en[t.en] = t
                order.append(t.en)
                added += 1
        used = {en for en, p in patch.items() if en in by_en}
        dangling = sorted(set(patch) - used)
        report = {"base": len(glossary_terms), "patched": patched,
                  "added": added, "dangling": dangling,
                  "total": len(order)}
        return [by_en[en] for en in order], report

    # ============================================================ 单条编辑（Sprint 7）
    def by_zh(self, zh, status="active"):
        """按**译名**查术语（术语统一要按"旧译名"反查英文护栏）。"""
        return [Term.from_row(r) for r in self.db.execute(
            "SELECT * FROM term WHERE zh=? AND status=? ORDER BY en", (zh, status))]

    def upsert_one(self, term):
        """`upsert_many` 的单条门面。"""
        return self.upsert_many([term])

    def candidates_of(self, en, limit=3):
        """某英文的**草案候选**（`status='candidate'`）—— 界面「dnd3r 参考」列用。"""
        return [Term.from_row(r) for r in self.db.execute(
            "SELECT * FROM term WHERE en=? AND status='candidate' "
            "ORDER BY (seq IS NULL), seq, zh LIMIT ?", (en, limit))]

    def set_zh(self, en, zh, required=None, type=None, note=None, src="manual", scope="*"):
        """改一条术语的译名 / 必守 / 分类。

        ★ **不能直接 `upsert_many`**：`term` 表主键是 **`(en, zh, status)`** ——
        直接 upsert 会"新增一行、旧 active 行还留着"，于是同一个 `en` 有两条 active，
        `hits` / `fuzzy` / 违规检查全都会错乱 ✗。这里先把旧 active 行**降级为 rejected**
        （保留在库里做溯源，但不再生效），再写新行。

        返回 `{"old", "new", "demoted"}`。
        """
        rows = [Term.from_row(r) for r in self.db.execute(
            "SELECT * FROM term WHERE lower(en)=lower(?) AND status='active'", (en,))]
        cur = rows[0] if rows else None
        old_zh = cur.zh if cur else None
        demoted = 0
        if cur is not None and old_zh != zh:
            # ★ 必须用 `UPDATE OR REPLACE`：`term` 主键是 `(en, zh, status)`，而
            #   "**改回历史译名**"时 `(en, 新译名, 'rejected')` 往往**已经存在** ——
            #   普通 UPDATE 会撞 UNIQUE 约束。实测：把 `Anima Mage` 从"魂灵法师"
            #   改回"灵魂法师"时就炸在这里（`UNIQUE constraint failed: term.en, term.zh,
            #   term.status`）。OR REPLACE 会先删掉那条冲突的历史行，正是我们想要的。
            self.db.execute(
                "UPDATE OR REPLACE term SET status='rejected', updated=? "
                "WHERE lower(en)=lower(?) AND status='active'", (now(), en))
            demoted = self.db.execute("SELECT changes()").fetchone()[0]
        t = Term(en=en, zh=zh,
                 type=type or (cur.type if cur else "name"),
                 required=int(required) if required is not None
                 else (cur.required if cur else 1),
                 status="active", scope=scope, src=src,
                 note=note if note is not None else (cur.note if cur else None),
                 seq=cur.seq if cur else None)
        self.upsert_many([t])
        return {"old": old_zh, "new": zh, "demoted": demoted}

    # ============================================================ 写入（Sprint 4）
    COLS = ("en", "zh", "type", "required", "status", "scope", "src", "note",
            "updated", "seq")

    def upsert_many(self, terms):
        """按主键 (en, zh, status) upsert。返回写入条数。"""
        ts = now()
        rows = []
        for t in terms:
            r = t.as_row()
            r["updated"] = r.get("updated") or ts
            r["required"] = 1 if r.get("required") else 0
            rows.append([r.get(c) for c in self.COLS])
        if not rows:
            return 0
        sql = ("INSERT INTO term(%s) VALUES(%s) ON CONFLICT(en,zh,status) DO UPDATE SET %s"
               % (",".join(self.COLS), ",".join("?" * len(self.COLS)),
                  ",".join("%s=excluded.%s" % (c, c) for c in self.COLS
                           if c not in ("en", "zh", "status"))))
        self.db.executemany(sql, rows)
        self.db.commit()
        return len(rows)

    def upsert_variants(self, rows):
        """rows = [(old, new, guard_en, scope, note)] —— 写译名替换规则。"""
        rows = list(rows)
        if rows:
            self.db.executemany(
                "INSERT INTO term_variant(old,new,guard_en,scope,note) VALUES(?,?,?,?,?) "
                "ON CONFLICT(old,new) DO UPDATE SET guard_en=excluded.guard_en, "
                "scope=excluded.scope, note=excluded.note", rows)
            self.db.commit()
        return len(rows)

    def clear(self, status=None):
        """删术语（默认全清）。只在导入前 `reset=True` 时用。"""
        if status:
            n = self.db.execute("DELETE FROM term WHERE status=?", (status,)).rowcount
        else:
            n = self.db.execute("DELETE FROM term").rowcount
        self.db.commit()
        return n

    def import_legacy(self, glossary_path, draft_path=None, variants_path=None,
                      overrides_path=None, reset=False, dry_run=False):
        """把四份旧 JSON 洗进 `term` / `term_variant` 表。

        · `glossary.json` -> `term(status='active')`，**保留数组顺序**（写进 `seq`，
          否则导出的 JSON 会与原件不等价）；
        · `glossary_overrides.json` -> **只做校验**：确认覆盖层已全部体现在
          glossary.json 里（`make_glossary.py` 生成时就应用过了），返回 dangling 清单；
        · `dnd3r_术语库_草案.json` -> `term(status='candidate')`；
        · `term_variants.json` -> `term_variant` 表。

        返回 report：{"active","candidate","variants","override_check","dry_run"}
        """
        gl = self.load_legacy_glossary(glossary_path)
        for i, t in enumerate(gl):
            t.seq = i
        cands = self.load_legacy_draft(draft_path) if draft_path else []
        for i, t in enumerate(cands):
            t.seq = i
        variants = self.load_legacy_variants(variants_path) if variants_path else []

        chk = {}
        if overrides_path and os.path.exists(overrides_path):
            patch, adds = self.load_legacy_overrides(overrides_path)
            # 在副本上预演，免得改到即将写入的对象
            _, chk = self.merge_legacy(copy.deepcopy(gl), patch, adds)

        rep = {"active": len(gl), "candidate": len(cands), "variants": len(variants),
               "override_check": chk, "dry_run": bool(dry_run)}
        if dry_run:
            return rep

        if reset:
            self.clear()
            self.db.execute("DELETE FROM term_variant")
            self.db.commit()
        self.upsert_many(gl)
        if cands:
            self.upsert_many(cands)
        if variants:
            self.upsert_variants(variants)
        return rep

    def export_json(self, path, status="active"):
        """导出成与 `glossary.json` **逐字节等价**的 JSON。

        五个要件缺一不可（都是实测出来的）：
          1. 顶层 `{"version": 1, "terms": [...]}`，键顺序 version -> terms；
          2. 每条术语键顺序 `en, zh, type, note, required`（与 `Glossary.add` 一致）；
          3. `ensure_ascii=False` + `indent=2`；
          4. 按 `seq` 排序 —— 保住原数组顺序（原文件不是按字母序）；
          5. **用文本模式 `open()`**：Windows 上会把 `\\n` 写成 `\\r\\n`，
             原文件正是 CRLF（实测末尾 `]\\r\\n}`）。
        """
        rows = self.db.execute(
            "SELECT * FROM term WHERE status=? ORDER BY seq IS NULL, seq, rowid",
            (status,)).fetchall()
        terms = [{"en": r["en"], "zh": r["zh"], "type": r["type"],
                  "note": r["note"] if r["note"] is not None else "",
                  "required": bool(r["required"])} for r in rows]
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)      # 目标目录不存在就建（旧版依赖调用方先建好）
        with open(path, "w", encoding="utf-8") as f:      # ★ 文本模式，保住 CRLF
            json.dump({"version": 1, "terms": terms}, f, ensure_ascii=False, indent=2)
        return len(terms)

    # ============================================================ 模糊匹配与守必校验
    def _all_active(self):
        return [Term.from_row(r) for r in self.db.execute(
            "SELECT * FROM term WHERE status='active' ORDER BY seq IS NULL, seq, rowid")]

    @staticmethod
    def _norm(s):
        """归一化：去空白与常见标点、小写化。让 `Mordenkainen's` 能匹配 `mordenkainens`。"""
        return re.sub(r"[\s'’\-_,.:;!?()\[\]\"“”‘’]", "", (s or "").lower())

    def fuzzy(self, q, limit=20, only_required=False):
        """模糊匹配术语：精确 > 前缀 > 子串 > 归一化子串 > 编辑相似度。

        返回 `[(Term, score, 命中方式)]`，按 score 降序 —— GUI 搜索框直接用。
        """
        import difflib
        q = (q or "").strip()
        if not q:
            return []
        ql, qn = q.lower(), self._norm(q)
        out = []
        for t in self._all_active():
            if only_required and not t.required:
                continue
            enl, zhl = t.en.lower(), (t.zh or "").lower()
            enn, zhn = self._norm(t.en), self._norm(t.zh)
            score, why = 0.0, None
            if ql == enl or q == t.zh:
                score, why = 1.0, "精确"
            elif enl.startswith(ql) or zhl.startswith(ql):
                score, why = 0.9, "前缀"
            elif ql in enl or q in (t.zh or ""):
                score, why = 0.8, "子串"
            elif qn and (qn in enn or qn in zhn):
                score, why = 0.7, "归一化子串"
            else:
                r = difflib.SequenceMatcher(None, ql, enl).ratio()
                if r >= 0.6:
                    score, why = round(r * 0.6, 3), "相似度 %.2f" % r
            if score:
                out.append((t, score, why))
        out.sort(key=lambda x: (-x[1], x[0].en))
        return out[:limit]

    def glossary(self, status="active"):
        """公开的 `Glossary` 构造入口 —— 批量检查时**只构造一次**再复用。

        （`Glossary.hits()` 是"每条文本 × 213 个术语"的正则扫描，循环里反复构造会明显变慢。）
        """
        return self._glossary(status)

    def _glossary(self, status="active"):
        """构造 modl10n 的 Glossary 对象 —— **匹配规则不重造**，直接用现有实现。"""
        from modl10n.glossary import Glossary
        terms = self.list(status=status) if status else self._all_active()
        return Glossary([{"en": t.en, "zh": t.zh, "type": t.type,
                          "note": t.note or "", "required": bool(t.required)}
                         for t in terms])

    def hits(self, text, status="active"):
        """文本里命中的术语 -> `[(en, zh, required)]`。"""
        g = self._glossary(status)
        return [(en, zh, bool(g._by_req.get(en, True))) for en, zh in g.hits(text)]

    def prompt_block(self, text, max_terms=40):
        """译前注入用的提示块（复用 modl10n 的分级逻辑）。"""
        return self._glossary().prompt_block(text, max_terms)

    def check_required(self, src, dst):
        """**强制守必校验**：原文命中的必守术语，译文里没用规范译名 → `[(en, zh)]`。

        与翻译流水线里的 `sfrules.validate` 是互补的：那个管标点/占位符/换行，
        这个管"必须用规范译名"。
        """
        return self._glossary().check(src, dst)

    def count_required(self, status="active"):
        return self.db.execute(
            "SELECT COUNT(*) FROM term WHERE status=? AND required=1",
            (status,)).fetchone()[0]
