#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`翻译流水线.py` 的 GUI 层验收（只读；绝不调 API、绝不改 tm ✓）。

用法：
    cd scripts
    python -m l10n.selftest_gui_pipeline
"""
import importlib.util
import os
import sys
import traceback

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

from l10n.db import connect                                    # noqa: E402
from l10n.services import diff_service, plan_service           # noqa: E402

GUI = os.path.join(SCRIPTS, "翻译流水线.py")
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
    print("翻译流水线 GUI 验收")
    spec = importlib.util.spec_from_file_location("pipeline", GUI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = None
    try:
        app = mod.App()
        app.update()

        print("\n[1] 界面与四个动作都在")
        for name in ("do_scan", "do_extract", "do_diff", "do_plan", "do_all"):
            chk("动作 %s 存在" % name, callable(getattr(app, name, None)))
        for name in ("log", "mod", "proj", "pin", "pout"):
            chk("控件 %s 存在" % name, getattr(app, name, None) is not None)

        print("\n[2] ★ 只读的两步真能跑（真实库，不写任何东西）")
        db = connect(os.path.join(SCRIPTS, "modl10n", "tm.sqlite"), migrate_now=False)
        d = diff_service.diff(db, project="prc8", sample=1)
        chk("diff 能算「还差哪些」", "units_all" in d and d["units_all"] > 0,
            "单元 %s / 待译 %s" % (d["units_all"], d["uniq_todo"]))
        p = plan_service.plan(db, project="prc8", price_in=1.0, price_out=2.0, sample=1)
        chk("plan 能估价（不花钱）", isinstance(p.get("cost"), (int, float)) and p["cost"] >= 0,
            "预估 %.4f 元 / %s 条" % (p.get("cost", -1), p.get("items")))
        chk("summarize 是一句人话", "预估花费" in plan_service.summarize(p))
        chk("日志区是只读的", str(app.log.cget("state")) == "disabled")
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
    print("结果：%s" % ("✅ 翻译流水线 GUI 全部通过" if not _bad else "✗ %d 项失败" % _bad))
    return 1 if _bad else 0


if __name__ == "__main__":
    sys.exit(main())
