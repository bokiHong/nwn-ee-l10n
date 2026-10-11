#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mod tool — 无冬之夜 .mod 汉化可视化编辑器

打开 .mod → 逐条查看/修改译文 → 存回翻译记忆库 → 重新打包（不花钱）。

为什么"存回记忆库"是核心：译文只存在 `modl10n\\tm.sqlite` 里，打包时脚本是从记忆库
取译文的。所以在本程序里改完必须点「保存到记忆库」，再「重新打包」，改动才会进 .mod。

    python "mod tool.py"                      # 打开图形界面
    python "mod tool.py" info    <X.mod>      # 命令行：这个 mod 有多少可译文本
    python "mod tool.py" export  <X.mod> <表.tsv>   # 导出审校表（同 review export）
    python "mod tool.py" apply   <表.tsv>     # 回导审校表
    python "mod tool.py" repack  <X.mod> [--inplace]  # 只打包已有译文，绝不调 API

安全设计：本程序**不会**直接改写 .mod，只改记忆库；打包交给经过验证的 pipeline
（原子写 + 覆盖前自动备份 + 读回校验）。GUI 的打包按钮使用"离线翻译器"，
**任何情况下都不会产生 API 费用**。
"""
import io
import os
import re
import sys
import queue
import threading
import argparse
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from modl10n.core import sfrules as R                     # noqa: E402
from modl10n import pipeline as P                         # noqa: E402
from modl10n.glossary import Glossary                     # noqa: E402

# Sprint 7：业务逻辑已下沉到 `l10n` 包。本文件只保留：
#   ① 调 service 的**同名薄封装**（`ModDoc` / `repack` / `make_repack_args` / `cmd_*`）
#   ② 界面（UI 状态与展示）。
from l10n.db import DEF_DB, connect as db_connect         # noqa: E402
from l10n.repo import TmRepo                              # noqa: E402
from l10n.services import mod_service, pack_service, review_service   # noqa: E402
from l10n.services.mod_service import ModDoc, nl_of       # noqa: E402
from l10n.services.pack_service import OfflineTranslator, make_repack_args   # noqa: E402

DEF_TM = DEF_DB
DEF_GLOSSARY = os.path.join(HERE, "modl10n", "glossary.json")
NL = R.NL


# ============================================================ 纯逻辑层（已下沉）
# `ModDoc` 的实现已搬到 `l10n/services/mod_service.py` —— 零 tk、连接可注入、
# 并修掉"写库失败却清空 edits"的丢数据 bug。这里保留**同名引用**，因为
# `启动mod编辑器.bat`、`modl10n\新手上手.md`、`modl10n\README.md` 都按这个类名用。
# （历史：`nl_of` 也曾在这里，现同样在 mod_service 里，保持同名单一来源。）


ModDoc = mod_service.ModDoc      # 已下沉到 l10n/services/mod_service.py


def repack(modpath, outdir=None, out=None, inplace=False, tm_path=DEF_TM,
           glossary=DEF_GLOSSARY, db=None, progress=None):
    """[已下沉到 `l10n.services.pack_service.repack`] 打包已有译文，**全程不调 API**。

    新增 `db` 参数：给了就用**中央库 `term` 表**做术语强校验（真源），
    否则退回 `glossary.json` 快照（旧行为）。
    """
    return pack_service.repack(modpath, outdir=outdir, out=out, inplace=inplace,
                               tm_path=tm_path, glossary=glossary, db=db, progress=progress)


# ============================================================ 图形界面
# ============================================================ 图形界面
def run_gui(test_hook=None, db_path=None, project=None):
    """图形界面入口。

    `db_path` / `project` 可注入 —— **测试必须传临时库**，否则会写真实 `tm.sqlite`（红线）。
    `test_hook(app)` 是无头测试钩子（界面起来 150ms 后调用，然后自动关窗）。
    """
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, simpledialog  # noqa: F401

    FONT = ("Microsoft YaHei", 10)
    MONO = ("Consolas", 10)

    class App(tk.Tk):
        def __init__(self, db_path=None, project=None):
            super().__init__()
            self.title("MOD Tool — 无冬之夜 .mod 汉化编辑器")
            self.geometry("1280x800")
            self.doc = None
            # ★ 可注入：测试用临时库，绝不碰真实 tm.sqlite（红线）
            self.tm_path = os.path.abspath(db_path or DEF_TM)
            self.project = project
            self._db_handle = None
            self._db_lock = threading.Lock()   # 惰性开库 + 后台线程 → 必须双检锁
            self.q = queue.Queue()
            self.cur_key = None
            self.busy = False
            self._build()
            self.protocol("WM_DELETE_WINDOW", self.on_close)
            self.after(80, self._pump)

        def _db(self):
            """惰性打开中央库 —— **整个 App 只用一个连接**。

            多连接同时跑迁移会争 WAL 写锁（tlk tool 那边踩过
            `view v_unit_todo already exists`），而 `load_file` 是在后台线程里调它的。
            """
            if self._db_handle is None:
                with self._db_lock:
                    if self._db_handle is None:
                        try:
                            self._db_handle = db_connect(self.tm_path)
                        except Exception as e:            # noqa: BLE001
                            print("[mod tool] 打开中央库失败：%r" % (e,), file=sys.stderr)
                            self._db_handle = False
            return self._db_handle or None

        # ---------------- 界面 ----------------
        def _build(self):
            m = tk.Menu(self)
            f = tk.Menu(m, tearoff=0)
            f.add_command(label="打开 .mod…", command=self.open_mod, accelerator="Ctrl+O")
            f.add_command(label="保存到记忆库", command=self.save_tm, accelerator="Ctrl+S")
            f.add_separator()
            f.add_command(label="重新打包（另存 _zh.mod）…", command=lambda: self.do_repack(False))
            f.add_command(label="重新打包并覆盖原件（自动备份）…", command=lambda: self.do_repack(True))
            f.add_separator()
            f.add_command(label="导出审校表…", command=self.do_export)
            f.add_command(label="从审校表回导…", command=self.do_import)
            f.add_separator()
            f.add_command(label="退出", command=self.on_close)
            m.add_cascade(label="文件", menu=f)

            t = tk.Menu(m, tearoff=0)
            t.add_command(label="把本条的修改全部撤销（重载记忆库）", command=self.reload_tm)
            t.add_command(label="统计信息", command=self.show_stat)
            t.add_separator()
            t.add_command(label="清空所有筛选", command=self.clear_filter)
            m.add_cascade(label="工具", menu=t)

            h = tk.Menu(m, tearoff=0)
            h.add_command(label="使用说明", command=self.show_help)
            h.add_command(label="关于记忆库与打包", command=self.show_about)
            m.add_cascade(label="帮助", menu=h)
            self.config(menu=m)

            bar = tk.Frame(self)
            bar.pack(fill="x", padx=6, pady=4)
            tk.Label(bar, text="搜索(原文/译文):", font=FONT).pack(side="left")
            self.qv = tk.StringVar()
            e = tk.Entry(bar, textvariable=self.qv, width=26, font=FONT)
            e.pack(side="left", padx=3)
            e.bind("<Return>", lambda _e: self.refresh())
            tk.Button(bar, text="搜索", font=FONT, command=self.refresh).pack(side="left")
            tk.Label(bar, text="资源名含:", font=FONT).pack(side="left", padx=(10, 0))
            self.rv = tk.StringVar()
            e2 = tk.Entry(bar, textvariable=self.rv, width=16, font=FONT)
            e2.pack(side="left", padx=3)
            e2.bind("<Return>", lambda _e: self.refresh())
            self.only_unt = tk.BooleanVar(value=False)
            tk.Checkbutton(bar, text="只看未译/待重译", variable=self.only_unt, font=FONT,
                           command=self.refresh).pack(side="left", padx=6)
            self.only_ed = tk.BooleanVar(value=False)
            tk.Checkbutton(bar, text="只看已改", variable=self.only_ed, font=FONT,
                           command=self.refresh).pack(side="left")
            self.count_lbl = tk.Label(bar, text="", fg="#555", font=FONT)
            self.count_lbl.pack(side="right")

            pane = tk.PanedWindow(self, orient="horizontal", sashwidth=5)
            pane.pack(fill="both", expand=True, padx=6, pady=(0, 4))

            left = tk.Frame(pane)
            body = tk.Frame(left)
            vs = ttk.Scrollbar(body, orient="vertical")
            self.tree = ttk.Treeview(body, columns=("st", "loc", "prev"), show="headings",
                                     selectmode="browse", yscrollcommand=vs.set)
            vs.config(command=self.tree.yview)
            for col, txt, w, anch in (("st", "状态", 70, "center"), ("loc", "位置", 190, "w"),
                                      ("prev", "译文预览", 470, "w")):
                self.tree.heading(col, text=txt)
                self.tree.column(col, width=w, anchor=anch,
                                 stretch=(col == "prev"))
            vs.pack(side="right", fill="y")
            self.tree.pack(side="left", fill="both", expand=True)
            body.pack(fill="both", expand=True)
            self.tree.bind("<<TreeviewSelect>>", self.on_pick)
            pane.add(left, width=740)

            right = tk.Frame(pane)
            top = tk.Frame(right)
            top.pack(fill="x")
            self.loc_lbl = tk.StringVar(value="(未打开 .mod)")
            tk.Label(top, textvariable=self.loc_lbl, font=("Consolas", 10, "bold")).pack(side="left")
            self.multi_lbl = tk.Label(top, text="", fg="#0a7", font=FONT)
            self.multi_lbl.pack(side="right")

            tk.Label(right, text="英文原文（只读）", fg="#666", font=FONT).pack(anchor="w")
            self.src_box = tk.Text(right, height=5, wrap="word", font=MONO,
                                   background="#f4f4f4", foreground="#333")
            self.src_box.pack(fill="x", pady=(0, 4))
            self.src_box.config(state="disabled")

            tk.Label(right, text="译文（可改；\\n 是换行标记，请保留）", fg="#666",
                     font=FONT).pack(anchor="w")
            self.editor = tk.Text(right, wrap="word", undo=True, font=("Microsoft YaHei", 11))
            self.editor.pack(fill="both", expand=True, pady=(0, 4))
            self.editor.bind("<Control-Return>", lambda _e: (self.apply_edit(), "break")[1])
            self.editor.bind("<KeyRelease>", lambda _e: self.mark_dirty())

            btns = tk.Frame(right)
            btns.pack(fill="x")
            tk.Button(btns, text="应用修改 (Ctrl+Enter)", font=FONT,
                      command=self.apply_edit).pack(side="left")
            tk.Button(btns, text="撤销本条", font=FONT,
                      command=self.undo_one).pack(side="left", padx=4)
            tk.Button(btns, text="标记待重译", font=FONT,
                      command=self.toggle_retrans).pack(side="left", padx=4)
            tk.Button(btns, text="↑上一条  ↓下一条", font=FONT,
                      command=self.next_item).pack(side="right")
            pane.add(right)

            self.status = tk.StringVar(value="就绪。文件 → 打开 .mod 开始。")
            tk.Label(self, textvariable=self.status, anchor="w", relief="sunken",
                     font=FONT).pack(fill="x", side="bottom")

        # ---------------- 后台任务队列 ----------------
        def _pump(self):
            try:
                while True:
                    kind, val = self.q.get_nowait()
                    if kind == "status":
                        self.status.set(val)
                    elif kind == "loaded":
                        self.on_loaded(val)
                    elif kind == "error":
                        self.busy = False
                        messagebox.showerror("出错了", val)
                    elif kind == "log":
                        self.log_write(val)
                    elif kind == "done":
                        self.busy = False
                        self.log_done(val)
            except queue.Empty:
                pass
            except tk.TclError:          # 窗口已关闭
                return
            try:
                self.after(80, self._pump)
            except tk.TclError:
                pass

        def log_write(self, s):
            if getattr(self, "logbox", None) is not None:
                try:
                    self.logbox.insert("end", s + "\n")
                    self.logbox.see("end")
                except tk.TclError:
                    self.logbox = None

        def log_done(self, info):
            if getattr(self, "logbox", None) is not None:
                self.logbox.insert("end", "\n" + info["summary"] + "\n")
                self.logbox.see("end")
            self.status.set(info["summary"].replace("\n", "  "))

        # ---------------- 打开 ----------------
        def open_mod(self):
            p = filedialog.askopenfilename(
                title="打开 .mod",
                initialdir=os.path.join(os.path.expanduser("~"), "Documents", "Neverwinter Nights", "modules"),
                filetypes=[("无冬之夜模组", "*.mod"), ("所有文件", "*.*")])
            if p:
                self.load_file(p)

        def load_file(self, path):
            if self.busy:
                return
            if self.doc and (self.doc.edits or self.doc.retrans):
                if not messagebox.askyesno("提示", "有 %d 条修改未保存到记忆库，仍要打开新文件吗？"
                                                   % (len(self.doc.edits) + len(self.doc.retrans))):
                    return
            self.busy = True
            self.status.set("正在解析 %s …（大模组可能要 1 分钟）" % os.path.basename(path))
            self.tree.delete(*self.tree.get_children())

            def work():
                try:
                    doc = ModDoc(path, db=self._db(),
                                 progress=lambda s: self.q.put(("status", s)))
                except Exception as e:                        # noqa: BLE001
                    self.q.put(("error", "打开失败：%r" % e))
                    return
                self.q.put(("loaded", doc))

            threading.Thread(target=work, daemon=True).start()

        def on_loaded(self, doc):
            self.doc = doc
            self.cur_key = None
            self.title("MOD Tool — %s" % os.path.basename(doc.modpath))
            self.refresh()
            st = doc.stat()
            self.status.set("%s ｜ 资源 %d ｜ 可译位置 %d ｜ 不重复文本 %d ｜ 记忆库 %d 条"
                            % (os.path.basename(doc.modpath), st["资源"], st["可译文本位置"],
                               st["不重复文本"], st["记忆库条数"]))
            self.busy = False

        # ---------------- 列表 ----------------
        def refresh(self):
            if not self.doc:
                return
            self.tree.delete(*self.tree.get_children())
            kw = self.qv.get().strip()
            rk = self.rv.get().strip().lower()
            n = 0
            for it in self.doc.items:
                if rk and rk not in (it["loc"] or "").lower():
                    continue
                st = self.doc.status_of(it)
                if self.only_unt.get() and st not in ("未译", "⟳重译"):
                    continue
                if self.only_ed.get() and st != "✎已改":
                    continue
                if kw:
                    hay = (it["src"] or "") + "\n" + self.doc.dst_now(it) + "\n" + (it["loc"] or "")
                    if kw not in hay:
                        continue
                prev = (self.doc.dst_now(it) or it["src"]).replace("\n", " ")
                self.tree.insert("", "end", iid=it["key"], values=(st, it["loc"], prev[:120]))
                n += 1
            self.count_lbl.config(text="显示 %d / %d 条" % (n, len(self.doc.items)))

        def selected_key(self):
            sel = self.tree.selection()
            return sel[0] if sel else None

        def on_pick(self, _e=None):
            k = self.selected_key()
            if not k or not self.doc:
                return
            self.stash_editor()                 # 切走前先把没应用的编辑收好，避免丢字
            self.cur_key = k
            it = self.doc.by_key[k]
            self.loc_lbl.set("%s · %s" % (it["loc"], it["label"]))
            self.multi_lbl.config(text=("⚠ 该文本在本 mod 出现 %d 处，改一次全部生效"
                                        % it["count"]) if it["count"] > 1 else "")
            self.src_box.config(state="normal")
            self.src_box.delete("1.0", "end")
            self.src_box.insert("1.0", self.doc.display_src(it))
            self.src_box.config(state="disabled")
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", self.doc.to_edit(it["kind"], self.doc.dst_now(it)))
            self.mark_dirty()

        def mark_dirty(self):
            if not self.doc or not self.cur_key:
                return
            it = self.doc.by_key[self.cur_key]
            cur = self.editor.get("1.0", "end-1c")
            stored = self.doc.to_store(it["kind"], cur)
            dirty = (stored != self.doc.dst_now(it))
            self.status.set(("● 本条已改动（记得 Ctrl+S 保存到记忆库）｜ " if dirty else "") +
                            "%d 条未保存" % len(self.doc.edits))

        def stash_editor(self):
            """把编辑器内容收进内存里的 edits（不落盘）。"""
            if not self.doc or not self.cur_key:
                return
            it = self.doc.by_key[self.cur_key]
            cur = self.editor.get("1.0", "end-1c")
            self.doc.set_edit(self.cur_key, self.doc.to_store(it["kind"], cur))

        def apply_edit(self):
            if not self.doc or not self.cur_key:
                return
            k = self.cur_key
            self.stash_editor()
            st = self.doc.status_of(self.doc.by_key[k])
            self._update_row(k)
            self.status.set("已应用到内存（%s）。点 文件→保存到记忆库 或按 Ctrl+S 写入。" % st)

        def _update_row(self, key):
            if not self.tree.exists(key):
                return
            it = self.doc.by_key[key]
            self.tree.item(key, values=(self.doc.status_of(it), it["loc"],
                                        (self.doc.dst_now(it) or it["src"]).replace("\n", " ")[:120]))

        def undo_one(self):
            if not self.doc or not self.cur_key:
                return
            k = self.cur_key
            self.doc.revert(k)          # ★ 撤掉本条未保存的改动（已收进 ModDoc 自身）
            it = self.doc.by_key[k]
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", self.doc.to_edit(it["kind"], it["dst"]))
            self._update_row(k)
            self.status.set("已撤销本条改动。")

        def toggle_retrans(self):
            if not self.doc or not self.cur_key:
                return
            k = self.cur_key
            on = k not in self.doc.retrans
            self.doc.mark_retrans(k, on)
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", self.doc.to_edit(self.doc.by_key[k]["kind"],
                                                      self.doc.dst_now(self.doc.by_key[k])))
            self._update_row(k)
            self.status.set("已标记待重译（保存后，下次用命令行跑 pipeline 时会重新翻译；"
                            "本程序的打包按钮不会调用 API）。" if on else "已取消待重译标记。")

        def next_item(self):
            kids = self.tree.get_children()
            if not kids:
                return
            cur = self.selected_key()
            i = (kids.index(cur) + 1) % len(kids) if cur in kids else 0
            self.tree.selection_set(kids[i])
            self.tree.see(kids[i])

        def clear_filter(self):
            self.qv.set("")
            self.rv.set("")
            self.only_unt.set(False)
            self.only_ed.set(False)
            self.refresh()

        # ---------------- 保存 / 打包 ----------------
        def save_tm(self):
            if not self.doc:
                return
            self.stash_editor()
            if not self.doc.edits and not self.doc.retrans:
                self.status.set("没有需要保存的改动。")
                return
            n, fail = self.doc.save()
            self.refresh()
            if fail:
                messagebox.showerror("部分保存失败", "\n".join("%s: %s" % x for x in fail[:5]))
            messagebox.showinfo("已保存到记忆库",
                                "写入 %d 条（status=manual，机器翻译不会再覆盖）。\n\n"
                                "下一步：文件 → 重新打包，让改动进 .mod（0 次 API 调用）。\n"
                                "记忆库：%s" % (n, self.doc.tm_path))
            self.status.set("已保存 %d 条到记忆库；现在可以重新打包了。" % n)

        def reload_tm(self):
            if not self.doc:
                return
            if self.doc.edits and not messagebox.askyesno(
                    "确认", "会丢弃 %d 条未保存的改动，继续？" % len(self.doc.edits)):
                return
            self.load_file(self.doc.modpath)

        def do_repack(self, inplace):
            if not self.doc:
                return
            self.stash_editor()
            if self.doc.edits and not messagebox.askyesno(
                    "提示", "还有 %d 条改动没保存到记忆库，打包不会包含它们。先保存吗？" % len(self.doc.edits)):
                pass
            pend = self.doc.pending()
            msg = ("将重新打包（不调用 API，0 花费）。\n\n"
                   "输出：%s\n" % ("覆盖原件（自动备份为 .orig-backup）" if inplace
                                 else "同目录的 汉化产物\\<名字>_zh.mod"))
            if pend:
                msg += ("\n注意：有 %d 条未译/待重译，本次会保持英文（要翻它们请用命令行跑 "
                        "pipeline，那会产生 API 费用）。\n" % pend)
            if not messagebox.askyesno("重新打包", msg):
                return
            if self.busy:
                return
            self.busy = True
            modpath, tm_path = self.doc.modpath, self.doc.tm_path
            db = self._db()
            self.open_log()

            def work():
                try:
                    # ★ 传 db：打包时的术语强校验用中央库 term 表（真源），
                    #   而不是降级的 glossary.json 快照 —— 否则"改了术语却不生效"。
                    r = repack(modpath, inplace=inplace, tm_path=tm_path, db=db,
                               progress=lambda s: self.q.put(("log", s)))
                    v = r["verified"]
                    summary = ("✅ 打包完成：%s\n读回校验：比对 %s 条，不一致 %s 条"
                               % (r["out"], r["compared"], 0 if v else "??"))
                    if v is False:
                        summary = "⚠ 打包完成但读回校验不一致：%s（别装进游戏，请把日志发我）" % r["out"]
                    self.q.put(("done", {"summary": summary, "log": r["log"]}))
                except Exception as e:                            # noqa: BLE001
                    self.q.put(("error", "打包失败：%r" % e))
                    self.q.put(("done", {"summary": "打包失败", "log": ""}))

            threading.Thread(target=work, daemon=True).start()

        def open_log(self):
            win = tk.Toplevel(self)
            win.title("打包日志")
            win.geometry("820x520")
            box = tk.Text(win, wrap="none", font=("Consolas", 9))
            sb = ttk.Scrollbar(win, orient="vertical", command=box.yview)
            box.config(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            box.pack(fill="both", expand=True)
            self.logbox = box
            tk.Button(win, text="关闭", command=lambda: (setattr(self, "logbox", None),
                                                         win.destroy())).pack(fill="x")

        # ---------------- 审校表 ----------------
        def do_export(self):
            if not self.doc:
                return
            p = filedialog.asksaveasfilename(title="导出审校表", defaultextension=".tsv",
                                             initialfile="审校表.tsv",
                                             filetypes=[("TSV 表格", "*.tsv"), ("所有文件", "*.*")])
            if not p:
                return
            try:
                # ★ 已下沉到 l10n.services.review_service（内部仍走已验证的 modl10n.review）
                review_service.export_table(self.doc.modpath, p, tm_path=self.tm_path)
                messagebox.showinfo("导出完成", "已导出到：\n%s" % p)
            except Exception as e:                                # noqa: BLE001
                messagebox.showerror("导出失败", repr(e))

        def do_import(self):
            if not self.doc:
                return
            p = filedialog.askopenfilename(title="选择回导的审校表",
                                           filetypes=[("TSV 表格", "*.tsv"), ("所有文件", "*.*")])
            if not p:
                return
            if not messagebox.askyesno("确认", "用 %s 回导？\n改过的条目会覆盖记忆库里的译文。"
                                               % os.path.basename(p)):
                return
            try:
                review_service.apply_table(p, tm_path=self.tm_path)
            except Exception as e:                                # noqa: BLE001
                messagebox.showerror("回导失败", repr(e))
                return
            messagebox.showinfo("回导完成", "已回导，窗口将重新载入。")
            self.load_file(self.doc.modpath)

        # ---------------- 信息 ----------------
        def show_stat(self):
            if not self.doc:
                messagebox.showinfo("统计", "还没有打开 .mod。")
                return
            self.stash_editor()
            st = self.doc.stat()
            lines = ["文件：%s" % self.doc.modpath, "记忆库：%s" % self.doc.tm_path, ""]
            lines += ["%s：%s" % (k, v) for k, v in st.items()]
            lines += ["", "提示：不重复文本 = 实际可编辑行数；位置数 = 改动生效的处数。"]
            messagebox.showinfo("统计信息", "\n".join(lines))

        def show_help(self):
            messagebox.showinfo("使用说明", HELP_TEXT)

        def show_about(self):
            messagebox.showinfo("关于记忆库与打包", ABOUT_TEXT)

        def on_close(self):
            if self.doc and (self.doc.edits or self.doc.retrans):
                if not messagebox.askyesno("退出", "有 %d 条修改未保存到记忆库，确定退出？"
                                                   % (len(self.doc.edits) + len(self.doc.retrans))):
                    return
            self.destroy()

    HELP_TEXT = """1) 文件 → 打开 .mod，等左下角出现统计数字
