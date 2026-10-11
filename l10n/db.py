#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中央库：连接、幂等迁移、事务助手、自检。

Sprint 0 的产物。三条纪律（对应方案第二节的红线）：

  1. **不新建库**：直接升级现有 `modl10n\\tm.sqlite`，只 `CREATE TABLE`，
     **绝不 ALTER / 重建 `tm` 与 `mods`**（43,942 条译文资产在里面）。
  2. **幂等**：迁移用 `PRAGMA user_version` 管版本，重复执行不报错、不丢数据。
  3. **可回滚**：本模块建的全部是"索引层"表，删掉它们不影响任何既有产物
     （`DROP TABLE unit; ...` 即可回到 Sprint 0 之前）。

用法：
    python -m l10n.db --check          # 自检并打印报告（Sprint 0 的验收命令）
    python -m l10n.db --migrate        # 只做迁移（幂等，可重复跑）
"""
import os
import sys
import sqlite3
import datetime
import threading
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)

# 中央库 = 现有的翻译记忆库（文件名保持不动：至少 10 处脚本写死了它）
DEF_DB = os.path.join(SCRIPTS, "modl10n", "tm.sqlite")

# 当前 schema 版本（写进 PRAGMA user_version）
#   v1 -> v2：`unit` 表补 `strref` 列（用户要求 StringUnit 必须含 StrRef；v1 漏了）
#   v2 -> v3：`term` 表补 `seq` 列（导出 glossary.json 要求「逐字节等价」，必须保住原顺序）
SCHEMA_VERSION = 3

# 本模块负责建的表（用于自检时区分"该有的"与"别的模块/历史遗留的"）
TABLES_V1 = [
    "project", "container", "resource", "unit", "unit_term",
    "term", "term_variant", "tlk_slot", "run", "change_log",
]
VIEWS_V1 = ["v_unit_todo", "v_term_violation"]

# 由 modl10n\\tm.py 负责的表 —— 本模块只看不建、绝不改
FOREIGN_TABLES = ["tm", "mods"]


# ============================================================ schema（v1）
SCHEMA_V1 = """
-- ============ 1. 项目 ============
CREATE TABLE IF NOT EXISTS project (
    id        TEXT PRIMARY KEY,          -- swordflight / prc8 / 全局
    title     TEXT,
    kind      TEXT,                      -- mod | tlk | mixed
    root      TEXT,                      -- 工作目录
    outdir    TEXT,                      -- 默认产物目录
    note      TEXT,
    updated   TEXT
);

-- ============ 2. 容器（物理文件） ============
CREATE TABLE IF NOT EXISTS container (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    project   TEXT NOT NULL,
    role      TEXT NOT NULL,             -- source（原件）| output（产物）| installed（已安装）
    path      TEXT NOT NULL,             -- 绝对路径
    fmt       TEXT NOT NULL,             -- mod | tlk | hak
    size      INTEGER,
    sha256    TEXT,
    mtime     TEXT,
    scanned   TEXT,
    UNIQUE(project, role, path)
);

-- ============ 3. 资源 ============
CREATE TABLE IF NOT EXISTS resource (
    rid           TEXT PRIMARY KEY,
    container_id  INTEGER NOT NULL REFERENCES container(id) ON DELETE CASCADE,
    resref        TEXT NOT NULL,
    ext           TEXT NOT NULL,
    size          INTEGER,
    sha256        TEXT,
    gff_type      TEXT,
    deps          TEXT,                  -- JSON
    units_total   INTEGER DEFAULT 0,
    units_done    INTEGER DEFAULT 0,
    change_state  TEXT DEFAULT 'new',    -- new | unchanged | modified | removed
    first_seen    TEXT,
    last_seen     TEXT,
    UNIQUE(container_id, resref, ext)
);

