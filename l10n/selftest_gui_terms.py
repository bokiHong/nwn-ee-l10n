#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`术语库编辑器.py` 的 GUI 层验收 —— 头号目标是**改动真的落到 `term` 表**。

用法：
    cd scripts
    python -m l10n.selftest_gui_terms

守的几条硬要求（用户 2026-10-08 拍板）：
  * 保存/新增术语 → 只写中央库 `term` 表；
  * **绝不自动覆盖 `glossary.json`**（它就是导出快照）→ 用 sha256 守门；
  * 改译名后**同一个 en 只能有一条 active**（旧行降级为 rejected），
    否则 `hits`/`fuzzy`/违规检查会错乱；
  * 候选（candidate）不能串进 active 列表。
"""
import hashlib
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
from l10n.model import Term                                    # noqa: E402
from l10n.repo import TermRepo                                 # noqa: E402
from l10n.services import term_editor_service                  # noqa: E402

EDITOR = os.path.join(SCRIPTS, "术语库编辑器.py")
GLOSSARY = os.path.join(SCRIPTS, "modl10n", "glossary.json")
OVERRIDES = os.path.join(SCRIPTS, "glossary_overrides.json")

_bad = 0


def chk(name, ok, detail=""):
    global _bad
    if not ok:
        _bad += 1
    print("  %s %-52s %s" % ("✓" if ok else "✗", name, detail))


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else None


def load_editor():
    spec = importlib.util.spec_from_file_location("termed", EDITOR)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def seed(db_path):
    """临时库：3 条 active + 1 条 candidate。"""
    from modl10n.tm import TM
    h = TM(db_path); h.db.close()
    db = connect(db_path)
    TermRepo(db).upsert_many([
        Term(en="Swordsage", zh="剑圣", type="class", required=1, src="glossary"),
        Term(en="Swordflight", zh="剑翔", type="misc", required=1, src="glossary"),
        Term(en="Healer", zh="治疗师", type="class", required=0, src="glossary"),
        Term(en="Anima Mage", zh="魂灵法师", type="class", required=0,
             status="candidate", src="draft"),
    ])
    db.close()


def main():
    global _bad
    _bad = 0
    mod = load_editor()
    gl0, ov0 = sha(GLOSSARY), sha(OVERRIDES)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db_path = os.path.join(d, "t.sqlite")
        seed(db_path)
        tkmb.showinfo = lambda *a, **k: None
        tkmb.showerror = lambda *a, **k: print("      [弹框]", a)
        tkmb.showwarning = lambda *a, **k: None
        tkmb.askyesno = lambda *a, **k: True
        tkfd.askopenfilename = lambda **kw: os.path.join(d, "draft.json")

        app = mod.App(db_path=db_path)
        repo = TermRepo(connect(db_path))
        print("=" * 84)
        print("术语库编辑器 GUI 验收（临时库；glossary.json 用 sha256 守门）")
        try:
            app.update()

            print("\n[1] 列表数据来自 term 表（active），candidate 不串进来")
            app.reload()
            app.update()
            ens = {t["en"] for t in app.visible}
            chk("3 条 active 都在", {"Swordsage", "Swordflight", "Healer"} <= ens, str(sorted(ens)))
            chk("★ candidate 没串进列表", "Anima Mage" not in ens, str(sorted(ens)))

            print("\n[2] ★ 改译名 → 只写 term 表，且同一个 en 只有一条 active")
            idx = next(i for i, t in enumerate(app.visible) if t["en"] == "Swordsage")
            app.tree.selection_set("r%d" % idx)
            app.on_pick()
            app.zh_box.delete(0, "end")
            app.zh_box.insert(0, "剑道家")
            app.save_zh()
            app.update()
            rows = repo.by_en("Swordsage")
            active = [t for t in rows if t.status == "active"]
            chk("★ 库里只有 1 条 active", len(active) == 1, "共 %d 行" % len(rows))
            chk("★ active 译名 = 剑道家", bool(active) and active[0].zh == "剑道家",
                active[0].zh if active else "无")
            chk("★ 旧行已降级 rejected",
                any(t.status == "rejected" and t.zh == "剑圣" for t in rows),
                str([(t.zh, t.status) for t in rows]))
            chk("界面列表已刷新为新译名",
                any(t["en"] == "Swordsage" and t["zh"] == "剑道家" for t in app.visible))

            print("\n[3] 新增术语 = 覆盖语义（active 条数不增加）")
            n0 = repo.count("active")
            infos = []
            r = term_editor_service.add_term(repo, "Swordsage", "贤者之剑",
                                             type_="class", required=True)
            infos.append(r)
            chk("active 条数不变", repo.count("active") == n0,
                "%d -> %d" % (n0, repo.count("active")))
            act = [t for t in repo.by_en("Swordsage") if t.status == "active"]
            chk("仍是 1 条 active 且译名为新值",
                len(act) == 1 and act[0].zh == "贤者之剑", str([(t.zh, t.status) for t in act]))

            print("\n[4] 导入草案 → 只加 candidate，不动 active")
            import json
            draft = {"swordsage": [{"zh": "剑圣", "raw": 10, "n": 3}],
                     "newterm": [{"zh": "新词", "raw": 5, "n": 1}]}
            dp = os.path.join(d, "draft.json")
            with open(dp, "w", encoding="utf-8") as f:
                json.dump(draft, f, ensure_ascii=False)
            na0, nc0 = repo.count("active"), repo.count("candidate")
            app.import_draft()
            app.update()
            chk("active 条数不变", repo.count("active") == na0,
                "%d -> %d" % (na0, repo.count("active")))
            chk("candidate 增加了", repo.count("candidate") > nc0,
                "%d -> %d" % (nc0, repo.count("candidate")))

            print("\n[5] ★★ 红线：两个 JSON 的 sha256 必须一字未动")
            chk("glossary.json 未被写入", sha(GLOSSARY) == gl0,
                "%s" % (sha(GLOSSARY) or "")[:16])
            chk("glossary_overrides.json 未被写入", sha(OVERRIDES) == ov0,
                "%s" % (sha(OVERRIDES) or "")[:16])

            print("\n[6] 关键控件仍在（创建控件 != 显示控件）")
            for name in ("tree", "zh_box", "en_box", "req", "type_box", "log", "count_var"):
                chk("控件 %s 存在" % name, getattr(app, name, None) is not None)
        except Exception:
            chk("用例无异常", False)
            print(traceback.format_exc())
        finally:
            try:
                app.destroy()
            except Exception:
                pass

    # 收尾再查一次（防止 destroy 时才写）
    chk("收尾：glossary.json 仍未被写入", sha(GLOSSARY) == gl0)
    print()
    print("=" * 84)
    print("结果：%s" % ("✅ GUI 层全部通过" if not _bad else "✗ %d 项失败" % _bad))
    return 1 if _bad else 0


if __name__ == "__main__":
    sys.exit(main())
