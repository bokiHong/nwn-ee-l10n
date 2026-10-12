#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RebuildService —— 一键**重建并安装** PRC8 的 TLK（0 次 API）。

把独立脚本 `scripts\\rebuild_tlk_safe.py` 的核心逻辑收进 service，让 GUI/CLI 都能调 ✓

**为什么必须"并回自加槽位"**：`modl10n.tlk.rebuild` 是"英文原版 + 记忆库"重建，
凡是**不在英文导出里**的槽位都会被清空 —— 我们后来自己加的中文界面标签
（90001~90089 共 89 条）正是这一类 ✗。2026-10-07 就是这么把"能力选择"界面的字
弄没的（用户发现）✗，所以这一步绝不能省。

流程：
  ① 读**当前已安装**的 TLK，找出"英文导出 `prc8.txt` 里没有、但有文本"的槽位 = 自加内容 ✓
  ② 调 `modl10n.tlk.rebuild`（英文原版 + `tm.sqlite`）重建到临时产物
  ③ 把第 ① 步那些槽位**并回**新 TLK
  ④ 校验：自加槽位缺失必须为 0、槽位总数不变
  ⑤ 安装（安装前自动备份 `.安全重建前_<时间戳>`）
"""
import importlib.util
import os
import re
import shutil
import time

from .. import paths

HERE = os.path.dirname(os.path.abspath(__file__))            # scripts\l10n\services
SCRIPTS = os.path.dirname(os.path.dirname(HERE))             # scripts

# 默认路径（与 `rebuild_tlk_safe.py` 保持一致）
_TLK_DIR = paths.nwn_sub("tlk")
DEF_GAME_TLK = os.environ.get("NWN_TLK") or os.path.join(_TLK_DIR, "prc8_consortium.tlk")
DEF_BASE_EN = os.environ.get("NWN_EN_TLK") or os.path.join(_TLK_DIR, "prc8_consortiumE.tlk")
DEF_EN_TXT = os.environ.get("PRC8_EN_TXT") or os.path.join(SCRIPTS, "module", "PRC", "prc8.txt")
DEF_TM = os.path.join(SCRIPTS, "modl10n", "tm.sqlite")
DEF_OUT = os.path.join(SCRIPTS, "tmp", "输出", "prc8", "prc8_consortium.tlk")


def default_paths():
    """返回一套默认路径（GUI 用）。"""
    return {"game_tlk": DEF_GAME_TLK, "base_en": DEF_BASE_EN, "en_txt": DEF_EN_TXT,
            "tm": DEF_TM, "out": DEF_OUT}


def load_tlktool():
    """借 `tlk tool.py` 的 load_tlk/save_tlk（已支持 ANSI/UTF-8 自动识别 ✓）。"""
    p = os.path.join(SCRIPTS, "tlk tool.py")
    spec = importlib.util.spec_from_file_location("tlktool", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def en_ids(en_txt):
    """英文导出 txt 里出现的 strref 集合（= 重建器会覆盖的槽位 ✓）。"""
    ids = set()
    with open(en_txt, encoding="utf-8-sig", errors="replace") as f:
        for ln in f:
            m = re.match(r"^\s*(\d+)\s*[:：\t ]", ln) or re.match(r"^#str\s+(\d+)", ln)
            if m:
                ids.add(m.group(1))
    return ids


def survey(paths=None):
    """**只读预演**：告诉调用方"将重建什么、要保住多少个自加槽位"。

    返回 `{"ok", "texts", "custom", "samples", "error", "missing"}`。
    """
    p = dict(default_paths())
    p.update(paths or {})
    out = {"ok": False, "texts": 0, "custom": 0, "samples": [], "missing": [], "error": None}
    for k in ("game_tlk", "base_en", "en_txt", "tm"):
        if not os.path.exists(p[k]):
            out["missing"].append(p[k])
    if out["missing"]:
        out["error"] = "缺文件（见 missing）"
        return out
    try:
        tool = load_tlktool()
        cur = tool.load_tlk(p["game_tlk"])
        known = en_ids(p["en_txt"])
        custom = {k: v for k, v in cur["texts"].items() if k not in known and (v or "").strip()}
        out.update(ok=True, texts=len(cur["texts"]), custom=len(custom),
                   samples=[(k, custom[k][:18]) for k in sorted(custom, key=int)[:5]])
    except Exception as ex:                                        # noqa: BLE001
        out["error"] = repr(ex)
    return out


def rebuild_and_install(paths=None, progress=None):
    """重建 + 并回自加槽位 + 校验 + 安装。**耗时约 30~60 秒**，GUI 里请放子线程 ✓

    `progress(msg)` 可选，用于回报进度。
    返回 `{"ok", "rebuilt", "custom", "total", "backup", "error"}`。
    """
    def say(m):
        if progress:
            progress(m)

    p = dict(default_paths())
    p.update(paths or {})
    res = {"ok": False, "rebuilt": 0, "custom": 0, "total": 0, "backup": None, "error": None}
    for k in ("game_tlk", "base_en", "en_txt", "tm"):
        if not os.path.exists(p[k]):
            res["error"] = "缺文件：%s" % p[k]
            return res
    try:
        tool = load_tlktool()
        cur = tool.load_tlk(p["game_tlk"])
        known = en_ids(p["en_txt"])
        custom = {k: v for k, v in cur["texts"].items() if k not in known and (v or "").strip()}
        say("当前 TLK：%d 个槽位有文本；自加槽位 %d 个 ✓" % (len(cur["texts"]), len(custom)))

        import modl10n.tlk as tlkmod
        os.makedirs(os.path.dirname(p["out"]), exist_ok=True)
        say("① 重建（英文原版 + 记忆库）…")
        tlkmod.rebuild(p["base_en"], p["en_txt"], p["out"], p["tm"])

        new = tool.load_tlk(p["out"])
        missing = [k for k in custom if k not in new["texts"]]
        if missing:
            say("② 并回自加槽位 %d 个 ✓" % len(missing))
            for k in missing:
                new["texts"][k] = custom[k]
            tool.save_tlk(p["out"], new["lang"], new["count"], new["texts"])
        else:
            say("② 自加槽位已在新 TLK 里 ✓")

        chk = tool.load_tlk(p["out"])
        bad = [k for k in custom if k not in chk["texts"]]
        say("③ 校验：自加槽位缺失 %d（必须 0）；槽位总数 %d" % (len(bad), chk["count"]))
        if bad:
            res["error"] = "自加槽位仍有缺失，**未安装**，请人工检查"
            return res

        stamp = time.strftime("%Y%m%d_%H%M%S")
        bak = p["game_tlk"] + ".安全重建前_" + stamp
        shutil.copy(p["game_tlk"], bak)
        shutil.copy(p["out"], p["game_tlk"])
        res.update(ok=True, rebuilt=len(chk["texts"]), custom=len(custom),
                   total=chk["count"], backup=bak)
        say("✅ 已安装（旧版备份 %s）" % os.path.basename(bak))
    except Exception as ex:                                        # noqa: BLE001
        res["error"] = repr(ex)
    return res