-- ============ 4. 单元（StringUnit 的位置层） ============
-- 注意：这里**不存译文** —— 译文只在 tm 表里，本表存 src_key 指过去。
CREATE TABLE IF NOT EXISTS unit (
    uid           TEXT PRIMARY KEY,
    project       TEXT NOT NULL,
    container_id  INTEGER NOT NULL REFERENCES container(id) ON DELETE CASCADE,
    resref        TEXT NOT NULL,
    ext           TEXT NOT NULL,
    kind          TEXT NOT NULL,         -- gff | nss | tlk
    field_path    TEXT,                  -- 给人看的定位
    locator       TEXT NOT NULL,         -- 给机器回写用的定位（JSON）
    strref        INTEGER,               -- GFF/TLK 原有的 strref（.nss 单元为 NULL）
    src_text      TEXT NOT NULL,
    src_key       TEXT NOT NULL,         -- -> tm.key
    src_hash_seen TEXT,                  -- 源文上次快照（判变更用）
    storage       TEXT DEFAULT 'inline', -- inline | tlk_strref | tlk_slot
    context       TEXT,
    placeholders  TEXT,                  -- JSON
    term_hits     TEXT,                  -- JSON
    status        TEXT DEFAULT 'new',    -- new|kept|src_changed|gone|skipped|ignored
    first_seen    TEXT,
    last_seen     TEXT
);
CREATE INDEX IF NOT EXISTS idx_unit_key  ON unit(src_key);
CREATE INDEX IF NOT EXISTS idx_unit_res  ON unit(container_id, resref, ext);
CREATE INDEX IF NOT EXISTS idx_unit_st   ON unit(project, status);
CREATE INDEX IF NOT EXISTS idx_unit_stor ON unit(storage);
CREATE INDEX IF NOT EXISTS idx_unit_res2 ON unit(resref, ext);

-- ============ 5. 单元 <-> 术语 命中（多对多） ============
CREATE TABLE IF NOT EXISTS unit_term (
    uid      TEXT NOT NULL,
    en       TEXT NOT NULL,
    zh       TEXT NOT NULL,
    required INTEGER DEFAULT 1,
    src      TEXT DEFAULT 'glossary',    -- glossary | draft | manual
    PRIMARY KEY (uid, en)
);
CREATE INDEX IF NOT EXISTS idx_ut_en ON unit_term(en);

-- ============ 6. 术语（合并 glossary + overrides + 草案） ============
-- 同一英文允许多行：status='candidate' 的多个候选译名（草案池）与
-- status='active' 的生效译名共存。
CREATE TABLE IF NOT EXISTS term (
    en       TEXT NOT NULL,
    zh       TEXT NOT NULL,
    type     TEXT DEFAULT 'name',        -- name|place|deity|class|spell|item|misc
    required INTEGER DEFAULT 1,
    status   TEXT DEFAULT 'active',      -- active | candidate | rejected
    scope    TEXT DEFAULT '*',           -- * 或项目名（"只在这些范围生效"）
    src      TEXT,                       -- glossary|override|draft|manual（溯源）
    note     TEXT,
    updated  TEXT,
    seq      INTEGER,                    -- 导入顺序：导出 glossary.json 要「逐字节等价」
    PRIMARY KEY (en, zh, status)
);
CREATE INDEX IF NOT EXISTS idx_term_en     ON term(en);
CREATE INDEX IF NOT EXISTS idx_term_status ON term(status, required);

-- ============ 7. 译名替换规则（原 term_variants.json，实测 77 条） ============
CREATE TABLE IF NOT EXISTS term_variant (
    old      TEXT NOT NULL,
    new      TEXT NOT NULL,
    guard_en TEXT,                       -- 英文护栏：原文含此串才替换
    scope    TEXT DEFAULT '*',
    note     TEXT,
    PRIMARY KEY (old, new)
);

-- ============ 8. TLK 槽位账本（专治 ID 冲突） ============
CREATE TABLE IF NOT EXISTS tlk_slot (
    tlk_path  TEXT NOT NULL,
    strref    INTEGER NOT NULL,
    owner     TEXT NOT NULL,
    purpose   TEXT DEFAULT 'reserved',   -- engine_text | routed | reserved
    text_key  TEXT,                      -- 内容哈希 -> tm.key
    protected INTEGER DEFAULT 1,         -- 1 = 重建时绝不能清
    note      TEXT,
    updated   TEXT,
    PRIMARY KEY (tlk_path, strref)
);
CREATE INDEX IF NOT EXISTS idx_slot_owner ON tlk_slot(owner, purpose);

-- ============ 9. 运行记录（取代散落的 report.json） ============
CREATE TABLE IF NOT EXISTS run (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    project   TEXT,
    kind      TEXT,                      -- scan|extract|translate|apply|pack|verify|unify|rename
    started   TEXT,
    finished  TEXT,
    args      TEXT,                      -- JSON
    units     INTEGER,
    uniq      INTEGER,
    reused    INTEGER,
    new_tr    INTEGER,
    api_calls INTEGER,
    cost_est  REAL,
    ok        INTEGER,
    summary   TEXT                       -- JSON
);

