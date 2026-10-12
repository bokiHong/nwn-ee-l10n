#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""术语表：专有名词（人名/地名/神祇/法术名）统一。

用法分三步：
  1. `build_candidates()` 从已有翻译记忆里挖出"独立成条的名称 -> 译名"候选；
  2. 人工/LLM 审校成术语表（历史资产是 `glossary.json`，2026-10-10 起真源是
     `tm.sqlite` 的 `term` 表）；
  3. 翻译时 `prompt_block()` 把"本句命中的术语"注入提示，`check()` 在译后强校验。

**两个来源（2026-10-11 新增第二个）**：
  * `Glossary.load(path)`       —— 冻结快照 `glossary.json`（2026-10-08，215 条，历史资产）
  * `Glossary.from_term_db(path)` —— **最新真源** `term` 表（6,585 条 active / 1,750 必守）
  * `Glossary.load_auto(path)`  —— 按扩展名自动选（`.sqlite`/`.db` -> term 表；其他 -> JSON）

    ⚠ 为什么必须加：`modl10n.pipeline` 一直只读 `glossary.json`，而 Sprint 4 之后
    术语真源已经搬到 `term` 表 —— 于是"翻译用的术语"比"术语库里的术语"落后了两个版本，
    **Ch1/Ch2 后期那 1000+ 行术语治理成果一条都用不上，而且不报任何错**（静默失效 ✗）。
    实测 Ch3 待译 6,640 条文本上：旧快照只命中 26 个术语，新真源命中 148 个 ✗✓。

**为什么分两档**（2026-10-11 实测：Ch3 待译 6,640 条 × 43,951 条 tm 语料）：
  `term` 表 6,585 条里混着大量"日常词冒充专名"—— `Trade`(商贸) / `Repose`(安息) /
  `Air`(气) / `Fine`(超微型) / `Failure`(失败) / `War`(战争) … 它们确实登记在册，
  但在普通语境里本来就该按字面译 ✗。若全部当"必守"塞进 `check()`，
  模型几乎每句都会被判"术语未统一" → 三档重试 → 白花钱还可能回退原文 ✗✗。

    · **硬档（required=True）**：`type ∈ HARD_TYPES`（或含空格的复合专名）且不在 `STOP_EN`
      —— 参与**译后强校验**，写进提示的【固定译名·必须使用】；
    · **软档（required=False）**：其余 active —— 只作【建议译名】注入，**不触发重试** ✓。

  `prompt_block()` 本来就把 two 档措辞分开写，`check()` 也只看 required ——
  所以分档**不需要改 `translator.py` 一个字节** ✓。

