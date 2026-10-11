#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`mod tool.py` 的 GUI 层验收 —— 无头驱动 `App`，验证"界面 → service → 中央库"整条链路。

用法：
    cd scripts
    python -m l10n.selftest_gui_mod

设计要点：
  * 用**临时库**（`tm` 表现建）与**临时产物目录**，绝不碰真实 `tm.sqlite` 与用户目录；
  * `filedialog` / `messagebox` 是在 `run_gui()` **内部** import 的，只能 patch
    `tkinter.filedialog` / `tkinter.messagebox` 的**模块属性**；
  * hook 在 `mainloop` 的 after 回调里执行，**不能嵌套 `app.update()`** —— 所以不调
    异步的 `load_file`，而是直接构造 `ModDoc` 再喂给 `app.on_loaded()`（同一段代码路径，
    只是绕过后台线程）。
"""
import importlib.util
import os
import sys
import tempfile
import traceback

import tkinter.filedialog as tkfd                              # noqa: E402
import tkinter.messagebox as tkmb                              # noqa: E402

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
from l10n.db import connect                                    # noqa: E402
from l10n.services import mod_service                          # noqa: E402

MODTOOL = os.path.join(SCRIPTS, "mod tool.py")
# 测试资产是**本机私有文件**（商业模组），不进 Git 仓库 —— 找不到就跳过对应用例 ✓
#   L10N_TEST_MOD        真实 Ch1 .mod
#   L10N_TEST_MOD_SMALL  小样本 .mod
CH1 = os.environ.get("L10N_TEST_MOD") or os.path.join(
    SCRIPTS, "module", "Sword Flight", "原始英文模组", "_original",
    "Swordflight - Chapter One.mod")
SMALL = os.environ.get("L10N_TEST_MOD_SMALL") or os.path.join(
    SCRIPTS, "module", "Sword Flight", "SF01导出", "sf01", "work", "_erf_selftest.mod")

_bad = 0


def chk(name, ok, detail=""):
    global _bad
    if not ok:
        _bad += 1
    print("  %s %-50s %s" % ("✓" if ok else "✗", name, detail))


def load_tool():
    spec = importlib.util.spec_from_file_location("modtool", MODTOOL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def make_db(path):
    """建临时中央库（先让 modl10n 建 tm 表，再迁移出视图），**建完关连接**。"""
    from modl10n.tm import TM
    h = TM(path)
    h.db.close()
    db = connect(path)
    db.close()


# ================================================================ 用例
def case_ch1(app, ctx, box):
    """1) 真实 Ch1：守"抽取 + 去重"没漂（方案第十节实测值）。"""
    if not os.path.exists(CH1):
        print("  ⚠ 找不到 Ch1，跳过")
        return
    doc = mod_service.ModDoc(CH1, db=app._db())
    app.on_loaded(doc)
    chk("不重复文本 = 3325", len(doc.items) == 3325, str(len(doc.items)))
    chk("单元 = 5731", doc.n_units == 5731, str(doc.n_units))
    chk("资源 = 1934 / .ncs = 634", (doc.n_res, doc.n_ncs) == (1934, 634),
        "%s" % ((doc.n_res, doc.n_ncs),))
    st = doc.stat()
    # 注意：这是**临时空库**，所以全部条目都是"未译" —— 正是用来确认没读到真实库
    chk("临时空库 → 未译 3325（确认没碰真实库）", st["未译"] == 3325, str(st["未译"]))
    chk("临时库里 0 条译文", st["记忆库条数"] == 0, str(st["记忆库条数"]))
    chk("界面标题已更新", "Chapter One" in app.title(), app.title()[:40])
    chk("列表已填充", len(app.tree.get_children()) == 3325,
        str(len(app.tree.get_children())))


def case_edit_save(app, ctx, box):
    """2) 改一条 → 保存 → 库里有 manual 行、内存状态同步。"""
    doc = mod_service.ModDoc(SMALL, db=app._db())
    app.on_loaded(doc)
    if not doc.items:
        chk("小样本 mod 有条目", False, "items 为空")
        return
    k = doc.items[0]["key"]
    doc.set_edit(k, "GUI测试译文")
    chk("set_edit 后状态 = ✎已改",
        doc.status_of(doc.by_key[k]) == "✎已改", doc.status_of(doc.by_key[k]))
    chk("edits 里有 1 条", len(doc.edits) == 1)
    n, fail = doc.save()
    chk("save 成功 1 条、无失败", (n, fail) == (1, []), "%s" % ((n, fail),))
    chk("edits 已清空", not doc.edits)
    chk("条目 dst 已更新（界面显示同源）", doc.by_key[k]["dst"] == "GUI测试译文")
    row = app._db().execute("SELECT status FROM tm WHERE key=?", (k,)).fetchone()
    chk("库里 status=manual", bool(row) and row[0] == "manual",
        str(tuple(row) if row else None))


def case_fail_inject(app, ctx, box):
    """3) ★ 失败注入守门：写库失败时**绝不能清空 edits**（用户特意叮嘱的边界）。

    旧实现无条件 `commit()` + `edits.clear()` —— 失败的行会"看起来已保存"然后丢失。
    """
    doc = mod_service.ModDoc(SMALL, db=app._db())
    k = doc.items[0]["key"]
    doc.set_edit(k, "这条注定写不进去")
    orig = doc.tm.mark_manual

    def boom(_k, _d, _s=None):
        raise RuntimeError("注入的写库失败")

    doc.tm.mark_manual = boom
    try:
        n, fail = doc.save()
    finally:
        doc.tm.mark_manual = orig
    chk("★ 报告了失败", len(fail) == 1 and n == 0, "%s" % ((n, fail),))
    chk("★ 失败的改动仍留在 edits 里（没被吞）", k in doc.edits, str(list(doc.edits))[:40])
    chk("★ 条目的 dst 没被误改", doc.by_key[k]["dst"] != "这条注定写不进去")
    doc.revert(k)
    chk("revert 后 edits 清空", not doc.edits)


def case_retrans(app, ctx, box):
    """4) 标记待重译 → 保存 → 库里 status=fallback。"""
    doc = mod_service.ModDoc(SMALL, db=app._db())
    k = doc.items[0]["key"]
    doc.mark_retrans(k)
    chk("标记后状态 = ⟳重译", doc.status_of(doc.by_key[k]) == "⟳重译")
    n, fail = doc.save()
    chk("保存成功、无失败", fail == [] and n == 1, "%s" % ((n, fail),))
    row = app._db().execute("SELECT status FROM tm WHERE key=?", (k,)).fetchone()
    chk("库里 status=fallback", bool(row) and row[0] == "fallback",
        str(tuple(row) if row else None))
    chk("retrans 已清空", not doc.retrans)


def case_widgets(app, ctx, box):
    """5) 关键控件真的挂上了（"创建控件 ≠ 显示控件"）。"""
    for name in ("tree", "editor", "src_box", "qv", "rv", "only_unt", "only_ed",
                 "count_lbl", "status"):
        chk("控件 %s 存在" % name, getattr(app, name, None) is not None)
    chk("editor 已映射到屏幕", bool(app.editor.winfo_exists()))
    chk("菜单栏已设置", app.cget("menu") != "")


# ================================================================ 入口
def main():
    global _bad
    _bad = 0
    tt = load_tool()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        ctx = {"db": os.path.join(d, "t.sqlite")}
        make_db(ctx["db"])
        box = {"done": False, "errors": [], "showerror": []}
        tkfd.asksaveasfilename = lambda **kw: os.path.join(d, "out.tsv")
        tkfd.askopenfilename = lambda **kw: os.path.join(d, "out.tsv")
        tkmb.showerror = lambda *a, **k: box["showerror"].append(a)
        tkmb.showinfo = lambda *a, **k: None
        tkmb.askyesno = lambda *a, **k: True

        cases = [case_ch1, case_edit_save, case_fail_inject, case_retrans, case_widgets]
        # 依赖本机私有样本的用例：找不到就整组剔除（**不计失败**）✓
        # 这样别人克隆仓库后 `python -m l10n.selftest` 也能全绿。
        if not os.path.exists(SMALL):
            print("⚠ 找不到小样本 mod，跳过 3 个依赖它的用例：")
            print("   %s" % SMALL)
            print("   （要跑就设 L10N_TEST_MOD_SMALL=<你的小样本 .mod>）")
            cases = [c for c in cases
                     if c not in (case_edit_save, case_fail_inject, case_retrans)]

        def hook(app):
            try:
                for fn in cases:
                    print("\n[%s]" % fn.__name__)
                    fn(app, ctx, box)
            except Exception:
                box["errors"].append(traceback.format_exc())
            finally:
                box["done"] = True

        print("=" * 82)
        print("GUI 层验收：无头驱动 mod tool.py（临时库 + 真实 mod 只读）")
        tt.run_gui(test_hook=hook, db_path=ctx["db"], project="selftest")

        chk("test_hook 被调用了（窗口正常关闭）", box["done"])
        chk("没有意外弹错误框", not box["showerror"], str(box["showerror"][:1]))
        if box["errors"]:
            chk("hook 无异常", False)
            print(box["errors"][0])

    print()
    print("=" * 82)
    print("结果：%s" % ("✅ GUI 层全部通过" if not _bad else "✗ %d 项失败" % _bad))
    return 1 if _bad else 0


if __name__ == "__main__":
    sys.exit(main())
