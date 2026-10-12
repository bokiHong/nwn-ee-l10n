#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UiState —— 记住 GUI 的"上次状态"（窗口尺寸 / 最近打开的文件 / 单价…）。

为什么：三个工具每次打开都要重新拖窗口、重新选文件 ✗（用户 2026-10-11 提过）。

存放位置：`scripts\\ui_state.json`（**跟工具放一起**，不进 tm 库 ✓ 也不进记忆库 ✗）。
用法：
    from l10n import ui_state
    st = ui_state.load()
    ui_state.restore_window(self, st, key="pipeline")   # 建好界面后调一次
    ui_state.save_window(self, key="pipeline")          # 关闭时调一次
    ui_state.get("pipeline", "mod", default="")         # 取单项
    ui_state.put("pipeline", "mod", path)               # 存单项
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))          # scripts\\l10n
SCRIPTS = os.path.dirname(HERE)
PATH = os.path.join(SCRIPTS, "ui_state.json")


def load():
    """读整份状态（坏文件当空 ✓，绝不因为一个 JSON 打不开就让工具起不来 ✗）。"""
    try:
        with open(PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:                                          # noqa: BLE001
        return {}


def save(st):
    """整份写回（原子写：先写 .tmp 再 replace ✓，避免写一半把状态写坏）。"""
    try:
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, PATH)
        return True
    except Exception:                                          # noqa: BLE001
        return False


def get(section, key, default=None):
    return (load().get(section) or {}).get(key, default)


def put(section, key, value):
    st = load()
    st.setdefault(section, {})[key] = value
    return save(st)


def restore_window(win, key, default="1000x680"):
    """把窗口尺寸/位置还原（存的是 `WxH+X+Y` ✓）。取不到就用默认 ✓"""
    st = load()
    geom = (st.get("window") or {}).get(key) or default
    try:
        win.geometry(geom)
    except Exception:                                          # noqa: BLE001
        pass
    return geom


def save_window(win, key):
    """把窗口尺寸/位置存下来（关闭时调 ✓）。"""
    try:
        geom = win.geometry()
    except Exception:                                          # noqa: BLE001
        return None
    st = load()
    st.setdefault("window", {})[key] = geom
    save(st)
    return geom
