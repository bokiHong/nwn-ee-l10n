#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ASCII-only launcher for the workbench GUI.

（同 `launch_editor.py`：`.bat` 内容必须全 ASCII，中文文件名交给 Python 打开 ✓）
"""
import os
import runpy
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "汉化工作台.py")

if not os.path.exists(TARGET):
    print("!! workbench GUI not found: %s" % TARGET)
    sys.exit(1)

try:
    runpy.run_path(TARGET, run_name="__main__")
except SystemExit:
    raise
except Exception:
    traceback.print_exc()
    sys.exit(1)
