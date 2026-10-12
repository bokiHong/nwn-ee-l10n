#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`汉化工作台.py`（统一启动器）的 GUI 层验收 —— **只读，不启动任何子工具** ✓。

用法：
    cd scripts
    python -m l10n.selftest_gui_workbench
"""
import importlib.util
import os
import sys
import traceback

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

GUI = os.path.join(SCRIPTS, "汉化工作台.py")
_bad = 0


def chk(name, ok, detail=""):
    global _bad
    if not ok:
        _bad += 1
    print("  %s %-52s %s" % ("✓" if ok else "✗", name, detail))


def main():
    global _bad
    _bad = 0
    print("=" * 84)
    print("汉化工作台（统一启动器）GUI 验收")
    import tkinter.messagebox as mb
    mb.showinfo = mb.showwarning = mb.showerror = lambda *a, **k: None
    spec = importlib.util.spec_from_file_location("workbench", GUI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = None
    try:
        app = mod.App()
        app.update()
        print("\n[1] 四个工具入口")
        chk("入口数量 = 4", len(mod.TOOLS) == 4, str([t[0] for t in mod.TOOLS]))
        miss = [b for _t, _d, b, _m in mod.TOOLS if not os.path.exists(os.path.join(SCRIPTS, b))]
        chk("★ 每个入口的启动 bat 都真实存在", not miss, str(miss))
        print("\n[2] 花钱标记")
        chk("★ 只有 TLK 编辑器被标为「会花钱」",
            [bool(t[3]) for t in mod.TOOLS] == [False, True, False, False],
            str([(t[0], t[3]) for t in mod.TOOLS]))
        print("\n[3] 上下文与日志")
        chk("最近文件控件存在", app.recent is not None)
        chk("项目名控件存在", app.proj is not None)
        chk("日志区只读", str(app.log.cget("state")) == "disabled")
    except Exception:
        chk("用例无异常", False)
        print(traceback.format_exc())
    finally:
        if app is not None:
            try:
                app.destroy()
            except Exception:                                  # noqa: BLE001
                pass
    print()
    print("=" * 84)
    print("结果：%s" % ("✅ 汉化工作台 GUI 全部通过" if not _bad else "✗ %d 项失败" % _bad))
    return 1 if _bad else 0


if __name__ == "__main__":
    sys.exit(main())
