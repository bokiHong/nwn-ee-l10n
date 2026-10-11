#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""修复 PRC8 的 NUI 点击失效（模块没设置 OnNuiEvent 事件脚本）。

【原理】NWN:EE 里 NUI 控件的点击**只会**送到模块的 `OnNuiEvent` 事件脚本
（见 https://nwnlexicon.com/OnNuiEvent）。PRC8 提供了总路由器 `prc_onplayernui`
（按窗口 ID 分派到 prc_nui_*_event），但**需要模块自己把它挂上**：

    SetEventScript(GetModule(), EVENT_SCRIPT_MODULE_ON_NUI_EVENT, "prc_onplayernui");

这个事件是 1.87 补丁才加进模块属性的（之前只能用脚本设）。2005 年代的老模组
（例如剑飞，`Mod_MinGameVer` 只有 1.68）的 `module.ifo` 里**根本没有这个事件字段**
（实测：136 个字段里含 "nui" 的 0 个）—— 结果就是：PRC8 的窗口能正常显示、
但所有控件点击都没有反应；只有引擎自绘的 ✕ / 收起仍然可用。

【本工具做的事】把上面那一行注入 PRC8 自己的 `prc_onenter`（任何 PRC8 模组载入时
都会执行它，日志里的 "Running modified prc_onenter with PW CD check" 就是证据），
用 nwnsc 重编译后放进 `development\` —— 该目录优先级高于 hak（我改的 prc_nui_* 汉化
能生效就是证明），于是**一次修好所有模组**，不用逐个改 .mod。

用法：
    python prc8_nui_fix.py                 # 打补丁（幂等；会自动备份同名旧文件）
    python prc8_nui_fix.py --uninstall     # 卸载：删掉 development 里的那个文件
    python prc8_nui_fix.py --dry-run       # 只编译+校验，不安装
"""
import os
import re
import sys
import shutil
import subprocess
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from modl10n.core.erf import Erf              # noqa: E402
from modl10n import ncs as NCS                # noqa: E402

HOME = os.path.join(os.path.expanduser("~"), "Documents", "Neverwinter Nights")
HAK = os.path.join(HOME, "hak", "prc8_scripts.hak")
SRC_DIR = os.path.join(HERE, "PRC\补丁\_src")           # 已抽好的 PRC8 源码树（含 include）
DEV = os.path.join(HOME, "development")
WORK = os.path.join(HERE, "PRC\补丁\prc8_patch")

MARK = "EVENT_SCRIPT_MODULE_ON_NUI_EVENT"      # 幂等标记
INJECT_LINES = """
    // === PRC8 NUI 点击修复（由 prc8_nui_fix.py 注入）===
    // NWN:EE 的 NUI 控件点击只会送到模块的 OnNuiEvent 事件脚本；老模组（1.87 之前
    // 保存的 module.ifo）没有这个事件字段，于是 PRC8 的窗口能显示却收不到点击。
    // 这里在模组载入时把它指向 PRC8 自己的路由器 prc_onplayernui —— 仅当模块
    // **自己没设过**时才接管，绝不覆盖别的模组自己的 NUI 处理。
    // 想撤销：删掉 development 下的 prc_onenter.ncs 即可恢复 PRC8 原版行为。
    if (GetEventScript(GetModule(), EVENT_SCRIPT_MODULE_ON_NUI_EVENT) == "")
    {
        SetEventScript(GetModule(), EVENT_SCRIPT_MODULE_ON_NUI_EVENT, "prc_onplayernui");
    }
"""


def read_onenter_source():
    e = Erf.load(HAK)
    for x in e.entries:
        if x.resref.lower() == "prc_onenter" and x.ext == "nss":
            return e.read_data(x).decode("utf-8", "replace")
    raise SystemExit("在 %s 里找不到 prc_onenter.nss" % HAK)


def make_patched_source(src):
    """在 main() 左括号后插入设置语句。**必须保留括号之后的所有源码**（这里曾出过截断 bug）。"""
    if MARK in src:
        return None, "已经打过补丁（源码里已有 %s）" % MARK
    m = re.search(r"void\s+main\s*\([^)]*\)\s*\{", src)
    if not m:
        raise SystemExit("找不到 void main() 入口，无法安全注入")
    out = src[:m.end()] + INJECT_LINES + src[m.end():]
    # 断言：只应"变长"，绝不能变短（防截断）
    if len(out) <= len(src):
        raise SystemExit("内部错误：注入后长度没有增加（%d → %d），已中止" % (len(src), len(out)))
    if len(out) < len(src) + 200:
        raise SystemExit("内部错误：注入内容异常短，已中止")
    return out, "已注入"


def compile_nss(nss_path, install=None):
    nwnsc = NCS.default_nwnsc(HOME)
    if not os.path.exists(nwnsc):
        raise SystemExit("找不到 nwnsc.exe：%s" % nwnsc)
    install = install or NCS.detect_install_dir(HOME)
    wd = os.path.dirname(nss_path)
    cmd = [nwnsc, "-q", "-c", "-i", SRC_DIR]
    if install:
        cmd += ["-n", install]
    cmd += ["-h", HOME, nss_path]
    log = os.path.join(wd, "nwnsc_fix.log")
    with open(log, "wb") as lf:
        rc = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, timeout=1800).returncode
    out = os.path.splitext(nss_path)[0] + ".ncs"
    ok = rc == 0 and os.path.exists(out)
    return ok, out, log, rc


def consts_of(ncs_path, install=None):
    """反汇编取字符串常量集合（用来验收补丁没弄坏别的东西）。"""
    nwnsc = NCS.default_nwnsc(HOME)
    install = install or NCS.detect_install_dir(HOME)
    wd = os.path.join(WORK, "dis")
    os.makedirs(wd, exist_ok=True)
    cmd = [nwnsc, "-d", "-i", SRC_DIR, "-n", install, "-h", HOME, "-b", wd,
           os.path.abspath(ncs_path)]
    with open(os.path.join(wd, "log.txt"), "wb") as lf:
        subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, timeout=1800)
    pc = os.path.splitext(os.path.basename(ncs_path))[0] + ".pcode"
    p = os.path.join(wd, pc)
    if not os.path.exists(p):
        return None
    txt = open(p, "r", encoding="utf-8", errors="replace").read()
    return set(re.findall(r'"([^"]*)"', txt))


def main(argv=None):
    ap = argparse.ArgumentParser(description="修复 PRC8 NUI 点击（注入 OnNuiEvent 设置）")
    ap.add_argument("--uninstall", action="store_true", help="删除 development 里的补丁文件")
    ap.add_argument("--dry-run", action="store_true", help="只编译校验，不安装")
    ap.add_argument("--install-dir", default=None)
    a = ap.parse_args(argv)

    target = os.path.join(DEV, "prc_onenter.ncs")
    if a.uninstall:
        if os.path.exists(target):
            os.remove(target)
            print("  已卸载：删除了 %s（PRC8 回到原始行为）" % target)
        else:
            print("  未发现 %s，无需卸载" % target)
        return 0

    os.makedirs(WORK, exist_ok=True)
    os.makedirs(DEV, exist_ok=True)

    print("=== 1) 从 hak 读 prc_onenter.nss ===")
    src = read_onenter_source()
    print("  源码 %d 字节" % len(src))

    print("=== 2) 注入 OnNuiEvent 设置 ===")
    patched, why = make_patched_source(src)
    if patched is None:
        print("  %s" % why)
        if os.path.exists(target):
            print("  development 里已有补丁文件：%s" % target)
            return 0
        print("  但 development 里没有成品，继续编译安装")
        patched = src

    nss = os.path.join(WORK, "prc_onenter.nss")
    with open(nss, "w", encoding="utf-8", newline="\n") as f:
        f.write(patched)
    print("  %s → %s（%d 字节）" % (why, nss, len(patched)))

    print("=== 3) 用 nwnsc 编译 ===")
    ok, out, log, rc = compile_nss(nss, a.install_dir)
    print("  退出码 %d，产物 %s（%s）" % (rc, out, "存在" if os.path.exists(out) else "缺失"))
    if not ok:
        tail = open(log, "rb").read().decode("utf-8", "replace").strip().splitlines()[-12:]
        print("  编译失败，日志尾部：")
        for ln in tail:
            print("    %s" % ln[:120])
        return 1
    print("  编译后 %d 字节" % os.path.getsize(out))

    print("=== 4) 验收：反汇编对比字符串常量 ===")
    orig_ncs = os.path.join(WORK, "prc_onenter_orig.ncs")
    e = Erf.load(HAK)
    for x in e.entries:
        if x.resref.lower() == "prc_onenter" and x.ext == "ncs":
            open(orig_ncs, "wb").write(e.read_data(x))
            break
    c_old = consts_of(orig_ncs, a.install_dir)
    c_new = consts_of(out, a.install_dir)
    if c_old is None or c_new is None:
        print("  反汇编失败，跳过对比（不影响安装）")
    else:
        lost = c_old - c_new
        gained = c_new - c_old
        print("  原版常量 %d 个，新版 %d 个；新增 %s；丢失 %s"
              % (len(c_old), len(c_new), sorted(gained)[:3] or "无", sorted(lost)[:3] or "无"))
        if lost:
            print("  ⚠ 有常量丢失，拒绝安装（fail-closed）")
            return 2
        if "prc_onplayernui" not in c_new:
            print("  ⚠ 新常量里没有 prc_onplayernui，拒绝安装")
            return 2
        print("  ✓ 原有常量一个没丢，且新增了 prc_onplayernui")

    if a.dry_run:
        print("  --dry-run，不安装。产物在 %s" % out)
        return 0

    print("=== 5) 安装到 development\\（优先级高于 hak）===")
    if os.path.exists(target):
        shutil.copy2(target, target + ".bak")
        print("  已备份旧文件 → %s.bak" % target)
    shutil.copy2(out, target)
    print("  ✓ 已安装 %s（%d 字节）" % (target, os.path.getsize(target)))
    print("  重启游戏后，PRC8 的升级/法术书/契灵/剑契等 NUI 窗口即可正常点击。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
