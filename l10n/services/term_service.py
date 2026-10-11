#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TermService —— 界面上的**实时术语检查**门面。

GUI 只调这一层；底层仍是 `TermRepo`（而它复用 `modl10n.glossary` 的匹配规则，
**不重造**边界正则）。

性能边界（重要）：
    `Glossary.hits()` 是"每条文本 × 213 个术语"的正则扫描。单条调用（选中某行即时检查）
    完全无感；但**全库 3 万条**会明显卡界面 —— 所以：
      * 选中/编辑时 → `status_of_text()`（单条）✓
      * 全库体检   → `violation_summary(..., limit=...)`，由界面**放后台线程**跑。
"""
from functools import lru_cache
from modl10n.glossary import Glossary

import re
import time

from ..model import Term

_PLURAL_TAIL = re.compile(r"(s|x|ch|sh)$", re.I)


@lru_cache(maxsize=20000)
def _tolerant_re(en):
    """宽容正则（方案 C，2026-10-08 用户拍板）。

    ★ **加 `lru_cache` 的原因（2026-10-08 用户实测反馈）**：术语库从 213 条涨到 **6700 条**后，
      `hits()` 每次都要为**每条术语现编译一次正则** ✗ → 点一下词条要编译 6700 个正则，
      用户直接反馈"**点击词条之后就卡住了**"✓。
      加缓存后：首次约 1 秒（照旧要全编一遍），之后每次只做 6700 次 `search`（~7ms）✓ **秒开**。

    * **忽略大小写** —— `swordsage` 也要能匹配 `Swordsage`；
    * **容忍复数** —— `Anima Mages` 匹配 `Anima Mage`；以 s/x/ch/sh 结尾用 `(?:es)?`；
    * **容忍拉丁第二变格复数** —— `Blood Magi` 匹配 `Blood Magus`（**实测踩过**：
      `-us → -i` 不在 `s/es` 规则里，导致术语统一漏改 1 条 ✗）；
    * 仍保留词边界 `(?<![A-Za-z])` / `(?![A-Za-z])`，不会把 `Bane` 匹配到 `Baneful`。
    """
    esc = re.escape(en)
    if en.endswith("us"):
        # 拉丁第二变格：`Magus` → `Magi` 是**替换词尾**（不是加后缀）——
        # 我第一版写成 `Magus(?:i)?` 就永远匹配不到 `Magi` ✗（实测踩过）。
        return re.compile(r"(?<![A-Za-z])" + esc[:-2] + r"(?:us|i|es|s)?(?![A-Za-z])",
                          re.IGNORECASE)
    tail = r"(?:es)?" if _PLURAL_TAIL.search(en) else r"(?:s)?"
    return re.compile(r"(?<![A-Za-z])" + esc + tail + r"(?![A-Za-z])", re.IGNORECASE)


# ★ 降噪白名单：**专有名词类**的术语才可能报"违规" ✓
#   首跑（2026-10-10）22701 条里绝大多数是误报 —— `Type`×2662、`Ability`×1086、`Good`×875、
#   `Time`×872、`Level`×824… **全是普通英语词**，译文里用「能力」「种类」本来就对 ✗。
#   （与 2026-10-08 译名漂移审计学到的"判据越依赖结构越准"完全同源 ✓）
NOUN_TYPES = {"race", "class", "prestige_class", "feat", "spell", "psionic", "martial",
              "monster", "deity", "place", "item", "name", "skill", "language", "creature"}

# 即使 type 是专有名词类，这些**普通词**也不该报违规（它们在日常语境里就是普通含义）
STOP_EN = {
    "type", "ability", "good", "time", "level", "bonus", "force", "protection",
    "character", "small", "turn", "shadow", "penalty", "healing", "hit", "kind",
    "undead", "negate", "natural", "save", "skill", "attack", "damage", "mind",
    "size", "speed", "fear", "light", "dark", "fire", "cold", "acid", "evil",
    "chaos", "law", "magic", "armor", "weapon", "shield", "strength", "dexterity",
    "constitution", "intelligence", "wisdom", "charisma", "poison", "disease",
    "curse", "bless", "spell", "form", "shape", "body", "soul", "life", "death",
    "power", "energy", "master", "lord", "lady", "king", "queen", "prince",
    # ↓ 2026-10-11 看样本后确认是"真误报"的（日常词义，非术语义）：
    #   `Doom`（"Doom, Despair!"＝毁灭；"Chamber of Doom"＝毁灭之室）30 处
    #   `Disable`（"Disable Trap"＝解除陷阱，是**技能名**）20 处
    #   `Bolt`（"1 Bolt"＝1 发弹药；"Bolt of Acid" 是复合法术名）13 处
    #   `Hammer`（"One With The Hammer"＝与锤合一；"Hammer of Witches"＝女巫之锤）9 处
    "doom", "disable", "bolt", "hammer",
    # ↓ 2026-10-11 第四批（长尾抠除时逐个看样本确认的「日常词义」误报，用户拍板加入）：
    #   `Bane`（"Bane of the Unsleeping"＝不眠者之灾 / "终极克星"；与神名班恩撞车）7 处
    #   `Daze`（"Daze Monster"＝怪物眩晕 / 普通「眩晕」）6 处
    #   `Adept`（日常「精通…」语境被匹到）4 处
    #   `Deflect`（"Deflect Arrows"＝拨挡箭矢，是**另一个专长**）4 处
    #   `Rebuke`（"Rebuke Undead"＝斥责不死生物，日常动词）4 处
    #   `Run`（对话里的动词「快跑」）3 处、`Expansion`（内部标记 `## iprp_* 扩展 ##`）3 处
    #   `Halt`（法术描述里的「停止」）3 处
    "bane", "daze", "adept", "deflect", "rebuke", "run", "expansion", "halt",
}


# 结构化词：出现在术语前面时**不算**"复合专名的一部分"（否则 `Type of Feat: Adept` 会被误杀 ✗）
STRUCT_WORDS = {
    "type", "level", "feat", "feats", "spell", "spells", "class", "classes", "race",
    "prerequisite", "prerequisites", "specifics", "benefit", "school", "component",
    "components", "range", "target", "targets", "duration", "save", "casting", "area",
    "use", "caster", "the", "a", "an", "of", "and", "or", "in", "on", "at", "for",
    "with", "by", "to", "from", "this", "that", "these", "those", "your", "you",
}


def _in_compound(src, en):
    """术语前面**紧邻一个首字母大写的英文词** → 它只是复合专名的一部分 ✗

    实测（2026-10-10，595 处"专名形态但日常词义"的误报里的大头）：
      · `Dragonfire Adept`（龙火术士）里的 `Adept`（侍僧）→ 98 条误报 ✗
      · `Damage Resistance`（伤害减免）里的 `Resistance`（提升抗力）→ 69 条 ✗
      · `Bolt of Acid`（酸液箭）里的 `Bolt`（箭石术）→ 大量 ✗
    但**结构词要放行**：`Type of Feat: Adept` 里的 `Feat` 是结构词，这时 `Adept` 应按术语报 ✓
    """
    m = re.search(r"(?<![A-Za-z])" + re.escape(en) + r"(?![A-Za-z])", src)
    if not m or m.start() == 0:
        return False
    pre = src[:m.start()].rstrip()
    parts = re.split(r"[^A-Za-z']+", pre)
    prev = parts[-1] if parts else ""
    if not prev or not prev[0].isupper():
        return False
    return prev.lower() not in STRUCT_WORDS


def violations(unit_repo, term_repo, tm_repo, project=None, limit=None,
               glossary=None, fuzzy_head=2, denoise=True):
    """**术语违规清单**：命中了必守术语、但译文里没用它的规范译名。

    `v_term_violation` 视图的判据是 `instr(dst, zh) = 0`（译文里完全不含术语值）——
    单用它会刷屏 ✗（首跑 22701 条）。这里的**四重降噪**：

    * ① `denoise=True` 时**只报专有名词类**术语（`NOUN_TYPES`）——
      `Type`/`Ability`/`Good`/`Time`/`Level` 这些普通词本就该按字面译 ✗；
    * ② `STOP_EN` 里**即使类型像专名也不报**（日常语境下就是普通含义）；
    * ③ 术语值的**前 `fuzzy_head` 字出现也算"采用了"** ✓（术语值「生命骰」、译文「生命骰数」）；
    * ④ **冲突术语排除**：同一 `en` 有多条 active（用户保留的"真冲突"，如 `Spell`）时，
      用了任一条就不报 ✓。

    返回 `[(en, zh, dst, uid, project, resref)]`，按 `en` 排序。
    """
    from collections import defaultdict

    conflict = defaultdict(set)
    typemap = {}
    for en, zh, ty in tm_repo.db.execute(
            "SELECT en, zh, type FROM term WHERE status='active'"):
        if not denoise:
            conflict[(en or "").lower()].add(zh or "")
            continue
        if (ty or "") not in NOUN_TYPES:
            continue                                   # 降噪 ①：非专名类，忽略
        if (en or "").strip().lower() in STOP_EN:
            continue                                   # 降噪 ②：普通词，忽略
        conflict[(en or "").lower()].add(zh or "")
        typemap[(en or "").lower()] = ty
    if denoise and not conflict:
        return []

    sql = ("SELECT ut.en, ut.zh, t.dst, u.uid, u.project, u.resref, u.src_text "
           "FROM unit_term ut JOIN unit u ON u.uid = ut.uid "
           "LEFT JOIN tm t ON t.key = u.src_key "
           "WHERE ut.required = 1 AND t.dst IS NOT NULL")
    params = []
    if project:
        sql += " AND u.project = ?"
        params.append(project)
    out = []
    for en, zh, dst, uid, proj, resref, src in unit_repo.db.execute(sql, params):
        if not zh or zh in dst:
            continue                                   # 完全采用 ✓
        if fuzzy_head and len(zh) > fuzzy_head and zh[:fuzzy_head] in dst:
            continue                                   # 降噪 ③
        key = (en or "").lower()
        if denoise and key not in conflict:
            continue
        # 降噪 ⑤（2026-10-10 加，关键）：**英文里该术语必须以"原样大小写"出现** ✓
        #   反例：对话里的动词 `run` 命中了术语 `Run`（飞跑）→ 146 条误报 ✗；
        #         `bolt`（弩矢）命中了法术 `Bolt`（箭石术）→ 145 条 ✗。
        #   判据依赖"**专名形态**"这个结构特征 ✓（宁缺勿滥）。
        if denoise and src and not re.search(
                r"(?<![A-Za-z])" + re.escape(en) + r"(?![A-Za-z])", src):
            continue
        # 降噪 ⑥（2026-10-10 加）：前面紧邻大写词 = 复合专名的一部分，不单独报 ✓
        if denoise and src and _in_compound(src, en):
            continue
        if any(z in dst for z in conflict.get(key, ())):
            continue                                   # 降噪 ④
        out.append((en, zh, dst, uid, proj, resref))
    out.sort()
    return out[:limit] if limit else out


def _term_buckets(terms):
    """按术语**首词**建倒排桶（含词干变体）—— `rebuild_unit_terms` 的性能关键。

    原实现对每条源文遍历**全部**术语做正则 `search` ✗ ——
    1722 条 × 30,205 条源文 ≈ 5,200 万次匹配，实测 **3 分 51 秒** ✗。
    改成：源文切词 → 用词/词干查桶 → 只对候选做完整正则 ✓（正确性由"最后仍用
    `_tolerant_re` 确认"保证，最坏情况是漏候选 —— 所以词干要切得宽一点）。
    """
    buckets = {}
    for t in terms:
        en = (t.get("en") or "").strip()          # ⚠ `glossary_of` 给的是 **dict 列表**，不是 Term 对象 ✗
        if not en:
            continue
        first = re.split(r"[^A-Za-z']+", en)[0].lower()
        if first:
            buckets.setdefault(first, []).append(t)
    return buckets


def _coarse_hit(el, tl):
    """廉价粗筛：判断术语的「字面或其常见变形」是否出现在小写源文里。

    ⚠ 必须**宁可放过、不可漏掉** ✗ —— 漏掉 = 该源文的术语命中丢失 = 违规清单漏报 ✓
    （实测第一版只试 `el` 和 `el[:-2]+"i"`，漏了 `elf`→`elves` 这类不规则复数，
     结果少 7 条源文 / 155 行 ✗）
    """
    if el in tl:
        return True
    if len(el) >= 4 and el[:-1] in tl:                 # 去尾字母（覆盖多数复数/变格）
        return True
    if el.endswith("f") and (el[:-1] + "ves") in tl:   # elf → elves / wolf → wolves
        return True
    if el.endswith("fe") and (el[:-2] + "ves") in tl:  # knife → knives
        return True
    if el.endswith("y") and (el[:-1] + "ies") in tl:   # lady → ladies
        return True
    if el.endswith("us") and (el[:-2] + "i") in tl:    # Magus → Magi（拉丁第二变格）
        return True
    if el.endswith("is") and (el[:-2] + "es") in tl:   # analysis → analyses
        return True
    return False


def rebuild_unit_terms(unit_repo, tm_repo, term_repo, required_only=True):
    """**重建 `unit_term`**（"某个单元命中了哪些必守术语"的缓存）—— 驱动上面的违规清单。

    ★ 性能要点（这个功能此前一直是**空的孤儿表**，2026-10-10 才补上 ✗）：
      * **按源文去重**后再匹配：走 `tm` 表的 43,945 条，不是 `unit` 的 64,929 条 ——
        同一源文的命中结果必然相同 ✓；
      * 术语只取 `required = 1`（约 1700 条）：全量 6700 条 × 全库 = 2 亿次匹配 ✗；
      * **按首词建倒排桶**：切词后只对候选做正则，把 5,200 万次匹配降到百万级 ✓

    返回 `{"terms", "texts", "rows"}`。
    """
    g = glossary_of(term_repo, required_only=required_only)
    # ⚠ **性能优化试过 3 个方案，全部因"漏检"回退**（2026-10-10 实测）：
    #   ① 首词倒排桶：8.5 秒，但命中源文 30,205 → 29,956（漏 249 条源文）✗
    #   ② 手工切词 + 词干：漏 359 条 ✗
    #   ③ `in` 粗筛（含不规则复数兜底）：40~60 秒，仍漏 7 条源文 / 155 行 ✗
    #   根因：术语在源文里的**形态空间是开放的**（所有格 `Dragon's`、派生词 `spellcasters`、
    #   不规则复数 `elf→elves`、拉丁变格…），任何"先粗筛再精筛"都可能漏 ✗。
    #   **违规清单的价值全在"不漏报"** ✓，所以这里坚持**全量遍历**（约 3 分 51 秒）——
    #   而这个操作**只在术语表变更后才需要跑一次**，不是高频操作 ✓。
    hits_by_key = {}
    for key, src in tm_repo.db.execute("SELECT key, src FROM tm WHERE status!='rejected'"):
        h = hits(term_repo, src, glossary=g)
        if h:
            hits_by_key[key] = h
    db = unit_repo.db
    db.execute("DELETE FROM unit_term")
    rows = []
    for uid, src_key in db.execute("SELECT uid, src_key FROM unit"):
        for en, zh, req in hits_by_key.get(src_key, ()):
            rows.append((uid, en, zh, 1 if req else 0, "glossary"))
    db.executemany("INSERT OR REPLACE INTO unit_term(uid,en,zh,required,src) "
                   "VALUES(?,?,?,?,?)", rows)
    db.commit()
    return {"terms": len(g.terms), "texts": len(hits_by_key), "rows": len(rows)}


def glossary_of(term_repo, status="active", required_only=False):
    """把 `term` 表里的生效术语构造成 modl10n 的 `Glossary`（匹配规则唯一真源）。

    批量场景**只构造一次**再复用，别在循环里反复构造。

    `required_only=True`：只要**必守**术语 —— 全库体检必须用它 ✗：
    术语库从 213 涨到 **6700** 条后，"30,168 条 × 6700 术语 = 2 亿次匹配"要跑 **~6 分钟** ✗，
    而后台线程受 **GIL** 制约，照样卡住界面 ✗（2026-10-08 用户实测反馈"点击词条就卡住"）。
    只查必守（**799 条**）→ 约 30 秒 ✓，而且**语义上也更对**：不必守的本来就不该报违规 ✓。
    """
    terms = term_repo.list(status=status) if status else term_repo._all_active()
    if required_only:
        terms = [t for t in terms if t.required]
    return Glossary([{"en": t.en, "zh": t.zh, "type": t.type,
                      "note": t.note or "", "required": bool(t.required)}
                     for t in terms])


def hits(term_repo, text, glossary=None, tolerant=True):
    """原文命中了哪些术语 —— 返回 `[(en, zh, required)]`。

    `tolerant=True`（默认）走**宽容匹配**（大小写 + 复数容忍，见 `_tolerant_re`）。
    """
    if not text:
        return []
    g = glossary or glossary_of(term_repo)
    if not tolerant:
        return [(en, zh, bool(g._by_req.get(en, True))) for en, zh in g.hits(text)]
    out = []
    matched = []
    for t in g.terms:
        en = t.get("en") or ""
        if en and _tolerant_re(en).search(text):
            matched.append((en, t.get("zh") or "", bool(g._by_req.get(en, True))))
    # ★ **最长匹配优先**（2026-10-09）：去掉"英文被更长命中术语包含"的那些 ✗ ——
    #   否则 `Shadow Blade`（影之刃）会**同时**报出 `Shadow`（幽影），术语条显示两条、纯属噪音 ✓。
    #   （这与译名漂移审计里学到的"判据要依赖结构"是同一个道理 ✓）
    lows = [e.lower() for e, _z, _r in matched]
    for en, zh, req in matched:
        el = en.lower()
        if any(el != o and el in o for o in lows):
            continue
        out.append((en, zh, req))
    return out


def check(term_repo, src, dst, glossary=None, tolerant=True):
    """守必校验 —— 返回未遵守规范译名的 `[(en, 规范译名)]`。

    ★ **与 `hits` 用同一套匹配规则**（用户 2026-10-08 明确提醒）：否则会出现
    "术语条说命中了、校验却说没遵守"的自相矛盾，比不检查还糟。
    """
    if not (src and dst):
        return []
    g = glossary or glossary_of(term_repo)
    if not tolerant:
        return g.check(src, dst)
    bad = []
    for en, zh, req in hits(term_repo, src, glossary=g, tolerant=True):
        if req and zh and zh not in dst:
            bad.append((en, zh))
    return bad


def status_of_text(term_repo, src, dst, glossary=None, tolerant=True):
    """一条（原文, 译文）的术语状态。

    返回 `{"hits": [(en, zh, required)], "missing": [(en, zh)], "ok": bool}`

        hits    —— 原文里命中了哪些术语（界面展示"这条涉及哪些术语"）
        missing —— 其中**必守**、但译文没用规范译名的（界面标红）
        ok      —— 没有违规
    """
    g = glossary or glossary_of(term_repo)
    hs = hits(term_repo, src, glossary=g, tolerant=tolerant)
    missing = check(term_repo, src, dst, glossary=g, tolerant=tolerant)
    return {"hits": hs, "missing": missing, "ok": not missing}


def violation_summary(term_repo, pairs, limit=None, glossary=None):
    """一批条目的违规汇总（界面刷新/全库体检用）。

    `pairs` 可以是 `{id: (src, dst)}` 或 `[(id, src, dst), ...]`。
    返回 `{"checked": n, "bad": n, "by_id": {id: [(en, zh)]}}`
    """
    # ★ 体检只查**必守**（6700 → 799 条）：全量扫描要 ~6 分钟，且因 **GIL** 照样卡界面 ✗
    g = glossary or glossary_of(term_repo, required_only=True)
    if isinstance(pairs, dict):
        items = [(i, v[0], v[1]) for i, v in pairs.items()]
    else:
        items = list(pairs)
    if limit:
        items = items[:limit]
    by_id, checked = {}, 0
    for n, (i, src, dst) in enumerate(items):
        if n % 1000 == 0:
            time.sleep(0.001)      # ★ 主动让出 GIL：后台体检不该把界面卡死
        checked += 1
        if not (src and dst):
            continue
        bad = check(term_repo, src, dst, glossary=g)     # 与 hits 同一套宽容规则
        if bad:
            by_id[str(i)] = bad
    return {"checked": checked, "bad": len(by_id), "by_id": by_id}


def fuzzy_suggest(term_repo, text, limit=10, only_required=False):
    """模糊搜术语（界面上的「术语查询」框直接用）。"""
    return term_repo.fuzzy(text, limit=limit, only_required=only_required)


def suggest_for_src(term_repo, src, limit=8, only_required=False):
    """为一条原文给出**相关术语建议**：先用精确命中，再用模糊补足。

    比直接 `fuzzy(整句)` 更实用 —— 整句模糊匹配基本搜不到东西。
    """
    g = glossary_of(term_repo)
    out, seen = [], set()
    for en, zh, req in hits(term_repo, src, glossary=g):
        t = Term(en=en, zh=zh, required=1 if req else 0)
        out.append((t, 1.0, "命中"))
        seen.add(en)
    if len(out) < limit:
        for word in _words(src or ""):
            for t, score, why in term_repo.fuzzy(word, limit=3, only_required=only_required):
                if t.en not in seen:
                    out.append((t, score, "模糊·" + why))
                    seen.add(t.en)
            if len(out) >= limit:
                break
    return out[:limit]


def _words(text, min_len=4):
    """把原文切成候选词（够长的英文词才值得去模糊搜）。"""
    import re
    return [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]{%d,}" % (min_len - 1), text)]


def prompt_block(term_repo, text, max_terms=40):
    """译前注入用的提示块（复用 modl10n 的分级逻辑）。"""
    return term_repo.prompt_block(text, max_terms=max_terms)
