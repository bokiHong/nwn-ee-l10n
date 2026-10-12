#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ASCII-only launcher for the TLK editor (`tlk tool.py`).

（同 `launch_editor.py` / `launch_pipeline.py`：`.bat` 必须全 ASCII，
中文文件名交给 Python 打开 ✓ —— 这个工具以前一直没有 bat，只能从别处起，现在补齐 ✓）
"""
import os
import runpy
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "tlk tool.py")

if not os.path.exists(TARGET):
    print("!! TLK editor not found: %s" % TARGET)
    sys.exit(1)

try:
    runpy.run_path(TARGET, run_name="__main__")
except SystemExit:
    raise
except Exception:
    traceback.print_exc()
    sys.exit(1)