-- ============ 10. 变更审计（支撑"影响面预览"与回滚） ============
CREATE TABLE IF NOT EXISTS change_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT,
    actor      TEXT,                     -- gui:mod-editor | cli:rename | pipeline
    table_name TEXT,
    pk         TEXT,
    field      TEXT,
    old        TEXT,
    new        TEXT,
    run_id     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cl_ts ON change_log(ts);
"""

# 视图单独一段：必须建在依赖的表之后
VIEWS_V1_SQL = """
-- (1) 待办清单：哪些单元还没译文 / 源文变了（GUI 左栏数据源）
DROP VIEW IF EXISTS v_unit_todo;
CREATE VIEW v_unit_todo AS
SELECT u.uid, u.project, u.container_id, u.resref, u.ext, u.field_path,
       u.src_text, u.src_key, t.dst AS dst, u.status, u.storage,
       u.term_hits, u.placeholders,
       (t.key IS NOT NULL) AS has_dst
FROM unit u
LEFT JOIN tm t ON t.key = u.src_key AND t.status != 'rejected';

-- (2) ★ 术语违规全库检查：原文命中【必守】术语、但译文没用规范译名。
--     因为 TLK 与 .mod 的单元都在同一张 unit 表里，这一条 SQL
--     就把"术语库实时检查 TLK 和 MOD"变成了可能。
DROP VIEW IF EXISTS v_term_violation;
CREATE VIEW v_term_violation AS
SELECT u.project, u.resref, u.ext, u.field_path, ut.en, ut.zh,
       1 AS required_hit, t.dst
FROM unit u
JOIN unit_term ut ON ut.uid = u.uid AND ut.required = 1
LEFT JOIN tm t ON t.key = u.src_key
WHERE t.dst IS NOT NULL AND instr(t.dst, ut.zh) = 0;
"""


# ============================================================ 基础工具
def now():
    """统一的时间戳格式（各表都用它，便于字符串排序）。"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect(path=None, migrate_now=True, timeout=30):
    """打开中央库。默认顺手做一次幂等迁移（可关掉，用于纯只读场景）。

    与 modl10n\\tm.py 保持同样的连接参数：check_same_thread=False + WAL。
    """
    p = os.path.abspath(path or DEF_DB)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    db = sqlite3.connect(p, check_same_thread=False, timeout=timeout)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    if migrate_now:
        migrate(db)
    return db


