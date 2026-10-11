CREATE VIEW v_term_violation AS
SELECT u.project, u.resref, u.ext, u.field_path, ut.en, ut.zh,
       1 AS required_hit, t.dst
FROM unit u
JOIN unit_term ut ON ut.uid = u.uid AND ut.required = 1
LEFT JOIN tm t ON t.key = u.src_key
WHERE t.dst IS NOT NULL AND instr(t.dst, ut.zh) = 0;
CREATE VIEW v_unit_todo AS
SELECT u.uid, u.project, u.container_id, u.resref, u.ext, u.field_path,
       u.src_text, u.src_key, t.dst AS dst, u.status, u.storage,
       u.term_hits, u.placeholders,
       (t.key IS NOT NULL) AS has_dst
FROM unit u
LEFT JOIN tm t ON t.key = u.src_key AND t.status != 'rejected';
CREATE TABLE _fixmark(t TEXT);
CREATE TABLE change_log (
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
CREATE TABLE container (
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
CREATE TABLE mods (
    name     TEXT PRIMARY KEY,
    path     TEXT,
    units    INTEGER,
    new_tr   INTEGER,
    reused   INTEGER,
    updated  TEXT
);
CREATE TABLE project (
    id        TEXT PRIMARY KEY,          -- swordflight / prc8 / 全局
    title     TEXT,
    kind      TEXT,                      -- mod | tlk | mixed
    root      TEXT,                      -- 工作目录
    outdir    TEXT,                      -- 默认产物目录
    note      TEXT,
    updated   TEXT
);
CREATE TABLE resource (
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
CREATE TABLE run (
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
CREATE TABLE term (
    en       TEXT NOT NULL,
    zh       TEXT NOT NULL,
    type     TEXT DEFAULT 'name',        -- name|place|deity|class|spell|item|misc
    required INTEGER DEFAULT 1,
    status   TEXT DEFAULT 'active',      -- active | candidate | rejected
    scope    TEXT DEFAULT '*',           -- * 或项目名（"只在这些范围生效"）
    src      TEXT,                       -- glossary|override|draft|manual（溯源）
    note     TEXT,
    updated  TEXT, seq INTEGER,
    PRIMARY KEY (en, zh, status)
);
CREATE TABLE term_variant (
    old      TEXT NOT NULL,
    new      TEXT NOT NULL,
    guard_en TEXT,                       -- 英文护栏：原文含此串才替换
    scope    TEXT DEFAULT '*',
    note     TEXT,
    PRIMARY KEY (old, new)
);
CREATE TABLE tlk_slot (
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
CREATE TABLE tm (
    key      TEXT PRIMARY KEY,
    src      TEXT NOT NULL,
    dst      TEXT NOT NULL,
    src_mod  TEXT,
    status   TEXT NOT NULL DEFAULT 'auto',   -- auto | manual | rejected
    note     TEXT,
    updated  TEXT
);
CREATE TABLE unit (
    uid           TEXT PRIMARY KEY,
    project       TEXT NOT NULL,
    container_id  INTEGER NOT NULL REFERENCES container(id) ON DELETE CASCADE,
    resref        TEXT NOT NULL,
    ext           TEXT NOT NULL,
    kind          TEXT NOT NULL,         -- gff | nss | tlk
    field_path    TEXT,                  -- 给人看的定位
    locator       TEXT NOT NULL,         -- 给机器回写用的定位（JSON）
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
, strref INTEGER);
CREATE TABLE unit_term (
    uid      TEXT NOT NULL,
    en       TEXT NOT NULL,
    zh       TEXT NOT NULL,
    required INTEGER DEFAULT 1,
    src      TEXT DEFAULT 'glossary',    -- glossary | draft | manual
    PRIMARY KEY (uid, en)
);
CREATE INDEX idx_cl_ts ON change_log(ts);
CREATE INDEX idx_slot_owner ON tlk_slot(owner, purpose);
CREATE INDEX idx_term_en     ON term(en);
CREATE INDEX idx_term_status ON term(status, required);
CREATE INDEX idx_tm_src ON tm(src);
CREATE INDEX idx_unit_key  ON unit(src_key);
CREATE INDEX idx_unit_res  ON unit(container_id, resref, ext);
CREATE INDEX idx_unit_res2 ON unit(resref, ext);
CREATE INDEX idx_unit_st   ON unit(project, status);
CREATE INDEX idx_unit_stor ON unit(storage);
CREATE INDEX idx_ut_en ON unit_term(en);
