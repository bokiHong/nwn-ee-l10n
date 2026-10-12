#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汉化工作台 —— **统一启动器**（2026-10-11，整合方案第 1 步）。

为什么做这个：四个工具各自双击各自的 bat，每次还要重新选文件 ✗。
这里给一个总入口：**看得到四个工具各干什么、记住最近打开的文件、一键带过去** ✓

设计原则（**零风险**）：本文件**不 import、不改动**任何旧工具 ——
它只用 `subprocess` 把对应对手的启动脚本**另起一个进程**拉起来 ✓
（所以旧工具的行为与以前逐字一致，出问题也只影响那个子进程 ✓）

    .mod 编辑器     mod tool.py       打开模组 → 逐条改译文 → 存记忆库 → 重新打包（0 次 API）
    TLK 编辑器      tlk tool.py       打开 TLK → 改文本 → AI 翻译（**会花钱** ✗）→ 回填
    术语库编辑器    术语库编辑器.py     术语管理 + 体检 + 撤销 + 重建产物 + 花费账本
    翻译流水线      翻译流水线.py       scan → extract → diff → plan（只登记/统计，不花钱 ✓）

用法：双击 `启动汉化工作台.bat`，或 `python "汉化工作台.py"`（`--self-test` 只自检界面）
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tkinter as tk                                          # noqa: E402
from tkinter import ttk, messagebox, filedialog               # noqa: E402

from l10n import ui_state                                     # noqa: E402

ROOT = HERE      # 开源布局：仓库根 == 原 scripts；源布局下父目录才是工作根

# (按键文字, 一句话说明, 启动脚本名, 是否花钱)
TOOLS = [
    ("📦  .mod 编辑器", "打开模组 → 逐条查看/修改译文 → 存回记忆库 → 重新打包（0 次 API）",
     "启动mod编辑器.bat", False),
    ("📜  TLK 编辑器", "打开 TLK → 改文本 → 「AI 翻译未翻译条目」（**会花钱** ✗）→ 回填到 TLK",
     "启动tlk编辑器.bat", True),
    ("📚  术语库编辑器", "术语管理 + 术语表体检 + 批量撤销 + 重建产物 + 花费账本 + 双击采纳",
     "启动术语库.bat", False),
    ("🚀  翻译流水线", "scan → extract → diff → plan 四步 + 估价（只登记/统计，绝不调 API ✓）",
     "启动翻译流水线.bat", False),
]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("汉化工作台 — 无冬之夜（统一入口）")
        ui_state.restore_window(self, "workbench", "820x560")
        self.minsize(700, 460)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build()

    def _build(self):
        tk.Label(self, text="汉化工作台", font=("", 15, "bold")).pack(anchor="w", padx=12,
                                                                     pady=(10, 0))
        tk.Label(self, text="四个工具的总入口；点哪个就打开哪个（各自独立进程，互不干扰 ✓）",
                 fg="#666").pack(anchor="w", padx=12)

        ctx = tk.Frame(self)
        ctx.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(ctx, text="最近打开：").pack(side="left")
        self.recent = tk.StringVar(value=ui_state.get("workbench", "recent", "") or "（还没有）")
        tk.Label(ctx, textvariable=self.recent, fg="#0a5", width=58, anchor="w").pack(side="left")
        tk.Button(ctx, text="选文件…", command=self.pick).pack(side="left", padx=6)
        tk.Label(ctx, text="项目名：").pack(side="left", padx=(12, 0))
        self.proj = tk.StringVar(value=ui_state.get("workbench", "project", "prc8"))
        tk.Entry(ctx, textvariable=self.proj, width=14).pack(side="left")

        body = tk.Frame(self)
        body.pack(fill="both", expand=True, padx=12, pady=8)
        for text, desc, bat, costs_money in TOOLS:
            row = tk.Frame(body)
            row.pack(fill="x", pady=4)
            tk.Button(row, text=text, width=18, anchor="w",
                      command=lambda b=bat: self.launch(b)).pack(side="left")
            tk.Label(row, text=desc, anchor="w", justify="left",
                     fg="#a00" if costs_money else "#333").pack(side="left", padx=8)

        tk.Label(self, text="提示：AI 翻译是**唯一会花钱**的动作，在 TLK 编辑器里（已标红 ✗）；"
                            "其余工具全程 0 次 API ✓",
                 fg="#666", wraplength=780, justify="left").pack(anchor="w", padx=12)
        box = tk.Frame(self)
        box.pack(fill="both", expand=True, padx=12, pady=(4, 10))
        self.log = tk.Text(box, height=8, wrap="word")
        self.log.pack(fill="both", expand=True)
        self.log.config(state="disabled")
        self.say("工作台已就绪。选一个工具开始 —— 改了术语/译文后，别忘了在对应工具里"
                 "「重建产物」或「重新打包」让改动进游戏 ✓")

    def pick(self):
        p = filedialog.askopenfilename(title="选择最常用的模组/TLK（记下来，下次自动带出）",
                                       initialdir=ROOT,
                                       filetypes=[("模组/TLK", "*.mod *.tlk"),
                                                  ("所有文件", "*.*")])
        if p:
            self.recent.set(p)
            self.say("已记住：%s" % p)

    def launch(self, bat):
        path = os.path.join(HERE, bat)
        if not os.path.exists(path):
            messagebox.showwarning("找不到启动脚本", "缺文件：\n%s\n\n（可以直接跑对应的 .py ✓）" % path)
            return
        try:
            # ★ 另起进程：不 import 旧工具（避免互相干扰）✓ 也不用 shell ✓
            os.startfile(path)                                    # noqa: S606 （Windows 打开文件）
            self.say("已启动：%s" % bat)
        except Exception as ex:                                    # noqa: BLE001
            # 退路：用 python 直接拉同名 .py（bat 万一被删/改坏也能用 ✓）
            alt = {"启动mod编辑器.bat": "mod tool.py",
                   "启动tlk编辑器.bat": "tlk tool.py",
                   "启动术语库.bat": "launch_editor.py",
                   "启动翻译流水线.bat": "launch_pipeline.py"}.get(bat)
            if alt and os.path.exists(os.path.join(HERE, alt)):
                env = dict(os.environ)
                env["PYTHONIOENCODING"] = "utf-8"
                subprocess.Popen([sys.executable, os.path.join(HERE, alt)], env=env, cwd=HERE)
                self.say("已启动（退路）：%s" % alt)
            else:
                messagebox.showerror("启动失败", "%r\n\n（可直接跑 %s ✓）" % (ex, alt or bat))

    def say(self, s):
        self.log.config(state="normal")
        self.log.insert("end", str(s) + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def on_close(self):
        try:
            ui_state.save_window(self, "workbench")
            st = ui_state.load()
            st.setdefault("workbench", {}).update({
                "recent": "" if self.recent.get() == "（还没有）" else self.recent.get(),
                "project": self.proj.get().strip()})
            ui_state.save(st)
        except Exception:                                          # noqa: BLE001
            pass
        self.destroy()


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        a = App()
        a.after(700, a.destroy)
        a.mainloop()
        print("自检通过：汉化工作台界面创建成功 ✓（%d 个工具入口）" % len(TOOLS))
        sys.exit(0)
    try:
        App().mainloop()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        try:
            messagebox.showerror("启动失败", tb[-1000:])
        except Exception:                                          # noqa: BLE001
            print(tb)
        raise