术语表 JSON 结构（`glossary.json` 冻结快照格式，导出时逐字节保持）：
{
  "version": 1,
  "terms": [
    {"en": "Zarala", "zh": "扎拉拉", "type": "name", "note": ""},
    ...
  ]
}
"""
import json
import os
import re
import sqlite3

# 独立成条、且是"单个大写开头的词"或"多词专名"
NAME_RE = re.compile(r"^[A-Z][A-Za-z'’\-]{2,20}$")
MULTI_RE = re.compile(r"^[A-Z][A-Za-z'’\-]+(?: [A-Z][A-Za-z'’\-]+){1,3}$")
ZH_NAME_RE = re.compile(r"^[\u4e00-\u9fff·]{2,10}$")

# ============================================================ 降噪常量（单一真源）
# ★ 2026-10-11：`STOP_EN` / `STRUCT_WORDS` / `in_compound` 从
#   `l10n/services/term_service.py` **提到这里**，因为现在**两处都要用**它们：
#     · `l10n.services.term_service`  —— 术语违规检查（报"该统一却没统一"）
#     · `modl10n.glossary`             —— 翻译时的译前注入 / 译后强校验
#   两份副本会漂移（一边拉黑 `doom` 另一边没拉黑 → 注入的术语被自己判违规 ✗），
#   所以定义留在本文件，l10n 那边改成 import。

# 结构化词：出现在术语前面时**不算**"复合专名的一部分"（否则 `Type of Feat: Adept` 会被误杀 ✗）
STRUCT_WORDS = {
    "type", "level", "feat", "feats", "spell", "spells", "class", "classes", "race",
    "prerequisite", "prerequisites", "specifics", "benefit", "school", "component",
    "components", "range", "target", "targets", "duration", "save", "casting", "area",
    "use", "caster", "the", "a", "an", "of", "and", "or", "in", "on", "at", "for",
    "with", "by", "to", "from", "this", "that", "these", "those", "your", "you",
}

# 即使 type 像专名，这些**普通词**也不该按术语硬处理（日常语境里就是普通含义）
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
    # ↓ 2026-10-11 加：**制作署名**（不是术语）—— PRC8 里那句
    #   `Crusaderky 修订制作保留` 是**制作人员署名**（与 `Leto` 是工具名同类 ✗），
    #   按术语报违规纯属噪声。凡"署名/工具名"混进术语表的都归这里 ✓
    "crusaderky",
}

# 硬档 type 白名单：这些 type 里绝大多数是**真专名** ✓
#   实测依据（Ch3 待译文本 × tm 语料）：`name`/`place`/`deity`/`prestige_class`/`class`/
#   `race`/`monster` 命中的几乎全是 `Zarala`/`Calimshan`/`Valkur`/`Red Wizard`/`Paladin`/
#   `Drow`/`Hobgoblin` 这类；
#   而 `domain`(35) / `term`(41) / `mantle`(11) 里混着 `Trade`/`Air`/`Repose`/`Fine`/`Failure` ✗，
#   `feat`/`spell`/`psionic`/`martial` 里的好条目多为**多词专名**（`Deep Impact`/`Twisted Rune`），
#   由下面的"含空格即硬档"规则捞回 ✓，不必整体放开。
HARD_TYPES = {"name", "place", "deity", "prestige_class", "race", "monster",
              "class", "item", "language", "creature"}


def in_compound(src, en, both=True):
    """术语**紧邻一个首字母大写的英文词** → 它只是复合专名的一部分 ✗

    实测（2026-10-10，595 处"专名形态但日常词义"的误报里的大头）：
      · `Dragonfire Adept`（龙火术士）里的 `Adept`（侍僧）→ 98 条误报 ✗
      · `Damage Resistance`（伤害减免）里的 `Resistance`（提升抗力）→ 69 条 ✗
      · `Bolt of Acid`（酸液箭）里的 `Bolt`（箭石术）→ 大量 ✗
    但**结构词要放行**：`Type of Feat: Adept` 里的 `Feat` 是结构词，这时 `Adept` 应按术语报 ✓

    ★ 2026-10-11 加 `both`（**双向**）：原来只看**前面**，于是 `Goblin Shark`（尖吻鲛）里的
      `Goblin`（地精）照样被报违规 ✗（实测 swordflight 2 处）。现在后面紧跟大写词也算复合专名 ✓
      代价是 `Goblin Attacks…` 这种句首大写也会被豁免 —— 但 violations/check 是**报告与重试**判据，
      少报比多报（白花钱重试）安全 ✓。
    """
    m = re.search(r"(?<![A-Za-z])" + re.escape(en) + r"(?![A-Za-z])", src)
    if not m:
        return False
    if m.start() > 0:
        pre = src[:m.start()].rstrip()
        parts = re.split(r"[^A-Za-z']+", pre)
        prev = parts[-1] if parts else ""
        if prev and prev[0].isupper() and prev.lower() not in STRUCT_WORDS:
            return True
    if both and m.end() < len(src):
        post = src[m.end():].lstrip()
        parts = re.split(r"[^A-Za-z']+", post)
        nxt = parts[0] if parts else ""
        if nxt and nxt[0].isupper() and nxt.lower() not in STRUCT_WORDS:
            return True
    return False


def _rx_of(en):
    """术语的**词边界**正则（缓存）—— 边界要对 `<...>` 这类标记也成立，故不用 `\\b`。"""
    rx = _RX_CACHE.get(en)
    if rx is None:
        rx = re.compile(r"(?<![A-Za-z])" + re.escape(en) + r"(?![A-Za-z])")
        _RX_CACHE[en] = rx
    return rx


_RX_CACHE = {}


def _strip_paren(zh):
    """剥掉术语值里的**括号消歧后缀**：`星质形态（灵能）` → `星质形态`。

    术语表内部用括号区分同名术语（`Ectopic Form` / `Ectoplasmic Form`），
    但正文里不会带这个括号 → 校验时必须先剥掉，否则永远误报 ✗。
    """
    return re.sub(r"[（(][^）)]*[）)]", "", zh or "").strip()


class Glossary(object):
    def __init__(self, terms=None):
        self.terms = []
        self._by_en = {}
        self._by_req = {}
        self._rx = _RX_CACHE          # 兼容旧字段名（同一份缓存，跨实例复用 ✓）
        self.conflicts = set()        # 同英多译（有歧义 -> 不注入）✗
        # ★ 译后校验是否用**宽容规则**（完全包含 / 前 2 字命中 / 去空格命中 都算采用）：
        #   `from_term_db()` 默认开 —— `term` 表里的译名有写法差异（`Nentyar Hunter` 对
        #   "南提亚 猎人"）、也有脏数据（`Wilder = 狂念者任意`），严格 `zh in dst` 会把
        #   好译文判成"术语未统一"→ 触发重试浪费钱 ✗。
        #   实测（4,000 条人工定稿译文）：严格 19.25% → 宽容 3.20%，且残留几乎全是
        #   **真不一致**（`Hexblade` 魔剑客/魔剑士、`Armor Bonus` 盔甲加值/护甲加值）✓。
        #   `load()`（JSON 快照）保持 False —— 旧行为逐字不变 ✓。
        self.tolerant = False
        self.hard = 0
        self.soft = 0
        for t in (terms or []):
            self.add(t["en"], t["zh"], t.get("type", "name"), t.get("note", ""),
                     t.get("required", True))

    def add(self, en, zh, type_="name", note="", required=True):
        if not en or not zh:
            return
        self.terms.append({"en": en, "zh": zh, "type": type_, "note": note,
                           "required": required})
        self._by_en[en] = zh
        self._by_req[en] = required
        if required:
            self.hard += 1
        else:
            self.soft += 1

    def __len__(self):
        return len(self.terms)

    def __repr__(self):
        return "<Glossary %d 条（硬档 %d / 软档 %d）>" % (len(self), self.hard, self.soft)

    @property
    def required_terms(self):
        return [t for t in self.terms if t.get("required", True)]

    # ---------------------------------------------------------------- 来源
    @classmethod
    def load(cls, path):
        """读冻结快照 `glossary.json`（**行为与 2026-10-08 版逐字一致**）。"""
        if not path or not os.path.exists(path):
            return cls([])
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(data.get("terms", []))

    @classmethod
    def from_term_db(cls, db_path, status="active", hard_types=None,
                     stop_en=None, required_only=False, on_conflict="skip",
                     tolerant=True):
        """从**最新真源** `term` 表构造（硬档 required / 软档建议，见模块 docstring）。

        db_path      : `modl10n/tm.sqlite`（只读打开，绝不写 ✓）
        hard_types   : 硬档 type 白名单（默认 `HARD_TYPES`）；含空格的复合专名一律硬档 ✓
        stop_en      : 普通词黑名单（默认 `STOP_EN`）；命中则整条**不注入**
        required_only: True = 只取 `term.required=1` 的（1,750 条，更保守）
        on_conflict  : 同一 `en` 有多条不同 `zh` 时（同英多译）怎么办 ——
                       `skip`（默认，不注入，避免把歧义喂给模型 ✓）/ `first` / `keep`
        tolerant     : 译后校验用宽容判据（默认 True，见 `__init__` 的实测数据）

        返回 `(Glossary, report)`；`report` 是条数明细（便于打印与测试断言）。
        """
        hard_types = HARD_TYPES if hard_types is None else set(hard_types)
        stop_en = STOP_EN if stop_en is None else set(stop_en)
        path = os.path.abspath(db_path)
        if not os.path.exists(path):
            raise IOError("找不到术语库：%s" % path)
        con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/").replace("?", "%3f"),
                              uri=True)
        try:
            sql = ("SELECT en, zh, type, required, note FROM term WHERE status=?"
                   " ORDER BY seq IS NULL, seq, rowid")
            if required_only:
                sql = sql.replace(" WHERE status=?", " WHERE status=? AND required=1")
            rows = con.execute(sql, (status,)).fetchall()
        except sqlite3.OperationalError as e:
            raise IOError("%s 里没有可用的 `term` 表（%s）—— 请用 glossary.json 回退"
                          % (path, e))
        finally:
            con.close()

        grouped = {}
        for en, zh, ty, req, note in rows:
            en = (en or "").strip()
            zh = (zh or "").strip()
            if not en or not zh:
                continue
            grouped.setdefault(en, []).append((zh, ty or "", bool(req), note or ""))

        terms, conflicts, skipped = [], set(), 0
        for en, items in grouped.items():
            zhs = {z for z, _t, _r, _n in items}
            if len(zhs) > 1:
                conflicts.add(en)
                if on_conflict == "skip":
                    skipped += 1
                    continue
                if on_conflict == "first":
                    items = items[:1]
            zh, ty, req, note = items[0]
            if en.strip().lower() in stop_en:
                skipped += 1
                continue
            # 硬档 = 用户标了必守 且（类型是真专名类 或 含空格的复合专名）
            hard = bool(req) and (ty in hard_types or " " in en.strip())
            terms.append({"en": en, "zh": zh, "type": ty, "note": note,
                          "required": hard})
        g = cls(terms)
        g.conflicts = conflicts
        # ★ 来自真源的 Glossary 默认开**宽容校验**（理由见 `__init__` 的注释与实测数据）
        g.tolerant = tolerant
        report = {"source": path, "source_kind": "term-db", "status": status,
                  "rows": len(rows), "terms": len(g), "hard": g.hard, "soft": g.soft,
                  "conflicts": len(conflicts), "skipped": skipped,
                  "tolerant": g.tolerant}
        return g, report

    @classmethod
    def load_auto(cls, path, **kw):
        """按扩展名自动选来源：`.sqlite`/`.db` -> `term` 表；其他 -> JSON 快照。

        返回 `(Glossary, report)`；JSON 来源时 `report["source_kind"] == "json-snapshot"`。
        """
        ext = os.path.splitext(str(path or ""))[1].lower()
        if ext in (".sqlite", ".sqlite3", ".db"):
            return cls.from_term_db(path, **kw)
        g = cls.load(path)
        g.hard = len([t for t in g.terms if t.get("required", True)])
        g.soft = len(g) - g.hard
        return g, {"source": path, "source_kind": "json-snapshot",
                   "terms": len(g), "hard": g.hard, "soft": g.soft}

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "terms": self.terms}, f, ensure_ascii=False, indent=2)

    # ---------------------------------------------------------------- 匹配
    def _hits_detail(self, text):
        """命中 -> `[(en, zh, type, required)]`（内部用）。"""
        out = []
        for t in self.terms:
            en = t["en"]
            # ★ 无损粗筛（2026-10-11）：`en` 是正则命中的**必要条件**，所以
            #   `en not in text` 直接跳过 **绝不会漏检** ✓（与"倒排/切词"那类有损
            #   粗筛不同 —— 那三个方案当年全漏检，已回退 ✗）。
            #   `in` 是 C 层子串查找，约比正则 `search` 快一个数量级：
            #   实测 6,585 条 × 6,640 条文本 从 101s 降到 ~4s ✓。
            if en not in text:
                continue
            if _rx_of(en).search(text):
                out.append((en, t["zh"], t.get("type", ""), t.get("required", True)))
        return out

    def hits(self, text):
        """返回本句出现的术语 [(en, zh)]。"""
        return [(en, zh) for en, zh, _ty, _rq in self._hits_detail(text)]

    def prompt_block(self, text, max_terms=40):
        hs = self.hits(text)
        if not hs:
            return ""
        hs = hs[:max_terms]
        hard = [(e, z) for e, z in hs if self._by_req.get(e, True)]
        soft = [(e, z) for e, z in hs if not self._by_req.get(e, True)]
        lines = []
        if hard:
            lines.append("【固定译名·必须使用】下列词出现在原文里，译文中必须原样使用括号内的中文，不得另译：")
            lines += ["  %s = %s" % (e, z) for e, z in hard]
        if soft:
            lines.append("【建议译名】尽量沿用（若语境确实不合可微调）：")
            lines += ["  %s = %s" % (e, z) for e, z in soft]
        return "\n".join(lines)

    def check(self, src, dst):
        """译后校验：原文命中的【必守】术语，译文里是否用上了规范译名。返回未命中的 [(en, zh)]。

        `self.tolerant=True` 时用**宽容判据**（与 `l10n.services.term_service.violations`
        同源）：完全包含 / 术语值前 2 字命中 / 去掉空格后命中 —— 都算"采用了" ✓。

        ★ 2026-10-11：术语值里的**括号后缀是术语表内部消歧用的**（`星质形态（灵能）`、
          `快速恢复（异怪之书）`），要求译文原样包含它永远是误报 ✗ → 先剥掉括号再比 ✓。
        """
        bad = []
        for en, zh in self.hits(src):
            if not self._by_req.get(en, True):
                continue
            if zh in dst:
                continue
            core = _strip_paren(zh)
            if core != zh and core in dst:
                continue
            if self.tolerant:
                if len(zh) > 2 and zh[:2] in dst:
                    continue
                if zh.replace(" ", "") in dst.replace(" ", ""):
                    continue
            bad.append((en, zh))
        return bad

    # ---------------------------------------------------------------- 候选挖掘
    @staticmethod
    def build_candidates(pairs, min_len=2):
        """pairs: [(英文原文, 中文译文)] -> [{'en','zh'}] 候选（仅"独立成条的名称"）。"""
        out = {}
        for en, zh in pairs:
            if not en or not zh or en == zh:
                continue
            e, z = en.strip(), zh.strip()
            if not (NAME_RE.match(e) or MULTI_RE.match(e)):
                continue
            if not ZH_NAME_RE.match(z):
                continue
            out[e] = z
        return [{"en": k, "zh": v} for k, v in sorted(out.items())]
