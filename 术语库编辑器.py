#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""术语库编辑器 —— 查看/搜索/修改专有名词译名，并一键把改动推到所有已翻资产。

设计要点（见记忆 rules）：
  * **改一处、动多文件 → 必须先出影响面预览**，用户点"确认执行"才动手；
  * 预览与执行走**同一段逻辑**（都调用 `modl10n.rename.plan/apply`），避免两边分叉；
  * 改译名写进 `汉化\glossary_overrides.json`（重新生成 glossary 也不会丢）；
  * 一键统一是 **0 次 API 调用**（只改术语表 + 替换表 + 记忆库，重建产物另跑 pipeline）。

用法：
    python 术语库编辑器.py            # 打开图形界面
    (或双击 启动术语库.bat)
"""
import os
import re
import sys
import json
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tkinter as tk                                        # noqa: E402
from tkinter import ttk, messagebox, filedialog             # noqa: E402

# Sprint 7：**术语真源 = 中央库 `term` 表**。只写库，**绝不自动覆盖 glossary.json**
# （用户 2026-10-08 拍板：JSON 降级为纯导出快照，要用 `terms export` 显式生成）。
from l10n.db import DEF_DB, connect as db_connect           # noqa: E402
from l10n.repo import TermRepo, TmRepo                      # noqa: E402
from l10n.services import (rename_service, term_editor_service,   # noqa: E402
                           tlk_fill_service, tlk_service)

GLOSSARY = os.path.join(HERE, "modl10n", "glossary.json")   # 只作"显式导出"的落点
OVERRIDES = os.path.join(HERE, "glossary_overrides.json")   # 冻结归档：只读不写
DRAFT = os.path.join(HERE, "dnd3r_术语库_草案.json")
REPORT = os.path.join(HERE, "dnd3r_术语核对报告.tsv")


def load_json(p, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def load_overrides():
    """读覆盖层。

    ⚠ **文件存在但读不动（损坏/被占用）时抛异常，而不是返回空字典** ✗→✓：
    否则调用方会拿空字典去整份覆盖写入 → 用户的术语（改译名/分类/新增）**全丢** ✗。
    这是 2026-10-07 那次"Swordsage 消失"事故的同类风险，必须拒绝写。
    """
    if not os.path.exists(OVERRIDES):
        return {}
    with open(OVERRIDES, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("覆盖层内容不是对象（JSON object）")
    return data


# `run_rename`（原来用 subprocess 调 `modl10n.rename`）已删除：
# Sprint 7 起直接调 `l10n.services.rename_service`（**直接 import、不再起子进程**），
# 顺便也就没有"Windows 子进程按 GBK 输出导致满屏乱码"那个坑了。


class App(tk.Tk):
    def __init__(self, db_path=None, project=None):
        super().__init__()
        self.title("术语库编辑器 — 无冬之夜汉化")
        self.geometry("1180x720")
        self.terms = []                    # [{en, zh, type, required, note}]
        self.draft = {}                    # {en: [{zh,n}...]} 从 dnd3r 草案导入的参考
        self.report = []                   # 核对报告的差异行
        self.visible = []                  # 列表里显示的行（iid "rN" -> visible[N]）
        self.cur_term = None               # 当前选中的条目
        self.sort_key = tk.StringVar(value="en")   # 点表头排序：en / zh / type / req
        # ★ 可注入：测试用临时库，绝不碰真实 tm.sqlite（红线）
        self.db_path = os.path.abspath(db_path or DEF_DB)
        self.project = project
        self._db_handle = None
        self._db_lock = threading.Lock()   # 惰性开库的双检锁
        self._term_repo = None
        self._build()
        self.reload()

    # ---------- 数据源（中央库） ----------
    def _db(self):
        """惰性打开中央库 —— **整个 App 只用一个连接**（多连接会争 WAL 写锁）。"""
        if self._db_handle is None:
            with self._db_lock:
                if self._db_handle is None:
                    try:
                        self._db_handle = db_connect(self.db_path)
                    except Exception as e:                # noqa: BLE001
                        print("[术语库编辑器] 打开中央库失败：%r" % (e,), file=sys.stderr)
                        self._db_handle = False
        return self._db_handle or None

    def _term(self):
        """`TermRepo`（术语）。"""
        if self._term_repo is None:
            db = self._db()
            self._term_repo = TermRepo(db) if db else False
        return self._term_repo or None

    def _tm(self):
        """`TmRepo`（记忆库，"一键统一"要改正文）。"""
        db = self._db()
        return TmRepo(db) if db else None

    # ---------- 界面 ----------
    def _build(self):
        top = tk.Frame(self)
        top.pack(fill="x", padx=6, pady=4)
        tk.Label(top, text="搜索(英文/中文):").pack(side="left")
        self.q = tk.StringVar()
        e = tk.Entry(top, textvariable=self.q, width=28)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda _e: self.refresh())
        tk.Button(top, text="搜索", command=self.refresh).pack(side="left")
        tk.Button(top, text="清空", command=lambda: (self.q.set(""), self.refresh())).pack(side="left")
        tk.Button(top, text="导入 dnd3r 草案…", command=self.import_draft).pack(side="left", padx=8)
        # ★ 分类筛选（用户 2026-10-08 改进要求）：
        #   ① 弹独立窗口 → **内嵌折叠面板**（像 Excel，往下展开、与主窗口相连 ✓）
        #   ② 单选 → **多选**（勾了立即生效 ✓）
        #   ③ 分类变多 → 面板内**可上下滚动** ✓
        self.type_sel = set()          # 已勾选的分类；**空集 = 显示全部**
        self.filter_open = False
        self.filter_btn = tk.Button(top, text="分类筛选 ▾  全部", width=20, anchor="w",
                                    command=self.toggle_filter)
        self.filter_btn.pack(side="left", padx=8)
        tk.Button(top, text="新增术语…", command=self.add_term).pack(side="left", padx=6)
        tk.Button(top, text="重新载入术语表", command=self.reload).pack(side="left", padx=6)
        # ★ 2026-10-10：术语违规检查 —— 命中必守术语、但译文没用规范译名 ✓
        #   （表 unit_term + 视图 v_term_violation 早就建好，却一直是**空的孤儿** ✗，
        #    所以这个检查以前永远报 0；现在用 CLI `terms --action index-terms` 填充后可用 ✓）
        tk.Button(top, text="术语违规检查…", command=self.check_violations).pack(side="left", padx=6)
        self.count_var = tk.StringVar()
        self.count_lbl = tk.Label(top, textvariable=self.count_var, fg="#555")
        self.count_lbl.pack(side="right")

        self._build_filter_panel()     # 折叠面板（默认收起，点「分类筛选」才展开 ✓）

        pane = tk.PanedWindow(self, orient="horizontal", sashwidth=5)
        self.pane = pane
        pane.pack(fill="both", expand=True, padx=6, pady=(0, 4))

        left = tk.Frame(pane)
        vs = ttk.Scrollbar(left, orient="vertical")
        self.tree = ttk.Treeview(left, columns=("en", "zh", "type", "req", "draft"),
                                 show="headings", selectmode="browse", yscrollcommand=vs.set)
        vs.config(command=self.tree.yview)
        for k, t, w in (("en", "英文", 210), ("zh", "当前译名", 130), ("type", "分类", 60),
                        ("req", "必守", 46), ("draft", "dnd3r 参考（双击右侧列表可填入）", 260)):
            # 用户要求：**只有「英文」和「必守」可排序** ✓；「分类」列标题改为筛选入口 ✓；
            # 其余列标题保持不可点 ✗（去掉多余的 ⇅，免得误以为能排）
            if k in ("en", "req"):
                # ★ 用户 2026-10-08：点一次正序、**再点一次反序** ✓
                self.tree.heading(k, text=t + " ⇅",
                                  command=lambda kk=k: self.toggle_sort(kk))
            else:
                # ★ 用户 2026-10-08：「分类」列标题上的「▾筛选」**删掉** ✓
                #   它跟顶部的「分类筛选 ▾」功能重复，而且**只能展开、关不掉面板** ✗
                self.tree.heading(k, text=t)
            self.tree.column(k, width=w, anchor="w" if k != "req" else "center")
        vs.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_pick)
        pane.add(left, width=700)

        right = tk.Frame(pane)
        tk.Label(right, text="英文（只读）").pack(anchor="w")
        self.en_box = tk.Entry(right, width=44)
        self.en_box.pack(anchor="w", fill="x", pady=(0, 6))
        self.en_box.config(state="readonly")
        tk.Label(right, text="译名（改这里，然后点「保存译名」）").pack(anchor="w")
        self.zh_box = tk.Entry(right, width=44)
        self.zh_box.pack(anchor="w", fill="x", pady=(0, 6))
        self.req = tk.BooleanVar(value=False)
        tk.Checkbutton(right, text="设为「必守」（Ch3~Ch5 与替换工具都会强制用它）",
                       variable=self.req).pack(anchor="w")
        tk.Label(right, text="分类（改完点下面「保存译名/分类」）").pack(anchor="w", pady=(6, 0))
        self.type_box = tk.StringVar(value="其他")
        ttk.Combobox(right, textvariable=self.type_box, state="readonly", width=27,
                     values=self.TYPE_CHOICES).pack(anchor="w")
        tk.Label(right, text="dnd3r 资料里的写法（供参考，点击可填入译名框）").pack(anchor="w", pady=(8, 0))
        self.draft_list = tk.Listbox(right, height=9)
        self.draft_list.pack(anchor="w", fill="both", expand=False, pady=(0, 6))
        self.draft_list.bind("<Double-Button-1>", self.use_draft)
        tk.Button(right, text="保存译名/分类（写入中央库）", command=self.save_zh).pack(anchor="w", pady=2)
        tk.Label(right, text="▼ 一键统一译名（把已有译文里的旧写法全改成新写法，0 次 API）",
                 fg="#000", font=("", 9, "bold")).pack(anchor="w", pady=(10, 2))
        tk.Label(right, text="用法：先 ① 预览（只看不改）→ 没问题再 ② 执行", fg="#666").pack(anchor="w")
        tk.Button(right, text="① 一键统一 · 预览影响面", width=30,
                  command=lambda: self.unify(True)).pack(anchor="w", pady=2)
        tk.Button(right, text="② 一键统一 · 确认执行", width=30,
                  command=lambda: self.unify(False)).pack(anchor="w", pady=2)
        tk.Button(right, text="打开核对报告（差异清单）", command=self.load_report).pack(anchor="w", pady=(10, 2))
        tk.Button(right, text="导出 glossary.json 快照（可选，不自动）",
                  command=self.export_glossary_json).pack(anchor="w", pady=(4, 2))
        # ★ 用户 2026-10-08 需求：在术语库这边改完，也能**就地**把结果推到 TLK
        tk.Button(right, text="▼ 把记忆库回填到 TLK…（选文件，先预览，自动备份）",
                  width=44, command=self.fill_tlk_dialog).pack(anchor="w", pady=(8, 2))
        pane.add(right, width=460)      # ⚠ 必须 add 才会显示（我第一版漏了这行，右侧面板整个不可见 ✗）

        self.log = tk.Text(self, height=11, wrap="word")
        self.log.pack(fill="both", padx=6, pady=(0, 6))
        self.log.insert("1.0", "提示：改译名后先「保存译名」，再「预览影响面」，确认无误才「确认执行」。\n")
        self.log.config(state="disabled")

    # ---------- 数据 ----------
    def check_violations(self, limit=60):
        """术语违规检查：命中必守术语、但译文里没用规范译名。

        数据来自 `unit_term`（术语命中缓存）+ `tm`（译文），判据走 `term_service.violations()`
        （已做四重降噪 ✓，否则会刷出 2 万条误报 ✗）。

        缓存为空时**只提示、不自动重建** —— 重建要扫 3 万条源文 × 1700 条必守术语，
        约 4 分钟，放在界面线程里会卡死 ✗（用户最反感的那类问题）。
        """
        from l10n.repo import TmRepo, UnitRepo
        from l10n.services import term_service
        tm_repo, term_repo = self._tm(), self._term()
        if tm_repo is None or term_repo is None:
            self.say("中央库打不开，无法检查。")
            return
        if not tm_repo.db.execute("SELECT count(*) FROM unit_term").fetchone()[0]:
            self.say("⚠ 术语命中缓存（unit_term）是空的，请先在命令行跑一次（约 4 分钟）：")
            self.say("    cd scripts")
            self.say("    python -m l10n terms --action index-terms")
            return
        rows = term_service.violations(UnitRepo(tm_repo.db), term_repo, tm_repo)
        from collections import Counter
        by_en = Counter(r[0] for r in rows)
        self.say("\n=== 术语违规检查（已降噪）===")
        self.say("  命中必守术语、但译文没用规范译名：共 %d 处，涉及 %d 个术语"
                 % (len(rows), len(by_en)))
        self.say("  按术语分布（前 15）：%s"
                 % "、".join("%s×%d" % kv for kv in by_en.most_common(15)))
        self.say("  明细（前 %d）：" % min(limit, len(rows)))
        for en, zh, dst, _uid, proj, resref in rows[:limit]:
            self.say("    %-30s 应有「%s」｜%s:%s" % (en[:30], zh, proj, resref))
            self.say("        现：%s" % (dst or "").replace("\n", " ")[:70])
        self.say("  （改完术语表后想复查，先重跑 index-terms 刷新缓存 ✓）")

    def reload(self):
        """从**中央库 `term` 表**读生效术语（`status='active'`）。

        ★ 不再读 `glossary.json` —— 它已降级为"导出快照"（用户 2026-10-08 拍板：
        编辑器只写库、绝不自动覆盖 JSON）。`dnd3r` 参考列**仍读草案 JSON**，
        因为它是"外部资料的原始统计"（含出现次数 raw/n），不是术语真源。
        """
        tr = self._term()
        if tr is None:
            self.terms = []
            self.say("⚠ 中央库打不开：%s（术语列表为空）" % self.db_path)
        else:
            self.terms = [{"en": t.en, "zh": t.zh, "type": t.type,
                           "required": bool(t.required), "note": t.note,
                           "status": t.status}
                          for t in tr.list(status="active")]
        # 启动就自动吃草案（否则「dnd3r 参考」列是空的，用户会以为坏了 ✗）
        if not self.draft:
            self.draft = load_json(DRAFT, {})
            if self.draft:
                self.say("已自动载入参考：%s（%d 条）" % (os.path.basename(DRAFT), len(self.draft)))
        self.refresh()

    # ★ 用户 2026-10-08 要求扩充分类：种族 / 专长 / 怪物 / 特殊能力
    #   （另加 灵能/武术/领域/灵域/技能/术语 —— 这些正是灰机 wiki 抓来的 `entry_type` ✓
    #    见 `tmp\_import_huiji_terms.py`，所以能自动回填 ✓）
    TYPE_ZH = {
        "name": "人物", "deity": "神祇", "place": "地名",
        "class": "职业", "prestige_class": "进阶职业",
        "spell": "法术", "feat": "专长", "psionic": "灵能", "martial": "武术",
        "race": "种族", "monster": "怪物", "domain": "领域", "mantle": "灵域",
        "skill": "技能", "ability": "特殊能力", "term": "术语",
        "item": "物品", "misc": "其他",
    }
    TYPE_CHOICES = ("人物", "神祇", "地名", "职业", "进阶职业", "法术", "专长", "灵能",
                    "武术", "种族", "怪物", "领域", "灵域", "技能", "特殊能力", "术语",
                    "物品", "其他")

    def refresh(self):
        q = self.q.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        # ⚠ 行 id **绝不能**用英文名：术语表里可能有重复英文名（实测 ("Selûne") 与
        # ("Selune") 两条 ✗），Tk 会抛 "Item xxx already exists" 让程序**启动就崩**。
        # 改用"可见序号"当 id，再用 self.visible 映射回条目。
        self.visible = []
        want = getattr(self, "type_sel", set())     # ★ 多选：空集 = 全部
        key = self.sort_key.get()
        rows = [t for t in self.terms
                if (not q or q in t["en"].lower() or q in t["zh"])
                and (not want
                     or self.TYPE_ZH.get(t.get("type", "misc"), "其他") in want)]
        # ★ 反向排序（用户 2026-10-08）：点同一列再点一次就反过来 ✓
        #   例：点「必守」正序 → 必守在前；再点 → 非必守在前 ✓
        rows.sort(key=lambda t: (t["en"].lower() if key == "en" else
                                 t["zh"] if key == "zh" else
                                 self.TYPE_ZH.get(t.get("type", "misc"), "其他") if key == "type" else
                                 (0 if t.get("required") else 1), t["en"].lower()),
                  reverse=bool(getattr(self, "sort_rev", False)))
        for t in rows:
            ref = ""
            if t["en"].lower() in self.draft:
                # 按【全文出现次数 raw】排序并显示（配对次数会被"标题式误配"灌水 ✗，
                # 例如 Gruumsh 的参考里混进过一句「占领精灵的林间小村」✗ —— 看 raw 就能分辨）
                vs = sorted(self.draft[t["en"].lower()],
                            key=lambda v: -(v.get("raw") or v.get("n") or 0))
                ref = "  ".join("%s(全文%d)" % (v["zh"], v.get("raw") or v.get("n") or 0)
                                for v in vs[:3])
            self.visible.append(t)
            self.tree.insert("", "end", iid="r%d" % (len(self.visible) - 1),
                             values=(t["en"], t["zh"], self.TYPE_ZH.get(t.get("type", "misc"), "其他"),
                                     "✓" if t["required"] else "", ref))
        self.count_var.set("显示 %d / 术语表 %d 条；dnd3r 参考 %d 条"
                           % (len(self.visible), len(self.terms), len(self.draft)))

    def on_pick(self, _ev=None):
        sel = self.tree.selection()
        if not sel:
            return
        try:
            t = self.visible[int(str(sel[0])[1:])]
        except (ValueError, IndexError):
            return
        self.cur_term = t
        self.en_box.config(state="normal")
        self.en_box.delete(0, "end")
        self.en_box.insert(0, t["en"])
        self.en_box.config(state="readonly")
        self.zh_box.delete(0, "end")
        self.zh_box.insert(0, t["zh"])
        self.req.set(bool(t["required"]))
        self.type_box.set(self.TYPE_ZH.get(t.get("type", "misc"), "其他"))
        self.draft_list.delete(0, "end")
        for v in sorted(self.draft.get(t["en"].lower(), []),
                        key=lambda v: -(v.get("raw") or v.get("n") or 0)):
            self.draft_list.insert("end", "%s   （全文出现 %d 次 / 配对 %d 次）"
                                   % (v["zh"], v.get("raw") or 0, v.get("n") or 0))

    def import_draft(self):
        p = filedialog.askopenfilename(title="选择术语库草案 JSON", initialdir=HERE,
                                       initialfile="dnd3r_术语库_草案.json",
                                       filetypes=[("JSON", "*.json")])
        if not p:
            return
        self.draft = load_json(p, {})
        n = 0
        try:
            # 同时写进中央库（`status='candidate'`，**不动 active**）
            n = term_editor_service.import_draft(self._term(), p)
        except Exception as ex:                                    # noqa: BLE001
            self.say("  ⚠ 写入中央库候选失败（不影响参考列）：%r" % ex)
        self.refresh()
        self.say("已导入参考 %d 条：%s（其中 %d 条写入中央库候选）"
                 % (len(self.draft), os.path.basename(p), n))

    def use_draft(self, _ev=None):
        sel = self.draft_list.curselection()
        if not sel:
            return
        txt = self.draft_list.get(sel[0])
        self.zh_box.delete(0, "end")
        self.zh_box.insert(0, txt.split("（")[0].strip())
        self.say("已填入参考译名（可再改），别忘了点「保存译名」")

    def load_report(self):
        if not os.path.exists(REPORT):
            messagebox.showinfo("没有报告", "没找到 %s\n先跑：python dnd_mine.py" % os.path.basename(REPORT))
            return
        rows = open(REPORT, encoding="utf-8-sig").read().splitlines()[1:]
        self.say("=== 核对报告（%d 条差异；「库」是资料里的写法，「你」是当前译文）===" % len(rows))
        for ln in rows[:200]:
            f = ln.split("\t")
            if len(f) >= 4:
                self.say("  %-24s 库=%-12s 你=%-16s [%s]" % (f[0][:24], f[1][:12], f[3][:16], f[4] if len(f) > 4 else ""))

    # ---------- 动作 ----------
    def export_glossary_json(self):
        """**显式**导出 `glossary.json` 快照（用户拍板：保存术语时**不自动**做这件事）。

        旧实现是每次保存都 `subprocess` 跑 `make_glossary.py` 去覆盖 `glossary.json` ——
        那条链**不写 `term` 表**，所以界面上的改动对其它工具其实完全不可见 ✗。
        """
        try:
            n = term_editor_service.export_glossary(self._term(), GLOSSARY)
        except Exception as ex:                                    # noqa: BLE001
            messagebox.showerror("导出失败", repr(ex))
            return
        self.say("已导出快照：%s（%d 条 active）" % (os.path.basename(GLOSSARY), n))

    def add_term(self):
        """手动新增一个术语：写进覆盖层的 add 段并重新生成术语表。"""
        dlg = tk.Toplevel(self)
        dlg.title("新增术语")
        dlg.transient(self)
        tk.Label(dlg, text="英文").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        en_v = tk.StringVar()
        tk.Entry(dlg, textvariable=en_v, width=32).grid(row=0, column=1, padx=6)
        tk.Label(dlg, text="中文译名").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        zh_v = tk.StringVar()
        tk.Entry(dlg, textvariable=zh_v, width=32).grid(row=1, column=1, padx=6)
        tk.Label(dlg, text="分类").grid(row=2, column=0, sticky="e", padx=6, pady=4)
        tp_v = tk.StringVar(value="人物")
        ttk.Combobox(dlg, textvariable=tp_v, state="readonly", width=29,
                     values=self.TYPE_CHOICES).grid(row=2, column=1, padx=6)
        req_v = tk.BooleanVar(value=True)
        tk.Checkbutton(dlg, text="设为必守（译后强校验）", variable=req_v).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=6)

        def go():
            en, zh = en_v.get().strip(), zh_v.get().strip()
            if not en or not zh:
                messagebox.showwarning("还差一步", "英文与中文译名都要填")
                return
            tp = {v: k for k, v in self.TYPE_ZH.items()}.get(tp_v.get(), "misc")
            try:
                # ★ 只写中央库 term 表（同 en 已有 active 行时是**覆盖语义**）
                r = term_editor_service.add_term(self._term(), en, zh, type_=tp,
                                                 required=bool(req_v.get()))
            except Exception as ex:                                # noqa: BLE001
                messagebox.showerror("写入中央库失败", repr(ex))
                return
            dlg.destroy()
            old = ("（之前的译名 %s 已降级）" % r["old"]) if r.get("demoted") else ""
            self.say("已写入中央库：%s = %s（%s，必守=%s）%s"
                     % (en, zh, tp_v.get(), req_v.get(), old))
            self.reload()

        tk.Button(dlg, text="保存", command=go).grid(row=4, column=0, columnspan=2, pady=8)

    def save_zh(self):
        t = self.cur_term
        if t is None:
            sel = self.tree.selection()
            if not sel:
                return
            try:
                t = self.visible[int(str(sel[0])[1:])]
            except (ValueError, IndexError):
                return
        en = t["en"]
        new = self.zh_box.get().strip()
        if not en or not new:
            return
        tp = {v: k for k, v in self.TYPE_ZH.items()}.get(self.type_box.get(), "misc")
        try:
            # ★ 只写中央库 term 表：改译名时旧 active 行由 `set_zh` 自动降级为 rejected
            r = term_editor_service.save_term(self._term(), en, new,
                                              required=bool(self.req.get()), type_=tp,
                                              note="术语库编辑器修改")
        except Exception as ex:                                    # noqa: BLE001
            messagebox.showerror("写入中央库失败", repr(ex))
            return
        t["zh"] = new
        t["required"] = bool(self.req.get())
        t["type"] = tp
        self.refresh()
        if r.get("demoted"):
            self.say("已写入中央库：%s  %s → %s（必守=%s）；旧译名行降级 %d 条"
                     % (en, r["old"], new, self.req.get(), r["demoted"]))
        else:
            self.say("已写入中央库：%s → %s（必守=%s，分类=%s）"
                     % (en, new, self.req.get(), self.type_box.get()))

    # ---------------- 分类筛选：内嵌折叠面板（Excel 风格）----------------
    def _build_filter_panel(self):
        """建一个**默认收起**的筛选面板 —— 不弹独立窗口 ✓（用户 2026-10-08 明确要求）。"""
        self.filter_panel = tk.Frame(self, bd=1, relief="solid")
        bar = tk.Frame(self.filter_panel)
        bar.pack(fill="x", padx=6, pady=(4, 2))
        tk.Button(bar, text="全选", width=6,
                  command=lambda: self._set_all_types(True)).pack(side="left")
        tk.Button(bar, text="清空(=全部)", width=12,
                  command=lambda: self._set_all_types(False)).pack(side="left", padx=4)
        tk.Label(bar, text="勾选即生效；不勾任何一项 = 显示全部",
                 fg="#555").pack(side="left", padx=8)

        # ★ 分类变多了要能滚动：Canvas + Scrollbar（用户要求"筛选界面可上下滑动"）
        cv = tk.Canvas(self.filter_panel, height=108, highlightthickness=0)
        sb = ttk.Scrollbar(self.filter_panel, orient="vertical", command=cv.yview)
        inner = tk.Frame(cv)
        cv.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: cv.configure(scrollregion=cv.bbox("all")))
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=(0, 6))
        sb.pack(side="right", fill="y", pady=(0, 6))

        self.type_vars = {}
        for i, v in enumerate(self.TYPE_CHOICES):
            var = tk.BooleanVar(value=False)
            self.type_vars[v] = var
            tk.Checkbutton(inner, text=v, variable=var, anchor="w", width=10,
                           command=self._on_type_check).grid(
                row=i // 6, column=i % 6, sticky="w", padx=2, pady=1)

    def _set_all_types(self, on):
        for v in self.type_vars.values():
            v.set(on)
        self._on_type_check()

    def _on_type_check(self):
        self.type_sel = {v for v, var in self.type_vars.items() if var.get()}
        self._update_filter_label()
        self.refresh()

    def _update_filter_label(self):
        if not getattr(self, "type_sel", None):
            txt = "分类筛选 ▾  全部"
        elif len(self.type_sel) <= 3:
            txt = "分类筛选 ▾  " + "、".join(sorted(self.type_sel))
        else:
            txt = "分类筛选 ▾  已选 %d 类" % len(self.type_sel)
        self.filter_btn.config(text=txt)

    def toggle_filter(self):
        """展开/收起筛选面板（**内嵌**在表格上方，跟主窗口连在一起 ✓ 不再是弹窗 ✗）。"""
        if self.filter_open:
            self.filter_panel.pack_forget()
        else:
            self.filter_panel.pack(fill="x", padx=6, pady=(0, 4), before=self.pane)
        self.filter_open = not self.filter_open

    def toggle_sort(self, key):
        """点列标题排序：**同一列再点一次 = 反向**（用户 2026-10-08 要求 ✓）。

        例：点「必守」→ 必守排前面；**再点一次** → 非必守排前面 ✓。
        """
        if self.sort_key.get() == key:
            self.sort_rev = not getattr(self, "sort_rev", False)
        else:
            self.sort_key.set(key)
            self.sort_rev = False        # 换列时回到正序
        self.refresh()

    def choose_type(self):
        """点「分类」列标题 = 展开筛选面板（该入口 2026-10-08 已从列标题去掉，
        此方法保留给旧引用/自动化调用 ✓）。"""
        if not self.filter_open:
            self.toggle_filter()

    def unify(self, dry):
        old = self.zh_box.get().strip()
        new = self.zh_box.get().strip()
        # 「一键统一」的语义：把"别人还在用的旧写法"改成这里的译名。
        # 界面上让用户填两行：先请他确认旧写法（从核对报告里常见）。
        dlg = tk.Toplevel(self)
        dlg.title("一键统一")
        dlg.transient(self)
        tk.Label(dlg, text="把下面的「旧译名」全部改成：").grid(row=0, column=0, columnspan=2, padx=8, pady=6)
        tk.Label(dlg, text="英文术语").grid(row=1, column=0, sticky="e", padx=6)
        en_v = tk.StringVar(value=self.en_box.get().strip())
        tk.Entry(dlg, textvariable=en_v, width=30).grid(row=1, column=1, padx=6, pady=2)
        tk.Label(dlg, text="① 旧译名（现在译文里用的那个）").grid(row=2, column=0, sticky="e", padx=6)
        old_v = tk.StringVar(value=self.zh_box.get().strip())
        tk.Entry(dlg, textvariable=old_v, width=30).grid(row=2, column=1, padx=6, pady=2)
        tk.Label(dlg, text="② 新译名（想改成这个）").grid(row=3, column=0, sticky="e", padx=6)
        new_v = tk.StringVar(value="")
        tk.Entry(dlg, textvariable=new_v, width=30).grid(row=3, column=1, padx=6, pady=2)
        tk.Label(dlg, text="（两个都填、且不同；旧译名留空会被拒绝）", fg="#666").grid(
            row=4, column=0, columnspan=2, pady=(2, 0))

        def go():
            o, n_, en = old_v.get().strip(), new_v.get().strip(), en_v.get().strip()
            if not o or not n_:
                messagebox.showwarning("还差一步", "「旧译名」和「新译名」都要填：\n"
                                       "旧译名 = 现在译文里用的写法（可先看核对报告）\n"
                                       "新译名 = 你想改成的写法")
                return
            if o == n_:
                messagebox.showwarning("没有变化", "新旧译名相同，不用改。\n"
                                       "（想让「已有译文」跟着术语表走时，"
                                       "这里要填的是译文里那个旧写法）")
                return
            dlg.destroy()
            tm_repo, term_repo = self._tm(), self._term()
            if tm_repo is None or term_repo is None:
                self.say("中央库打不开，无法执行。")
                return
            try:
                # ★ 预览与执行**同源**（rename_service.plan/apply 共用 rows_of_rename）
                rep = rename_service.plan(tm_repo, term_repo, [(o, n_)],
                                          en_override=en or None)
            except Exception as ex:                                # noqa: BLE001
                self.say("预览失败：%r" % ex)
                return
            self.say("\n=== %s：%s → %s ===" % ("预览影响面" if dry else "执行结果", o, n_))
            for r in rep:
                self.say("  英文护栏：%s" % (r["guard"] or "（无！会全库替换，务必人工复核）"))
                self.say("  术语表命中：%s"
                         % (", ".join("%s=%s" % (h["en"], h["zh"])
                                      for h in r["glossary_hits"]) or "（无）"))
                self.say("  记忆库将改：%d 条（原文空 %d 条）｜无护栏全库命中：%d 条"
                         % (r["tm_rows"], r["empty_src_rows"], r["loose_rows"]))
                self.say("  涉及：%s" % (", ".join("%s(%d)" % (m, c)
                                                   for m, c in r["mods"].items()) or "（无）"))
                # ★ 2026-10-10：危险替换（短旧译名 / 无护栏）在这里就告知，
                #   执行时 `rename_service.apply` 会直接拒绝 ✓
                for w in (r.get("risky") or []):
                    self.say("  ⚠ 危险，执行会被拒绝：%s" % w)
            if dry:
                self.say("  （以上是**预览**，没有改动任何数据）")
                return
            try:
                res = rename_service.apply(tm_repo, term_repo, [(o, n_)], rep)
            except Exception as ex:                                # noqa: BLE001
                self.say("执行失败：%r" % ex)
                return
            self.say("  已改记忆库 %d 条、写入术语 %d 条、替换规则 %d 条（记忆库改动标 manual）"
                     % (res["tm_rows"], res["terms"], res["variants"]))
            messagebox.showinfo("完成", "记忆库与术语表已更新。\n"
                                       "请重建受影响的产物（0 次 API）。")
        tk.Button(dlg, text=("预览影响面" if dry else "确认执行"), command=go).grid(
            row=4, column=0, columnspan=2, pady=8)

    def fill_tlk_dialog(self):
        """选一个 TLK，把**记忆库**的译文回填进去（预览 → 备份 → 写 → 读回校验）。

        用户 2026-10-08 需求：术语库这边改完，也能**就地**把结果推到 TLK。
        英文源按方案 A 自动找（同目录 `<名字>E.tlk` 等）；找不到就拒绝（无法按内容回填）。
        """
        p = filedialog.askopenfilename(title="选择要回填的 TLK", initialdir=HERE,
                                       filetypes=[("TLK 文件", "*.tlk"), ("所有文件", "*.*")])
        if not p:
            return
        tm = self._tm()
        if tm is None:
            messagebox.showerror("记忆库不可用", "打不开：%s" % self.db_path)
            return
        try:
            en, why = tlk_service.find_en_source(p, db=self._db(), project=self.project)
        except Exception as ex:                                    # noqa: BLE001
            en, why = None, repr(ex)
        if not en:
            messagebox.showinfo("回填到 TLK",
                                "找不到这个 TLK 的**英文源**，没法按内容回填。\n\n原因：%s\n\n"
                                "提示：把英文源放在同目录（如 `xxxE.tlk`）通常就能自动识别。" % why)
            return
        try:
            rep = tlk_fill_service.plan_fill(p, en, tm)
        except Exception as ex:                                    # noqa: BLE001
            messagebox.showerror("预览失败", repr(ex))
            return
        lines = [
            "TLK：%s" % os.path.basename(p),
            "英文源：%s（%s）" % (os.path.basename(en), why),
            "",
            "会改：**%d 条**｜不变：%d｜记忆库没译文保持原样：%d｜自加槽位原样保留：%d"
            % (rep["changeable"], rep["same"], rep["no_tm"], rep["custom_slots"]),
            "",
            "写前自动备份为：%s.回填前_<时间戳>" % os.path.basename(p),
            "写后逐条读回校验。",
        ]
        if rep["preview"]:
            lines += ["", "前 %d 条预览：" % len(rep["preview"])]
            lines += ["  #%s  %s → %s" % (c["strref"], c["old"][:18], c["new"][:18])
                      for c in rep["preview"][:10]]
        if not rep["changeable"]:
            messagebox.showinfo("回填到 TLK", "\n".join(lines) + "\n\n（没有需要改的条目）")
            return
        if not messagebox.askyesno("回填到 TLK", "\n".join(lines) + "\n\n确定执行？"):
            return
        try:
            res = tlk_fill_service.fill_tlk(p, en, tm)
        except Exception as ex:                                    # noqa: BLE001
            messagebox.showerror("回填失败", repr(ex))
            return
        self.say("回填 %s：写 %d 条，读回校验 %s，备份 %s"
                 % (os.path.basename(p), res["written"], res["verified"], res["backup"]))
        if res["verified"]:
            messagebox.showinfo("回填完成", "已回填 %d 条，读回校验通过 ✓\n\n备份：\n%s"
                                % (res["written"], res["backup"]))
        else:
            messagebox.showwarning("回填完成但有异常",
                                   "应改未改 %d / 误改 %d\n备份：%s"
                                   % (len(res["bad"]), len(res["untouched_bad"]), res["backup"]))

    def say(self, s):
        self.log.config(state="normal")
        self.log.insert("end", str(s) + "\n")
        self.log.see("end")
        self.log.config(state="disabled")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        # 无头自检：能创建界面并进入事件循环 0.8 秒即算通过（用于改完代码后自动验证）
        _app = App()
        _app.after(800, _app.destroy)
        _app.mainloop()
        print("自检通过：界面创建成功、事件循环正常 ✓（术语表 %d 条）" % len(_app.terms))
        sys.exit(0)
    # 启动失败时**别让窗口一闪就没**：弹框 + 落日志（用户双击运行时尤其重要）
    try:
        App().mainloop()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        log = os.path.join(HERE, "term_editor_error.log")   # ASCII 名，方便 .bat/命令行引用
        try:
            open(log, "w", encoding="utf-8").write(tb)
        except Exception:
            pass
        try:
            messagebox.showerror("术语库编辑器启动失败",
                                 tb[-1200:] + "\n\n完整日志已写到：\n" + log)
        except Exception:
            print(tb)
        raise
