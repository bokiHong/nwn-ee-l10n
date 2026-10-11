#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ScanService —— 容器（`.mod` / `.tlk`）-> `container` + `resource` 落库。

流水线第 1 步。只做"这个文件里有哪些资源、各自多大、变没变"，
不解析内容、不抽取文本（那是第 2 步 `ExtractService`）。

为什么要 sha256：`resource.sha256` 是"这个资源这次要不要重新抽取"的判据，
也是 `change_state`（new/unchanged/modified/removed）的唯一依据。
"""
import os

from ..db import now
from ..repo import ContainerRepo, ResourceRepo, sha256_of
from ..model import Resource

# 常见 NWN GFF 资源首 4 字节（与 modl10n.extract.GFF_MAGICS 同源）
GFF_MAGICS = {
    b"UTI ", b"UTC ", b"DLG ", b"ARE ", b"GIT ", b"IFO ", b"ITP ", b"UTT ",
    b"UTS ", b"FAC ", b"UTE ", b"UTD ", b"UTP ", b"GIC ", b"UTM ", b"JRL ",
    b"UTW ", b"BIC ", b"SET ", b"PTM ", b"UPE ",
}

EXT_ROLE = {".mod": "mod", ".tlk": "tlk", ".hak": "hak", ".erf": "erf"}


def guess_fmt(path):
    return EXT_ROLE.get(os.path.splitext(path)[1].lower(), "mod")


def scan(db, project, path, role="source", fmt=None, resource_hash=True, verbose=True):
    """把一个容器扫进库。幂等：同一文件反复扫描不产生重复行。

    返回 report：{"container_id", "path", "fmt", "bytes", "resources", "stats"}
    """
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise SystemExit("文件不存在：%s" % path)
    fmt = fmt or guess_fmt(path)
    raw = open(path, "rb").read()
    crepo, rrepo = ContainerRepo(db), ResourceRepo(db)
    cid = crepo.upsert(project, role, path, fmt,
                       size=len(raw), sha256=sha256_of(raw),
                       mtime=now())
    if verbose:
        print("  容器 #%d %s（%s，%d 字节）" % (cid, os.path.basename(path), fmt, len(raw)))

    # ---- 资源清单
    if fmt == "tlk":
        # TLK 不是容器，只有一个"资源"= 它自己；条目放在 Sprint 5 的 tlk_slot 账本里
        rows = [(os.path.splitext(os.path.basename(path))[0], "tlk", len(raw),
                 sha256_of(raw), None)]
    else:
        from modl10n.core.erf import Erf
        erf = Erf.load(path)
        rows = []
        for ent in erf.entries:
            data = erf.orig(ent)
            magic = data[:4]
            rows.append((ent.resref, ent.ext, len(data),
                         sha256_of(data) if resource_hash else None,
                         magic.decode("latin-1") if magic in GFF_MAGICS else None))

    stats = rrepo.upsert_many(cid, rows)
    if verbose:
        print("  资源 %d 个：%s" % (len(rows), stats))
    return {"container_id": cid, "path": path, "fmt": fmt, "bytes": len(raw),
            "resources": len(rows), "stats": stats}


def require_container(db, project, path, scan_if_missing=True, verbose=False):
    """确保容器已入库，返回 (container_id, fmt)；缺了就顺手 scan。"""
    path = os.path.abspath(path)
    crepo = ContainerRepo(db)
    row = crepo.get(project, "source", path)
    if row:
        return row["id"], row["fmt"]
    if not scan_if_missing:
        raise SystemExit("容器尚未入库，请先 scan：%s" % path)
    rep = scan(db, project, path, verbose=verbose)
    return rep["container_id"], rep["fmt"]
