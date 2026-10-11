#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`.nss` 改完之后的 `.ncs` 重编译与注入（含硬校验）。

为什么必须做：.mod 里 .nss 是源码，游戏运行时只读编译后的 .ncs。
为什么必须校验：nwnsc 是另一个编译器，重编译会改变字节码；万一它把某个字符串优化掉、
或源码里有被注释掉的代码，直接替换 .ncs 就可能悄悄丢内容。做法是反汇编新旧 .ncs，
比较字符串常量多重集必须等于"原常量按译文表映射"的结果，不通过就保留原始 .ncs。

依赖：nwnsc.exe（NWN:EE 社区的 NWScript 编译器）。
"""
import os
import re
import sys
import shutil
import struct
import subprocess
import collections

DIS_TRUNC = 128      # nwnsc -d 把字符串常量截断到 128 字节
PREFIX_BYTES = 100   # 只比较前 100 字节，避开截断边界


def _norm(s):
    return s.encode("utf-8", "replace")[:PREFIX_BYTES].decode("utf-8", "ignore")


def detect_install_dir(home=None):
    """从 nwn.ini 的 NWMFiles 反推 NWN:EE 安装目录。"""
    home = home or os.path.join(os.path.expanduser("~"), "Documents", "Neverwinter Nights")
    ini = os.path.join(home, "nwn.ini")
    try:
        with open(ini, "r", encoding="latin-1") as f:
            for line in f:
                if line.strip().lower().startswith("nwmfiles="):
                    p = line.split("=", 1)[1].strip()
                    low = p.lower()
                    for sep in ("\\data\\nwm", "/data/nwm"):
                        i = low.find(sep)
                        if i > 0:
                            return p[:i]
                    return os.path.dirname(os.path.dirname(p))
    except OSError:
        pass
    return None


def default_nwnsc(home=None):
    home = home or os.path.join(os.path.expanduser("~"), "Documents", "Neverwinter Nights")
    p = os.path.join(home, "hak", "nwnsc.exe")
    return p if os.path.exists(p) else "nwnsc"


def include_closure(nss_dir, changed):
    """changed ∪ 所有（直接/间接）include 了 changed 的脚本。

    NWScript 的 #include 是编译期文本内联，库文件里的译文必须让引用它的脚本一起重编译才生效。
    """
    INC = re.compile(r'^\s*#include\s+"([^"]+)"', re.M)
    incs = {}
    for fn in os.listdir(nss_dir):
        if not fn.lower().endswith(".nss"):
            continue
        try:
            with open(os.path.join(nss_dir, fn), "r", encoding="utf-8", errors="replace") as f:
                s = f.read()
        except OSError:
            continue
        incs[fn[:-4].lower()] = set(m.group(1).lower() for m in INC.finditer(s))
    rev = collections.defaultdict(set)
    for r, deps in incs.items():
        for d in deps:
            rev[d].add(r)
    need, added = set(changed), []
    queue = [r for r in changed if r.lower() in incs]
    seen = set(r.lower() for r in queue)
    while queue:
        cur = queue.pop()
        for p in rev.get(cur.lower(), ()):
            if p not in seen:
                seen.add(p)
                queue.append(p)
                need.add(p)
                added.append(p)
    return need, added


def disasm_consts(nwnsc, install, home, workdir, items, extra=None, chunk=40):
    """items: [(name, ncs_bytes)] -> {name: [字符串常量...]}；**失败的名字映射为 None**。"""
    workdir = os.path.abspath(workdir)
    if os.path.isdir(workdir):
        shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)
    srcs = []
    for name, data in items:
        p = os.path.join(workdir, name + ".ncs")
        with open(p, "wb") as f:
            f.write(data)
        srcs.append(p)
    if not srcs:
        return {}
    log = os.path.join(workdir, "_disasm.log")
    # 分批调用：nwnsc 一次要把所有文件路径塞进命令行，Windows 命令行约 32K 上限，
    # 文件一多就抛 WinError 206「文件名或扩展名太长」并把整条流水线搞崩（Ch2 实测踩过）。
    for i in range(0, len(srcs), chunk):
        part = srcs[i:i + chunk]
        cmd = [nwnsc, "-d"] + list(extra or [])
        if install:
            cmd += ["-n", install]
        if home:
            cmd += ["-h", home]
        cmd += ["-b", workdir] + part
        try:
            with open(log, "ab") as lf:
                subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, timeout=1800)
        except (OSError, subprocess.TimeoutExpired) as e:
            print("  [警告] 反汇编第 %d 批失败（该批 %d 个文件按「未通过校验」处理）：%r"
                  % (i // chunk + 1, len(part), e), flush=True)
    out = {}
    for name, _d in items:
        p = os.path.join(workdir, name + ".pcode")
        if not os.path.exists(p):
            out[name] = None          # 没产出 .pcode = 反汇编失败，交给 inject 判为未通过
            continue
        vals = []
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "CONSTS" not in line:
                    continue
                i2, j = line.find('"'), line.rfind('"')
                if i2 >= 0 and j > i2:
                    vals.append(line[i2 + 1:j])
        out[name] = vals
    return out


def recompile(erf, changed_resrefs, workdir, nwnsc=None, install=None, home=None,
              extra=None, log_name="nwnsc.log"):
    """逐文件重编译改过的 .nss。

    erf: 已读过 data 的 Erf（ent.data 为改后内容）
    changed_resrefs: 需要编译的 resref 集合
    返回 (produced: {resref: ncs_bytes}, errors: {resref: msg}, logpath)
    """
    nss_dir = os.path.abspath(os.path.join(workdir, "nss_build"))
    ncs_dir = os.path.abspath(os.path.join(workdir, "ncs_new"))
    for d in (nss_dir, ncs_dir):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)
    for ent in erf.entries:
        if ent.ext.lower() != "nss":
            continue
        data = ent.data if ent.data is not None else erf.read_data(ent)
        with open(os.path.join(nss_dir, ent.resref + ".nss"), "wb") as f:
            f.write(data)

    closure, added = include_closure(nss_dir, set(changed_resrefs))
    if added:
        print("  include 闭包补充 %d 个引用脚本（合计 %d 个待编译）"
              % (len(added), len(closure)), flush=True)
    home = home or os.path.join(os.path.expanduser("~"), "Documents", "Neverwinter Nights")
    nwnsc = nwnsc or default_nwnsc(home)
    install = install or detect_install_dir(home)
    logpath = os.path.abspath(os.path.join(workdir, log_name))

    base = [nwnsc, "-q", "-c"] + list(extra or [])
    if install:
        base += ["-n", install]
    if home:
        base += ["-h", home]
    base += ["-i", nss_dir, "-b", ncs_dir]

    produced, errors = {}, {}
    todo = sorted(closure)
    with open(logpath, "wb") as lf:
        for i, r in enumerate(todo, 1):
            src = os.path.join(nss_dir, r + ".nss")
            out = os.path.join(ncs_dir, r + ".ncs")
            if not os.path.exists(src):
                continue
            if os.path.exists(out):
                os.remove(out)
            lf.write(("### [%d/%d] %s\n" % (i, len(todo), r)).encode("utf-8", "replace"))
            lf.flush()
            try:
                p = subprocess.run(base + [src], stdout=lf, stderr=subprocess.STDOUT,
                                   cwd=nss_dir, timeout=180)
                rc = p.returncode
            except subprocess.TimeoutExpired:
                rc = -999
            except OSError as e:
                # 例如 Windows CreateProcess 的 WinError 206（命令行/文件名太长）。
                # 只跳过这一个文件，绝不能 break —— 否则一个坏文件会让整个重编译中断。
                errors[r] = "无法执行 nwnsc: %r" % e
                continue
            lf.write(b"\n")
            if rc == 0 and os.path.exists(out) and os.path.getsize(out) > 8:
                with open(out, "rb") as f:
                    produced[r] = f.read()
            else:
                errors[r] = "nwnsc 退出码 %d" % rc
            if i % 25 == 0:
                print("  已编译 %d/%d（成功 %d）" % (i, len(todo), len(produced)), flush=True)
    return produced, errors, logpath, nss_dir


def inject(erf, produced, line_map, orig_getter, workdir,
           nwnsc=None, install=None, home=None, extra=None):
    """硬校验后注入 .ncs。返回 (accepted, rejected)。

    line_map: {原文前 100 字节: 译文前 100 字节}（全局，跨文件，因为 #include 会内联）
    orig_getter: 取原始 .ncs 字节的函数
    """
    home = home or os.path.join(os.path.expanduser("~"), "Documents", "Neverwinter Nights")
    nwnsc = nwnsc or default_nwnsc(home)
    install = install or detect_install_dir(home)
    names = sorted(produced)
    so = disasm_consts(nwnsc, install, home, os.path.join(workdir, "dis_orig"),
                       [("orig_" + r, orig_getter(r)) for r in names], extra)
    sn = disasm_consts(nwnsc, install, home, os.path.join(workdir, "dis_new"),
                       [("new_" + r, produced[r]) for r in names], extra)
    accepted, rejected = {}, []
    for r in names:
        o_raw = so.get("orig_" + r)
        n_raw = sn.get("new_" + r)
        if o_raw is None or n_raw is None:
            # 反汇编没成功 -> 无法校验 -> **按未通过处理**（保留原 .ncs）。
            # 绝不能让它走到"两个空列表相等"那条路上，否则等于放未经校验的 .ncs 进游戏。
            rejected.append([r, -1, -1, ["反汇编失败，无法校验"], []])
            continue
        o = [_norm(x) for x in o_raw]
        n = [_norm(x) for x in n_raw]
        exp = [line_map.get(x, x) for x in o]
        if collections.Counter(exp) == collections.Counter(n):
            accepted[r] = produced[r]
        else:
            miss = list((collections.Counter(exp) - collections.Counter(n)).items())[:2]
            more = list((collections.Counter(n) - collections.Counter(exp)).items())[:2]
            rejected.append([r, len(o), len(n), [x[0][:40] for x in miss],
                             [x[0][:40] for x in more]])
    return accepted, rejected


def apply_accepted(erf, accepted):
    """把通过校验的 .ncs 写回 erf 条目，返回替换个数。"""
    n = 0
    for ent in erf.entries:
        if ent.ext.lower() != "ncs" or ent.resref not in accepted:
            continue
        nd = accepted[ent.resref]
        if nd != (ent.data if ent.data is not None else erf.read_data(ent)):
            ent.data = nd
            n += 1
    return n
