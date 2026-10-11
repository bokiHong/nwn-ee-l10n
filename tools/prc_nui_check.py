#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""体检：模组的 PRC8 NUI 点击会不会失效。

【判据】NWN:EE 的 NUI 点击只会送到模块级事件脚本 `OnNuiEvent`
（`module.ifo` 里的字段 `Mod_OnNuiEvent`）：
  - 该字段**存在**且值为 `prc_onplayernui` → 点击正常 ✓
  - 该字段**存在**但为空/别的值     → 空值会被全局补丁接管 ✓；别的值不被覆盖（要看那个脚本会不会转发给 PRC8）
  - 该字段**根本不存在**            → 老 schema（1.87 之前保存的模组）。
    注意 PRC8ModuleUpdater / PRC 5.0 的更新器**都无法新增字段**（`.hif` 里没有这一项、
    二进制里也没有相关代码），所以更新器跑多少遍都补不上 ✗
    → 只能靠运行时脚本接管；本机的全局补丁 = `development\prc_onenter.ncs`
      （由 `prc8_nui_fix.py` 生成，只在字段为空时才设置）

用法：
    python prc_nui_check.py                       # 扫本机默认位置
    python prc_nui_check.py "D:\some\mods" x.mod  # 扫指定目录/文件
"""
import os
import sys
import glob
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from modl10n.core.erf import Erf              # noqa: E402
from modl10n.core.gff import Gff              # noqa: E402

HOME = os.path.join(os.path.expanduser("~"), "Documents", "Neverwinter Nights")
GAME_CANDIDATES = [
    r"D:\Program Files (x86)\Steam\steamapps\common\Neverwinter Nights",
    r"C:\Program Files (x86)\Steam\steamapps\common\Neverwinter Nights",
]


def _txt(v):
    if isinstance(v, bytes):
        return v.decode("latin-1", "replace")
    return v


def inspect(path):
    """返回 (模组名, 是否挂 PRC8, Mod_OnNuiEvent 状态, 结论)。"""
    try:
        e = Erf.load(path)
    except Exception as ex:
        return (os.path.basename(path), "?", "读取失败", "读取失败：%r" % ex)
    for ent in e.entries:
        if ent.ext == "ifo":
            g = Gff(e.read_data(ent)); g._ensure()
            haks, nui_present, nui_val, has_events = [], False, None, 0
            for f in g.fields:
                lab = str(f.label)
                if lab == "Mod_Hak":
                    haks.append(_txt(f.value))
                elif lab.startswith("Mod_On"):
                    has_events += 1
                    if lab == "Mod_OnNuiEvent":
                        nui_present = True
                        nui_val = _txt(f.value)
            prc = [h for h in haks if h.lower().startswith(("prc8", "prc_"))]
            if not prc:
                return (os.path.basename(path), "无", "不适用",
                        "没挂 PRC 的 hak → 用不到 PRC8 的 NUI 窗口，不适用")
            state = ("存在，值=%r" % nui_val) if nui_present else "字段不存在（老 schema）"
            if nui_present and nui_val == "prc_onplayernui":
                verdict = "正常 ✓"
            elif nui_present and not (nui_val or "").strip():
                verdict = "字段为空 → 全局补丁会接管 ✓（前提：development 里有补丁）"
            elif nui_present:
                verdict = "值=%r，全局补丁**不覆盖** → 需确认该脚本是否转发给 PRC8 ⚠" % nui_val
            else:
                verdict = "字段不存在 → 只能靠运行时补丁接管（本机已装 ✓）"
            return (os.path.basename(path), "是(%d 个)" % len(prc), state, verdict)
    return (os.path.basename(path), "?", "无 .ifo", "不是标准模组/读取不到 module.ifo")


def collect(targets):
    out = []
    for t in targets:
        if os.path.isdir(t):
            for pat in ("*.mod", "*.nwm"):
                out += glob.glob(os.path.join(t, pat))
        elif os.path.exists(t):
            out.append(t)
    return sorted(set(out))


def main(argv=None):
    ap = argparse.ArgumentParser(description="体检模组的 PRC8 NUI 点击")
    ap.add_argument("paths", nargs="*", help="目录或 .mod/.nwm 文件（默认扫本机常用位置）")
    a = ap.parse_args(argv)
    targets = a.paths
    if not targets:
        targets = [os.path.join(HOME, "modules"), os.path.join(HOME, "premium")]
        for g in GAME_CANDIDATES:
            if os.path.isdir(g):
                targets += [os.path.join(g, "data", "nwm"), os.path.join(g, "data", "mod")]
                break
    files = collect(targets)
    dev_fix = os.path.join(HOME, "development", "prc_onenter.ncs")
    print("=== 环境 ===")
    print("  全局 NUI 补丁 development\\prc_onenter.ncs：%s"
          % ("已安装 ✓" if os.path.exists(dev_fix)
             else "未安装 ✗（PRC8 窗口在下列老模组里会点不动）"))
    print("  扫描 %d 个模组文件" % len(files))
    print()
    print("  %-36s %-10s %-26s %s" % ("模组", "挂PRC?", "Mod_OnNuiEvent", "结论"))
    for p in files:
        name, prc, state, verdict = inspect(p)
        print("  %-36s %-10s %-26s %s" % (name[:36], prc, state[:26], verdict))
    return 0


if __name__ == "__main__":
    sys.exit(main())
