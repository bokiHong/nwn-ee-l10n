#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PackService —— `.mod` 打包（"只打包已有译文，**绝不调 API**"）。

从 `mod tool.py` 搬出的 `make_repack_args` / `OfflineTranslator` / `repack`。

**安全约定（沿用旧实现，别改）**：
  * 本模块**不直接改写 .mod**，一律交给经过验证的 `modl10n.pipeline`
    （原子写 + 覆盖前自动备份 + 读回校验）；
  * 翻译器固定用 `OfflineTranslator`（什么都不做）→ **任何情况下都不产生 API 费用**；
  * `_Tee` 的**重入保护必须保留**：`progress` 若往 `sys.stdout` 写，而此时 stdout 已被
    重定向到本对象，就会无限递归。

**术语来源（用户 2026-10-08 拍板）**：打包时的术语强校验必须用**中央库 `term` 表**
（真源），否则会出现"改了术语却不生效"的静默失效；只有拿不到 db 时才退回
`glossary.json` 快照（兼容旧 CLI 调用）。
"""
import argparse
import contextlib
import io
import os
import re

from ..db import DEF_DB
from ..repo import TermRepo

try:
    from modl10n import pipeline as P
    from modl10n.glossary import Glossary
except ImportError:                       # pragma: no cover - modl10n 不在路径时
    P = None
    Glossary = None

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEF_TM = DEF_DB
DEF_GLOSSARY = os.path.join(HERE, "modl10n", "glossary.json")


def make_repack_args(modpath, outdir, out=None, inplace=False, workers=5, tm_path=DEF_TM,
                     glossary=DEF_GLOSSARY):
    """拼出 `pipeline.process()` 需要的参数（与命令行 `--xxx` 一一对应）。

    ⚠ 这里是**手抄**的 20 个字段，与原 `mod tool.py:182-190` 逐字一致 —— 这是刻意的：
    `pipeline.build_parser()` 的默认值未必与旧行为相同，换成 parse_args 会让**打包产物
    可能变化**，而我们的验收标准是"与命令行流程逐字节相同"。改动请同步核对验收用例。
    """
    return argparse.Namespace(
        mod=modpath, sf01=None, tm=tm_path, glossary=glossary, outdir=outdir, out=out,
        inplace=inplace, workers=workers, limit=0,
        punct=os.environ.get("SF_PUNCT", "full"), extract_only=False, dry_run=False,
        no_ncs=False, no_nss=False, no_var_assign=False, nwnsc=None, install_dir=None,
        extra_args="", api_key=None, yes=True)


class OfflineTranslator:
    """占位翻译器：**绝不调用 API**，未译条目原样保留英文。"""

    def run(self, pending, tm, src_mod="", workers=1):
        return {"离线模式·跳过未译": len(pending)}


def load_glossary(db, glossary_path=DEF_GLOSSARY):
    """取术语表：**优先中央库 `term` 表**（真源），拿不到才退回 JSON 快照。"""
    if db is not None:
        try:
            g = TermRepo(db).glossary()
            if g is not None:
                return g
        except Exception:                 # noqa: BLE001 - 退回快照，不因术语库故障挡住打包
            pass
    return Glossary.load(glossary_path)


def repack(modpath, outdir=None, out=None, inplace=False, tm_path=DEF_TM,
           glossary=DEF_GLOSSARY, db=None, progress=None):
    """复用经过验证的 pipeline 打包（原子写 + 自动备份 + 读回校验），全程不调 API。

    返回 `{"log", "outdir", "out", "verified", "compared", "glossary_src"}`。
    """
    modpath = os.path.abspath(modpath)
    if outdir is None:
        outdir = os.path.join(os.path.dirname(modpath), "汉化产物")
    os.makedirs(outdir, exist_ok=True)
    args = make_repack_args(modpath, outdir, out=out, inplace=inplace, tm_path=tm_path,
                            glossary=glossary)
    gl = load_glossary(db, glossary)
    from modl10n.tm import TM
    tm = TM(tm_path)
    buf = io.StringIO()

    class _Tee:
        """既收集日志，又把每行转给 progress 回调。

        ⚠ 重入保护必须保留：progress 若写 sys.stdout，而 stdout 已被重定向到本对象，
        就会无限递归。用 _busy 挡住。
        """

        def __init__(self):
            self._busy = False

        def write(self, s):
            buf.write(s)
            if progress and s.strip() and not self._busy:
                self._busy = True
                try:
                    progress(s.rstrip("\n"))
                finally:
                    self._busy = False

        def flush(self):
            pass

    with contextlib.redirect_stdout(_Tee()):
        P.process(modpath, args, tm=tm, glossary=gl, translator=OfflineTranslator())
    text = buf.getvalue()
    m = re.search(r"读回校验：比对 (\d+) 条，不一致 (\d+) 条", text)
    return {"log": text, "outdir": outdir,
            "out": out or os.path.join(outdir, os.path.basename(modpath)[:-4] + "_zh.mod"),
            "verified": (int(m.group(2)) == 0) if m else None,
            "compared": int(m.group(1)) if m else None,
            "glossary_src": "term 表" if db is not None else glossary}
