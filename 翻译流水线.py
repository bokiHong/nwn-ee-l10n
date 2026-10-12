#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""翻译流水线 —— 把 **scan → extract → diff → plan** 四步串成一个界面（**0 次 API** ✓）。

为什么需要它：这四步以前只能在命令行一条条跑
（`python -m l10n scan / extract / diff / plan`），
**看不到进度、也看不到"还差多少条 / 大概要花多少钱"** ✗。
而它们正是 Ch3~Ch5 翻译的必经入口 ✓。

四步各干什么（都只登记/统计，**绝不调 API、绝不花钱** ✓）：
    ① scan     打开模组 → 把资源登记进 `container`/`resource`
    ② extract  抽出文本 → 落到 `unit`（去重后进记忆库键）
    ③ diff     算"还要翻哪些"：记忆库已命中多少 / 真正新增多少
    ④ plan     花钱前预览：这次要翻多少条、**按单价估算要花多少钱**

用法：双击 `启动翻译流水线.bat`，或 `python "翻译流水线.py"`（`--self-test` 只建界面自检）
"""
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tkinter as tk                                          # noqa: E402
from tkinter import ttk, messagebox, filedialog               # noqa: E402

from l10n.db import DEF_DB, connect as db_connect            # noqa: E402
from l10n import ui_state                                    # noqa: E402 ★ 记住上次状态
from l10n.services import diff_service, extract_service, plan_service, scan_service  # noqa: E402

ROOT = HERE      # 开源布局：仓库根 == 原 scripts；源布局下父目录才是工作根
DEF_MOD = os.path.join(ROOT, "module", "Sword Flight", "Swordflight - Chapter One.mod")


class App(tk.Tk):
    def __init__(self, db_path=None):
        super().__init__()
        self.title("翻译流水线 — 无冬之夜汉化（0 次 API）")
        # ★ 记住上次的窗口尺寸/位置（存在 `scripts\ui_state.json` ✓，坏文件不影响启动 ✓）
        ui_state.restore_window(self, "pipeline", "1000x680")
        self.minsize(760, 480)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.db_path = os.path.abspath(db_path or DEF_DB)
        self.db = None
        self.busy = False
        self._build()

    # ---------- 数据 ----------
    def _conn(self):
        if self.db is None:
            try:
                self.db = db_connect(self.db_path)
            except Exception as ex:                            # noqa: BLE001
                self.say("⚠ 打不开中央库：%r" % (ex,))
        return self.db

    # ---------- 界面 ----------
    def _build(self):
        top = tk.Frame(self)
        top.pack(fill="x", padx=8, pady=6)
        tk.Label(top, text="模组文件（.mod / .tlk）：").grid(row=0, column=0, sticky="e")
        self.mod = tk.StringVar(value=DEF_MOD if os.path.exists(DEF_MOD) else "")
        tk.Entry(top, textvariable=self.mod, width=62).grid(row=0, column=1, columnspan=3,
                                                            sticky="w", padx=4)
        tk.Button(top, text="浏览…", command=self.pick).grid(row=0, column=4, padx=4)
        tk.Label(top, text="项目名：").grid(row=1, column=0, sticky="e", pady=(4, 0))
        self.proj = tk.StringVar(value=ui_state.get("pipeline", "project", "swordflight"))
        tk.Entry(top, textvariable=self.proj, width=18).grid(row=1, column=1, sticky="w",
                                                             pady=(4, 0))
        tk.Label(top, text="单价（元/百万 token）　输入：").grid(row=1, column=2, sticky="e",
                                                                 pady=(4, 0))
        self.pin = tk.StringVar(value=str(ui_state.get("pipeline", "price_in", 1.0)))
        tk.Entry(top, textvariable=self.pin, width=7).grid(row=1, column=3, sticky="w",
                                                           pady=(4, 0))
        tk.Label(top, text="输出：").grid(row=1, column=4, sticky="e", pady=(4, 0))
        self.pout = tk.StringVar(value=str(ui_state.get("pipeline", "price_out", 2.0)))
        tk.Entry(top, textvariable=self.pout, width=7).grid(row=1, column=5, sticky="w",
                                                            pady=(4, 0))

        bar = tk.Frame(self)
        bar.pack(fill="x", padx=8)
        for text, fn in (("① 扫描（登记资源）", self.do_scan),
                         ("② 抽取文本", self.do_extract),
                         ("③ 看还差哪些", self.do_diff),
                         ("④ 估价预览（不花钱）", self.do_plan)):
            tk.Button(bar, text=text, width=20, command=fn).pack(side="left", padx=3, pady=4)
        tk.Button(bar, text="▶ 一键跑完 ①②③④", width=20, bg="#eef",
                  command=self.do_all).pack(side="left", padx=8, pady=4)

        tk.Label(self, text="日志（看进度与结果；四步都只登记/统计，绝不调用 API）",
                 fg="#666").pack(anchor="w", padx=8)
        box = tk.Frame(self)
        box.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        vs = ttk.Scrollbar(box, orient="vertical")
        self.log = tk.Text(box, wrap="word", yscrollcommand=vs.set)
        vs.config(command=self.log.yview)
        vs.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.config(state="disabled")
        self.say("步骤顺序：① 扫描 → ② 抽取 → ③ 看还差哪些 → ④ 估价预览。")
        self.say("③④ 是**只读**的，跑多少次都不花钱 ✓；跑完 ④ 就知道这次翻译大概要花多少钱。")

    def pick(self):
        p = filedialog.askopenfilename(title="选择模组或 TLK", initialdir=ROOT,
                                       filetypes=[("模组/TLK", "*.mod *.tlk"), ("所有文件", "*.*")])
        if p:
            self.mod.set(p)

    def on_close(self):
        """关窗前把"上次状态"存下来（窗口尺寸 / 模组路径 / 项目名 / 单价 ✓）。"""
        try:
            ui_state.save_window(self, "pipeline")
            st = ui_state.load()
            st.setdefault("pipeline", {}).update({
                "mod": self.mod.get().strip(),
                "project": self.proj.get().strip(),
                "price_in": self.pin.get().strip(),
                "price_out": self.pout.get().strip()})
            ui_state.save(st)
        except Exception:                                      # noqa: BLE001
            pass
        self.destroy()

    def say(self, s):
        self.log.config(state="normal")
        self.log.insert("end", str(s) + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    # ---------- 动作（都放子线程，避免界面卡死 ✓）----------
    def _run(self, fn, title):
        if self.busy:
            messagebox.showinfo("正在跑", "上一步还没跑完，请稍等。")
            return
        if not self._conn():
            return
        self.busy = True
        self.say("\n=== %s ===" % title)

        def work():
            try:
                fn()
            except Exception as ex:                             # noqa: BLE001
                self.after(0, self.say, "  ✗ 出错：%r" % (ex,))
            finally:
                self.busy = False

        threading.Thread(target=work, daemon=True).start()

    def _paths(self):
        mod = self.mod.get().strip()
        proj = self.proj.get().strip() or "default"
        if not mod or not os.path.exists(mod):
            messagebox.showwarning("还没选文件", "请先选一个存在的 .mod / .tlk 文件。")
            return None, None
        return mod, proj

    def do_scan(self):
        mod, proj = self._paths()
        if not mod:
            return

        def job():
            r = scan_service.scan(self.db, proj, mod, resource_hash=True)
            self.after(0, self.say, "  ✅ 容器 #%s：登记 %d 个资源"
                       % (r.get("container_id"), r.get("resources")))
        self._run(job, "① 扫描：%s" % os.path.basename(mod))

    def do_extract(self):
        mod, proj = self._paths()
        if not mod:
            return

        def job():
            r = extract_service.extract(self.db, proj, mod)
            self.after(0, self.say, "  ✅ 单元 %s 个（去重 %s 条文本）"
                       % (r.get("units"), r.get("unique")))
        self._run(job, "② 抽取：%s" % os.path.basename(mod))

    def do_diff(self):
        _mod, proj = self._paths()
        if not proj:
            return

        def job():
            d = diff_service.diff(self.db, project=proj, sample=8)
            self.after(0, self.say, "  " + diff_service.summarize(d))
            for s in (d.get("samples") or [])[:8]:
                self.after(0, self.say, "     [%s] %s:%s  %r"
                           % (s["cat"], s["resref"], s["field_path"], (s["src_text"] or "")[:52]))
        self._run(job, "③ 还差哪些（只读）")

    def do_plan(self):
        _mod, proj = self._paths()
        if not proj:
            return
        try:
            pin, pout = float(self.pin.get()), float(self.pout.get())
        except ValueError:
            messagebox.showwarning("单价填错了", "单价请填数字（元/百万 token）。")
            return

        def job():
            p = plan_service.plan(self.db, project=proj, price_in=pin, price_out=pout, sample=6)
            self.after(0, self.say, "  " + plan_service.summarize(p))
            self.after(0, self.say, "  " + diff_service.summarize(p["diff"]))
            self.after(0, self.say, "  💰 这次翻译预估花费：**%.2f 元**（单价 in=%.2f / out=%.2f）"
                       % (p["cost"], pin, pout))
            self.after(0, self.say, "  （以上是**预估**，没有调用任何 API ✓）")
        self._run(job, "④ 估价预览（只读，不花钱）")

    def do_all(self):
        def job():
            mod, proj = self._paths()
            if not mod:
                return
            r = scan_service.scan(self.db, proj, mod, resource_hash=True)
            self.after(0, self.say, "  ① 扫描 ✅ 资源 %d 个" % (r.get("resources") or 0))
            r = extract_service.extract(self.db, proj, mod)
            self.after(0, self.say, "  ② 抽取 ✅ 单元 %s / 去重 %s"
                       % (r.get("units"), r.get("unique")))
            d = diff_service.diff(self.db, project=proj, sample=4)
            self.after(0, self.say, "  ③ diff：%s" % diff_service.summarize(d))
            try:
                pin, pout = float(self.pin.get()), float(self.pout.get())
            except ValueError:
                pin = pout = 0.0
            p = plan_service.plan(self.db, project=proj, price_in=pin, price_out=pout, sample=3)
            self.after(0, self.say, "  ④ 估价：%s" % plan_service.summarize(p))
            self.after(0, self.say, "  💰 预估花费 **%.2f 元**（没有调用 API ✓）" % p["cost"])

        self._run(job, "▶ 一键跑完 ①②③④")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        a = App()
        a.after(700, a.destroy)
        a.mainloop()
        print("自检通过：翻译流水线界面创建成功 ✓")
        sys.exit(0)
    try:
        App().mainloop()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        try:
            messagebox.showerror("启动失败", tb[-1000:])
        except Exception:                                      # noqa: BLE001
            print(tb)
        raise
