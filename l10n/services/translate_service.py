#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TranslateService —— 把**翻译引擎**接到 CLI / 流水线（0 行重写 ✓）。

⚠ 引擎本体住在 `tlk tool.py`（`translate_with_memory` / `translate_texts`），
   而且它**自带**一个能跑的入口：

       python "tlk tool.py" translate <txt文件> --key=sk-xxx --all --tm=<记忆库>

   这里**不重写引擎**，用**独立子进程**调它 —— 好处：
     ① 不干扰当前进程的库连接（WAL 写锁）✓
     ② 与用户已经跑通的流程**逐字一致**（验收标准就是"产物相同"✓）
     ③ 引擎崩了只崩子进程，主程序还能报错 ✓

**安全红线**（用户很在意花费 ✓）：
   · `estimate()` 只读 → 复用 `plan_service`，**绝不调 API** ✓
   · `run()` **必须 `confirm=True` 且给了 key** 才动手，默认拒绝 ✗
   · 跑完把花费落进 `cost_service` 账本 ✓
   · 子进程必须**钉 `PYTHONIOENCODING=utf-8`**（否则中文输出在 GBK 控制台会乱码 ✗）
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))            # scripts\l10n\services
SCRIPTS = os.path.dirname(os.path.dirname(HERE))             # scripts
TOOL = os.path.join(SCRIPTS, "tlk tool.py")
DEF_TM = os.path.join(SCRIPTS, "modl10n", "tm.sqlite")


def estimate(db, project=None, container_id=None, price_in=0.0, price_out=0.0, sample=6):
    """花钱前估价（**只读、不调 API** ✓）。复用 `plan_service.plan()`。"""
    from . import plan_service
    p = plan_service.plan(db, project=project, container_id=container_id,
                          price_in=price_in, price_out=price_out, sample=sample)
    return {"items": p.get("items"), "cost": p.get("cost"),
            "summary": plan_service.summarize(p), "diff": p.get("diff")}


def run(txt_path, key=None, tm_path=None, do_all=True, model=None, base=None,
        timeout=3600, confirm=False, progress=None):
    """**真跑翻译**（会花钱 ✗）—— 调 `tlk tool.py translate` 子进程。

    返回 `{"ok", "returncode", "output", "error"}`；`confirm=False` 时**直接拒绝** ✗。
    """
    if not confirm:
        return {"ok": False, "returncode": None, "output": "",
                "error": "拒绝执行：翻译会调用 API 并产生费用。请显式传 confirm=True（CLI 用 --yes）"}
    if not key:
        return {"ok": False, "returncode": None, "output": "",
                "error": "缺少 API key（CLI 用 --key，或设环境变量）"}
    if not os.path.exists(txt_path):
        return {"ok": False, "returncode": None, "output": "",
                "error": "找不到 txt：%s" % txt_path}
    if not os.path.exists(TOOL):
        return {"ok": False, "returncode": None, "output": "",
                "error": "找不到引擎 %s" % TOOL}

    cmd = [sys.executable, TOOL, "translate", txt_path, "--key=%s" % key,
           "--tm=%s" % (tm_path or DEF_TM)]
    if do_all:
        cmd.append("--all")
    if model:
        cmd.append("--model=%s" % model)
    if base:
        cmd.append("--base=%s" % base)

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"          # ★ 否则中文输出在 GBK 控制台乱码 ✗
    if progress:
        progress("调引擎：%s …（可能要几分钟，会花钱）" % os.path.basename(txt_path))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=timeout, cwd=SCRIPTS)
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "output": "",
                "error": "超时（%ds）—— 翻译可能还在后台跑，请查看记忆库变化" % timeout}
    out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
    return {"ok": r.returncode == 0, "returncode": r.returncode, "output": out,
            "error": None if r.returncode == 0 else "引擎返回 %d" % r.returncode}


def record_cost(db, project, items, note=None, price_in=0.0, price_out=0.0,
                tokens_in=0, tokens_out=0):
    """把一次翻译落进花费账本（`cost_service` ✓）。"""
    from . import cost_service
    return cost_service.record(db, project or "default", "translate", items=items,
                               tokens_in=tokens_in, tokens_out=tokens_out,
                               price_in=price_in, price_out=price_out, note=note)