def table_names(db):
    """库里所有的**表**名（不含视图、不含 sqlite_ 内部表）。"""
    return {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def view_names(db):
    return {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='view'")}


def column_names(db, table):
    """某张表的列名集合 —— 迁移时用来判断"这一列补过没有"。"""
    return {r[1] for r in db.execute("PRAGMA table_info(%s)" % table)}


_MIGRATE_LOCK = threading.Lock()


def migrate(db, verbose=False):
    """幂等迁移（**加模块级锁串行**，原因见 `_migrate_impl` 的说明）。"""
    with _MIGRATE_LOCK:
        return _migrate_impl(db, verbose)


def _migrate_impl(db, verbose=False):
    """幂等迁移：把库升级到最新 schema。可重复执行。

    只做 `CREATE TABLE IF NOT EXISTS` + `CREATE VIEW`，**不碰 tm / mods**。
    返回 dict：from / to / created / altered / existed / tables / views / tm_rows

    ⚠ **必须串行**：视图那段是 `DROP VIEW` + `CREATE VIEW` 两条语句，两个连接/线程
    同时迁移会报 `view v_unit_todo already exists`（2026-10-08 实测踩过：GUI 的
    后台"术语体检"线程与主线程同时首次开库）。所以外层 `migrate()` 加了锁。
    """
    before = int(db.execute("PRAGMA user_version").fetchone()[0])
    had = table_names(db)

    db.executescript(SCHEMA_V1)      # 建表（含索引）
    # 视图依赖 tm 表：若库里还没有 tm（全新库），跳过视图，等 tm.py 建好表再迁移
    if "tm" in table_names(db):
        db.executescript(VIEWS_V1_SQL)
    db.commit()

    created = sorted(table_names(db) - had)

    # ---- v1 -> v2：老库的 `unit` 表缺 `strref` 列
    #      （`CREATE TABLE IF NOT EXISTS` 不会修改已存在的表，必须显式补列）
    altered = []
    if "unit" in table_names(db) and "strref" not in column_names(db, "unit"):
        db.execute("ALTER TABLE unit ADD COLUMN strref INTEGER")
        altered.append("unit.strref")
    # ---- v2 -> v3：`term` 表补 `seq` 列（保住 glossary.json 的原始顺序）
    if "term" in table_names(db) and "seq" not in column_names(db, "term"):
        db.execute("ALTER TABLE term ADD COLUMN seq INTEGER")
        altered.append("term.seq")
    db.commit()

    if before < SCHEMA_VERSION:
        # PRAGMA 不支持参数绑定；SCHEMA_VERSION 是模块内常量，无注入风险
        db.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
        db.commit()

    info = {
        "from": before,
        "to": int(db.execute("PRAGMA user_version").fetchone()[0]),
        "created": created,
        "altered": altered,
        "existed": sorted(had),
        "tables": sorted(table_names(db)),
        "views": sorted(view_names(db)),
        "tm_rows": (db.execute("SELECT COUNT(*) FROM tm").fetchone()[0]
                    if "tm" in table_names(db) else None),
    }
    if verbose:
        if created:
            print("  新建：%s" % ", ".join(created))
        if altered:
            print("  补列：%s" % ", ".join(altered))
        print("  schema 版本 %d -> %d；表 %d 张、视图 %d 个；tm 现有 %s 条"
              % (info["from"], info["to"], len(info["tables"]),
                 len(info["views"]), info["tm_rows"]))
    return info


@contextmanager
def tx(db):
    """事务助手：with tx(db): ... —— 出错自动回滚。"""
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise


# ============================================================ 自检
def check(path=None):
    """Sprint 0 的验收命令：打印中央库现状。

    期望：新增 10 张表全在、2 个视图在、tm 条数不变、integrity_check = ok。
    """
    p = os.path.abspath(path or DEF_DB)
    print("=" * 74)
    print("中央库自检：%s" % p)
    if not os.path.exists(p):
        print("  ✗ 文件不存在"); return 1
    print("  大小 %d 字节（%.1f MB）" % (os.path.getsize(p), os.path.getsize(p) / 1048576.0))

    db = connect(p, migrate_now=False)
    try:
        ver = int(db.execute("PRAGMA user_version").fetchone()[0])
        tables, views = table_names(db), view_names(db)
        print("  schema 版本：%d（本模块期望 %d）" % (ver, SCHEMA_VERSION))

        print("\n  [本模块负责的表 %d 张]" % len(TABLES_V1))
        missing = []
        for t in TABLES_V1:
            n = db.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] if t in tables else None
            if t not in tables:
                missing.append(t)
            print("    %-14s %s" % (t, "缺失 ✗" if n is None else "%d 行" % n))

        print("\n  [视图 %d 个]" % len(VIEWS_V1))
        for v in VIEWS_V1:
            print("    %-18s %s" % (v, "在 ✓" if v in views else "缺失 ✗"))

        print("\n  [沿用 modl10n\\tm.py 的表（本模块只看不改）]")
        for t in FOREIGN_TABLES:
            if t in tables:
                print("    %-14s %d 行" % (t, db.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]))
            else:
                print("    %-14s 不存在 ✗" % t)
        if "tm" in tables:
            st = db.execute("SELECT status, COUNT(*) FROM tm GROUP BY status").fetchall()
            print("    tm 状态分布：%s" % ", ".join("%s=%d" % (r[0], r[1]) for r in st))

        others = sorted(tables - set(TABLES_V1) - set(FOREIGN_TABLES))
        if others:
            print("\n  [其它历史表] %s" % ", ".join(others))

        integ = db.execute("PRAGMA integrity_check").fetchone()[0]
        fk = db.execute("PRAGMA foreign_key_check").fetchall()
        print("\n  integrity_check：%s" % integ)
        print("  foreign_key_check：%s" % ("无问题 ✓" if not fk else "%d 处 ✗" % len(fk)))
        bad = (missing or [v for v in VIEWS_V1 if v not in views]
               or integ != "ok" or fk or ver != SCHEMA_VERSION)
        print("\n  %s" % ("✗ 自检未通过" if bad else "✅ 自检通过：索引层就位，tm 未受影响"))
        return 1 if bad else 0
    finally:
        db.close()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    db_path = None
    if "--db" in argv:
        db_path = argv[argv.index("--db") + 1]
    if "--check" in argv or not argv:
        return check(db_path)
    if "--migrate" in argv:
        print("=" * 74)
        print("迁移中央库：%s" % os.path.abspath(db_path or DEF_DB))
        db = connect(db_path, migrate_now=False)
        try:
            migrate(db, verbose=True)
        finally:
            db.close()
        print("  ✅ 迁移完成（幂等，可重复执行）")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