2) 左边列表点一条，右边上面是英文原文（只读），下面是译文（可改）
   多行文本里的 \\n 是两个字符的换行标记，请保留
3) 改完按 Ctrl+Enter 应用（只是收进内存），改完一批后按 Ctrl+S 保存到记忆库
   —— 保存的条目标为 manual，机器翻译永远不会再覆盖你的润色
4) 文件 → 重新打包，让改动进 .mod（不调用 API，0 花费）
   想替换原始文件名就选"覆盖原件"（会先自动备份成 .orig-backup）
5) 一句话在 mod 里出现多次时只占一行，右上角会提示"出现 N 处"——改一次全部生效
6) "标记待重译"只是做个记号；要让模型重翻，用命令行跑 pipeline（会产生费用），
   本程序的打包按钮不会调用 API

快捷键：Ctrl+O 打开、Ctrl+S 保存、Ctrl+Enter 应用本条、上下条按钮切换"""

    ABOUT_TEXT = """译文存在哪：modl10n\\tm.sqlite（表 tm，key=原文sha256，dst=译文）。
打包时脚本是从这个记忆库取译文的，所以必须"保存到记忆库"才生效；
改完再"重新打包"才会进 .mod。

为什么分两步：本程序不直接改写 .mod（那很危险），只改记忆库；
打包交给经过验证的 pipeline —— 原子写（先写临时文件再替换，绝不会出现 0 字节文件）、
覆盖原件前自动备份、打完自动读回校验（不一致必须为 0）。

