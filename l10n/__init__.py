#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""l10n —— 无冬之夜汉化工作台的核心包（渐进式重构，不推翻 modl10n）。

设计前提（详见 scripts\\汉化工作台_整合方案.md）：
  * modl10n 是内核，本包**包住**它，而不是替代它；
  * 译文唯一存在 tm 表（key = sha256(norm(src))），本包的表只是"位置索引层"；
  * 索引层可以随时删掉重建 —— 这是回滚方式。
"""
import os
import sys

__version__ = "0.2.0"

# 让 `import modl10n` 在任何工作目录下都能工作（三个旧 GUI 也是这么做的）
SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
