#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""repo 层：把中央库的读写收成"每个实体一个类"，GUI 与 CLI 都只跟它打交道。

Sprint 1 只实现**只读**方法（对应方案的"先只读实现对现有库的查询"），
写入类方法在后续 Sprint 随用随加，避免未经验证的代码先落地。

    TmRepo    翻译记忆（43,942 条）—— 顺手收编 tlk tool.py 里那两处裸 SQL
    TermRepo  术语（Sprint 4 起承接 glossary.json 等四份旧资产）
    UnitRepo  单元（位置层，Sprint 2 落库后有数据）
    TlkRepo   TLK 槽位账本（Sprint 5：认领 / 分配 / 版本基线）
"""
from .container_repo import ContainerRepo, ResourceRepo, sha256_of   # noqa: F401
from .tm_repo import TmRepo          # noqa: F401
from .term_repo import TermRepo      # noqa: F401
from .unit_repo import UnitRepo      # noqa: F401
from .run_repo import RunRepo        # noqa: F401
from .tlk_repo import TlkRepo        # noqa: F401

__all__ = ["TmRepo", "TermRepo", "UnitRepo", "ContainerRepo", "ResourceRepo",
           "RunRepo", "TlkRepo", "sha256_of"]
