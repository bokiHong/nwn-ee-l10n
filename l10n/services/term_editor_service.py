#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TermEditorService —— 术语编辑（**只写中央库 `term` 表**）。

★ 用户 2026-10-08 拍板：术语编辑器的改动**只写 `term` 表**，
**绝不自动覆盖 `glossary.json`** —— 后者降级为"纯导出快照"，需要时用
`python -m l10n terms --action export` 显式生成。

**为什么必须改**：Sprint 4 把真源换成 `term` 表之后，旧编辑器还在写
`glossary_overrides.json` + 跑 `make_glossary.py` → **界面改的译名对其它工具完全不可见**
（术语条 / fuzzy / 违规检查全读 `term` 表）✗；而 `mod tool.py` 打包时的术语强校验
也读旧快照 → "改了术语却不生效"（不报错、只是静默用旧值）。
"""
import re

from ..model import Term
from ..repo import TermRepo

# 分类自动推测规则（用户 2026-10-08：「分类=根据原文自动推测」）
_TYPE_RULES = (
    ("spell", r"\b(spell|invocation|essence|blast|maneuver|stance|psionic|power)\b"),
    ("class", r"\b(class|prestige|archetype)\b"),
    ("deity", r"\b(deity|god|goddess|domain|lord|lady|temple)\b"),
    ("item", r"\b(sword|axe|armor|ring|amulet|cloak|boots|helm|shield|potion|wand|rod)\b"),
    ("place", r"\b(city|town|village|forest|mountain|river|vale|keep|tower|isle)\b"),
)


def guess_type(en, default="name"):
    """按英文自动推测术语分类（加术语库时用，用户不必手选）。"""
    low = (en or "").lower()
    for tp, pat in _TYPE_RULES:
        if re.search(pat, low):
            return tp
    return default


def list_terms(term_repo, status="active"):
    """列术语（默认只列生效的 active）。"""
    return term_repo.list(status=status)


def candidates_of(term_repo, en, limit=3):
    """某英文的草案候选（界面的「dnd3r 参考」列）。"""
    return term_repo.candidates_of(en, limit)


def save_term(term_repo, en, zh, required=None, type_=None, note=None):
    """改一条术语的译名 / 必守 / 分类（**旧 active 行自动降级为 rejected**）。"""
    if not en or not zh:
        raise ValueError("英文与译名都不能为空")
    return term_repo.set_zh(en, zh, required=required, type=type_, note=note, src="manual")


def add_term(term_repo, en, zh, type_="name", required=True, note="术语库编辑器新增"):
    """新增术语（同 `en` 已有 active 行时是**覆盖语义**，不会多出一条 active）。"""
    if not en or not zh:
        raise ValueError("英文与译名都不能为空")
    return term_repo.set_zh(en, zh, required=required, type=type_, note=note, src="manual")


def import_draft(term_repo, path, min_count=1, limit=None):
    """导入草案 JSON → 写 `status='candidate'`（**不动 active**）。返回写入条数。"""
    terms = TermRepo.load_legacy_draft(path, min_count=min_count, limit=limit)
    return term_repo.upsert_many(terms)


def export_glossary(term_repo, path):
    """**显式**导出 `glossary.json` 快照（编辑器保存时不再自动做这件事）。"""
    return term_repo.export_json(path, status="active")


def term_hits(term_repo, text):
    """某段文本命中的术语（编辑时实时提示用）。"""
    return term_repo.hits(text)
