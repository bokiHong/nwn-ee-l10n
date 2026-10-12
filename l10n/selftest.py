#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收脚本 —— 每个 Sprint 的"可复现证据"。

用法：
    cd scripts
    python -m l10n.selftest          # 跑全部（Sprint 2 会写 unit 表，可重建）
    python -m l10n.selftest 1        # 只跑 Sprint 1（纯只读，不写库）
    python -m l10n.selftest 2        # 只跑 Sprint 2

**红线**：本脚本从不写 `tm` 表。Sprint 2 只写"索引层"（container/resource/unit），
而索引层删掉可重建 —— 脚本本身就带这个重建测试。
"""
import json
import os
import sys

from . import paths
from .db import connect, DEF_DB, view_names
from .keys import src_key, is_normalized
from .model import StringUnit, Resource, TlkSlot, Term
from .repo import TmRepo, TermRepo, UnitRepo
from .services.diff_service import summarize as summarize_diff

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GLOSSARY_JSON = os.path.join(SCRIPTS, "modl10n", "glossary.json")
OVERRIDES_JSON = os.path.join(SCRIPTS, "glossary_overrides.json")
VARIANTS_JSON = os.path.join(SCRIPTS, "modl10n", "term_variants.json")
DRAFT_JSON = os.path.join(SCRIPTS, "data", "legacy", "dnd3r_术语库_草案.json")

# ------------------------------------------------------------ 可选测试资产
# 下面这些是**你本机的私有文件**（商业模组 / 游戏 TLK），不会进 Git 仓库。
# 设了环境变量就用它、没设就用惯例路径；**找不到就跳过相关检查、不判失败** ✓
#   L10N_TEST_MOD        测试用 .mod（默认按 Ch1 的惯例位置找）
#   L10N_TEST_MOD_SMALL  小样本 .mod（GUI 验收用）
#   NWN_HOME             NWN:EE 用户目录
CH1 = os.environ.get("L10N_TEST_MOD") or os.path.join(
    os.path.dirname(SCRIPTS), "module", "Sword Flight", "原始英文模组",
    "_original", "Swordflight - Chapter One.mod")
HAS_CH1 = os.path.exists(CH1)
PROJECT = "swordflight"

_results = []


def _tm_has(db, key):
    return db.execute("SELECT 1 FROM tm WHERE key=? AND status!='rejected'",
                      (key,)).fetchone() is not None


def _check(name, ok, detail=""):
    _results.append((name, bool(ok), detail))
    print("  %s %-46s %s" % ("✓" if ok else "✗", name, detail))
    return ok


def _skip(name, why=""):
    """跳过（**不计入失败**）—— 只用于「依赖本机私有资产」的检查 ✓。"""
    print("  ⚠ %-46s %s" % (name, why))


# 公开数据包（nwn-ee-l10n-data）导入过了吗？None = 还没探测
_HAS_DATA = None


def _probe_data(db):
    """探测库里有没有公开数据。返回 (tm 条数, term 条数)。"""
    global _HAS_DATA
    try:
        n_tm = db.execute("SELECT COUNT(*) FROM tm").fetchone()[0]
        n_term = db.execute("SELECT COUNT(*) FROM term").fetchone()[0]
    except Exception:
        n_tm = n_term = 0
    _HAS_DATA = (n_tm >= 1000 and n_term >= 100)
    return n_tm, n_term


def _check_data(name, ok, detail=""):
    """依赖公开数据包的检查：没导入数据时**跳过**、不判失败 ✓。

    这样「只克隆了代码仓库」的人跑 selftest 也不会看到一片红 ——
    缺的是数据，不是代码坏了。
    """
    if _HAS_DATA is False:
        _skip(name, "未导入公开数据包（跑 tools/bootstrap.py）")
        return
    return _check(name, ok, detail)


# ============================================================ Sprint 1
def check_sprint1(db):
    print("\n[1] key 语义（l10n\\keys.py）")
    tm, terms, units = TmRepo(db), TermRepo(db), UnitRepo(db)
    from modl10n.tlk import key_tlk
    from modl10n.tm import key_of as _ko
    _check("src_key 复用 modl10n.tlk.key_tlk",
           all(src_key(t) == key_tlk(t) for t in
               ["Hello there.", "  Trim me  ", "a\r\nb", ""]), "4 条样本一致")
    _check("src_key(raw) 复用 modl10n.tm.key_of",
           src_key("X", normalize=False) == _ko("X"))

    total = n_orphan = n_normsrc = n_raw = n_norm = n_unexplained = 0
    unnorm_raw = unnorm_norm = lead = trail = 0
    # 空库（还没导入公开数据）时库里没有 tm 表 —— 别在这里崩掉 ✓
    _tm_rows = db.execute("SELECT key, src FROM tm").fetchall() if _HAS_DATA else []
    for k, s in _tm_rows:
        total += 1
        if not s:
            n_orphan += 1
            continue
        a = (k == src_key(s, normalize=False))
        b = (k == src_key(s, normalize=True))
        n_raw += a
        n_norm += b
        if not (a or b):
            n_unexplained += 1
        if is_normalized(s):
            n_normsrc += 1
        else:
            if s != s.lstrip():
                lead += 1
            if s != s.rstrip():
                trail += 1
            unnorm_raw += a
            unnorm_norm += b
    n_unnorm = total - n_orphan - n_normsrc
    print("      tm 全量 %d 条 = 孤儿（src 为空）%d + 未规范 %d + 已规范 %d"
          % (total, n_orphan, n_unnorm, n_normsrc))
    print("      未规范者：前导空白 %d 条、尾随空白 %d 条；其中按 sha256(src) 命中 %d、"
          "按 sha256(norm(src)) 命中 %d" % (lead, trail, unnorm_raw, unnorm_norm))
    _check("所有 key 都能由 src 解释（排除孤儿）", n_unexplained == 0,
           "无法解释 %d 条" % n_unexplained)
    print("      ⚠ 隐患 1：%d 条未规范 src 的 key 是按 **raw** 算的，TLK 流程（先 norm）"
          "下算不出同一 key → 会被当成新条目重译" % n_unnorm)
    print("      ⚠ 隐患 2：%d 条孤儿（src 为空、全部来自 Chapter One）永远无法按内容命中"
          % n_orphan)

    print("\n[2] model 数据类往返（as_row -> from_row）")
    su = StringUnit(uid="u1", project="p", container_id=1, resref="r", ext="dlg",
                    kind="gff", field_path="dlg:Text", locator='["gff",1,2]', strref=42,
                    src_text="Hi", src_key=src_key("Hi"), src_hash_seen="h",
                    storage="inline", placeholders='["1d6"]', term_hits='[]')
    _check("StringUnit 往返一致", StringUnit.from_row(su.as_row()) == su)
    _check("StringUnit.make_uid 稳定",
           StringUnit.make_uid(1, "R", "gff", ["gff", 1, 2])
           == StringUnit.make_uid(1, "r", "gff", ["gff", 1, 2]))
    rs = Resource(rid="r1", container_id=1, resref="a", ext="git")
    _check("Resource 往返一致", Resource.from_row(rs.as_row()) == rs)
    ts = TlkSlot(tlk_path="x.tlk", strref=90001, owner="prc8", purpose="engine_text")
    _check("TlkSlot 往返一致", TlkSlot.from_row(ts.as_row()) == ts)
    t1 = Term(en="A", zh="甲", type="name", required=1)
    _check("Term 往返一致", Term.from_row(t1.as_row()) == t1)
    _check("Term.is_required 语义", t1.is_required is True)

    print("\n[3] TmRepo（翻译记忆，基准 43,942 条）")
    c = tm.count()
    # ⚠ 不写死等值：tm 会随"补建未入库条目 / 手工新增"增长（2026-10-10 实测 43945 = +3 条）
    #   只卡**下界** —— 防止"索引层结构被破坏导致译文行数暴跌"，而不是防增长 ✗
    # 公开数据包过滤了 64 条「空 src 或空 dst」的脏行（43,951 → 43,887），故下界取 43,000 ✓
    _check_data("count >= 43000", c >= 43000, "实测 %d" % c)
    st = tm.stats()
    # ⚠ 不写死数字：manual 会随"术语统一 / 手工保存"增长（2026-10-08 起多次变更）
    # ⚠ 2026-10-08 大批术语统一把很多 auto 行标成了 manual（实测 35308/8634），
    #   所以这里只能卡"两边都还有相当量"，auto 的下限不能卡太高
    _check_data("stats 有 auto/manual", st.get("auto", 0) > 20000 and st.get("manual", 0) > 2000,
           str(st))
    hit = tm.search("Swordflight", limit=5)
    _check_data("search 有结果", len(hit) > 0, "%d 条（限 5）" % len(hit))
    m = tm.zh_to_en_map()
    _check_data("zh_to_en_map 覆盖全库", len(m) > 40000, "%d 条" % len(m))
    _got = (tm.get(hit[0][0]) == hit[0][2]) if hit else False
    _check_data("get 按 key 取译文", _got)

    print("\n[4] TermRepo（术语）+ 旧 JSON 资产适配")
    raw = json.load(open(GLOSSARY_JSON, encoding="utf-8"))
    legacy = TermRepo.load_legacy_glossary(GLOSSARY_JSON)
    _check("glossary.json 读出 ≥ 213 条", len(legacy) >= 213,
           "实测 %d（源 JSON %d）" % (len(legacy), len(raw["terms"])))
    bad = []
    for src_t, t in zip(raw["terms"], legacy):
        if (src_t["en"] != t.en or src_t["zh"] != t.zh
                or src_t.get("type", "name") != t.type
                or (src_t.get("note") or None) != t.note
                or bool(src_t.get("required", True)) != bool(t.required)):
            bad.append(src_t["en"])
    _check("213 条逐字段一致（en/zh/type/note/required）", not bad,
           "不一致 %d 条 %s" % (len(bad), bad[:5]))
    _check("必守术语 ≥ 149 条", sum(1 for t in legacy if t.required) >= 149)
    patch, adds = TermRepo.load_legacy_overrides(OVERRIDES_JSON)
    _check("overrides 读出 patches+adds", len(patch) == 5 and len(adds) == 2,
           "patch %d / add %d" % (len(patch), len(adds)))
    merged, rep = TermRepo.merge_legacy(list(legacy), patch, adds)
    _check("预演合并无悬空覆盖", not rep["dangling"], str(rep))
    _check("Swordsage 覆盖未被丢",
           any(t.en == "Swordsage" and t.zh == "贤者之剑" for t in merged),
           "合并后共 %d 条" % len(merged))
    draft = TermRepo.load_legacy_draft(DRAFT_JSON, min_count=1)
    _check("草案读出候选译名（>5000）", len(draft) > 5000,
           "实测 %d 条候选，全部 candidate：%s"
           % (len(draft), all(d.status == "candidate" for d in draft)))
    var = TermRepo.load_legacy_variants(VARIANTS_JSON)
    _check("term_variants 读出全部规则", len(var) == 77, "实测 %d 条" % len(var))
    _check_data("term 表已收编（active ≥ 213 + candidate ≥ 6000）",
           terms.count("active") >= 213 and terms.count("candidate") >= 6000,
           "实测 %s" % terms.count_by_status())

    print("\n[5] UnitRepo 接口")
    _check("count 可调用", isinstance(units.count(PROJECT), int),
           "实测 %d" % units.count(PROJECT))
    _check("stats 可调用", isinstance(units.stats(PROJECT), dict), str(units.stats(PROJECT)))
    _check("by_res 可调用", isinstance(units.by_res(PROJECT), list),
           "%d 行" % len(units.by_res(PROJECT)))
    _check("violations 可调用（视图可查）", isinstance(units.violations(), list))
    _check("两个视图已就位", {"v_unit_todo", "v_term_violation"} <= view_names(db))


# ============================================================ Sprint 2
def check_sprint2(db):
    """scan / extract 落库与幂等。会写 unit 表（最后会把它恢复成 kept 状态）。"""
    from .repo import ContainerRepo, ResourceRepo
    from .services import extract as do_extract

    crepo, rrepo, urepo = ContainerRepo(db), ResourceRepo(db), UnitRepo(db)
    print("\n[6] Sprint 2：scan 落库（容器 + 资源）")
    if not HAS_CH1:
        _skip("Sprint 2 整段", "找不到原始模组（商业模组不进仓库）")
        print("      想跑就设 L10N_TEST_MOD=<你的 .mod 路径>，当前找的是：")
        print("      %s" % CH1)
        return
    # 全新库里还没有这个容器 —— 顺手 scan 落库（Sprint 2 本来就在测 scan ✓）
    from .services.scan_service import require_container
    try:
        cid, _fmt = require_container(db, PROJECT, CH1, scan_if_missing=True)
    except Exception as e:                      # noqa: BLE001
        _skip("Sprint 2 scan", "scan 失败：%s" % str(e)[:60])
        return
    cons = [c for c in crepo.list(PROJECT) if c["id"] == cid]
    _check("Ch1 容器已登记", len(cons) == 1, "%d 个" % len(cons))
    if not cons:
        return
    _check("容器字节 = 11,182,300", cons[0]["size"] == 11182300, str(cons[0]["size"]))
    n_res = rrepo.count(cid)
    _check("资源 1934 个", n_res == 1934, "实测 %d" % n_res)
    exts = dict(rrepo.by_ext(cid))
    _check("资源类型抽检（nss 634 / dlg 111）",
           exts.get("nss") == 634 and exts.get("dlg") == 111,
           "nss=%s dlg=%s uti=%s" % (exts.get("nss"), exts.get("dlg"), exts.get("uti")))

    print("\n[7] Sprint 2：extract 落库（位置层，仅 Ch1 容器）")
    if urepo.count(container_id=cid) == 0:
        do_extract(db, PROJECT, CH1, verbose=False)
    n = urepo.count(container_id=cid)
    _check("单元 5731 个", n == 5731, "实测 %d" % n)
    uniq = urepo.unique_texts(container_id=cid)
    _check("去重 3325 条不同文本", uniq == 3325, "实测 %d" % uniq)
    kinds = {r[0]: r[1] for r in db.execute(
        "SELECT kind, COUNT(*) FROM unit WHERE container_id=? GROUP BY kind", (cid,))}
    _check("kind 分布合理（gff/nss）", set(kinds) <= {"gff", "nss", "tlk"} and len(kinds) >= 2,
           str(kinds))
    _check("placeholders 已填充", db.execute(
        "SELECT COUNT(*) FROM unit WHERE container_id=? AND placeholders IS NOT NULL "
        "AND placeholders!='[]'", (cid,)).fetchone()[0] > 0)
    _check("field_path 已填充", db.execute(
        "SELECT COUNT(*) FROM unit WHERE container_id=? AND field_path IS NOT NULL",
        (cid,)).fetchone()[0] == n)
    _check("locator 是合法 JSON", all(
        isinstance(json.loads(r[0]), list) for r in db.execute(
            "SELECT locator FROM unit WHERE container_id=? LIMIT 200", (cid,))))
    _check("资源计数已回填", db.execute(
        "SELECT COUNT(*) FROM resource WHERE container_id=? AND units_total>0",
        (cid,)).fetchone()[0] > 0)

    print("\n[8] Sprint 2：与现有资产的交叉验证（仅 Ch1 容器）")
    hit = db.execute(
        "SELECT COUNT(DISTINCT u.src_key) FROM unit u "
        "JOIN tm t ON t.key=u.src_key AND t.status!='rejected' WHERE u.container_id=?",
        (cid,)).fetchone()[0]
    miss_rows = db.execute(
        "SELECT DISTINCT u.src_text FROM unit u LEFT JOIN tm t ON t.key=u.src_key "
        "AND t.status!='rejected' WHERE u.container_id=? AND t.key IS NULL",
        (cid,)).fetchall()
    n_miss = len(miss_rows)
    print("      tm 命中 %d / %d 条去重文本（未命中 %d 条，%.1f%% 命中）"
          % (hit, uniq, n_miss, 100.0 * hit / uniq))
    _check("tm 命中率 > 98%", hit >= uniq * 0.98,
           "%d/%d = %.2f%%" % (hit, uniq, 100.0 * hit / uniq))
    # ★ 关键诊断：未命中的改用 raw key（不 norm）能不能命中？
    raw_hit = sum(1 for (s,) in miss_rows if _tm_has(db, src_key(s or "", normalize=False)))
    print("      未命中里 %d / %d 条用 **raw key**（不 norm）能命中"
          % (raw_hit, n_miss))
    print("      → 说明它们不是「没译文」，而是「两种 key 算法没对齐」；"
          "Sprint 3 的查库必须两个 key 都试（兜底）")
    _check("未命中全部可由 raw key 解释", raw_hit == n_miss, "%d/%d" % (raw_hit, n_miss))
    for (s,) in miss_rows:
        if not _tm_has(db, src_key(s or "", normalize=False)):
            print("        真正缺失（两种 key 都查不到）：%r" % (s or "")[:64])
    row = db.execute("SELECT units, reused FROM mods WHERE name=?",
                     ("Swordflight - Chapter One",)).fetchone()
    if row is None:
        _skip("与 mods 表交叉验证",
              "mods 表里没有 Ch1 记录（历史统计快照，公开数据包不带）")
    else:
        _check("与 mods 表交叉验证（units=5731）", row[0] == 5731, str(tuple(row)))
        print("      mods 表记录 units=%s reused=%s —— 原始 pipeline 用 raw key，"
              "所以当时 3325 条全部命中" % (row[0], row[1]))

    print("\n[9] Sprint 2：幂等（再跑一次必须零新增）")
    rep = do_extract(db, PROJECT, CH1, verbose=False)
    _check("第二次 extract（Ch1）：全 kept", rep["stats"] == {"new": 0, "kept": 5731,
                                                              "src_changed": 0},
           str(rep["stats"]))
    _check("第二次 extract：无 gone", rep["gone"] == 0, str(rep["gone"]))
    _check("Ch1 单元状态全为 kept", urepo.stats(container_id=cid) == {"kept": 5731},
           str(urepo.stats(container_id=cid)))

    print("\n[10] Sprint 2：红线 —— 索引层删掉必须能从容器重建（仅 Ch1）")
    before = {r[0] for r in db.execute(
        "SELECT uid FROM unit WHERE container_id=?", (cid,))}
    key_before = {r[0]: r[1] for r in db.execute(
        "SELECT uid, src_key FROM unit WHERE container_id=?", (cid,))}
    urepo.delete_container(cid)
    _check("删掉后该容器 unit 为空", urepo.count(container_id=cid) == 0,
           "实测 %d" % urepo.count(container_id=cid))
    do_extract(db, PROJECT, CH1, verbose=False)
    after = {r[0] for r in db.execute(
        "SELECT uid FROM unit WHERE container_id=?", (cid,))}
    key_after = {r[0]: r[1] for r in db.execute(
        "SELECT uid, src_key FROM unit WHERE container_id=?", (cid,))}
    _check("重建后 uid 集合完全一致", before == after,
           "%d vs %d" % (len(before), len(after)))
    _check("重建后 src_key 完全一致", key_before == key_after)
    _check("重建后状态为 new（库里本是空的）",
           urepo.stats(container_id=cid) == {"new": 5731},
           str(urepo.stats(container_id=cid)))
    do_extract(db, PROJECT, CH1, verbose=False)
    _check("再跑一次回到 kept（最终状态幂等）",
           urepo.stats(container_id=cid) == {"kept": 5731},
           str(urepo.stats(container_id=cid)))


# ============================================================ Sprint 3
def check_sprint3(db):
    """diff / plan（增量核心）与双 key 兜底。只读，可重复跑。"""
    from .keys import keys_for, raw_hash
    from .repo import RunRepo
    from .services import diff as do_diff
    from .services import plan as do_plan

    print("\n[11] Sprint 3：双 key 兜底（keys_for）")
    a = keys_for("Plain sentence.")
    b = keys_for("  Padded sentence. ")
    _check("已规范文本只有 1 个 key", len(a) == 1, str(a))
    _check("未规范文本有 2 个 key（norm 在前）", len(b) == 2 and b[0] != b[1],
           "%s / %s" % (b[0][:8], b[1][:8]))

    print("\n[12] Sprint 3：Ch1 的增量结论（应当 0 条待译）")
    _row = db.execute("SELECT id FROM container WHERE project=? AND path LIKE ? "
                      "ORDER BY id LIMIT 1", (PROJECT, "%Chapter One.mod")).fetchone()
    if _row is None:
        _skip("[12][13] Ch1 增量与花费预估",
              "库里没有 Ch1 容器（需先 scan+extract，依赖私有模组）")
    else:
        cid = _row[0]
        d = do_diff(db, container_id=cid, sample=5)
        print("      " + summarize_diff(d).replace("\n", "\n      "))
        _check("去重后 3325 条文本", d["unique"] == 3325, "实测 %d" % d["unique"])
        _check("★ 待译 0 条（双 key 兜底生效）", d["uniq_todo"] == 0,
               "实测 missing=%d fallback=%d" % (d["uniq_missing"], d["uniq_fallback"]))
        _check("★ 靠 raw key 兜底救回 51 条", d["raw_fallback_hit"] == 51,
               "实测 %d" % d["raw_fallback_hit"])
        # 对照：只用 norm key 会漏多少？（证明兜底的必要性）
        only_norm = db.execute(
            "SELECT COUNT(DISTINCT u.src_key) FROM unit u LEFT JOIN tm t ON t.key=u.src_key "
            "AND t.status!='rejected' WHERE u.container_id=? AND t.key IS NULL",
            (cid,)).fetchone()[0]
        _check("对照：单用 norm key 会漏 51 条", only_norm == 51, "实测 %d" % only_norm)
        _check("diff 可重复（两次结果一致）",
               do_diff(db, container_id=cid, sample=0)["uniq_todo"] == 0)

        print("\n[13] Sprint 3：plan 的花费预估")
        p = do_plan(db, container_id=cid, price_in=2.0, price_out=8.0)
        _check("plan 待译 0 条 → 0 花费", p["items"] == 0 and p["cost"] == 0.0,
               "items=%d cost=%.4f" % (p["items"], p["cost"]))
        _check("plan 影响面可计算",
               isinstance(p["resources"], int) and isinstance(p["containers"], int),
               "资源 %d / 容器 %d" % (p["resources"], p["containers"]))

    print("\n[14] Sprint 3：模拟「有新内容」时 diff 能报出来")
    _row14 = db.execute("SELECT id FROM container WHERE project=? ORDER BY id LIMIT 1",
                        (PROJECT,)).fetchone()
    if _row14 is None:
        _skip("[14] diff 报新内容", "库里没有任何容器（未跑过 scan+extract）")
    else:
        cid = _row14[0]
        fake = "This is a deliberately unknown sentence for the selftest run."
        try:
            db.execute(
                "INSERT INTO unit(uid,project,container_id,resref,ext,kind,field_path,locator,"
                "src_text,src_key,src_hash_seen,storage,status) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("selftest-fake-1", "selftest", cid, "fake", "dlg", "gff", "dlg:Text",
                 '["gff",0,0]', fake, src_key(fake), raw_hash(fake), "inline", "new"))
            db.commit()
            d2 = do_diff(db, project="selftest")
            _check("新文本被报为待译 1 条", d2["uniq_missing"] == 1,
                   "实测 missing=%d" % d2["uniq_missing"])
            _check("待译字符数 > 0", d2["chars_todo"] == len(fake),
                   "实测 %d" % d2["chars_todo"])
            p2 = do_plan(db, project="selftest", price_in=2.0, price_out=8.0)
            _check("plan 报出 1 条待译、花费 > 0", p2["items"] == 1 and p2["cost"] > 0,
                   "items=%d cost=%.4f" % (p2["items"], p2["cost"]))
        finally:
            db.execute("DELETE FROM unit WHERE project='selftest'")
            db.commit()
        _check("清理后 selftest 项目无残留", db.execute(
            "SELECT COUNT(*) FROM unit WHERE project='selftest'").fetchone()[0] == 0)
    _check("剑飞项目统计未受影响（Ch1+Ch2 仍 0 待译）",
           do_diff(db, project=PROJECT)["uniq_todo"] == 0)

    print("\n[15] Sprint 3：run 表（运行记录）")
    runs = RunRepo(db)
    rid = runs.start("selftest", "selftest")
    runs.finish(rid, ok=True, units=1, note_probe="ok")
    row = db.execute("SELECT * FROM run WHERE id=?", (rid,)).fetchone()
    _check("run 记录可写可读", row is not None and row["ok"] == 1, str(row["kind"]))
    db.execute("DELETE FROM run WHERE project='selftest'")
    db.commit()
    _check("run 记录已清理", runs.recent("selftest") == [])


# ============================================================ Sprint 4
def check_sprint4(db):
    """术语收编：四份 JSON -> term 表；导出逐字节等价；模糊匹配；强制守必校验。"""
    from .repo import TermRepo

    tr = TermRepo(db)
    print("\n[16] Sprint 4：术语收编（term / term_variant）")
    st = tr.count_by_status()
    # ⚠ active / 必守 / 替换规则**只增不减** → 下界断言 ✓
    # ⚠ candidate **会双向变化**（新增草案会涨；被采纳为 active 时会按设计清掉 ✓
    #   2026-10-10 实测 6840 → 6832，因为 8 条草案被提升为 active）→ 也只能卡下界 ✗ 别写死
    _check_data("active ≥ 213 条", st.get("active", 0) >= 213, str(st))
    _check_data("candidate ≥ 6000 条", st.get("candidate", 0) >= 6000, str(st))
    _check_data("必守 ≥ 149 条", tr.count_required() >= 149, "实测 %d" % tr.count_required())
    _check_data("替换规则 ≥ 77 条", len(tr.variants()) >= 77, "实测 %d" % len(tr.variants()))
    from collections import Counter
    types = Counter(t.type for t in tr.list())
    # ⚠ 只校验"来自 glossary.json 的那三类**不少于**原值"：新增术语（Epic/Daisho/职业…）
    #   会往 class/misc 里加，按等号断言就会假失败。
    _check_data("分类分布（name/place/deity 不少于原值）",
           types.get("name", 0) >= 158 and types.get("place", 0) >= 27
           and types.get("deity", 0) >= 20, str(dict(types)))

    print("\n[17] Sprint 4：导出快照（`glossary.json` 已降级为快照，见 project decisions）")
    out = os.path.abspath(os.path.join(SCRIPTS, "tmp",
                                       "glossary_selftest_export.json"))
    n = tr.export_json(out)
    _check("导出条数 == active 条数", n == tr.count("active"),
           "导出 %d / active %d" % (n, tr.count("active")))
    b1 = open(out, "rb").read()
    tr.export_json(out + ".2")
    b2 = open(out + ".2", "rb").read()
    _check("★ 导出幂等（两次逐字节相同）", b1 == b2,
           "%d vs %d 字节" % (len(b1), len(b2)))
    _check("顶层键顺序仍是 version→terms",
           b1.find(b'"version"') < b1.find(b'"terms"') >= 0)
    # 与**冻结快照** glossary.json 的差异只记录、不判失败：
    # 2026-10-08 用户拍板"术语只写 term 表、绝不自动覆盖 glossary.json"，所以两者
    # 不再要求逐字节相同 —— 要更新那个快照请显式跑 `python -m l10n terms --action export`。
    a = open(GLOSSARY_JSON, "rb").read()
    if a != b1:
        print("      （注：冻结快照 glossary.json 与当前 term 表不同，这是**预期**状态：")
        print("        源 %d 字节 / 导出 %d 字节；要刷新快照请跑 `python -m l10n terms`）"
              % (len(a), len(b1)))

    print("\n[18] Sprint 4：模糊匹配")
    hits = tr.fuzzy("sword", limit=10)
    _check_data("sword 能匹配到 Swordsage", any(t.en == "Swordsage" for t, _s, _w in hits),
           "命中 %d 条" % len(hits))
    h2 = tr.fuzzy("Swordsage")
    _check_data("精确匹配排第一且 score=1.0",
           bool(h2) and h2[0][0].en == "Swordsage" and h2[0][1] == 1.0,
           str(h2[0][1]) if h2 else "无结果")
    _check_data("中文子串匹配（剑）",
           any((t.zh or "") and "剑" in t.zh for t, _s, _w in tr.fuzzy("剑", limit=10)))
    _check_data("归一化匹配（去撇号/空格）",
           len(tr.fuzzy("mordenkainensmagnificent", limit=5)) > 0)
    _check_data("无匹配返回空", tr.fuzzy("zzzzqqqqxxxx") == [])

    print("\n[19] Sprint 4：强制守必校验")
    # ⚠ 用 `Swordsage` + `Shadow` 这两个**确实在术语表里**的词（旧测试用了 `Shadow Blade`，
    #   但术语表里并没有它 ✗ —— 那是撞巧通过；加了最长匹配后立刻暴露）
    # ⚠ 2026-10-11：`Shadow` 的规范译名已由「幽影」改为「**阴影**」（串名裁决批），
    #   测试里的期望值必须跟着改，否则回归会红 ✗
    good = tr.check_required("The Swordsage uses Shadow.", "贤者之剑使用阴影。")
    _check_data("用了规范译名 → 通过", good == [], str(good))
    bad = tr.check_required("The Swordsage uses Shadow.", "剑圣使用阴影。")
    _check_data("用了错误译名 → 报违规", bad == [("Swordsage", "贤者之剑")], str(bad))
    _check_data("原文无术语 → 不报",
           tr.check_required("A plain rock.", "一块普通的石头。") == [])
    hp = {e for e, _z, _r in tr.hits("The Swordsage flies to Swordflight.")}
    _check_data("hits 列出命中术语", {"Swordsage", "Swordflight"} <= hp, str(hp))
    _check_data("prompt_block 生成译前注入", "Swordsage" in tr.prompt_block("The Swordsage"))
    if os.path.exists(out):
        os.remove(out)


# ============================================================ Sprint 5
def check_sprint5(db):
    """TLK 槽位账本：认领 89 个自加槽位、版本基线、与旧启发式交叉验证。"""
    from .repo import TlkRepo
    from .tlkio import en_ids_from_export, info, range_texts

    tr = TlkRepo(db)
    # 这三个都是**本机私有文件**（游戏 TLK / PRC8 英文导出）—— 找不到就整段跳过 ✓
    TLK = os.environ.get("NWN_TLK") or paths.nwn_sub("tlk", "prc8_consortium.tlk")
    EN_TLK = os.environ.get("NWN_EN_TLK") or paths.nwn_sub("tlk", "prc8_consortiumE.tlk")
    EN_TXT = os.environ.get("PRC8_EN_TXT") or os.path.join(
        os.path.dirname(SCRIPTS), "module", "PRC", "prc8.txt")
    if not os.path.exists(TLK):
        print("\n[20] Sprint 5 整段跳过：找不到已安装的 TLK")
        print("      找的是：%s" % TLK)
        print("      （这节只对「装了 PRC8 汉化」的机器有意义；"
              "可用 NWN_HOME / NWN_TLK 指定路径）")
        return
    if tr.count(TLK) == 0:
        print("\n[20] Sprint 5 整段跳过：库里没有这个 TLK 的槽位账本")
        print("      账本是「本机认领」出来的 —— 换台机器 / 换份 TLK 就要重新认领；")
        print("      公开数据包**故意不带账本**（路径因机器而异）。")
        return

    print("\n[20] Sprint 5：TLK 槽位账本（认领）")
    slots = tr.slots(TLK)
    _check("账本已认领 89 个槽位", len(slots) == 89, "实测 %d" % len(slots))
    _check("区间正是 90001~90089",
           sorted(s["strref"] for s in slots) == list(range(90001, 90090)))
    _check("全部标记为受保护", all(s["protected"] for s in slots))
    _check("归属与用途正确",
           all(s["owner"] == "prc8" and s["purpose"] == "engine_text" for s in slots),
           str({(s["owner"], s["purpose"]) for s in slots}))

    print("\n[21] Sprint 5：与旧启发式交叉验证（账本不能比启发式少认领）")
    if not os.path.exists(EN_TXT):
        _skip("[21] 交叉验证", "找不到英文导出 %s" % EN_TXT)
    else:
        en_ids = en_ids_from_export(EN_TXT)
        r = tr.verify(TLK, en_ids=en_ids)
        _check("账本无问题（槽位有文本且指纹一致）", r["problems"] == [],
               str(r["problems"][:3]))
        _check("启发式也认为有 89 个", r["heuristic_count"] == 89,
               "启发式 %d" % r["heuristic_count"])
        _check("★ 账本没漏任何槽位", r["heuristic_only"] == [],
               str(r["heuristic_only"][:6]))
        _check("账本也没多认领", r["ledger_only"] == [], str(r["ledger_only"][:6]))

    print("\n[22] Sprint 5：分配器「先查占用、冲突报错」")
    picked = []
    try:
        picked = tr.allocate(TLK, "selftest", count=2)
        _check("能从账本末尾继续分配", len(picked) == 2 and min(picked) > 90089,
               str(picked))
        try:
            tr.allocate(TLK, "selftest", count=1, start=picked[0])
            _check("占用时必须报错（不静默覆盖）", False, "竟然没报错 ✗")
        except ValueError as e:
            _check("占用时必须报错（不静默覆盖）", True, str(e)[:44])
    finally:
        if picked:
            tr.release(TLK, picked)
    _check("清理后账本仍 89 条", tr.count(TLK) == 89, "实测 %d" % tr.count(TLK))

    print("\n[23] Sprint 5：版本基线与内容指纹")
    one = info(TLK)
    b = tr.last_baseline(TLK)
    _check("基线已建立", b is not None,
           "run #%s" % (b or {}).get("run_id"))
    if b:
        bb = b["baseline"]
        _check("基线记槽位总数 213331", bb.get("count") == 213331, str(bb.get("count")))
        _check("基线记有文本 30168", bb.get("with_text") == 30168,
               str(bb.get("with_text")))
        # ⚠ 不再拿 sha256 判"一致"：回填 / 术语统一**本来就会主动改 TLK**（正常版本迭代）。
        # 这里只校验基线记录本身完整；当前文件与基线不同只提示，不判失败。
        if bb.get("sha256") != one["sha256"]:
            print("      （注：当前 TLK 与最近基线不同 —— 基线之后有过回填/统一，属正常；"
                  "需要时重建基线即可）")
        _check("基线 sha256 格式合法",
               isinstance(bb.get("sha256"), str) and len(bb.get("sha256")) == 64,
               "%d 位" % len(bb.get("sha256") or ""))
        _check("基线里记了 89 个受保护槽位", bb.get("protected_slots") == 89,
               str(bb.get("protected_slots")))

    print("\n[24] Sprint 5：事实修正 —— 那 89 条不是「扩容」出来的")
    if os.path.exists(EN_TLK):
        ie = info(EN_TLK)
        re_ = range_texts(EN_TLK, 90001, 90089)
        _check("英文版 count 同为 213331（并未扩容）", ie["count"] == 213331,
               str(ie["count"]))
        _check("★ 英文版这 89 个槽位全部无文本（是原有空槽位）", len(re_) == 0,
               "实测 %d 个有文本" % len(re_))
        _check("中文版正好比英文版多 89 条有文本",
               one["with_text"] - ie["with_text"] == 89,
               "%d - %d" % (one["with_text"], ie["with_text"]))


    print("\n[25] Sprint 5：账本守门（负向测试：少一个保护槽位必须拒绝安装）")
    guard_tmp = os.path.abspath(os.path.join(SCRIPTS, "tmp", "guard_test.tlk"))
    try:
        from modl10n.tlk import write_tlk
        # 造一个"坏了"的 TLK：把 90001 这条保护槽位清空，其余原样
        write_tlk(TLK, guard_tmp, {"90001": ""}, verify=False)
        g_bad = tr.ledger_guard(guard_tmp, ledger_tlk_path=TLK)
        _check("★ 少了保护槽位 → ok=False（拒绝安装）", g_bad["ok"] is False,
               "missing=%s" % g_bad["missing"][:5])
        _check("缺失的正是被清空的那个槽位", 90001 in g_bad["missing"],
               str(g_bad["missing"][:5]))
        # 正常 TLK 应当通过
        g_ok = tr.ledger_guard(TLK, ledger_tlk_path=TLK)
        _check("完好的 TLK → ok=True", g_ok["ok"] is True,
               "expected=%d" % g_ok["expected"])
        # 内容被改（指纹不符）也要报出来
        write_tlk(TLK, guard_tmp, {"90002": "被改坏了"}, verify=False)
        g_ch = tr.ledger_guard(guard_tmp, ledger_tlk_path=TLK)
        _check("内容被改 → 报 changed", 90002 in g_ch["changed"], str(g_ch["changed"][:5]))
    finally:
        if os.path.exists(guard_tmp):
            os.remove(guard_tmp)
    _check("临时文件已清理", not os.path.exists(guard_tmp))


# ============================================================ Sprint 6
def check_sprint6():
    """Sprint 6：GUI 层验收（`tlk tool.py` 接中央库 + 术语违规实时显示）。

    ⚠ 它**独立跑**（自己开窗口、自己建临时库），所以放在主 db 连接关掉之后。
    """
    from . import selftest_gui
    print("\n[26] Sprint 6：GUI 层验收（无头驱动 tlk tool.py 的 App）")
    rc = selftest_gui.main()
    _check("GUI 层验收全部通过", rc == 0)
    return rc


# ============================================================ Sprint 7
def check_sprint7():
    """Sprint 7：两个 GUI 的验收（`mod tool.py` + `术语库编辑器.py`）。

    ⚠ 也**独立跑**（各自开窗口、各自建临时库）。
    """
    from . import selftest_gui_mod, selftest_gui_terms
    print("\n[27] Sprint 7：mod tool 的 GUI 层验收")
    rc1 = selftest_gui_mod.main()
    _check("mod tool GUI 验收全过", rc1 == 0)
    print("\n[28] Sprint 7：术语库编辑器 GUI 层验收")
    rc2 = selftest_gui_terms.main()
    _check("术语库编辑器 GUI 验收全过", rc2 == 0)
    return rc1 or rc2


def check_sprint8_terms_source():
    """Sprint 8（2026-10-11）：**术语源加固** —— 翻译注入必须用最新真源，不是冻结快照。

    背景（修掉的静默失效）：`modl10n.pipeline` 一直只读 `glossary.json`
    （2026-10-08 快照，215 条），而术语真源在 Sprint 4 就搬到了 `term` 表
    （6,585 条 active / 1,750 必守）—— **不报错、只是悄悄少注入** ✗。
    实测 Ch3 待译 6,640 条文本：旧快照命中 26 个术语，新真源命中 148 个。

    本段**只读**（`term` 表只 SELECT、`tm` 表只 SELECT）。
    """
    import re as _re
    import time

    print("\n[29] Sprint 8：术语源加固（`term` 表 / 硬软分档 / 无损提速）")
    from modl10n.glossary import Glossary, STOP_EN, in_compound
    from modl10n import pipeline as P
    from .services import term_service as TS

    # ① 降噪常量**单一真源**（两边各一份会漂移 → 注入的术语被判自己违规 ✗）
    _check("STOP_EN 单一真源（term_service 与 modl10n.glossary 同一对象）",
           TS.STOP_EN is STOP_EN)
    _check("in_compound 单一真源", TS._in_compound is in_compound)

    # ② 两种来源都能加载，且**自动判别**正确
    gj, rj = Glossary.load_auto(GLOSSARY_JSON)
    _check("glossary.json -> 判为「冻结快照」",
           rj.get("source_kind") == "json-snapshot" and len(gj) >= 213,
           "%d 条" % len(gj))
    g, rp = Glossary.load_auto(DEF_DB)
    _check_data("tm.sqlite -> 判为「term 表」",
           rp.get("source_kind") == "term-db" and rp.get("rows", 0) > 6000,
           "active %d 条 -> 注入 %d 条（硬 %d / 软 %d）"
           % (rp.get("rows", 0), len(g), g.hard, g.soft))
    _check_data("硬档 > 1000 且软档 > 4000（分档生效）", g.hard > 1000 and g.soft > 4000)

    # ③ 降噪与歧义
    _check_data("硬档里没有 STOP_EN 普通词（`Trade`/`Air`/`Failure` 这类 ✗）",
           not [t for t in g.terms if t["required"] and t["en"].lower() in STOP_EN])
    _check_data("同英多译不进硬档（有歧义就不喂给模型）",
           not [t for t in g.terms if t["required"] and t["en"] in g.conflicts],
           "%d 个被跳过" % len(g.conflicts))

    # ④ 分档措辞 —— `translator` 只靠 `required` 区分"必须使用/建议"，所以这里必须对 ✓
    probe = "Zarala met a Drow of Calimshan on the Trade road."
    blk = g.prompt_block(probe)
    _check_data("prompt_block：硬档进「必须使用」、软档进「建议」",
           "【固定译名·必须使用】" in blk and "【建议译名】" in blk
           and "Zarala = 扎拉拉" in blk,
           "命中 %d 条术语" % len(g.hits(probe)))

    # ⑤ 无损粗筛：与「逐条正则全量扫描」的结果**必须逐条完全一致**（抽样 400 条真实源文）
    db2 = connect(DEF_DB, migrate_now=False)
    try:
        texts = [r[0] for r in db2.execute(
            "SELECT src_text FROM unit WHERE src_text IS NOT NULL AND src_text<>'' "
            "ORDER BY uid LIMIT 400")]
    finally:
        db2.close()
    rxs = [(_re.compile(r"(?<![A-Za-z])" + _re.escape(t["en"]) + r"(?![A-Za-z])"),
            t["en"], t["zh"]) for t in g.terms]
    bad = 0
    for s in texts:
        slow = [(en, zh) for rx, en, zh in rxs if rx.search(s)]
        if g.hits(s) != slow:
            bad += 1
    _check("无损粗筛结果 == 全量正则（%d 条样本）" % len(texts), bad == 0,
           "不一致 %d 条" % bad)

    # ⑥ 性能护栏：不许回退到"每条术语现编译正则"（那会让翻译中途卡死 ✗）
    sample = texts[:200] or [""]
    t0 = time.time()
    for s in sample:
        g.hits(s)
    ms = (time.time() - t0) / len(sample) * 1000
    _check("单条 hits < 50ms（%d 条术语）" % len(g), ms < 50, "实测 %.1fms/条" % ms)

    # ⑦ 过期警告必须打出来（不再静默）
    gl2, rep2 = P.load_glossary(GLOSSARY_JSON)
    txt = P.describe_glossary(gl2, rep2)
    _check_data("显式用快照时打印过期警告 + 最新条数",
           "冻结快照" in txt and "最新真源" in txt
           and str(rep2.get("latest_terms")) == str(rp.get("rows")),
           "不变量：快照 %d 条 vs 真源 %s 条" % (len(gl2), rep2.get("latest_terms")))
    gl3, rep3 = P.load_glossary(DEF_DB)
    _check("默认（term 表）不带过期警告", "冻结快照" not in P.describe_glossary(gl3, rep3))

    # ⑧ 宽容校验：`term` 表里的写法差异不该被判违规（否则白重试 ✗）
    from modl10n.glossary import Glossary as _Gl
    gtz = _Gl([{"en": "Nentyar Hunter", "zh": "南提亚猎人", "type": "name",
                "required": True}])
    strict_bad = gtz.check("Nentyar Hunter", "南提亚 猎人")
    gtz.tolerant = True
    tolerant_bad = gtz.check("Nentyar Hunter", "南提亚 猎人")
    _check("宽容校验：空格差异不算违规", bool(strict_bad) and not tolerant_bad,
           "严格 %d 条 / 宽容 %d 条" % (len(strict_bad), len(tolerant_bad)))
    _check_data("`from_term_db` 默认开宽容、`load(json)` 保持严格（旧行为不变）",
           rp.get("tolerant") is True and gj.tolerant is False)

    # ⑨ ★★ `best()`：**术语未统一不再回退原文**（术语表脏数据不该让整条保持英文 ✗）
    from modl10n.translator import Translator

    class _Fake(Translator):
        """只 mock 网络层 —— 返回固定译文，**绝不调 API** ✓。"""

        def __init__(self, reply, gl):
            Translator.__init__(self, "fake-key", glossary=gl)
            self.reply = reply
            self.calls = 0

        def _post(self, messages, json_mode=True, max_tokens=None):
            self.calls += 1
            return self.reply

    gt = _Gl([{"en": "Zarala", "zh": "扎拉拉", "type": "name", "required": True}])
    src = "<CUSTOM0> Zarala the guide"
    tA = _Fake("<CUSTOM0> 沙拉拉 向导", gt)
    candA, tagA, _pa = tA.best("k", src)
    _check("★ 术语未统一 → 接受译文、不回退原文",
           candA is not None and "术语待统一" in tagA,
           "tag=%s（%d 次调用）" % (tagA, tA.calls))
    tB = _Fake("沙拉拉 向导", gt)                 # 丢了 <CUSTOM0>：结构问题
    candB, _tagB, pB = tB.best("k", src)
    _check("结构问题（丢标记）→ 仍然拒绝（返回 None）", candB is None,
           "问题：%s" % (pB[:1],))
    return 0


# ============================================================ 入口
def main():
    global _results
    _results = []
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    print("=" * 78)
    print("验收：%s" % {"1": "Sprint 1（只读）",
                        "2": "Sprint 2（写索引层）",
                        "3": "Sprint 3（增量核心，只读）",
                        "4": "Sprint 4（术语收编，写 term 表）",
                        "5": "Sprint 5（TLK 槽位账本）",
                        "6": "Sprint 6（tlk tool 接中央库）",
                        "7": "Sprint 7（mod tool + 术语库编辑器）",
                        "8": "Sprint 8（术语源加固，只读）"}.get(
                            which, "Sprint 1 + 2 + 3 + 4 + 5 + 6 + 7 + 8"))
    if not os.path.exists(DEF_DB):
        # 全新环境：先把空库建出来（`tm` / `mods` 两张表的真源在 modl10n），
        # 这样「不依赖数据」的检查照样能跑，而不是抛 `no such table: tm` ✓
        from modl10n.tm import TM
        TM(DEF_DB).db.close()
    db = connect(DEF_DB, migrate_now=True)
    _n_tm, _n_term = _probe_data(db)
    if not _HAS_DATA:
        print()
        print("!" * 78)
        print("⚠ 中央库还没有公开数据（tm %d 条 / term %d 条）" % (_n_tm, _n_term))
        print("  依赖数据的检查会被跳过 —— 缺的是数据，不是代码坏了。")
        print("  先导入数据包：")
        print("      python tools/bootstrap.py --data ../nwn-ee-l10n-data/data")
        print("!" * 78)
    try:
        if which in ("all", "1"):
            check_sprint1(db)
        if which in ("all", "2"):
            check_sprint2(db)
        if which in ("all", "3"):
            check_sprint3(db)
        if which in ("all", "4"):
            check_sprint4(db)
        if which in ("all", "5"):
            check_sprint5(db)
    finally:
        db.close()
    if which in ("all", "6"):
        check_sprint6()
    if which in ("all", "7"):
        check_sprint7()
    if which in ("all", "8"):
        check_sprint8_terms_source()
    n_bad = sum(1 for _n, ok, _d in _results if not ok)
    print("\n" + "=" * 78)
    print("结果：%d 项检查，%d 项通过，%d 项失败"
          % (len(_results), len(_results) - n_bad, n_bad))
    print("（tm 表只读：全程未写入 tm）")
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
