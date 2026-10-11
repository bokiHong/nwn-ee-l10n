#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ExtractService —— 容器 -> `unit`（位置层）落库。

流水线第 2 步。**复用 modl10n 的抽取器**（`modl10n.extract.extract_units`），
一行都不重写抽取逻辑 —— 只负责把它的 `Unit` 映射成 `StringUnit` 并落库。

映射要点：
  * `Unit.locator`（tuple）序列化成 JSON，回写器原样能用；
  * `uid` 由"容器 + 资源 + 类型 + locator"决定，所以**同一位置永远是同一个 uid**，
    这是"第二次跑全都 kept"（幂等）的根基；
  * `src_key` 走 `l10n.keys`（默认 norm），与 TLK 流程一致；
  * `status` 由 `UnitRepo.upsert_many` 按 src_key 变化判定，本模块不自己判。
"""
import json
import os
import re

from ..keys import raw_hash, src_key
from ..model import StringUnit
from ..repo import ResourceRepo, UnitRepo
from .scan_service import require_container

# 必须原样保留的记号：引擎标记 / 变量（复用 modl10n 的 TOKEN_RE）+ 骰子 / DC / 带符号数值
try:
    from modl10n.core.sfrules import TOKEN_RE as _TOKEN_RE
    _PAT = _TOKEN_RE.pattern
except ImportError:                     # 兜底：与 sfrules 保持一致
    _PAT = r"</?[A-Za-z][^>]*>|%\([^)]*\)|%[sdif]|\$\{[^}]*\}"
PLACEHOLDER_RE = re.compile(
    _PAT + r"|\b\d+[dD]\d+\b|\bDC\s*\d+\b|[+-]\d+")


def placeholders_of(text):
    """抽出"改一处就会坏事"的记号清单（去重、排序）。"""
    return sorted(set(PLACEHOLDER_RE.findall(text or "")))


def field_path_of(unit, ext):
    """给人看的定位。GFF 用字段名，.nss 用行号+字面量序号。"""
    if unit.kind == "gff":
        return "%s:%s" % (ext, unit.label if unit.label else "?")
    if unit.kind == "nss":
        return "nss:%s#%s" % (unit.lineno, unit.lit_index)
    return str(unit.locator)


def to_string_units(erf, units, project, container_id):
    """modl10n 的 Unit 列表 -> StringUnit 列表（纯函数，不碰库）。"""
    out = []
    for u in units:
        ent = erf.entries[u.keyidx]
        resref, ext = (ent.resref or "").lower(), (ent.ext or "").lower()
        locator = json.dumps(list(u.locator))
        out.append(StringUnit(
            uid=StringUnit.make_uid(container_id, resref, u.kind, locator),
            project=project, container_id=container_id,
            resref=resref, ext=ext, kind=u.kind,
            field_path=field_path_of(u, ext), locator=locator,
            strref=getattr(u, "strref", None),
            src_text=u.text, src_key=src_key(u.text),
            src_hash_seen=raw_hash(u.text),
            storage="inline",
            placeholders=json.dumps(placeholders_of(u.text), ensure_ascii=False),
            status="new"))
    return out


def read_tlk_texts(path, decode="replace"):
    """读 TLK -> {strref(str): 文本}。

    实现已抽到 `l10n\\tlkio.py`（repo 层也要用，避免 repo 反向依赖 services）；
    `decode` 的取舍见方案文档 10.1。
    """
    from ..tlkio import read_texts
    return read_texts(path, decode=decode)


def extract_tlk(db, project, path, only_needs_translation=True, decode="replace",
                verbose=True):
    """TLK -> unit（kind='tlk'、storage='tlk_slot'）。

    **要点：请抽"英文源 TLK"（如 `prc8_consortiumE.tlk`），不要抽已汉化的那个。**
    理由：本工作台的模型是"译文只存在 tm 里"。抽英文源 → `src_key` 指向 tm →
    喂给 diff 就能算出"哪些还没翻"；抽已汉化的 TLK 则 src_text 本身是中文，
    与 tm 的英文键对不上，diff 会荒谬地报"全部待译"。

    已汉化的 TLK 应该登记成 `role='installed'` 的容器，用于"产物对账"，
    而不是当成翻译来源。
    """
    from modl10n.tlk import needs_translation

    path = os.path.abspath(path)
    cid, fmt = require_container(db, project, path, verbose=False)
    texts = read_tlk_texts(path, decode=decode)   # {strref(str): 文本}
    resref = os.path.splitext(os.path.basename(path))[0].lower()
    su = []
    for k, t in texts.items():
        if only_needs_translation and not needs_translation(t):
            continue
        locator = json.dumps(["tlk", int(k)])
        su.append(StringUnit(
            uid=StringUnit.make_uid(cid, resref, "tlk", locator),
            project=project, container_id=cid, resref=resref, ext="tlk", kind="tlk",
            field_path="tlk:%s" % k, locator=locator, strref=int(k),
            src_text=t, src_key=src_key(t), src_hash_seen=raw_hash(t),
            storage="tlk_slot",
            placeholders=json.dumps(placeholders_of(t), ensure_ascii=False),
            status="new"))
    urepo, rrepo = UnitRepo(db), ResourceRepo(db)
    stats = urepo.upsert_many(su)
    gone = urepo.mark_gone(cid, [s.uid for s in su])
    rrepo.refresh_unit_counts(cid)
    uniq = len({s.src_key for s in su})
    if verbose:
        print("  TLK 槽位 %d 个有文本；需要翻译的 %d 个（去重后 %d 条不同文本）"
              % (len(texts), len(su), uniq))
        print("  落库：新建 %d / 未变 %d / 源文变了 %d；标记消失 %d"
              % (stats["new"], stats["kept"], stats["src_changed"], gone))
    return {"container_id": cid, "path": path, "fmt": "tlk", "units": len(su),
            "unique": uniq, "stats": stats, "gone": gone, "slots": len(texts)}


def extract(db, project, path, include_nss=True, var_assign=True, decode="replace",
            verbose=True):
    """抽取一个容器里的全部可译单元并落库。幂等。

    自动按格式分发：`.tlk` 走 `extract_tlk`，其余（`.mod`）走 ERF 抽取。

    返回 report：{"container_id", "units", "unique", "stats", "gone", "resources"}
    """
    from modl10n.extract import extract_units

    if os.path.splitext(path)[1].lower() == ".tlk":
        return extract_tlk(db, project, path, decode=decode, verbose=verbose)

    path = os.path.abspath(path)
    cid, fmt = require_container(db, project, path, verbose=False)
    erf, units = extract_units(path, include_nss=include_nss, var_assign=var_assign)
    su = to_string_units(erf, units, project, cid)
    urepo, rrepo = UnitRepo(db), ResourceRepo(db)
    stats = urepo.upsert_many(su)
    gone = urepo.mark_gone(cid, [s.uid for s in su])
    rrepo.refresh_unit_counts(cid)
    uniq = len({s.src_key for s in su})
    if verbose:
        print("  抽取：%d 个单元（去重后 %d 条不同文本）" % (len(su), uniq))
        print("  落库：新建 %d / 未变 %d / 源文变了 %d；标记消失 %d"
              % (stats["new"], stats["kept"], stats["src_changed"], gone))
    return {"container_id": cid, "path": path, "fmt": fmt, "units": len(su),
            "unique": uniq, "stats": stats, "gone": gone,
            "resources": len(erf.entries)}
