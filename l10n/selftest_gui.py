#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GUI 层验收 —— 无头驱动 `tlk tool.py` 的 `App`，验证"界面 → service → 中央库"整条链路。

用法：
    cd scripts
    python -m l10n.selftest_gui

设计要点（都踩过坑）：
  * 用**临时的小 TLK + 临时库**，绝不动真实 `tm.sqlite`（红线）与真实 TLK；
  * 所有模态对话框（filedialog / messagebox）都要打桩，否则测试会卡在弹窗上；
  * `run_gui` 的 `test_hook` 必须自己兜异常 —— 旧写法在 hook 抛异常时**窗口不关、mainloop 不返回**（已在本次改造修掉）；
  * 术语体检那条链路是**后台线程 + after 回灌**的，测试里不等它，直接断言同步可得的 `show_term_bar` / `term_service`。
"""
import importlib.util
import os
import sys
import tempfile
import traceback

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
from l10n import tlkio, txtio                                  # noqa: E402
from l10n.db import connect                                    # noqa: E402
from l10n.model import Term                                    # noqa: E402
from l10n.repo import TermRepo, TmRepo                         # noqa: E402
from l10n.services import term_editor_service, term_service, tlk_fill_service   # noqa: E402

import tkinter.filedialog as tkfd                              # noqa: E402
import tkinter.messagebox as tkmb                              # noqa: E402

_bad = 0


def chk(name, ok, detail=""):
    global _bad
    if not ok:
        _bad += 1
    print("  %s %-52s %s" % ("✓" if ok else "✗", name, detail))


def load_tool():
    """按文件名加载 `tlk tool.py`（文件名带空格，只能 importlib）。"""
    p = os.path.join(SCRIPTS, "tlk tool.py")
    spec = importlib.util.spec_from_file_location("tlktool", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def make_tlk(path, texts, lang=0):
    n = max(int(k) for k in texts) + 1
    return tlkio.write_texts(path, lang, n, texts)


def make_db(path, terms=None):
    """建一个临时中央库（先让 modl10n 建 tm 表，再迁移出视图），**建完就关连接**。

    关连接很重要：`App` 会自己开一个连接，测试这边要是还挂着两个连接，
    WAL 下会争写锁（实测表现为"有时打不开记忆库"）。
    """
    from modl10n.tm import TM
    handle = TM(path)
    db = connect(path)
    if terms:
        TermRepo(db).upsert_many(terms)
    db.close()
    handle.db.close()
    return None, None


# ================================================================ 用例
def case_open(app, ctx, box):
    """1) 打开 TLK：数据进 TlkDoc、列表填充、初始不脏。"""
    app.load_file(ctx["tlk"])
    chk("doc 已建立", app.doc is not None)
    chk("stringCount 正确", app.doc.count == ctx["count"], str(app.doc.count))
    chk("有文本条数正确", len(app.doc.texts) == len(ctx["texts"]),
        str(len(app.doc.texts)))
    chk("初始未标脏", not app.doc.modified)
    chk("列表行数 = 有文本条数",
        len(app.tree.get_children()) == len(ctx["texts"]),
        str(len(app.tree.get_children())))
    chk("状态栏报告已加载", "已加载" in app.status.get(), app.status.get()[:40])
    chk("orig_texts 是独立拷贝", app.doc.orig_texts is not app.doc.texts)


def case_search(app, ctx, box):
    """2) 搜索：走 TmRepo.search_texts（**不截断**），结果与旧规则一致。"""
    app.q.set("Sword")
    app.refresh_list()
    shown = set(app.tree.get_children())
    expect = {i for i, t in app.doc.texts.items() if "Sword" in i or "Sword" in t}
    chk("按当前文件内容过滤正确", shown >= expect, "显示 %d" % len(shown))
    app.q.set("")
    app.refresh_list()
    chk("清空搜索后恢复全部",
        len(app.tree.get_children()) == len(ctx["texts"]))


def case_edit_save(app, ctx, box):
    """3) 编辑 → 应用 → 保存 → 读回（只该变那一条）。"""
    app.tree.selection_set("0")
    app.load_into_editor("0")
    app.editor.delete("1.0", "end")
    app.editor.insert("1.0", "贤者之剑之剑")
    app.apply_edit()
    chk("doc 里已改", app.doc.texts["0"] == "贤者之剑之剑")
    chk("已标脏", "0" in app.doc.modified)
    chk("列表行加了 * 前缀",
        app.tree.item("0")["values"][1].startswith(("* ", "⚠ ")),
        str(app.tree.item("0")["values"][1])[:24])
    app.doc.path = ctx["out_tlk"]
    app.save_cur()
    back = tlkio.read_texts(ctx["out_tlk"])
    chk("保存后读回该条", back.get("0") == "贤者之剑之剑")
    diff = [k for k, v in app.doc.texts.items()
            if back.get(k) != (v if k == "0" else app.doc.orig_texts.get(k))]
    chk("其余条目字节不变", not diff, "差异 %d" % len(diff))
    chk("保存后 cleared", not app.doc.modified)


def case_memory(app, ctx, box):
    """4) ★ 保存到记忆库（旧版 100% 失败的那条路径）—— 本用例是它的守门测试。"""
    app.doc = None
    app.load_file(ctx["tlk"])                 # 重新载入，回到干净状态
    app.tree.selection_set("1")
    app.load_into_editor("1")
    app.editor.delete("1.0", "end")
    app.editor.insert("1.0", "手改译文")
    app.apply_edit()
    box["showerror"] = []
    before = len(box["showerror"])
    app.save_to_memory()
    chk("★ 没有弹错误框（旧版必弹）", len(box["showerror"]) == before,
        str(box["showerror"][:1]))
    tm = app._tm()
    chk("译文进了记忆库", tm.lookup_one(app.doc.orig_texts["1"]) == "手改译文")
    row = app._tm().db.execute(
        "SELECT status, note FROM tm WHERE key=?",
        (tmp_key(app.doc.orig_texts["1"]),)).fetchone()
    chk("status=manual", bool(row) and row[0] == "manual",
        str(tuple(row) if row else None))
    chk("note 落库（旧版传 note 会崩）",
        bool(row) and row[1] == "tlk tool 手工编辑")
    chk("状态栏报告写入条数", "已写入记忆库" in app.status.get(), app.status.get()[:40])


def tmp_key(text):
    from l10n.keys import src_key
    return src_key(text)


def case_export_import(app, ctx, box):
    """5) 导出（**内存态**，修掉旧的"走磁盘重读"）+ 从 txt 导入。"""
    ok_old = dict(app.doc.texts)
    app.editor.delete("1.0", "end")
    app.editor.insert("1.0", "未保存的修改")
    app.tree.selection_set("2")
    app.load_into_editor("2")
    app.editor.delete("1.0", "end")
    app.editor.insert("1.0", "未保存的修改2")
    app.apply_edit()
    app.doc.export_txt(ctx["out_txt"])
    _meta, entries = txtio.read(ctx["out_txt"])
    got = dict(entries)
    chk("★ 导出的是内存态（含未保存的修改）",
        txtio.clean(got.get("2") or "") == "未保存的修改2",
        repr(got.get("2")))
    # 导入一个 txt
    tip = ctx["in_txt"]
    with open(tip, "w", encoding="utf-8-sig", newline="") as f:
        f.write("#// TLKCount: 6\r\n#// LanguageID: 0\r\n0 := a\r\n1 := b\r\n2 := c\r\n")
    old_count = app.doc.count
    app.doc = __import__("l10n.services.tlk_service", fromlist=["x"]).import_txt(
        tip, fallback_count=old_count)
    chk("导入后条数", len(app.doc.texts) == 3, str(len(app.doc.texts)))
    chk("导入后整体未保存（旧语义）", app.doc.modified == set(app.doc.texts))
    chk("导入缺 TLKCount 时用 fallback", app.doc.count == 6, str(app.doc.count))
    app.refresh_list()
    chk("导入后列表刷新", len(app.tree.get_children()) == 3)


def case_punct(app, ctx, box):
    """6) 标点转换：只标脏真正变化的条目。"""
    from l10n.services.tlk_service import TlkDoc
    app.doc = TlkDoc(path=ctx["tlk"], lang=0, count=5,
                     texts={"0": "Hello, world.", "1": "你好,世界.",
                            "2": "<CUSTOM0>再见!", "3": "3.5 DC"})
    app.refresh_list()
    n = app.doc.set_punct("cn")
    chk("转换条数 = 2", n == 2, str(n))
    chk("纯英文不动", app.doc.texts["0"] == "Hello, world.")
    chk("中文标点转全角", app.doc.texts["1"] == "你好，世界。", repr(app.doc.texts["1"]))
    chk("<标签> 内不动、句末转全角", app.doc.texts["2"] == "<CUSTOM0>再见！",
        repr(app.doc.texts["2"]))
    chk("数字不动", app.doc.texts["3"] == "3.5 DC")
    chk("只标脏变化的 2 条", app.doc.modified == {"1", "2"}, str(sorted(app.doc.modified)))


def case_terms(app, ctx, box):
    """7) ★ 术语违规实时显示（本次新增需求）。"""
    app.load_file(ctx["tlk"])
    app.tree.selection_set("0")               # 原文含 "Swordsage"
    app.load_into_editor("0")
    app.editor.delete("1.0", "end")
    app.editor.insert("1.0", "剑圣")           # 故意用错译名
    app.apply_edit()
    bar = app.term_bar.get()
    chk("★ 术语条报出必守违规", "必守未遵守" in bar and "Swordsage" in bar, bar[:56])
    app.editor.delete("1.0", "end")
    app.editor.insert("1.0", "贤者之剑的法术")
    app.apply_edit()
    bar2 = app.term_bar.get()
    chk("★ 改用规范译名后不再违规", "必守未遵守" not in bar2, bar2[:56])
    tr = app._term()
    chk("term_service 命中列表可用", bool(term_service.status_of_text(
        tr, "The Swordsage", "剑圣")["missing"]))
    hits = term_service.fuzzy_suggest(tr, "mordenkainens")
    chk("模糊搜去撇号命中", any(t.en == "Mordenkainen" for t, _s, _w in hits),
        str([(t.en, w) for t, _s, w in hits][:3]))
    sm = term_service.violation_summary(
        tr, {"0": ("The Swordsage", "剑圣"), "1": ("The Swordsage", "贤者之剑")})
    chk("违规汇总", sm["bad"] == 1, str(sm))


# ================================================================ 入口
def case_en_source(app, ctx, box):
    """8) ★ 方案 A：打开**汉化版**时自动找到英文源（文件名启发那一级）。"""
    app.load_file(ctx["zh_tlk"])
    chk("★ 找到英文源 demoE.tlk", "demoE.tlk" in app.en_info, app.en_info)
    chk("英文源说明里带原因", "文件名启发" in app.en_info, app.en_info[:40])
    chk("★ strref → 英文原文映射可用",
        app.src_texts.get("0") == "Sword of the Swordsage",
        repr(app.src_texts.get("0")))
    chk("状态栏显示了英文源", "demoE.tlk" in app.status.get(), app.status.get()[:60])


def case_tolerant(app, ctx, box):
    """9) ★ 方案 C：宽容匹配（大小写 + 复数 s/es）。"""
    from l10n.model import Term
    tr = app._term()
    tr.upsert_many([Term(en="Anima Mage", zh="魂灵法师", type="class", required=0)])
    app.tree.selection_set("2")                 # 2 = "Anima Mages"（复数）
    app.load_into_editor("2")
    bar = app.term_bar.get()
    chk("★ 复数容忍：Anima Mages 命中 Anima Mage", "Anima Mage" in bar, bar[:60])
    st = term_service.status_of_text(tr, "the swordsage", "剑圣")
    chk("★ 大小写容忍：小写 swordsage 也命中",
        any(e == "Swordsage" for e, _z, _r in st["hits"]), str(st["hits"]))


def case_term_bar(app, ctx, box):
    """10) ★ 术语条四类明确提示（用户要求"更有信息量"）。"""
    app.tree.selection_set("0")                 # 英文 "Sword of the Swordsage"
    app.load_into_editor("0")
    bar = app.term_bar.get()
    chk("★ [命中] 文案（含箭头）", bar.startswith("[命中]") and "Swordsage -> " in bar,
        bar[:56])
    # 故意用错的译名 → 应报"必守未遵守"
    app.editor.delete("1.0", "end")
    app.editor.insert("1.0", "剑圣之剑")
    app.apply_edit()
    bad = app.term_bar.get()
    chk("★ [⚠ 必守未遵守] 文案", bad.startswith("⚠ 必守未遵守：") and "Swordsage" in bad,
        bad[:56])
    app.editor.delete("1.0", "end")
    app.editor.insert("1.0", "贤者之剑")
    app.apply_edit()
    chk("改回规范译名后不再报警", app.term_bar.get().startswith("[命中]"),
        app.term_bar.get()[:40])


def case_add_term(app, ctx, box):
    """11) ★ 「加入术语库」：按钮在、service 可用、**加完术语条立即生效**。"""
    chk("界面有 add_current_term 方法", hasattr(app, "add_current_term"))
    tr = app._term()
    n0 = tr.count("active")
    # 用**已存在**的 en 才叫"覆盖语义"（换个新 en 本来就是新增一条）
    r = term_editor_service.add_term(tr, "Swordsage", "剑道家", type_="class", required=True)
    chk("写入成功（覆盖语义）", r["new"] == "剑道家" and r["demoted"] == 1, str(r))
    chk("★ active 条数不变（旧行降级，不会多出一条）", tr.count("active") == n0,
        "%d -> %d" % (n0, tr.count("active")))
    app.tree.selection_set("0")
    app.show_term_bar("0")                      # 即加即用：直接刷术语条
    chk("★ 术语条仍正常（没被新术语打乱）", app.term_bar.get().startswith(("[命中]", "⚠")),
        app.term_bar.get()[:48])


# ================================================================ 入口
def case_fill_tlk(app, ctx, box):
    """12) ★ TLK 回填：只改该改的、自加槽位一字不动、有备份、读回校验。

    ⚠ 用**独立临时库 + 独立一对 TLK**：前面的用例会往共享临时库写译文，
    否则 `changeable`/`no_tm` 这些数会被污染（第一版就是这么踩的）。
    """
    from l10n.model import Term
    from l10n.repo import TermRepo, TmRepo
    from modl10n.tm import TM
    d = os.path.dirname(ctx["zh_tlk"])
    zh2, en2 = os.path.join(d, "fill.tlk"), os.path.join(d, "fillE.tlk")
    dbp = os.path.join(d, "fill.sqlite")
    make_tlk(en2, {"0": "Sword of the Swordsage", "1": "Hello, world.", "2": "Anima Mages"})
    # #3 故意做成"英文源里没有的槽位"——验收回填时**自加槽位原样保留**
    make_tlk(zh2, {"0": "贤者之剑之剑", "1": "你好，世界。", "2": "魂灵法师",
                   "3": "自定义界面标签"})
    h = TM(dbp)
    h.db.close()
    db2 = connect(dbp)
    tm2, tr2 = TmRepo(db2), TermRepo(db2)
    try:
        tm2.put_manual([("Anima Mages", "灵魂法师", "selftest")])
        rep = tlk_fill_service.plan_fill(zh2, en2, tm2)
        chk("★ 预览：只改 1 条（#2）", rep["changeable"] == 1, str(rep["changeable"]))
        chk("★ 预览：识别出自加槽位 #3", rep["custom_ids"] == ["3"], str(rep["custom_ids"]))
        chk("预览：其余 2 条没有记忆库译文 → 保持原样", rep["no_tm"] == 2, str(rep["no_tm"]))

        res = tlk_fill_service.fill_tlk(zh2, en2, tm2)
        chk("★ 写了 1 条", res["written"] == 1, str(res["written"]))
        chk("★ 读回校验通过（应改的都改了 / 不该动的没动）", res["verified"] is True,
            "应改未改=%s 误改=%s" % (res["bad"], res["untouched_bad"]))
        chk("★ 就地写回有备份文件", bool(res["backup"]) and os.path.exists(res["backup"]),
            os.path.basename(res["backup"] or ""))

        back = tlkio.load(zh2)
        chk("★ #2 已回填为新译文", back["texts"].get("2") == "灵魂法师",
            repr(back["texts"].get("2")))
        chk("★ 自加槽位 #3 一字未动", back["texts"].get("3") == "自定义界面标签",
            repr(back["texts"].get("3")))
        chk("★ 记忆库没译文的条目未动", back["texts"].get("0") == "贤者之剑之剑",
            repr(back["texts"].get("0")))
        chk("槽位总数不变", back["count"] == 4, str(back["count"]))

        # 端到端（用户点名要的那条）：回填后**术语校验不再报「必守未遵守」**
        tr2.upsert_many([Term(en="Anima Mage", zh="灵魂法师", type="class", required=1)])
        st = term_service.status_of_text(tr2, "Anima Mages", back["texts"].get("2"))
        chk("★ 回填后术语校验通过（不再报必守未遵守）",
            st["ok"] and not st["missing"], str(st["missing"]))
        chk("（对照）改回旧译名会报违规",
            not term_service.status_of_text(tr2, "Anima Mages", "魂灵法师")["ok"])
    finally:
        db2.close()
    chk("界面有 do_fill_tlk 方法", hasattr(app, "do_fill_tlk"))


def main():
    global _bad
    _bad = 0
    tt = load_tool()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        ctx = {"tlk": os.path.join(d, "t.tlk"), "out_tlk": os.path.join(d, "o.tlk"),
               "out_txt": os.path.join(d, "o.txt"), "in_txt": os.path.join(d, "i.txt"),
               "db": os.path.join(d, "t.sqlite")}
        ctx["texts"] = {"0": "Sword of the Swordsage", "1": "Hello, world.",
                        "2": "你好,世界.", "3": "Mordenkainen's sword",
                        "4": "Plain text"}
        ctx["count"] = max(int(k) for k in ctx["texts"]) + 1
        make_tlk(ctx["tlk"], ctx["texts"])
        # ★ Sprint 7 方案 A：造一对"中文版 + 英文源"，用来验收术语命中修复
        ctx["zh_tlk"] = os.path.join(d, "demo.tlk")
        ctx["en_tlk"] = os.path.join(d, "demoE.tlk")
        make_tlk(ctx["en_tlk"], {"0": "Sword of the Swordsage", "1": "Hello, world.",
                                 "2": "Anima Mages"})
        # #3 故意做成"英文源里没有的槽位"——验收回填时**自加槽位必须原样保留**
        make_tlk(ctx["zh_tlk"], {"0": "贤者之剑之剑", "1": "你好，世界。", "2": "魂灵法师",
                                 "3": "自定义界面标签"})
        make_db(ctx["db"], terms=[
            Term(en="Swordsage", zh="贤者之剑", required=1, type="class"),
            Term(en="Mordenkainen", zh="魔邓肯", required=1, type="name"),
        ])

        box = {"done": False, "errors": [], "showerror": []}
        # 打桩：所有模态对话框（否则测试会卡死）
        # ⚠ `tlk tool.py` 里 filedialog/messagebox 是**在 run_gui() 内部 import** 的，
        #   所以 `tt.filedialog` 根本不存在 ✗ —— 只能 patch tkinter 的**模块属性**
        #   （`from tkinter import filedialog` 拿到的是同一个模块对象，patch 属性全局生效）。
        tkfd.asksaveasfilename = lambda **kw: ctx["out_txt"]
        tkfd.askopenfilename = lambda **kw: ctx["in_txt"]
        tkmb.showerror = lambda *a, **k: box["showerror"].append(a)
        tkmb.showinfo = lambda *a, **k: None
        tkmb.askyesno = lambda *a, **k: True

        cases = [case_open, case_search, case_edit_save, case_memory,
                 case_export_import, case_punct, case_terms,
                 case_en_source, case_tolerant, case_term_bar, case_add_term,
                 case_fill_tlk]

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
        print("GUI 层验收：无头驱动 tlk tool.py 的 App（临时 TLK + 临时库，不碰真实数据）")
        tt.run_gui(test_hook=hook, db_path=ctx["db"], project="selftest")

        chk("test_hook 被调用了（窗口正常关闭）", box["done"])
        if box["errors"]:
            chk("hook 无异常", False)
            print(box["errors"][0])

    print()
    print("=" * 82)
    print("结果：%s" % ("✅ GUI 层全部通过" if not _bad else "✗ %d 项失败" % _bad))
    return 1 if _bad else 0


if __name__ == "__main__":
    sys.exit(main())
