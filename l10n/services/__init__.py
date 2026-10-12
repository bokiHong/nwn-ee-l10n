#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""services 层：把"一件事"写成一个函数，GUI 与 CLI 都只调这一层。

    scan_service     scan     容器 -> container + resource
    extract_service  extract  容器 -> unit（位置层）；TLK 走 extract_tlk
    diff_service     diff     算出"还要翻哪些"（只读）
    plan_service     plan     花钱前的影响面预览（只读，不调 API）
    tlk_service      TlkDoc   TLK 编辑的业务逻辑（Sprint 6）
    term_service     术语     实时违规检查（Sprint 6）
    mod_service      ModDoc   .mod 编辑的业务逻辑（Sprint 7）
    pack_service     repack   打包（固定离线占位翻译器，绝不调 API）（Sprint 7）
    review_service   审校表   TSV 导出/回导门面（Sprint 7）

后续 Sprint 会往这里加 translate / apply / impact。
"""
from .scan_service import scan, require_container, guess_fmt              # noqa: F401
from .extract_service import extract, extract_tlk, to_string_units       # noqa: F401
from .diff_service import diff, summarize as summarize_diff              # noqa: F401
from .plan_service import plan, summarize as summarize_plan, estimate    # noqa: F401
from .tlk_service import TlkDoc, open_tlk, import_txt, save_to_memory    # noqa: F401
from .tlk_fill_service import fill_tlk, plan_fill                        # noqa: F401
from .rebuild_service import survey as survey_rebuild                    # noqa: F401
from .rebuild_service import rebuild_and_install as rebuild_and_install_tlk  # noqa: F401
from .term_service import status_of_text, violation_summary              # noqa: F401
from .term_editor_service import guess_type, save_term                   # noqa: F401
from .mod_service import ModDoc, open_mod_doc, nl_of, raw_key            # noqa: F401
from .pack_service import make_repack_args, repack, OfflineTranslator    # noqa: F401
from .review_service import export_table, apply_table                    # noqa: F401

__all__ = ["scan", "require_container", "guess_fmt",
           "extract", "extract_tlk", "to_string_units",
           "diff", "summarize_diff", "plan", "summarize_plan", "estimate",
           "TlkDoc", "open_tlk", "import_txt", "save_to_memory",
           "fill_tlk", "plan_fill", "guess_type", "save_term",
           "survey_rebuild", "rebuild_and_install_tlk",
           "status_of_text", "violation_summary",
           "ModDoc", "open_mod_doc", "nl_of", "raw_key",
           "make_repack_args", "repack", "OfflineTranslator",
           "export_table", "apply_table"]
