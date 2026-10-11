#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TLK 汉化记忆库：把 TLK 的中英对照按【英文原文内容】存进翻译记忆库，
并支持"PRC 更新后只翻增量"。

**键法**：`key = sha256(norm(英文原文))`（norm = 统一换行 + 去首尾空白）
  * 与 modl10n 的 `.mod` 流程共用同一个 `tm.sqlite`，可互相复用（同 key 同译文）；
  * 规范化让 key 跨版本稳定：导出格式差异（CRLF、尾部换行）不会导致命中失败；
  * 因为**存了英文原文**，所以"源文被改了"会被自动识别成新条目重译，
    "索引位移"也完全不影响 —— 这正是按 strref 存的旧缓存做不到的
    （旧缓存两种失效都会静默发生）。

**子命令**：
    python -m modl10n.tlk import-txt <英文导出.txt> <中文导出.txt> [--tm X]
    python -m modl10n.tlk status     <TLK> [--tm X]
    python -m modl10n.tlk fill       <TLK> <输出TLK> [--tm X]
"""
import os
import re
import sys
import argparse
import collections

from .tm import TM, key_of

HERE = os.path.dirname(os.path.abspath(__file__))
DEF_TM = os.path.join(HERE, "tm.sqlite")


# ============================================================ 文本规范化与键
def norm(t):
    """统一换行 + 去首尾空白。凡是要跨版本/跨文件匹配的文本都先过这一层。"""
    return (t or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def key_tlk(text):
    """TLK 文本 -> 记忆库 key（与 .mod 流程同一个 key_of，只是先规范化）。"""
    return key_of(norm(text))


def needs_translation(text):
    """这条是否还需要中文：以 ASCII 为主且含 2 个以上字母。

    判据与 `tlk tool.py` 的 find_untranslated 保持一致（实测 PRC8 上两者候选集相同）。
    """
    s = norm(text)
    core = [c for c in s if not c.isspace()]
    if not core:
        return False
    if sum(1 for c in core if ord(c) < 128) / len(core) <= 0.9:
        return False
    return bool(re.search(r"[A-Za-z]{2,}", s))


# ============================================================ TLK 读写
def read_raw(path):
    d = open(path, "rb").read()
    if len(d) < 20 or d[0:4] != b"TLK " or d[4:8] != b"V3.0":
        raise ValueError("不是 TLK V3.0 文件：%s" % path)
    lang = int.from_bytes(d[8:12], "little")
    n = int.from_bytes(d[12:16], "little")
    data_off = int.from_bytes(d[16:20], "little")
    if data_off != 20 + n * 40:
        raise ValueError("索引表布局与预期不符（dataOffset=%d，期望 %d）"
                         % (data_off, 20 + n * 40))
    if data_off > len(d):
        raise ValueError("数据区偏移越界（%d > %d）" % (data_off, len(d)))
    return d, lang, n, data_off


def load_texts(path):
    """{strref(str): 文本}；只取 flags==1 且长度 > 0 的条目。"""
    d, _lang, n, data_off = read_raw(path)
    out = {}
    for i in range(n):
        e = 20 + i * 40
        flags = int.from_bytes(d[e:e + 4], "little")
        off = int.from_bytes(d[e + 28:e + 32], "little")
        ln = int.from_bytes(d[e + 32:e + 36], "little")
        if flags == 1 and ln > 0 and data_off + off + ln <= len(d):
            out[str(i)] = d[data_off + off:data_off + off + ln].decode("utf-8", "replace")
    return out


def write_tlk(src_path, out_path, overrides, verify=True):
    """外科式写出：**保留原索引表其余字段**，只重建字符串数据区。

    overrides: {strref(str): 新文本}；不在里面的条目按原始字节原样保留。
    相比"整体重写"（save_tlk），这样连声音长度/偏移等不明字段也不会被弄丢。
    """
    d, lang, n, data_off = read_raw(src_path)
    if not overrides:
        open(out_path, "wb").write(d)
        return {"out": out_path, "changed": 0, "count": n, "bytes": len(d), "mismatch": 0}
    recs = [bytearray(d[20 + i * 40: 20 + (i + 1) * 40]) for i in range(n)]
    data = bytearray()
    changed = 0
    for i in range(n):
        rec = recs[i]
        k = str(i)
        flags = int.from_bytes(rec[0:4], "little")
        off = int.from_bytes(rec[28:32], "little")
        ln = int.from_bytes(rec[32:36], "little")
        old = d[data_off + off:data_off + off + ln] if (flags == 1 and ln > 0) else b""
        if k in overrides:
            blob = overrides[k].encode("utf-8")
            if blob != old:
                changed += 1
        elif old:
            blob = old
        else:
            blob = None
        if blob is None:
            rec[0:4] = (0).to_bytes(4, "little")
            rec[28:32] = (0).to_bytes(4, "little")
            rec[32:36] = (0).to_bytes(4, "little")
        else:
            rec[0:4] = (1).to_bytes(4, "little")
            rec[28:32] = len(data).to_bytes(4, "little")
            rec[32:36] = len(blob).to_bytes(4, "little")
            data += blob
    buf = bytearray(b"TLK V3.0" + lang.to_bytes(4, "little") + n.to_bytes(4, "little")
                    + (20 + n * 40).to_bytes(4, "little"))
    for rec in recs:
        buf += rec
    buf += data
    with open(out_path, "wb") as f:
        f.write(bytes(buf))
    info = {"out": out_path, "changed": changed, "count": n, "bytes": len(buf)}
    if verify:
        back = load_texts(out_path)
        src_texts = load_texts(src_path)
        bad = 0
        for k, v in overrides.items():
            if norm(back.get(k, "")) != norm(v):
                bad += 1
        for k, v in src_texts.items():
            if k not in overrides and norm(back.get(k, "")) != norm(v):
                bad += 1
        info["mismatch"] = bad
    return info


# ============================================================ 导出 txt 解析
def append_tlk(src_path, out_path, new_texts, verify=True):
    """把新文本**追加**到 TLK 末尾，返回 {文本: 新索引}。

    只追加、不动既有条目：既保住原有 strref 指向的内容，也不会打乱已有索引。
    （模块的自定义 TLK 只能有一个，所以要把译文加进 Mod_CustomTlk 指定的那个文件里。）
    """
    d, lang, n, data_off = read_raw(src_path)
    data = bytearray(d[data_off:])          # 既有数据区原样保留
    recs = [bytearray(d[20 + i * 40: 20 + (i + 1) * 40]) for i in range(n)]
    # 已经存在的文本直接复用原索引，避免反复追加把 TLK 撑大
    rev = {}
    for k, v in load_texts(src_path).items():
        rev.setdefault(norm(v), int(k))
    index = {}
    for t in new_texts:
        if t in index:
            continue
        key = norm(t)
        if key in rev:
            index[t] = rev[key]
            continue
        blob = t.encode("utf-8")
        rec = bytearray(40)
        rec[0:4] = (1).to_bytes(4, "little")            # flags：有文本、无声音
        rec[28:32] = len(data).to_bytes(4, "little")    # TextOffset
        rec[32:36] = len(blob).to_bytes(4, "little")    # TextLength
        data += blob
        recs.append(rec)
        index[t] = len(recs) - 1
        rev[key] = index[t]
    total = len(recs)
    buf = bytearray(b"TLK V3.0" + lang.to_bytes(4, "little") + total.to_bytes(4, "little")
                    + (20 + total * 40).to_bytes(4, "little"))
    for rec in recs:
        buf += rec
    buf += data
    with open(out_path, "wb") as f:
        f.write(bytes(buf))
    info = {"out": out_path, "added": len(index), "count": total, "bytes": len(buf)}
    if verify:
        back = load_texts(out_path)
        old = load_texts(src_path)
        bad = sum(1 for k, v in index.items() if norm(back.get(str(v), "")) != norm(k))
        bad += sum(1 for k, v in old.items() if norm(back.get(k, "")) != norm(v))
        info["mismatch"] = bad
    return index, info


def parse_export(path):
    """读 Simiy's NWN2 TLK Editor 导出的 txt：`<strref> := <文本>`（多行直到下一个 strref）。

    文件里可能有 CP-1252 字节（不是合法 UTF-8），所以读取时容错替换。
    """
    raw = open(path, "rb").read().decode("utf-8", "replace")
    out, cur, buf = {}, None, []
    for line in raw.split("\n"):
        m = re.match(r"^(\d+) := (.*)$", line)
        if m:
            if cur is not None:
                out[cur] = "\n".join(buf)
            cur, buf = m.group(1), [m.group(2)]
        elif cur is not None and not line.startswith("#//"):
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf)
    return out


# ============================================================ 记忆库操作
def pairs_from_exports(en_path, zh_path, keep_same=True):
    """两个导出 txt -> ([(src, dst)], 统计)。

    keep_same=True 时把「中文与英文完全相同」的条目也收进来（dst = 英文原样）。
    这类是**缩写代码与占位符**（`PDK`、`UrP`、`== ac reserved ==`…）：它们本来就不该翻译，
    但 `needs_translation` 的启发式判据判不出来。把它们作为"就该是英文"存进记忆库，
    以后就不会再被送去 API 白花钱、也不会被模型改坏 —— 让记忆库把判不准的情况沉淀成事实。
    """
    en, zh = parse_export(en_path), parse_export(zh_path)
    by_src = collections.defaultdict(collections.Counter)
    n_same = n_empty = n_missing = 0
    for k, v in en.items():
        if k not in zh:
            n_missing += 1
            continue
        s, t = norm(v), norm(zh[k])
        if not s:
            continue
        if not t:
            n_empty += 1
            continue
        if s == t:
            n_same += 1
            if not keep_same:
                continue
        by_src[s][t] += 1
    conflicts = sum(1 for c in by_src.values() if len(c) > 1)
    pairs = [(s, c.most_common(1)[0][0]) for s, c in by_src.items()]
    stats = {"pairs": len(pairs), "same": n_same, "empty": n_empty,
             "missing": n_missing, "conflicts": conflicts}
    return pairs, stats


def import_pairs(en_path, zh_path, tm_path=DEF_TM, overwrite=False, tag="PRC8", keep_same=True):
    """把中英对照导入记忆库；**默认跳过已存在的 key**（不覆盖既有译文）。"""
    pairs, stats = pairs_from_exports(en_path, zh_path, keep_same=keep_same)
    tm = TM(tm_path)
    rows = [(key_of(s), s, t, tag) for s, t in pairs]
    have = tm.get_many([r[0] for r in rows])
    todo = [r for r in rows if overwrite or not have.get(r[0])]
    n = tm.put_many(todo, status="auto")
    stats.update({"unique": len(rows), "skipped_existing": len(rows) - len(todo),
                  "written": n, "tm_total": tm.count()})
    return stats


def conflicts_of(en_path, zh_path):
    """列出「同英文、不同译法」的分组（术语不一致，可拿来统一）。"""
    en, zh = parse_export(en_path), parse_export(zh_path)
    by_src = collections.defaultdict(collections.Counter)
    for k, v in en.items():
        if k not in zh:
            continue
        s, t = norm(v), norm(zh[k])
        if s and t and s != t:
            by_src[s][t] += 1
    return [(s, c) for s, c in by_src.items() if len(c) > 1]


def analyze(tlk_path, tm_path=DEF_TM):
    """看一个 TLK 里有多少条命中记忆库、多少条需要翻译（不花钱）。"""
    texts = load_texts(tlk_path)
    ids = [i for i, t in texts.items() if needs_translation(t)]
    tm = TM(tm_path)
    keys = {i: key_tlk(texts[i]) for i in ids}
    hits = tm.get_many(set(keys.values()))
    hit = [i for i in ids if hits.get(keys[i])]
    miss = [i for i in ids if not hits.get(keys[i])]
    return {"texts": texts, "ids": ids, "hit": hit, "miss": miss,
            "keys": keys, "hits": hits, "total": len(texts)}


def fill(tlk_path, out_path, tm_path=DEF_TM):
    """用记忆库把 TLK 填成中文（不花钱）；命中的条目写入，缺失的保持原文。"""
    a = analyze(tlk_path, tm_path)
    overrides = {i: a["hits"][a["keys"][i]] for i in a["hit"]}
    info = write_tlk(tlk_path, out_path, overrides)
    info.update({"hit": len(a["hit"]), "miss": len(a["miss"]),
                 "candidates": len(a["ids"]), "texts": a["total"]})
    return info


# ============================================================ 术语统一
def decisions(en_path, zh_path, max_len=60):
    """找出「同一英文、多种译法」的**短词条**，按多数派给出建议。

    长文本的多译法不在这里处理 —— 它们由"用记忆库重建 TLK"自动统一
    （同一英文原文在内容哈希记忆库里只可能有一个译法）。
    """
    en, zh = parse_export(en_path), parse_export(zh_path)
    by_src = collections.defaultdict(collections.Counter)
    for k, v in en.items():
        if k in zh:
            s, t = norm(v), norm(zh[k])
            if s and t and s != t:
                by_src[s][t] += 1
    out = []
    for s, c in by_src.items():
        if len(c) > 1 and len(s) <= max_len:
            canon = c.most_common(1)[0][0]
            out.append({"en": s, "canonical": canon,
                        "variants": [t for t, _ in c.most_common() if t != canon],
                        "note": " / ".join("%s×%d" % (t, n) for t, n in c.most_common())})
    # 影响面大的排前面
    out.sort(key=lambda d: -len(d["variants"]))
    return out


def load_table(path):
    """读术语统一表（TSV：英文术语 / 规范译法 / 被替换的变体(;分隔) / 说明 / 范围）。

    范围 = 全文（默认，凡原文里出现该英文词的条目都改）或 仅词条（只改该词条本身）。
    像 `Command`/`Orcish` 这种本身也是常用词的，必须用「仅词条」，否则会改坏别的句子。
    """
    import csv
    table = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f, delimiter="\t")
        for row in rd:
            en = (row.get("英文术语") or "").strip()
            canon = (row.get("规范译法") or "").strip()
            vs = [v.strip() for v in (row.get("被替换的变体") or "").split(";") if v.strip()]
            scope = (row.get("范围") or "全文").strip()
            prot = [p.strip() for p in (row.get("保护") or "").split(";") if p.strip()]
            if en and canon and vs:
                table.append({"en": en, "canonical": canon, "variants": vs,
                              "note": row.get("说明") or "",
                              "scope": "exact" if scope.startswith("仅") else "all",
                              "protect": prot})
    return table


def apply_variants(tm_path, table, dry_run=False, tag="PRC8"):
    """把术语表应用到记忆库：只改「原文命中该英文词」的条目，把变体替换成规范译法。

    四条安全约束：①只动 src_mod = tag 的条目（不碰其它项目）；②只对原文里
    真的出现该英文词的条目动手（按词边界匹配；范围=仅词条时要求原文完全等于该词）；
    ③**保护词**先遮蔽再替换（例如 `Vile` 里的「邪恶」不能把规则术语「邪恶阵营」也改掉）；
    ④长变体优先替换（避免"舍卡尔尔"）。
    """
    tm = TM(tm_path)
    rows = tm.db.execute("SELECT key,src,dst FROM tm WHERE status!='rejected' AND src_mod=?",
                         (tag,)).fetchall()
    n_edit = 0
    per_term = collections.Counter()
    examples = []
    # 关键：每条内容只写一次。若"每条规则各写一次"，后面的规则会拿【旧快照】覆盖前面
    # 规则的修改 —— 长文本（一条里含多个术语）就只会改到一半（实测踩过这个坑）。
    pending = {}
    for item in table:
        en_term, canon = item["en"], item["canonical"]
        rx = re.compile(r"(?<![A-Za-z])" + re.escape(en_term) + r"(?![A-Za-z])")
        variants = sorted(item["variants"], key=len, reverse=True)
        protect = item.get("protect") or []
        for key, src, dst in rows:
            if not src or not dst:
                continue
            ns = norm(src)
            if item.get("scope") == "exact":
                if ns != en_term:
                    continue
            elif not rx.search(src):
                continue
            base = pending.get(key, dst)          # 已改过的用改过之后的版本接着改
            # 保护词遮蔽 -> 替换 -> 还原
            marked = base
            holes = []
            for p in protect:
                while p and p in marked:
                    holes.append(p)
                    marked = marked.replace(p, "\x00%d\x00" % (len(holes) - 1), 1)
            nd = marked
            for v in variants:
                if v in nd:
                    nd = nd.replace(v, canon)
            for i, p in enumerate(holes):
                nd = nd.replace("\x00%d\x00" % i, p)
            if nd != base:
                pending[key] = nd
                n_edit += 1
                per_term[en_term] += 1
                if len(examples) < 8:
                    examples.append((en_term, base[:44], nd[:44]))
    if not dry_run:
        for key, nd in pending.items():
            tm.mark_manual(key, nd)
        tm.commit()
    return {"edited": n_edit, "terms": len(per_term), "per_term": per_term,
            "examples": examples, "scanned": len(rows), "keys": len(pending)}


def rebuild(base_tlk, en_txt, out_tlk, tm_path=DEF_TM):
    """用记忆库重建中文 TLK：同一英文原文只会有一个译法，重复文本自动统一。

    base_tlk 提供槽位结构（用已汉化的那份最稳），en_txt 提供"哪个 strref 是英文原文"。
    """
    en = parse_export(en_txt)
    ov = {k: norm(v) for k, v in en.items() if norm(v)}
    tmp = out_tlk + ".en.tmp"
    write_tlk(base_tlk, tmp, ov, verify=False)
    try:
        r = fill(tmp, out_tlk, tm_path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return r


# ============================================================ 命令行
def cmd_import_txt(args):
    r = import_pairs(args.en, args.zh, tm_path=args.tm, overwrite=args.overwrite,
                     tag=args.tag, keep_same=not args.skip_same)
    print("英文源 %s / 中文 %s" % (args.en, args.zh))
    print("  可配对的中英对照去重后 %d 条（其中「中英相同·按原样收录」%d 条，"
          "同原文多译法冲突 %d 组取多数，中文为空 %d 条，单侧缺失 %d 条）"
          % (r["unique"], r["same"], r["conflicts"], r["empty"], r["missing"]))
    print("  记忆库里已存在而跳过 %d 条（不覆盖既有译文；--overwrite 可强制）"
          % r["skipped_existing"])
    print("  本次写入 %d 条；记忆库现有 %d 条" % (r["written"], r["tm_total"]))
    return 0


def cmd_conflicts(args):
    groups = conflicts_of(args.en, args.zh)
    groups.sort(key=lambda x: -sum(x[1].values()))
    print("发现 %d 组「同英文、不同译法」：\n" % len(groups))
    lines = ["英文原文\t译法\t出现次数\t建议"]
    for s, c in groups:
        best, cnt = c.most_common(1)[0]
        for t, n in c.most_common():
            mark = "← 多数" if t == best else ""
            print("  %-46s %-24s x%d %s" % (s[:46], t[:24], n, mark))
            lines.append("%s\t%s\t%d\t%s" % (s, t, n, mark))
        print()
    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
            f.write("\n".join(lines))
        print("已导出 %s（%d 组）" % (args.csv, len(groups)))
    return 0


def cmd_status(args):
    a = analyze(args.tlk, args.tm)
    print("TLK：%s" % args.tlk)
    print("  有文本条目 %d 条；其中需要中文的 %d 条" % (a["total"], len(a["ids"])))
    print("  记忆库命中 %d 条（重新汉化时这 %d 条 0 花费）" % (len(a["hit"]), len(a["hit"])))
    print("  需要翻译   %d 条（这才是更新后真正要花钱的部分）" % len(a["miss"]))
    if a["ids"]:
        print("  命中率 %.1f%%" % (100.0 * len(a["hit"]) / len(a["ids"])))
    for i in a["miss"][:5]:
        print("    [待译] strref=%s %s" % (i, norm(a["texts"][i])[:70]))
    return 0


def cmd_fill(args):
    r = fill(args.tlk, args.out, args.tm)
    print("源 TLK：%s" % args.tlk)
    print("  有文本 %d 条；需要中文 %d 条" % (r["texts"], r["candidates"]))
    print("  从记忆库填入 %d 条（0 花费）；%d 条缺译文保持原文" % (r["hit"], r["miss"]))
    print("  已写出：%s（%d 字节，%d 个槽位，实际改动 %d 条）"
          % (r["out"], r["bytes"], r["count"], r["changed"]))
    print("  读回校验：不一致 %d 条 %s" % (r["mismatch"], "✅" if r["mismatch"] == 0 else "❌"))
    return 0


def cmd_decisions(args):
    import csv
    table = decisions(args.en, args.zh, max_len=args.max_len)
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["英文术语", "规范译法", "被替换的变体", "说明", "范围", "保护"])
        for d in table:
            w.writerow([d["en"], d["canonical"], ";".join(d["variants"]), d["note"], "全文", ""])
    print("已生成术语统一表 %d 组 -> %s" % (len(table), args.out))
    print("  默认规则：规范译法 = 文件里出现最多的那个（多数派）；")
    print("  你可直接改「规范译法」列（改成另一个变体、或你自己的译法）后再跑 unify。")
    return 0


def cmd_unify(args):
    table = load_table(args.table)
    if args.only:
        keep = set(x.strip() for x in args.only.split(","))
        table = [t for t in table if t["en"] in keep]
    print("待应用的术语规则 %d 条" % len(table))
    r = apply_variants(args.tm, table, dry_run=args.dry_run, tag=args.tag)
    print("  扫描 PRC8 条目 %d 条；%s %d 处（涉及 %d 个术语）"
          % (r["scanned"], "将改写" if args.dry_run else "已改写", r["edited"], r["terms"]))
    for en in list(r["per_term"])[:12]:
        print("    %-34s %d 处" % (en[:34], r["per_term"][en]))
    if r["examples"]:
        print("  例子：")
        for en, a, b in r["examples"][:5]:
            print("    %-22s %s  ->  %s" % (en[:22], a, b))
    return 0


def cmd_rebuild(args):
    r = rebuild(args.base, args.en, args.out, args.tm)
    print("用记忆库重建 TLK：")
    print("  基准（槽位结构）：%s" % args.base)
    print("  英文来源：%s（%d 条对照）" % (args.en, len(parse_export(args.en))))
    print("  命中记忆库填入 %d 条；%d 条缺译文保持原文" % (r["hit"], r["miss"]))
    print("  已写出 %s（%d 字节，%d 个槽位，实际改动 %d 条）"
          % (r["out"], r["bytes"], r["count"], r["changed"]))
    print("  读回校验：不一致 %d 条 %s" % (r["mismatch"], "✅" if r["mismatch"] == 0 else "❌"))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="modl10n.tlk",
                                 description="TLK 汉化记忆库：按英文原文存取译文，支持只翻增量")
    ap.add_argument("--tm", default=DEF_TM)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("import-txt", help="把两个导出 txt 的中英对照存进记忆库")
    p1.add_argument("en")
    p1.add_argument("zh")
    p1.add_argument("--overwrite", action="store_true", help="覆盖记忆库里已存在的同 key 条目")
    p1.add_argument("--skip-same", action="store_true",
                    help="不收录「中文与英文相同」的条目（默认收录，它们多是缩写代码/占位符）")
    p1.add_argument("--tag", default="PRC8", help="来源标记，写入 tm.src_mod")
    p2 = sub.add_parser("conflicts", help="列出「同英文、不同译法」的分组（术语不一致）")
    p2.add_argument("en")
    p2.add_argument("zh")
    p2.add_argument("--csv", default=None, help="同时导出成 CSV")
    p3 = sub.add_parser("status", help="看新版 TLK 有多少条能命中记忆库（不花钱）")
    p3.add_argument("tlk")
    p4 = sub.add_parser("fill", help="用记忆库把 TLK 填成中文（不花钱）")
    p4.add_argument("tlk")
    p4.add_argument("out")
    p5 = sub.add_parser("decisions", help="生成「同英文多译法」的术语统一表（供人工审阅）")
    p5.add_argument("en")
    p5.add_argument("zh")
    p5.add_argument("--out", required=True)
    p5.add_argument("--max-len", type=int, default=60, dest="max_len",
                    help="只处理长度不超过此值的英文词条（长文本交给 rebuild 自动统一）")
    p6 = sub.add_parser("unify", help="按术语统一表改写记忆库（0 花费）")
    p6.add_argument("--table", required=True)
    p6.add_argument("--only", default=None, help="只应用这些英文术语（逗号分隔）")
    p6.add_argument("--tag", default="PRC8", help="只改这些来源的条目")
    p6.add_argument("--dry-run", action="store_true")
    p7 = sub.add_parser("rebuild", help="用记忆库重建中文 TLK（同一英文只留一个译法）")
    p7.add_argument("base", help="基准 TLK（提供槽位结构）")
    p7.add_argument("en", help="英文导出 txt（哪个 strref 是英文原文）")
    p7.add_argument("out")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return {"import-txt": cmd_import_txt, "conflicts": cmd_conflicts,
            "status": cmd_status, "fill": cmd_fill, "decisions": cmd_decisions,
            "unify": cmd_unify, "rebuild": cmd_rebuild}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
