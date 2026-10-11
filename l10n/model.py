#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一数据模型的数据类（StringUnit / Resource / TlkSlot / Term）。

字段与 `l10n\\db.py` 的 DDL **一一对应**，改动必须两边同时改。
设计要点见 `scripts\\汉化工作台_整合方案.md` 第三节：

  * `StringUnit` **不持有译文**，只持有 `src_key`（指向 tm.key）与 `src_hash_seen`；
  * `field_path` 给人看，`locator` 给机器回写用（JSON）；
  * `TlkSlot` 是槽位账本，取代"英文导出里没有的就算自加"的启发式反推。
"""
import hashlib
import json
from dataclasses import dataclass, fields


def _sha1_16(*parts):
    """稳定短哈希：同一组输入永远得到同一个 id。"""
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def _json_or_none(v):
    return json.dumps(v, ensure_ascii=False) if v is not None else None


# ============================================================ 单元
@dataclass
class StringUnit:
    """一条"可译出现"。注意：**它不持有译文**。"""

    uid: str
    project: str
    container_id: int
    resref: str
    ext: str
    kind: str                       # gff | nss | tlk
    field_path: str = None          # 给人看的定位
    locator: str = None             # 给机器回写用的定位（JSON 字符串）
    strref: int = None              # GFF/TLK 原有的 strref（.nss 单元为 None）
    src_text: str = None
    src_key: str = None             # -> tm.key
    src_hash_seen: str = None       # 源文上次快照（判变更）
    storage: str = "inline"         # inline | tlk_strref | tlk_slot
    context: str = None
    placeholders: str = None        # JSON
    term_hits: str = None           # JSON
    status: str = "new"             # new|kept|src_changed|gone|skipped|ignored
    first_seen: str = None
    last_seen: str = None

    # ---------------------------------------------------------- 映射
    @classmethod
    def from_row(cls, row):
        """sqlite3.Row -> StringUnit（缺列时用默认值）。"""
        d = dict(row)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def as_row(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}

    # ---------------------------------------------------------- 便捷
    @staticmethod
    def make_uid(container_id, resref, kind, locator):
        """稳定主键：容器 + 资源 + 类型 + 定位。同一位置永远是同一个 uid。"""
        loc = locator if isinstance(locator, str) else json.dumps(list(locator))
        return _sha1_16(container_id, resref.lower(), kind, loc)

    @property
    def locator_list(self):
        """locator（JSON 字符串）-> Python list；用于直接喂给回写器。"""
        if not self.locator:
            return None
        try:
            return json.loads(self.locator)
        except (ValueError, TypeError):
            return None

    def __repr__(self):
        return "<StringUnit %s %s:%s %r>" % (
            self.kind, self.resref, self.field_path or self.locator,
            (self.src_text or "")[:36])


# ============================================================ 资源
@dataclass
class Resource:
    rid: str
    container_id: int
    resref: str
    ext: str
    size: int = None
    sha256: str = None
    gff_type: str = None
    deps: str = None                # JSON
    units_total: int = 0
    units_done: int = 0
    change_state: str = "new"       # new | unchanged | modified | removed
    first_seen: str = None
    last_seen: str = None

    @classmethod
    def from_row(cls, row):
        d = dict(row)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def as_row(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @staticmethod
    def make_rid(container_id, resref, ext):
        return _sha1_16(container_id, resref.lower(), ext.lower())

    def __repr__(self):
        return "<Resource %s.%s>" % (self.resref, self.ext)


# ============================================================ TLK 槽位
@dataclass
class TlkSlot:
    """TLK 槽位账本的一行 —— 专治"ID 冲突"与"编号记错"。"""

    tlk_path: str
    strref: int
    owner: str
    purpose: str = "reserved"       # engine_text | routed | reserved
    text_key: str = None            # 内容哈希 -> tm.key
    protected: int = 1              # 1 = 重建时绝不能清
    note: str = None
    updated: str = None

    @classmethod
    def from_row(cls, row):
        d = dict(row)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def as_row(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def __repr__(self):
        return "<TlkSlot %s#%d %s%s>" % (
            self.owner, self.strref, self.purpose,
            " 受保护" if self.protected else "")


# ============================================================ 术语
@dataclass
class Term:
    """一条术语。同一英文可以有**多行**：active（生效）+ candidate（草案候选）。"""

    en: str
    zh: str
    type: str = "name"              # name|place|deity|class|spell|item|misc
    required: int = 1
    status: str = "active"          # active | candidate | rejected
    scope: str = "*"                # * 或项目名
    src: str = None                 # glossary|override|draft|manual（溯源）
    note: str = None
    updated: str = None
    seq: int = None                 # 导入顺序（导出 glossary.json 要逐字节等价）

    @classmethod
    def from_row(cls, row):
        d = dict(row)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def as_row(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @property
    def is_required(self):
        return bool(self.required)

    def __repr__(self):
        return "<Term %s = %s%s>" % (self.en, self.zh, " [必守]" if self.required else "")
