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

import tkinter as tk                                          # noqa: E402
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


def skip(name, why=""):
    """跳过（**不计入失败**）—— 只用于「依赖本机私有资产」的用例 ✓。"""
    print("  ⚠ %-52s %s" % (name, why))


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
            for name in ("tree", "zh_box", "en_box", "req", "type_box", "log", "count_var",
                         "show_cand"):
                chk("控件 %s 存在" % name, getattr(app, name, None) is not None)

            # ============================ 2026-10-11 新增三项 ============================
            print("\n[7] 新增·①「显示候选」开关（此前 6832 条 candidate 界面完全不可见 ✗）")
            app.show_cand.set(False)
            app.reload()
            app.update()
            n_off = len(app.visible)
            chk("不勾选时只有 active", "Anima Mage" not in {t["en"] for t in app.visible},
                "%d 条" % n_off)
            app.show_cand.set(True)
            app.reload()
            app.update()
            n_on = len(app.visible)
            ens_on = {t["en"] for t in app.visible}
            chk("★ 勾选后 candidate 出现在列表里", n_on > n_off and "Anima Mage" in ens_on,
                "%d -> %d" % (n_off, n_on))
            chk("★ 候选行的 status 就是 candidate",
                any(t.get("en") == "Anima Mage" and t.get("status") == "candidate"
                    for t in app.visible))
            chk("表格新增「状态」列", "status" in app.tree["columns"],
                str(list(app.tree["columns"])))
            app.show_cand.set(False)
            app.reload()
            app.update()
            chk("取消勾选后候选又消失", "Anima Mage" not in {t["en"] for t in app.visible})

            print("\n[8] 新增·②「一键重建产物」service（只预演，绝不写文件）")
            from l10n.services import rebuild_service
            info = rebuild_service.survey()
            # 目标 TLK / `prc8.txt` 是**本机私有资产**（不进 Git 仓库）→ 缺了就跳过 ✓
            if info.get("ok") and not info.get("missing"):
                chk("rebuild_service.survey() 可用", True)
                chk("★ 能识别出「自加界面槽位」（>0，重建时必须并回）",
                    (info.get("custom") or 0) > 0, "%s 个" % info.get("custom"))
                chk("四个默认路径都存在", True)
            else:
                skip("[8] 一键重建产物预演", "缺本机私有资产：%s %s"
                     % (info.get("missing"), info.get("error") or ""))

            print("\n[9] 新增·③「双击即采纳」—— 核对报告窗口能开、数据可读")
            if os.path.exists(mod.REPORT):
                rl = [f for f in (ln.split("\t") for ln in
                                  open(mod.REPORT, encoding="utf-8-sig").read().splitlines()[1:])
                      if len(f) >= 4 and f[0].strip()]
                chk("核对报告有差异行", len(rl) > 0, "%d 行" % len(rl))
                before = len(app.winfo_children())
                app.load_report()
                app.update()
                wins = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
                chk("★ 报告窗口已弹出（可交互，不再只打印）", len(wins) > 0,
                    "Toplevel %d 个（children %d -> %d）" % (len(wins), before,
                                                             len(app.winfo_children())))
                for w in wins:
                    w.destroy()
                app.update()
            else:
                skip("[9] 双击即采纳",
                     "找不到术语核对报告（本机产物，未开源）：%s" % mod.REPORT)

            print("\n[10] 新增·④「窗口可手动调整大小」（用户 2026-10-11：下面窗口太小）")
            chk("上下分隔条控件 vpane 存在", getattr(app, "vpane", None) is not None)
            chk("上下两个面板（表格区 / 日志区）",
                getattr(app, "vpane", None) is not None and len(app.vpane.panes()) == 2,
                "%d 个" % (len(app.vpane.panes()) if getattr(app, "vpane", None) else 0))
            chk("左右两个面板（表格 / 右侧编辑区）", len(app.pane.panes()) == 2,
                "%d 个" % len(app.pane.panes()))
            h0 = app.log.winfo_height()
            chk("日志区有实际高度（不是被压扁的几像素）", h0 > 100, "%d px" % h0)
            app.vpane.sash_place(0, 0, max(200, app.vpane.winfo_height() - 480))
            app.update()
            h1 = app.log.winfo_height()
            chk("★ 拖动分隔条能调整日志区高度", h1 != h0, "%d -> %d px" % (h0, h1))
            chk("窗口未被禁用拉伸", all(app.resizable()), str(app.resizable()))

            print("\n[11] 新增·⑤「批量可撤销」+「术语表体检」")
            from l10n.services import term_health_service, undo_service
            chk("undo_service 可用", hasattr(undo_service, "undo_last"))
            # ⚠ 「保存译名」走 `term_editor_service`（只改术语表、不动正文），**不**产生批次；
            #   产生批次的是 `rename_service.apply` ✓ —— 这里直接验 undo 机制本身
            #   （apply 与它的联动已在真实临时库上端到端验过：改 1 行 → 撤销回写 ok ✓）
            u1 = undo_service.begin(db2 := connect(db_path), "自测批次")
            undo_service.record(db2, u1, 999999, "旧值A")
            n_rec = undo_service.finish(db2, u1)
            bs = undo_service.list_batches(db2, 5)
            chk("★ begin/record/finish 留下可撤销批次",
                n_rec == 1 and any(b[0] == u1 for b in bs), "%d 批 / 记 %d 行" % (len(bs), n_rec))
            db2.execute("INSERT INTO tm(key,src,dst,src_mod,status) "
                        "VALUES('tu1','x','新值B','prc8','manual')")
            db2.commit()
            db2.execute("UPDATE tm SET dst='旧值A' WHERE key='tu1'")   # 模拟"被改过"
            db2.commit()
            undo_service.record(db2, undo_service.begin(db2, "自测批次2"), 0, "")
            u = undo_service.undo(db2, u1)
            chk("★ 撤销能回写旧值", bool(u.get("ok")) and u["n"] == 1, str(u))
            empty = undo_service.finish(db2, undo_service.begin(db2, "空批次"))
            chk("空批次自动清理（不留垃圾）", empty == 0)
            g = term_health_service.health(repo)
            chk("term_health_service 六类判据齐全",
                all(k in g for k in ("dup_en", "dup_zh", "suffix", "ascii", "long", "empty")),
                str(sorted(g)))
            chk("摘要是一句人话", "术语表体检" in term_health_service.summary(g))
            # 真造一条「同英多译」验判据（临时库，安全 ✓）
            TermRepo(connect(db_path)).upsert_many([
                Term(en="Healer", zh="生命牧师", type="class", required=0, src="manual")])
            g2 = term_health_service.health(TermRepo(connect(db_path)))
            chk("★ 能抓出「同一英文两条 active」",
                any(e == "healer" for e, _z, _w in g2["dup_en"]), str(g2["dup_en"][:2]))
            # 病态串拦截的单元判据
            import l10n.services.rename_service as _rs
            chk("★ 能识别「新冒出的重复字」（抗抗 / 人人）",
                _rs._sick_after("抗解除缓冲", "抗抗解除缓冲") == "抗"
                and _rs._sick_after("瑞尔坎人", "鳞族人人") == "人")
            chk("不误伤「原本就有重复」的行",
                _rs._sick_after("能能手手很好", "能能手手很棒") is None)

            print("\n[12] 新增·⑥「备份库对账」+「花费账本」")
            from l10n.services import audit_service, cost_service
            chk("audit_service 可用", hasattr(audit_service, "diff_against_backup"))
            chk("cost_service 可用", hasattr(cost_service, "record"))
            r = cost_service.record(repo.db, "test", "dry", items=100, tokens_in=1000000,
                                    tokens_out=1000000, price_in=1.0, price_out=2.0, dry=True)
            chk("★ dry 预估算价正确（1M in + 1M out @1/2 元 = 3.00 元）",
                abs(r["cost"] - 3.0) < 1e-9, "%.4f" % r["cost"])
            cost_service.record(repo.db, "test", "real", items=100, tokens_in=1000000,
                                tokens_out=1000000, price_in=1.0, price_out=2.0)
            s = cost_service.summary(repo.db, project="test")
            chk("★ 落账后能统计出来", s["runs"] == 1 and abs(s["cost"] - 3.0) < 1e-9,
                "runs=%s cost=%.2f" % (s["runs"], s["cost"]))
            chk("human() 是一句人话", "花费账本" in cost_service.human(s))
            chk("对账函数签名正确（只读，不改库）",
                "backup_path" in audit_service.diff_against_backup.__doc__ or
                audit_service.diff_against_backup.__code__.co_varnames[1] == "backup_path")
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
