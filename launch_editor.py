#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ASCII-only launcher for the glossary editor.

为什么要这个文件：`.bat` 的内容必须全是 ASCII（cmd 按系统 GBK 读 .bat，
中文会被解成乱码并当成命令去执行 ✗），但编辑器文件名是中文。
所以让 .bat 只调用这个 ASCII 名字的脚本，由 Python 去打开中文名的编辑器 ✓。
"""
import os
import runpy
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "术语库编辑器.py")

if not os.path.exists(TARGET):
    print("!! editor not found: %s" % TARGET)
    sys.exit(1)

try:
    runpy.run_path(TARGET, run_name="__main__")
except SystemExit:
    raise
except Exception:
    tb = traceback.format_exc()
    try:
        with open(os.path.join(HERE, "term_editor_error.log"), "w", encoding="utf-8") as f:
            f.write(tb)
    except Exception:
        pass
    print(tb)
    print("\n== START FAILED ==  full log: term_editor_error.log")
    sys.exit(1)