一句话在 mod 里出现多次时只占一行（按内容哈希去重），所以 3000 多行就覆盖了全部文本位置。

本程序不调用任何 API；想批量翻译请用命令行 python -m modl10n.pipeline"""

    app = App(db_path=db_path, project=project)
    if test_hook:
        # ⚠ 必须 try/finally：旧写法 `(test_hook(app), app.destroy())` 在 hook 抛异常时
        #   **元组求值中断、destroy 不执行** → mainloop 永不返回 → 测试直接挂死。
        #   （Tk 的 after 回调异常只走 report_callback_exception，不会传到调用者。）
        def _run_hook():
            try:
                test_hook(app)
            except Exception:
                import traceback
                traceback.print_exc()
            finally:
                try:
                    app.destroy()
                except Exception:
                    pass
        app.after(150, _run_hook)
    app.mainloop()
    return 0


# ============================================================ 命令行
def cmd_info(modpath, tm_path=DEF_TM):
    doc = ModDoc(modpath, tm_path=tm_path)
    try:
        print("文件：%s" % doc.modpath)
        print("资源 %d 个（其中 .ncs %d 个）" % (doc.n_res, doc.n_ncs))
        for k, v in doc.stat().items():
            print("  %s: %s" % (k, v))
    finally:
        doc.close()
    return 0


def cmd_export(modpath, out, tm_path=DEF_TM):
    """[已下沉到 `l10n.services.review_service.export_table`]"""
    return review_service.export_table(modpath, out, tm_path=tm_path)


def cmd_apply(table, tm_path=DEF_TM):
    """[已下沉到 `l10n.services.review_service.apply_table`]"""
    return review_service.apply_table(table, tm_path=tm_path)


def cmd_repack(modpath, inplace=False, outdir=None, out=None, tm_path=DEF_TM):
    real = sys.stdout                      # 在重定向之前抓住真正的 stdout
    # ★ 传中央库连接：让打包时的术语强校验用 **term 表**（真源），而不是降级的 JSON 快照
    db = db_connect(tm_path)
    try:
        r = repack(modpath, outdir=outdir, out=out, inplace=inplace, tm_path=tm_path,
                   db=db, progress=lambda s: real.write(s + "\n"))
    finally:
        db.close()
    print("\n输出：%s" % r["out"])
    print("读回校验：%s" % ("通过（不一致 0 条）" if r["verified"] else "未取得结果"))
    return 0


def main():
    args = sys.argv[1:]
    if not args:
        return run_gui()
    cmd = args[0]

    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default

    if cmd == "gui":
        return run_gui()
    if cmd == "info":
        return cmd_info(args[1])
    if cmd == "export":
        return cmd_export(args[1], args[2])
    if cmd == "apply":
        return cmd_apply(args[1])
    if cmd == "repack":
        return cmd_repack(args[1], inplace="--inplace" in args,
                          outdir=opt("--outdir"), out=opt("--out"))
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
