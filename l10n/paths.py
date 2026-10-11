#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NWN:EE 目录探测 —— 让工具在**任何人的机器上**都能找到游戏数据。

优先级：环境变量 > 惯例路径。

    NWN_HOME   NWN:EE 的「用户目录」（放 settings.tml / tlk / hak / development 的地方）
    NWN_GAME   NWN:EE 的「安装目录」（放 nwmain.exe / version.dll 的地方）

都没设时按惯例猜（Windows）：`%USERPROFILE%\\Documents\\Neverwinter Nights`；
若 Documents 被 OneDrive 重定向，会一并尝试。

设计原则：**找不到就返回惯例路径、由调用方检查存在性并优雅跳过** ——
开源版里别人的机器上不会有你的 `prc8_consortium.tlk`，测试不能因此崩掉。
"""
import os


def _first_dir(*cands):
    for c in cands:
        if c and os.path.isdir(c):
            return os.path.abspath(c)
    return None


def nwn_home():
    """NWN:EE 用户目录。可能不存在（调用方自行 os.path.exists 判断）。"""
    env = os.environ.get("NWN_HOME")
    if env:
        return os.path.abspath(env)
    home = os.path.expanduser("~")
    return (_first_dir(
        os.path.join(home, "Documents", "Neverwinter Nights"),
        os.path.join(home, "OneDrive", "Documents", "Neverwinter Nights"),
        os.path.join(home, "Documents", "Neverwinter Nights Enhanced Edition"),
    ) or os.path.join(home, "Documents", "Neverwinter Nights"))


def nwn_sub(*parts):
    """用户目录下的路径（不检查存在性）。"""
    return os.path.join(nwn_home(), *parts)


def nwn_find(*rel):
    """用户目录下按相对路径找文件；不存在返回 None。"""
    p = nwn_sub(*rel)
    return p if os.path.exists(p) else None


def game_dir():
    """NWN:EE 安装目录；没设 NWN_GAME 时返回 None。"""
    env = os.environ.get("NWN_GAME")
    return os.path.abspath(env) if env else None


def describe():
    """一行说明当前解析结果（供 --help / 出错提示打印）。"""
    return ("NWN_HOME=%s（%s）  NWN_GAME=%s"
            % (nwn_home(), "存在" if os.path.isdir(nwn_home()) else "不存在",
               game_dir() or "未设置"))
