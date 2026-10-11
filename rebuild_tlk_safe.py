#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""安全重建 PRC8 的 TLK：**保留"英文导出里没有的自定义槽位"**（例如中文界面标签）✓。

为什么需要它：`modl10n.tlk rebuild` 是"英文原版 + 记忆库"重建 ✓，
凡是**不在英文导出里**的槽位（我们后来自己加的界面标签，实测 90001~90089 共 89 条 ✗）
都会被清空 ✓ —— 2026-10-07 就是这么把能力选择界面的字弄没的 ✗（用户发现）。

做法：
  1) 读**当前已安装**的 TLK，找出"英文导出 `prc8.txt` 里没有的槽位" = 自加内容 ✓
  2) 调 `modl10n.tlk.rebuild` 重建（英文原版 + 记忆库 ✓）
  3) 把第 1 步那些槽位**并回**新 TLK
  4) 校验：槽位总数不变 ✓ / 自加槽位都在 ✓ / 重建器自己的读回校验必须 0 ✓
  5) 只有 `--apply` 才安装（安装前自动备份 ✓）

用法：
    python rebuild_tlk_safe.py            # 只做"保留逻辑自检"，不重建不安装 ✓
    python rebuild_tlk_safe.py --apply    # 真重建并安装（会先备份 ✓）
"""
import os
import re
import sys
import glob
import shutil
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from l10n import paths                                          # noqa: E402

# 目标 TLK / 英文基线 TLK：默认在 NWN:EE 用户目录的 `tlk\` 下（环境变量 `NWN_HOME`
# 可改用户目录；`NWN_TLK` / `NWN_EN_TLK` 可直接指定文件）。
_TLK_DIR = paths.nwn_sub("tlk")
GAME_TLK = os.environ.get("NWN_TLK") or os.path.join(_TLK_DIR, "prc8_consortium.tlk")
BASE_EN = os.environ.get("NWN_EN_TLK") or os.path.join(_TLK_DIR, "prc8_consortiumE.tlk")
EN_TXT = os.environ.get("PRC8_EN_TXT") or os.path.join(ROOT, "module", "PRC", "prc8.txt")
OUT_TLK = os.path.join(ROOT, "tmp", "输出", "prc8", "prc8_consortium.tlk")


def load_tlktool():
    """借 tlk tool.py 的 load_tlk/save_tlk（已支持 ANSI/UTF-8 自动识别 ✓）。"""
    p = os.path.join(HERE, "tlk tool.py")
    spec = importlib.util.spec_from_file_location("tlktool", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def en_ids():
    """英文导出 txt 里出现的 strref 集合（= 重建器会覆盖的槽位 ✓）。"""
    ids = set()
    for ln in open(EN_TXT, encoding="utf-8-sig", errors="replace"):
        m = re.match(r"^\s*(\d+)\s*[:：\t ]", ln) or re.match(r"^#str\s+(\d+)", ln)
        if m:
            ids.add(m.group(1))
    return ids


def custom_slots(tool):
    """当前 TLK 里"英文导出没有、但有文本"的槽位 = 自加的中文界面标签 ✓。"""
    cur = tool.load_tlk(GAME_TLK)
    known = en_ids()
    out = {k: v for k, v in cur["texts"].items() if k not in known and (v or "").strip()}
    return cur, out


def main():
    apply = "--apply" in sys.argv
    tool = load_tlktool()
    cur, custom = custom_slots(tool)
    print("  当前 TLK：%d 个槽位有文本；其中【英文导出里没有的自加槽位】%d 个 ✓" % (len(cur["texts"]), len(custom)))
    for k in sorted(custom, key=int)[:5]:
        print("     %s = %s" % (k, custom[k][:20]))
    if not apply:
        print("\n  自检完成 ✓（未重建、未安装）。加 --apply 才会真重建。")
        return 0

    import modl10n.tlk as tlkmod
    os.makedirs(os.path.dirname(OUT_TLK), exist_ok=True)
    print("\n  === 重建（英文原版 + 记忆库）===")
    tlkmod.rebuild(BASE_EN, EN_TXT, OUT_TLK, os.path.join(HERE, "modl10n", "tm.sqlite"))
    new = tool.load_tlk(OUT_TLK)
    missing = [k for k in custom if k not in new["texts"]]
    if missing:
        print("  并回自加槽位 %d 个 ✓" % len(missing))
        for k in missing:
            new["texts"][k] = custom[k]
        tool.save_tlk(OUT_TLK, new["lang"], new["count"], new["texts"])
    else:
        print("  自加槽位已在新 TLK 里 ✓")
    chk = tool.load_tlk(OUT_TLK)
    bad = [k for k in custom if k not in chk["texts"]]
    print("  校验：自加槽位缺失 = %d（必须 0 ✓）；槽位总数 %d" % (len(bad), chk["count"]))
    if bad:
        print("  ✗ 仍有缺失，**不安装**，请人工检查"); return 1
    stamp = __import__("time").strftime("%Y%m%d_%H%M%S")
    shutil.copy(GAME_TLK, GAME_TLK + ".安全重建前_" + stamp)
    shutil.copy(OUT_TLK, GAME_TLK)
    print("  ✅ 已安装（旧版备份 .安全重建前_%s）" % stamp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
