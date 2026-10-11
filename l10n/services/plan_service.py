#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PlanService —— 花钱前的"影响面预览"（流水线第 4 步）。**不调 API，不写库。**

产出三样东西，正好回答"这次要花多少钱、会动哪些东西"：

    1. 工作量：待译 N 条（去重后）/ M 字符
    2. 花费：按批次数与 token 粗估（单价可由 --price-in/--price-out 给）
    3. 影响面：会动到几个容器 / 几个资源

批次数口径沿用 `modl10n\\translator.py` 的实际参数
（`BATCH_CHARS = 3500`、`BATCH_ITEMS = 25`），所以估出来的批次数与真跑时一致。

**本工作台的铁律**：改一处动多文件的操作必须先出影响面预览、用户确认后才执行；
而且预览与执行必须共用同一段查询逻辑（这里 = `diff_service.diff`）。
"""
import math

from .diff_service import diff

# 与 modl10n\translator.py 保持一致，别各算各的
BATCH_CHARS = 3500
BATCH_ITEMS = 25
PROMPT_TOKENS_PER_BATCH = 250      # 每批提示词开销（粗估，含术语注入）


def estimate(chars, items):
    """(批次数, 预估输入 tokens, 预估输出 tokens)。"""
    batches = max(math.ceil(items / float(BATCH_ITEMS)),
                  math.ceil(chars / float(BATCH_CHARS))) if items else 0
    tokens_in = chars / 4.0 + batches * PROMPT_TOKENS_PER_BATCH     # 英文 ≈ 4 字符/token
    tokens_out = chars / 2.0                                        # 中文输出 ≈ 2 字符/token
    return batches, tokens_in, tokens_out


def plan(db, project=None, container_id=None, price_in=0.0, price_out=0.0, sample=10):
    """算出"要不要花钱、花多少、动哪些"。price_in/price_out 单位：元/百万 token。"""
    d = diff(db, project=project, container_id=container_id, sample=sample)
    items, chars = d["uniq_todo"], d["chars_todo"]
    batches, t_in, t_out = estimate(chars, items)
    cost = (t_in / 1e6) * float(price_in or 0) + (t_out / 1e6) * float(price_out or 0)
    return {
        "items": items, "chars": chars, "batches": batches,
        "tokens_in": int(t_in), "tokens_out": int(t_out),
        "cost": cost, "price_in": price_in, "price_out": price_out,
        # 影响面：只算"真的有待译内容的"（没有待译就是 0，不虚报）
        "containers": d["containers_todo"],
        "resources": len(d["by_res"]),
        "diff": d,
    }


def summarize(p, indent="  "):
    """把 plan 打印成人话（含"不需要花钱"的明确提示）。"""
    lines = ["%s待译 %d 条（去重后）/ %d 字符；预计 %d 批 API 调用"
             % (indent, p["items"], p["chars"], p["batches"])]
    lines.append("%s预估 token：输入 ~%d、输出 ~%d（粗估）"
                 % (indent, p["tokens_in"], p["tokens_out"]))
    if p["price_in"] or p["price_out"]:
        lines.append("%s预估花费：￥%.4f（单价 输入 %s、输出 %s 元/百万 token）"
                     % (indent, p["cost"], p["price_in"], p["price_out"]))
    else:
        lines.append("%s花费：未给单价（加 --price-in / --price-out 可换算成金额）"
                     % indent)
    lines.append("%s影响面：%d 个资源、%d 个容器"
                 % (indent, p["resources"], p["containers"]))
    if p["items"] == 0:
        lines.append("%s✅ 没有待译内容 —— 这次跑起来 0 花费。" % indent)
    return "\n".join(lines)
