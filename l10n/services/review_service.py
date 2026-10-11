#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ReviewService —— 审校表（TSV）导出 / 回导的门面。

从 `mod tool.py` 的 `cmd_export` / `cmd_apply` 搬出来，**语义与旧实现逐字一致**：
内部仍然调 `modl10n.review.cmd_export` / `cmd_apply`（那是已经验证过、带 utf-8-sig BOM、
只改「现译」列、改动写 `status=manual` 的实现），这里只是把"拼 Namespace"这件事收到
一处，让 GUI 与 CLI 共用同一份参数。

⚠ `review` 内部自己开 `TM(tm_path)` 连接 —— 这是 pipeline 侧的固有行为（与命令行
完全一致），所以这里传的是**库路径**而不是连接对象。
"""
import argparse
import os

from ..db import DEF_DB

try:
    from modl10n import review as RV
except ImportError:                       # pragma: no cover - modl10n 不在路径时
    RV = None

DEF_TM = DEF_DB


def export_table(modpath, out, tm_path=DEF_TM, filter=None, kind=None, fname=None,
                 min_len=0, limit=0, offset=0, no_nss=False):
    """导出审校表（8 列 TSV，utf-8-sig，Excel 不乱码）。返回 `review.cmd_export` 的返回值。"""
    return RV.cmd_export(argparse.Namespace(
        tm=tm_path, mod=os.path.abspath(modpath), out=out, filter=filter, kind=kind,
        fname=fname, min_len=min_len, limit=limit, offset=offset, no_nss=no_nss))


def apply_table(table, tm_path=DEF_TM, dry_run=False):
    """回导审校表（只吃「现译」列，改动写 `status=manual`，机器永不覆盖）。"""
    return RV.cmd_apply(argparse.Namespace(tm=tm_path, file=table, dry_run=dry_run))
