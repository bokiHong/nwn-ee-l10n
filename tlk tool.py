#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================
 TLK Tool  ——  无冬之夜(NWN2) TLK 汉化工具（图形界面 + 命令行）
==============================================================

功能：
  * 打开 / 保存 TLK，**直接在界面里逐条查看和修改**（一次显示全部条目）
  * 标点一键转中文(全角) / 转英文(半角)
  * 导出成 txt、从 txt 导入
  * 调用 DeepSeek API 翻译"还没翻译"的条目

用法：
  * 双击本文件 → 打开图形界面（推荐）
  * 命令行：
      python "tlk tool.py" info      <tlk文件>
      python "tlk tool.py" export    <tlk文件> <输出txt>
      python "tlk tool.py" import    <输入txt> <输出tlk>
      python "tlk tool.py" translate <txt文件> [--key=sk-xxx] [--all] [--tm=记忆库路径]
      python "tlk tool.py" update    <新版tlk> <输出tlk> [--dry-run] [--tm=记忆库路径]
                                      ↑ 一键增量汉化：先吃记忆库（0 花费），只翻没命中的

**翻译记忆库**：默认用 `modl10n\tm.sqlite`，按【英文原文内容哈希】存译文，
所以 PRC 更新后凡是没变的字符串都能 0 花费复用，只翻真正新增/改动的。
（老式"按 strref 序号"的缓存做不到这点：源文改了会静默吐旧译文，索引位移会整体错位。）

TLK 结构 = 20字节头部 + count×40字节索引表 + 连续字符串数据区（无长度前缀）。
全程按 UTF-8 处理，不会像某些编辑器那样把编码写坏。
"""
import json
import os
import random
import re
import sys
import threading
import time

# Sprint 6：业务逻辑已下沉到 `l10n` 包。
# 本文件只保留：①调 service 的薄封装（**同名保留**，因为 rebuild_tlk_safe.py 等
# 6 个外部脚本靠 importlib 加载本文件并调用 load_tlk/save_tlk）②界面。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from l10n import textops, tlkio, txtio                          # noqa: E402
from l10n.db import DEF_DB, connect as db_connect               # noqa: E402
from l10n.repo import TermRepo, TlkRepo, TmRepo                 # noqa: E402
from l10n.services import (term_editor_service, term_service, tlk_fill_service,   # noqa: E402
                           tlk_service)

# ============================================================ TLK 读写
# ============================================================ TLK 读写（薄封装 → l10n.tlkio）
# ⚠ 这三个名字**必须保留**：`rebuild_tlk_safe.py`、`_tlk_probe.py`、`_patch*.py` 等
#   6 个脚本用 importlib 加载本文件并直接调 `load_tlk` / `save_tlk`。
def read_tlk_raw(path):
    """[已下沉到 `l10n.tlkio.read_raw_entries`] 读 TLK 头 + 索引表。"""
    return tlkio.read_raw_entries(path)


def load_tlk(path):
    """[已下沉到 `l10n.tlkio.load`] 读整份 TLK -> dict。

    默认 `decode="per-item"` —— **逐条**先试 UTF-8、失败才回退 CP1252，
    与旧实现逐字等价（已实测：英文版 30079 条、中文版 30168 条逐条相同）。
    """
    return tlkio.load(path)


def save_tlk(path, lang, count, texts):
    """[已下沉到 `l10n.tlkio.write_texts`] 整体重写 TLK（与旧实现字节级一致）。"""
    return tlkio.write_texts(path, lang, count, texts)


# ============================================================ txt 读写
def parse_txt(data):
    meta, entries, cur, parts = {}, [], None, []

    def flush():
        if cur is not None:
            entries.append((cur, "".join(parts)))

    for line in data.splitlines(keepends=True):
        cc = line[:-2] if line.endswith("\r\n") else line
        if cc.startswith("#//"):
            m = re.search(r"TLKCount:\s*(\d+)", cc)
            if m:
                meta["count"] = int(m.group(1))
            m = re.search(r"LanguageID:\s*(\d+)", cc)
            if m:
                meta["lang"] = int(m.group(1))
            m = re.search(r"Total Item = \[(\d+)/(\d+)\]", cc)
            if m and "count" not in meta:
                meta["count"] = int(m.group(2))
            continue
        m = re.match(r"^(\d+) := (.*)$", cc)
        if m:
            flush()
            cur, parts = m.group(1), [m.group(2) + ("\r\n" if line.endswith("\r\n") else "")]
        elif cur is not None:
            parts.append(line)
    flush()
    return meta, entries


def clean(s):
    if s.endswith("\r\n"):
        s = s[:-2]
    elif s.endswith("\n"):
        s = s[:-1]
    return s.replace("\r\n", "\n")


def to_txt(s):
    return s.replace("\r\n", "\n").replace("\n", "\r\n")


def build_txt(meta, entries, src_path):
    lines = ["#// Exported by TLK Tool\n",
             "#// File Path: '%s'\n" % src_path,
             "#// Language: %d, Total Item = [%d/%d]\n"
             % (meta.get("lang", 0), len(entries), meta.get("count", 0)),
             "#// TLKCount: %d\n" % meta.get("count", 0),
             "#// LanguageID: %d\n" % meta.get("lang", 0),
             "#// \n"]
    for eid, text in entries:
        lines.append("%s := %s\r\n" % (eid, to_txt(text)))
    return "".join(lines)


# ============================================================ 标点处理
# ============================================================ 标点处理（薄封装 → l10n.textops）
# 整套半角/全角转换逻辑（含 `<...>` 标签跳过、数字与占位符保护、中文语境判定）已搬到
# `l10n/textops.py`，GUI 与 CLI 共用一份。这里只留同名薄封装。
def to_cn_punct(text):
    """[已下沉到 `l10n.textops.to_cn_punct`] 半角标点 -> 中文全角。"""
    return textops.to_cn_punct(text)


def to_en_punct(text):
    """[已下沉到 `l10n.textops.to_en_punct`] 中文全角 -> 英文半角。"""
    return textops.to_en_punct(text)


# ============================================================ AI 翻译
SYSTEM_PROMPT = (
    "你是专业的游戏本地化翻译，负责把《无冬之夜》(Neverwinter Nights) 的英文文本翻译成简体中文。\n"
    "必须严格遵守：\n"
    "1. 保留所有占位符、标记和数字，原样不动，例如 <CUSTOM0>、%(NAME)、%(SL)、1d6、+2、DC 15。\n"
    "   尖括号标签必须逐字节原样保留：<CUSTOM0>、<spellname>、</c>、以及颜色标签 <c\u00ff\u00ff\u00ff>、<c'' > 等。\n"
    "2. 保留原文换行结构，不要合并或拆分行。\n"
    "3. 标点使用中文全角标点(。，、：；（）！？“”)，不要用英文半角标点。\n"
    "4. 技术标识(如 == ac reserved ==、###_XXX_###、PDK 这类缩写)保持原样，不要翻译。\n"
    "5. 术语用常见简体中文奇幻/D&D 译名；人名地名优先音译。\n"
    "6. 只输出翻译结果本身，不要任何解释。"
)

def normalize(s):
    """[已下沉到 `l10n.textops.normalize`] 写记忆库前的译文清洗。

    ⚠ 与 `l10n.keys.norm` 不是一回事：那个是"翻译记忆的 key 规范化"（统一换行 + 去首尾空白）。
    """
    return textops.normalize(s)


class FatalAPIError(RuntimeError):
    """认证/权限类错误：重试与逐条兜底都没有意义，必须立刻中止。"""


def _post(cfg, messages, json_mode):
    import requests
    url = cfg["base"].rstrip("/") + "/chat/completions"
    body = {"model": cfg["model"], "messages": messages, "temperature": 0.2,
            "stream": False, "max_tokens": 8192}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": "Bearer " + cfg["key"], "Content-Type": "application/json"}
    last = None
    for attempt in range(6):
        fatal = None
        try:
            r = requests.post(url, headers=headers, json=body, timeout=600)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            if r.status_code in (401, 403):
                # 认证问题重试再多次也没用，直接失败（否则每批要白等 63 秒退避）
                fatal = "API Key 无效或无权限 (HTTP %d)，已停止重试" % r.status_code
            else:
                last = "HTTP %d %s" % (r.status_code, r.text[:200])
        except Exception as e:
            last = repr(e)
        if fatal:
            raise FatalAPIError(fatal)
        if attempt < 5:
            time.sleep(min(60, 2 ** attempt) + random.uniform(0, 1))
    raise RuntimeError("API 调用失败: %s" % last)


def _extract_json(t):
    t = t.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return json.loads(t)


def _translate_one(cfg, text):
    msgs = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Translate the following game text into "
                                        "Simplified Chinese. Output only the translation:\n\n" + text}]
    return _post(cfg, msgs, False).strip()


def translate_items(cfg, items):
    """批量翻译；模型若原样返回则自动单条重译。"""
    payload = json.dumps({k: v for k, v in items}, ensure_ascii=False)
    user = ("Translate each value of the following JSON object into Simplified Chinese "
            "(keep the keys unchanged) and return the same JSON object:\n" + payload)
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
    res = {}
    for _ in range(4):
        try:
            data = _extract_json(_post(cfg, msgs, True))
            if isinstance(data, dict):
                got = {str(k): str(v) for k, v in data.items()}
                if all(k in got for k, _ in items):
                    res = got
                    break
        except FatalAPIError:
            raise
        except Exception:
            pass
    for k, src in items:
        v = res.get(k)
        if (v is None) or (not re.search(r"[\u4e00-\u9fff]", v)
                           and v.strip() == src.strip() and re.search(r"[A-Za-z]", src)):
            try:
                v2 = _translate_one(cfg, src)
            except FatalAPIError:
                raise
            except Exception:
                continue
            if v2:
                res[k] = v2
    return res


def mostly_ascii(s):
    """[已下沉到 `l10n.textops.mostly_ascii`]"""
    return textops.mostly_ascii(s)


def find_untranslated(texts):
    """[已下沉到 `l10n.textops.find_untranslated`]

    ⚠ 与 `modl10n.tlk.needs_translation` **不等价**（后者先 norm 去首尾空白），
    这里是原始文本判据 —— 保持原语义，别顺手统一。
    """
    return textops.find_untranslated(texts)


def make_batches(pairs, char_target=5000, max_items=40, single_max=4500):
    plan, cur, clen = [], [], 0

    def flush():
        nonlocal cur, clen
        if cur:
            plan.append(cur)
            cur, clen = [], 0

    for k, v in pairs:
        if len(v) > single_max:
            flush()
            plan.append([(k, v)])
            continue
        cur.append((k, v))
        clen += len(v)
        if clen >= char_target or len(cur) >= max_items:
            flush()
    flush()
    return plan


def translate_texts(texts, ids, key, model=None, base=None, progress=None, stop=None):
    """就地翻译 texts 中 ids 指定的条目，返回翻译条数。"""
    cfg = {"key": key, "model": model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
           "base": base or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")}
    pairs = [(i, clean(texts[i])) for i in ids if texts.get(i, "").strip()]
    plan = make_batches(pairs)
    done = 0
    for bi, batch in enumerate(plan):
        if stop and stop():
            break
        try:
            res = translate_items(cfg, batch)
        except FatalAPIError as e:
            if progress:
                progress("致命错误，已停止翻译（记忆库已填的部分仍会写出）：%s" % e)
            break
        except Exception as e:
            if progress:
                progress("批 %d/%d 失败: %s" % (bi + 1, len(plan), e))
            continue
        for k, src in batch:
            if k in res and res[k].strip():
                texts[k] = normalize(res[k]).strip()
                done += 1
        if progress:
            progress("已完成 %d/%d 条（批 %d/%d）" % (done, len(pairs), bi + 1, len(plan)))
    return done


# ============================================================ 翻译记忆库（0 花费复用）
HERE_DIR = os.path.dirname(os.path.abspath(__file__))
DEF_TM = os.path.join(HERE_DIR, "modl10n", "tm.sqlite")
if HERE_DIR not in sys.path:
    sys.path.insert(0, HERE_DIR)


def ask_key(prompt="请输入 DeepSeek API Key: "):
    """要 key；非交互环境（管道/无 stdin）下不抛异常，直接返回空串。"""
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def load_tm(tm_path=None):
    """打开翻译记忆库；modl10n 不在时就退化成纯 API 模式（返回 None）。"""
    try:
        from modl10n.tm import TM
        from modl10n.tlk import key_tlk, norm as norm_tlk
        return TM(tm_path or DEF_TM), (key_tlk, norm_tlk)
    except Exception:
        return None, None


def split_by_memory(texts, ids, tm_path=None, progress=None):
    """把待译 id 分成「记忆库已命中」与「真的要调 API」；命中的直接填进 texts。

    记忆库按【英文原文内容哈希】存，所以跨版本、跨项目都能命中，且不会错位。
    """
    tm, K = load_tm(tm_path)
    if not tm:
        return list(ids), 0
    key_tlk, _norm = K
    keys = {i: key_tlk(texts[i]) for i in ids}
    hits = tm.get_many(set(keys.values()))
    todo, reused = [], 0
    for i in ids:
        h = hits.get(keys[i])
        if h:
            texts[i] = h
            reused += 1
        else:
            todo.append(i)
    if progress and reused:
        progress("记忆库命中 %d 条（0 花费）；剩 %d 条需要调用 API" % (reused, len(todo)))
    return todo, reused


def translate_with_memory(texts, ids, key, tm_path=None, model=None, base=None,
                          progress=None, stop=None):
    """先吃记忆库（0 花费），只把没命中的送去 API，并把新译文写回记忆库。

    返回 {"api": 新译条数, "reused": 记忆命中条数, "pending": 送 API 的条数}。
    """
    orig = {i: texts[i] for i in ids}
    todo, reused = split_by_memory(texts, ids, tm_path, progress)
    n = translate_texts(texts, todo, key, model=model, base=base,
                        progress=progress, stop=stop) if todo else 0
    tm, K = load_tm(tm_path)
    if tm and todo:
        key_tlk, norm_tlk = K
        rows = []
        for i in todo:
            src, dst = orig.get(i, ""), texts.get(i, "")
            if src and dst and dst != src:
                rows.append((key_tlk(src), norm_tlk(src), norm_tlk(dst), "TLK"))
        if rows:
            w = tm.put_many(rows, status="auto")
            if progress:
                progress("已把 %d 条新译文写回记忆库（下次这些就不用再翻了）" % w)
    return {"api": n, "reused": reused, "pending": len(todo)}


def cli_update(tlk_in, tlk_out, key=None, tm_path=None, do_all=False, dry_run=False):
    """**一键增量汉化**：新版 TLK -> 中文 TLK。先吃记忆库（0 花费），只翻没命中的增量。"""
    t = load_tlk(tlk_in)
    texts = t["texts"]
    ids = list(texts) if do_all else find_untranslated(texts)
    print("源 TLK：%s" % tlk_in)
    print("  有文本 %d 条；其中需要中文 %d 条" % (len(texts), len(ids)))
    if dry_run:
        todo, reused = split_by_memory(texts, ids, tm_path, progress=print)
        print("  [dry-run] 记忆库可覆盖 %d 条（0 花费）；需翻译 %d 条" % (reused, len(todo)))
        print("  未调用 API、未写出文件。")
        return 0
    key = (key or os.environ.get("DEEPSEEK_API_KEY", "")).strip()
    if not key:
        key = ask_key("DeepSeek API Key（直接回车 = 只用记忆库，不调用 API）: ")
    reused = n = pending = 0
    if key and ids:
        r = translate_with_memory(texts, ids, key, tm_path=tm_path,
                                  progress=lambda m: sys.stderr.write("  " + m + "\n"))
        reused, n, pending = r["reused"], r["api"], r["pending"]
    else:
        todo, reused = split_by_memory(texts, ids, tm_path,
                                      progress=lambda m: sys.stderr.write("  " + m + "\n"))
        pending = len(todo)
    try:
        from modl10n.tlk import write_tlk
    except Exception as e:                                        # noqa: BLE001
        raise SystemExit("需要 modl10n 才能外科式写出 TLK：%r" % e)
    info = write_tlk(tlk_in, tlk_out, {i: texts[i] for i in ids if texts.get(i)})
    print("\n完成：")
    print("  记忆库命中（0 花费）: %d 条" % reused)
    print("  API 新译            : %d 条" % n)
    print("  未译（保持英文）    : %d 条" % pending)
    print("  已写出 %s（%d 字节，%d 个槽位，实际改动 %d 条）"
          % (info["out"], info["bytes"], info["count"], info["changed"]))
    print("  读回校验：不一致 %d 条 %s"
          % (info.get("mismatch", -1), "✅" if info.get("mismatch") == 0 else "❌"))
    return 0


# ============================================================ 命令行
def cli_info(path):
    d, lang, n, data_off, ents = read_tlk_raw(path)
    used = [i for i, e in enumerate(ents) if e[0] == 1 and e[2] > 0]
    print("文件          : %s" % path)
    print("大小          : %d 字节" % len(d))
    print("languageID    : %d" % lang)
    print("stringCount   : %d" % n)
    print("有文本条目    : %d" % len(used))
    print("字符串数据区  : offset=%d, 共 %d 字节, 最长 %d 字节"
          % (data_off, sum(e[2] for e in ents), max((e[2] for e in ents), default=0)))
    if used:
        print("ID 范围       : %d ~ %d" % (min(used), max(used)))


def cli_export(tlk, out):
    t = load_tlk(tlk)
    meta = {"lang": t["lang"], "count": t["count"]}
    entries = [(i, txt.replace("\n", "\r\n"))
               for i, txt in sorted(t["texts"].items(), key=lambda kv: int(kv[0]))]
    open(out, "w", encoding="utf-8-sig", newline="").write(build_txt(meta, entries, tlk))
    print("已导出 %d 条 -> %s (%d 字节)" % (len(entries), out, os.path.getsize(out)))


def cli_import(txt, out, count=None, lang=None):
    meta, entries = parse_txt(open(txt, "r", encoding="utf-8-sig", newline="").read())
    N = count or meta.get("count")
    L = lang if lang is not None else meta.get("lang", 0)
    if not N:
        raise SystemExit("txt 头部没有 TLKCount，请用 --count=N 指定")
    texts = {str(i): clean(t) for i, t in entries}
    size = save_tlk(out, L, N, texts)
    print("已导入 %d 条 -> %s (%d 字节, stringCount=%d)" % (len(texts), out, size, N))


def leading_comments(raw):
    """[已下沉到 `l10n.txtio.leading_comments`] 取开头连续的 `#//` 行。"""
    return txtio.leading_comments(raw)


def cli_punct(path, mode):
    """在 txt 上批量转换标点: mode=cn / en"""
    raw = open(path, "r", encoding="utf-8-sig", newline="").read()
    meta, entries = parse_txt(raw)
    fn = to_cn_punct if mode == "cn" else to_en_punct
    head = leading_comments(raw)
    out = ["%s := %s\r\n" % (i, to_txt(fn(clean(t)))) for i, t in entries]
    open(path, "w", encoding="utf-8-sig", newline="").write("".join(head) + "".join(out))
    print("已把 %d 条标点转为「%s」-> %s"
          % (len(entries), "中文全角" if mode == "cn" else "英文半角", path))


def cli_translate(txt, key=None, do_all=False, tm_path=None):
    key = key or os.environ.get("DEEPSEEK_API_KEY", "") or ask_key()
    raw = open(txt, "r", encoding="utf-8-sig", newline="").read()
    meta, entries = parse_txt(raw)
    texts = {i: clean(t) for i, t in entries}
    ids = list(texts) if do_all else find_untranslated(texts)
    print("待翻译: %d 条" % len(ids))
    if not ids:
        return
    r = translate_with_memory(texts, ids, key, tm_path=tm_path,
                              progress=lambda m: sys.stderr.write("  " + m + "\n"))
    head = leading_comments(raw)
    out = ["%s := %s\r\n" % (i, to_txt(texts[i])) for i, _ in entries]
    open(txt, "w", encoding="utf-8-sig", newline="").write("".join(head) + "".join(out))
    print("完成：记忆库命中 %d 条（0 花费），调用 API 新译 %d 条 -> %s"
          % (r["reused"], r["api"], txt))


# ============================================================ 图形界面
def run_gui(test_hook=None, db_path=None, project=None):
    """图形界面入口。

    `db_path` / `project` 可注入 —— **测试必须传临时库**，否则会写真实 `tm.sqlite`（红线）。
    `test_hook(app)` 是无头测试钩子（GUI 起来 200ms 后调用，然后自动关窗）。
    """
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, simpledialog

    class App(tk.Tk):
        def __init__(self, db_path=None, project=None):
            super().__init__()
            self.title("TLK Tool — 无冬之夜 TLK 汉化工具")
            self.geometry("1200x760")
            # 数据全在 TlkDoc 里；界面只留"选中了谁""哪些条目术语违规"这类 UI 状态
            self.doc = None
            self.violations = {}       # {id: [(en, zh)]} 术语违规表（列表里标记用）
            self.cur_id = None
            # ★ 可注入：测试用临时库，绝不碰真实 tm.sqlite（红线）
            self.db_path = db_path or DEF_DB
            self.project = project
            self._term_repo = None
            self._db_lock = threading.Lock()   # 惰性开库的双检锁（见 _db() 的说明）
            # 方案 A：英文源 TLK 的 strref -> 英文原文（术语检查要用它当"原文"）
            self.src_texts = {}
            self.en_info = ""          # 英文源来源说明；找不到时写"降级"原因
            self._build()
            self.protocol("WM_DELETE_WINDOW", self.on_close)

        # ---------- 界面 ----------
        def _build(self):
            m = tk.Menu(self)
            f = tk.Menu(m, tearoff=0)
            f.add_command(label="打开 TLK…", command=self.open_tlk, accelerator="Ctrl+O")
            f.add_command(label="保存 TLK", command=self.save_cur, accelerator="Ctrl+S")
            f.add_command(label="另存为…", command=self.save_as)
            f.add_separator()
            f.add_command(label="导出为 txt…", command=self.do_export)
            f.add_command(label="从 txt 导入…", command=self.do_import)
            f.add_separator()
            f.add_command(label="退出", command=self.on_close)
            m.add_cascade(label="文件", menu=f)

            t = tk.Menu(m, tearoff=0)
            t.add_command(label="AI 翻译未翻译条目…", command=self.do_ai)
            t.add_separator()
            t.add_command(label="标点转中文全角(。，：)", command=lambda: self.do_punct("cn"))
            t.add_command(label="标点转英文半角(. , :)", command=lambda: self.do_punct("en"))
            t.add_separator()
            t.add_command(label="术语查询…（模糊搜 + 双击插入）", command=self.open_term_search)
            t.add_command(label="术语体检（重扫当前文件，列表标 ⚠）",
                          command=self.refresh_violations)
            t.add_command(label="把记忆库回填到本 TLK…（先预览，自动备份）",
                          command=self.do_fill_tlk)
            t.add_separator()
            t.add_command(label="统计信息", command=self.show_info)
            m.add_cascade(label="工具", menu=t)

            h = tk.Menu(m, tearoff=0)
            h.add_command(label="使用说明", command=self.show_help)
            m.add_cascade(label="帮助", menu=h)
            self.config(menu=m)

            top = tk.Frame(self)
            top.pack(fill="x", padx=6, pady=4)
            tk.Label(top, text="搜索(id 或内容):").pack(side="left")
            self.q = tk.StringVar()
            en = tk.Entry(top, textvariable=self.q, width=34)
            en.pack(side="left", padx=4)
            en.bind("<Return>", lambda e: self.refresh_list())
            tk.Button(top, text="搜索", command=self.refresh_list).pack(side="left")
            tk.Button(top, text="清空", command=lambda: (self.q.set(""), self.refresh_list())).pack(side="left")
            self.only_en = tk.BooleanVar(value=False)
            tk.Checkbutton(top, text="只看未翻译", variable=self.only_en,
                           command=self.refresh_list).pack(side="left", padx=8)
            self.count_lbl = tk.Label(top, text="", fg="#555")
            self.count_lbl.pack(side="right")

            pane = tk.PanedWindow(self, orient="horizontal", sashwidth=5)
            pane.pack(fill="both", expand=True, padx=6, pady=(0, 4))

            left = tk.Frame(pane)
            body = tk.Frame(left)
            vs = ttk.Scrollbar(body, orient="vertical")
            self.tree = ttk.Treeview(body, columns=("id", "prev"), show="headings",
                                     selectmode="browse", yscrollcommand=vs.set)
            vs.config(command=self.tree.yview)
            self.tree.heading("id", text="ID")
            self.tree.column("id", width=66, anchor="e", stretch=False)
            self.tree.heading("prev", text="内容预览")
            self.tree.column("prev", width=470, anchor="w")
            vs.pack(side="right", fill="y")
            self.tree.pack(side="left", fill="both", expand=True)
            body.pack(fill="both", expand=True)
            self.tree.bind("<<TreeviewSelect>>", self.on_pick)
            pane.add(left, width=560)

            right = tk.Frame(pane)
            row = tk.Frame(right)
            row.pack(fill="x")
            self.id_lbl = tk.StringVar(value="(未打开文件)")
            tk.Label(row, textvariable=self.id_lbl, font=("Consolas", 11, "bold")).pack(side="left")
            tk.Button(row, text="跳到 ID…", command=self.goto_id).pack(side="right")
            tk.Label(right, text="▼ 对照（记忆库里的另一种语言，只读）", fg="#0a5",
                     font=("Microsoft YaHei", 9, "bold")).pack(anchor="w", pady=(6, 0))
            self.counterpart = tk.Text(right, height=4, wrap="word", bg="#f4f7f4",
                                       font=("Microsoft YaHei", 11), state="disabled")
            self.counterpart.pack(fill="x", pady=(0, 4))
            self.editor = tk.Text(right, wrap="word", undo=True, font=("Microsoft YaHei", 11))
            self.editor.pack(fill="both", expand=True, pady=4)
            self.editor.bind("<Control-Return>", lambda e: (self.apply_edit(), "break")[1])
            # ★ 术语条：实时显示"这条命中了哪些术语 / 有没有必守没遵守"（选中或改完即刷新）
            self.term_bar = tk.StringVar(value="术语：（选中一条后显示）")
            tbar = tk.Frame(right)
            tbar.pack(fill="x")
            tk.Label(tbar, textvariable=self.term_bar, anchor="w", fg="#a00",
                     font=("Microsoft YaHei", 9)).pack(side="left", fill="x", expand=True)
            tk.Button(tbar, text="术语查询", command=self.open_term_search).pack(side="right")
            # 「加入术语库」：看到一条好译文，一键收进术语表（加完当前术语条立刻刷新）
            tk.Button(tbar, text="加入术语库", command=self.add_current_term).pack(
                side="right", padx=4)
            btns = tk.Frame(right)
            btns.pack(fill="x")
            tk.Button(btns, text="应用修改 (Ctrl+Enter)", command=self.apply_edit).pack(side="left")
            tk.Button(btns, text="保存到记忆库", command=self.save_to_memory).pack(side="left", padx=6)
            # ★ 用户 2026-10-08 反馈"没看到回填功能"（原来只藏在「工具」菜单里）
            #   → 除了菜单，再放一个**显眼按钮**在这里
            tk.Button(btns, text="回填到 TLK", command=self.do_fill_tlk).pack(side="left", padx=6)
            self.auto_mem = tk.BooleanVar(value=True)
            tk.Checkbutton(btns, text="保存时自动写入记忆库", variable=self.auto_mem).pack(side="left")
            tk.Button(btns, text="还原本条",
                      command=lambda: self.load_into_editor(self.cur_id)).pack(side="left", padx=6)
            tk.Label(btns, text="提示: 换行原样保存；标点可用中文(工具菜单可一键转换)",
                     fg="#888").pack(side="right")
            pane.add(right)

            self.status = tk.StringVar(value="就绪。文件 → 打开 TLK 开始。")
            tk.Label(self, textvariable=self.status, anchor="w", relief="sunken").pack(
                fill="x", side="bottom")

        # ---------- 数据 ----------
        def open_tlk(self):
            p = filedialog.askopenfilename(title="打开 TLK",
                                           filetypes=[("TLK 文件", "*.tlk"), ("所有文件", "*.*")])
            if p:
                self.load_file(p)

        def load_file(self, path):
            try:
                self.doc = tlk_service.open_tlk(path)      # ★ 读文件逻辑已下沉到 l10n
            except Exception as e:
                messagebox.showerror("打开失败", str(e))
                return
            self.q.set("")
            self.load_en_source()                          # ★ 方案 A：找英文源（术语检查要用）
            self.refresh_list()
            # ★ 后台预热术语正则（6491 个正则首次编译要 ~1.8 秒 ✗ 用户会感觉"点一下就卡"）
            self._gloss_cache = None
            threading.Thread(target=self.warm_terms, daemon=True).start()
            self.refresh_violations()                      # 术语体检（放后台，别卡界面）
            self.set_status("已加载 %s：%d 条有文本（共 %d 条）｜文本编码：%s｜%s"
                            % (os.path.basename(path), len(self.doc.texts), self.doc.count,
                               self.doc.encoding, self.en_info or "英文源：未启用"))

        def refresh_list(self):
            if not self.doc:
                return
            q = self.q.get().strip()
            ids = list(self.doc.texts)
            if self.only_en.get():
                ids = find_untranslated(self.doc.texts)
            if q:
                # 搜索除了当前 TLK 的文本，**还查记忆库里的另一种语言** ✓：
                # 打开汉化版时用英文搜、打开英文版时用中文搜，都能命中 ✓。
                # 做法：先对 tm 做一次 LIKE 拿到命中的"英文/中文"集合，再拿它过滤列表 ✓。
                # ★ 这处裸 SQL 已收编到 `TmRepo.search_texts()`（且**不截断**）。
                hit = set()
                tm = self._tm()
                if tm:
                    try:
                        hit = tm.search_texts(q)
                    except Exception:
                        pass
                ids = [i for i in ids
                       if q in i or q in self.doc.texts[i]
                       or self.doc.texts[i].strip() in hit]
            ids.sort(key=lambda x: int(x))
            self.tree.delete(*self.tree.get_children())
            for i in ids:
                txt = self.doc.texts[i].replace("\n", " ")
                mark = "* " if i in self.doc.modified else ""
                if self.violations.get(i):
                    mark = "⚠ " + mark          # 术语违规的条目在列表里一眼可见
                self.tree.insert("", "end", iid=i, values=(i, mark + txt[:150]))
            self.count_lbl.config(text="显示 %d 条 / 共 %d 条" % (len(ids), len(self.doc.texts)))

        def on_pick(self, _ev=None):
            sel = self.tree.selection()
            if sel:
                self.load_into_editor(sel[0])

        # ---------- 对照区（英文 ↔ 中文） ----------
        def _db(self):
            """惰性打开中央库 —— **整个 App 只用一个连接**。

            ⚠ 不能让 `_tm` 和 `_term` 各开一个：`l10n.db.connect()` 里带一次幂等迁移
            （会写 `PRAGMA user_version`），多连接在 WAL 下会**争写锁**，表现为
            "有时打不开记忆库"（实测踩过一次，症状是间歇性的）。
            GUI 与 CLI 并发写同一条红线：串行。
            """
            if getattr(self, "_db_handle", None) is None:
                # ★ 双检锁：后台"术语体检"线程与主线程可能**同时**首次开库；
                #   两个连接同时跑迁移会撞 `view v_unit_todo already exists`（实测踩过）。
                with self._db_lock:
                    if getattr(self, "_db_handle", None) is None:
                        try:
                            self._db_handle = db_connect(self.db_path)
                        except Exception as _e:
                            import sys as _sys
                            print("[tlk tool] 打开中央库失败：%r" % (_e,), file=_sys.stderr)
                            self._db_handle = False
            return self._db_handle

        def _tm(self):
            """惰性拿 `TmRepo`，并建一份"中文→英文"缓存（反向对照用）。

            ★ 这里原来有**第二处裸 SQL**（全表 44k 行扫一遍建缓存），已收编到
              `TmRepo.zh_to_en_map()`；缓存**只建一次**，别每次调用重建（会卡界面）。
            """
            if getattr(self, "_tm_handle", None) is None:
                try:
                    db = self._db()
                    if not db:
                        raise RuntimeError("中央库打不开：%s" % self.db_path)
                    self._tm_handle = TmRepo(db)
                    self._dst2src = self._tm_handle.zh_to_en_map()
                except Exception as _e:
                    # 别再说"打不开记忆库"却不知道为啥 —— 把真实原因打到 stderr
                    import sys as _sys
                    print("[tlk tool] 打开记忆库失败：%r" % (_e,), file=_sys.stderr)
                    self._tm_handle, self._dst2src = False, {}
            return self._tm_handle

        def _term(self):
            """惰性拿 `TermRepo`（术语检查用）—— 复用同一个连接。"""
            if self._term_repo is None:
                try:
                    db = self._db()
                    if not db:
                        raise RuntimeError("中央库打不开：%s" % self.db_path)
                    self._term_repo = TermRepo(db)
                except Exception as _e:
                    import sys as _sys
                    print("[tlk tool] 打开术语库失败：%r" % (_e,), file=_sys.stderr)
                    self._term_repo = False
            return self._term_repo

        def _glossary(self):
            """术语表**缓存**（2026-10-08 用户反馈"点击词条卡住"后加的）。

            6700 条术语，`glossary_of()` 每次都查库重建 ✗；配合 `_tolerant_re` 的 lru_cache，
            真正贵的是**首次编译 6491 个正则 ≈ 1.8 秒** ✗ —— 所以这里缓存 Glossary，
            并在 `load_file` 之后用**后台线程预热**（见 `warm_terms`）。

            ★ **失效检查**：每次比对 `count("active")`（一次 COUNT，几 ms ✓）——
              否则"刚加入的术语"会被缓存挡住、术语条显示不出来 ✗（GUI 自检正好抓到这一点 ✗）。
            """
            tr = self._term()
            if not tr:
                return None
            try:
                n = tr.count("active")
            except Exception:                                     # noqa: BLE001
                n = None
            if (getattr(self, "_gloss_cache", None) is None
                    or n != getattr(self, "_gloss_n", None)):
                self._gloss_cache = term_service.glossary_of(tr)
                self._gloss_n = n
            return self._gloss_cache

        def warm_terms(self):
            """后台预热：把全部术语正则**预先编译**（否则用户点第一条要等 ~1.8 秒 ✗）。"""
            try:
                g = self._glossary()
                if not g:
                    return
                for t in g.terms:
                    en = t.get("en")
                    if en:
                        term_service._tolerant_re(en)
                print("[tlk tool] 术语预热完成：%d 条" % len(g.terms))
            except Exception as _e:                                # noqa: BLE001
                print("[tlk tool] 术语预热失败：%r" % (_e,))

        # ---------- 方案 A：英文源（术语检查要用"英文原文"当被测文本） ----------
        def load_en_source(self):
            """找对应的**英文源 TLK**（四级降级）—— 找不到就优雅降级为"按中文反查"。

            背景：这里打开的往往是**已汉化的 TLK**，`orig_texts` 里全是中文，而术语检查是
            "拿英文术语匹配原文" → 中文当然匹配不到 ✗（用户实测就是这么发现问题的）。
            """
            self.src_texts, self.en_info, self._en_source_path = {}, "", None
            if not self.doc or not self.doc.path:
                return
            try:
                p, why = tlk_service.find_en_source(self.doc.path, db=self._db(),
                                                    project=self.project)
                if not p:
                    self.en_info = "降级：没找到英文源（%s）→ 按中文反查" % why
                    return
                en = tlkio.load(p)
                self.src_texts = en["texts"]
                self._en_source_path = p       # 「回填到 TLK」要用它做内容对照
                self.en_info = "英文源：%s（%s）" % (os.path.basename(p), why)
            except Exception as e:                         # noqa: BLE001
                self.en_info = "降级：英文源载入失败 %r" % (e,)

        def _status_by_zh(self, tr, src, dst):
            """**降级路径**（拿不到英文源时）：文本是中文，用模糊反查术语表倒推涉及的术语。

            ⚠ 模糊匹配天生有误报风险，所以只在降级时用；正常路径永远优先英文源。
            """
            text = (dst or src or "").strip()
            if not text:
                return {"hits": [], "missing": [], "ok": True}
            hits = []
            try:
                for t, _score, _why in tr.fuzzy(text, limit=6):
                    if t.zh and t.zh in text:
                        hits.append((t.en, t.zh, bool(t.required)))
            except Exception:                              # noqa: BLE001
                pass
            return {"hits": hits, "missing": [], "ok": True}

        def refresh_violations(self):
            """扫一遍当前文档，标出**术语违规**的条目（列表里显示 ⚠）。

            "3 万条文本 × 213 个术语"的正则扫描会明显卡界面，所以**放后台线程**跑，
            跑完回主线程刷列表与状态栏。
            """
            if not self.doc:
                return
            snapshot = dict(self.doc.texts)
            srcs = self.doc.orig_texts or {}
            self.set_status("术语体检中…（后台跑，不影响继续编辑）")

            def work():
                tr = self._term()
                if not tr:
                    return
                try:
                    rep = term_service.violation_summary(
                        tr, [(i, srcs.get(i, ""), t) for i, t in snapshot.items()])
                except Exception:
                    return
                bad = dict(rep["by_id"])

                def apply_():
                    self.violations = bad
                    self.refresh_list()
                    self.set_status("术语体检：%d 条违规（列表里带 ⚠）" % len(bad)
                                    if bad else "术语体检：没发现违规 ✓")
                try:
                    self.after(0, apply_)
                except Exception:
                    pass

            threading.Thread(target=work, daemon=True).start()

        def _show_cp_text(self, s):
            self.counterpart.config(state="normal")
            self.counterpart.delete("1.0", "end")
            self.counterpart.insert("1.0", s)
            self.counterpart.config(state="disabled")

        def show_counterpart(self, text):
            """在只读区显示这条文本在记忆库里的**另一种语言**。

            打开英文 TLK 时显示记忆库里的中文（翻译时对照用 ✓）；
            打开汉化版时反过来查英文原文 ✓（用 src↔dst 反向缓存 ✓）。
            ⚠ 纯显示功能，任何异常都不允许弄崩界面 ✗（出错就把原因显示在对照区里 ✓）。
            """
            try:
                tm = self._tm()
                out = ""
                if tm:
                    t = (text or "").strip()
                    if t:
                        # ★ 收编原来两处（`tm.get(单key)` + `_dst2src.get`）；
                        #   `counterpart` 内部走**双 key 兜底**，比旧实现多查出一批
                        #   （旧版那两段 `isinstance(got, (tuple, list))` 是死代码，已去掉）。
                        got = tm.counterpart(t, dst2src=self._dst2src)
                        if got and got.strip() != t:
                            out = got
                self._show_cp_text(out or "（记忆库里没有这条的对照）")
            except Exception as ex:
                self._show_cp_text("（对照查询失败：%r）" % ex)

        def load_into_editor(self, i):
            if not self.doc or i is None or i not in self.doc.texts:
                return
            self.cur_id = i
            self.id_lbl.set("ID %s%s" % (i, "   [已修改]" if i in self.doc.modified else ""))
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", self.doc.texts[i])
            self.show_counterpart(self.doc.texts[i])
            self.show_term_bar(i)

        def apply_edit(self):
            if not self.doc or self.cur_id is None:
                return
            new = self.editor.get("1.0", "end-1c")
            if self.doc.set_text(self.cur_id, new):        # ★ 变化判定与标脏在 TlkDoc 里
                self.id_lbl.set("ID %s   [已修改]" % self.cur_id)
                if self.tree.exists(self.cur_id):
                    self.tree.item(self.cur_id,
                                   values=(self.cur_id, "* " + new.replace("\n", " ")[:150]))
                self.set_status("已修改 ID %s（记得保存）" % self.cur_id)
                self.show_term_bar(self.cur_id)

        def show_term_bar(self, i):
            """刷「术语条」：四类**明确**提示（用户 2026-10-08 要求"更有信息量"）。

                ⚠ 必守未遵守：X -> Y
                [命中] X -> Y
                [无术语记录] 但记忆库有对照：Z
                [无术语记录]（记忆库也无对照）
            """
            if not hasattr(self, "term_bar") or not self.doc:
                return
            tr = self._term()
            if not tr:
                self.term_bar.set("术语：中央库不可用（%s）" % self.db_path)
                return
            # ★ 优先用**英文源**里同 strref 的原文当被测文本；没有才退回本文件的原文
            src = self.src_texts.get(i) or (self.doc.orig_texts or {}).get(i, "")
            dst = (self.doc.texts or {}).get(i, "")
            try:
                g = self._glossary()   # ★ 用缓存（否则每次点击都重建 6700 条 + 编译正则 ✗）
                if self.src_texts.get(i):
                    st = term_service.status_of_text(tr, src, dst, glossary=g)
                    via = ""
                elif mostly_ascii(src or ""):
                    # 打开的就是**英文 TLK** → 原文本身就能做正向术语检查，不必降级
                    st = term_service.status_of_text(tr, src, dst, glossary=g)
                    via = ""
                else:
                    st = self._status_by_zh(tr, src, dst)
                    via = "（降级：按中文反查）"
            except Exception as ex:
                self.term_bar.set("术语：查询失败 %r" % ex)
                return
            if st["missing"]:
                self.term_bar.set("⚠ 必守未遵守：%s" % "、".join(
                    "%s -> %s" % (e, z) for e, z in st["missing"]))
            elif st["hits"]:
                self.term_bar.set("[命中] %s%s" % ("、".join(
                    "%s -> %s%s" % (e, z, "" if req else "(建议)")
                    for e, z, req in st["hits"][:6]), via))
            else:
                # ★ 用户 2026-10-08 要求：术语条**只说术语**，不要在这里重复"记忆库对照"
                #   —— 右侧已经有专门的内存库对照面板，重复提示会造成信息混乱 ✗
                self.term_bar.set("[无术语记录]%s" % via)

        def add_current_term(self):
            """把当前条目的（英文, 译文）加进术语库 —— **极简**：只确认译文与是否必守。

            用户 2026-10-08 拍板："默认抓取原文和当前译文，弹窗只让用户确认'译文'和
            '是否必守'，点确定后写入，并**立刻刷新**当前界面的术语提示"。分类自动推测。
            """
            if not self.doc or self.cur_id is None:
                messagebox.showinfo("加入术语库", "先在列表里选中一条。")
                return
            tr = self._term()
            if not tr:
                messagebox.showerror("术语库不可用", "打不开中央库：%s" % self.db_path)
                return
            i = self.cur_id
            en = (self.src_texts.get(i) or (self.doc.orig_texts or {}).get(i, "") or "").strip()
            zh = (self.doc.texts.get(i, "") or "").strip()
            if not en:
                messagebox.showinfo("加入术语库", "拿不到这条的英文原文，没法建术语。")
                return
            if not zh:
                messagebox.showinfo("加入术语库", "这条还没有译文。")
                return
            dlg = tk.Toplevel(self)
            dlg.title("加入术语库")
            dlg.transient(self)
            tk.Label(dlg, text="英文原文（只读）").grid(row=0, column=0, sticky="e", padx=6, pady=4)
            en_v = tk.StringVar(value=en)
            e1 = tk.Entry(dlg, textvariable=en_v, width=44)
            e1.grid(row=0, column=1, padx=6)
            e1.config(state="readonly")
            tk.Label(dlg, text="译名").grid(row=1, column=0, sticky="e", padx=6, pady=4)
            zh_v = tk.StringVar(value=zh)
            tk.Entry(dlg, textvariable=zh_v, width=44).grid(row=1, column=1, padx=6)
            req_v = tk.BooleanVar(value=False)
            tk.Checkbutton(dlg, text="设为必守（译后强校验）", variable=req_v).grid(
                row=2, column=0, columnspan=2, sticky="w", padx=6)
            tk.Label(dlg, text="分类自动推测；确定后**立即生效**（当前术语条马上刷新）",
                     fg="#666").grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=(2, 2))

            def go():
                z = zh_v.get().strip()
                if not z:
                    messagebox.showwarning("还差一步", "译名不能为空")
                    return
                tp = term_editor_service.guess_type(en)
                try:
                    r = term_editor_service.add_term(tr, en, z, type_=tp,
                                                     required=bool(req_v.get()))
                except Exception as ex:                    # noqa: BLE001
                    messagebox.showerror("写入失败", repr(ex))
                    return
                dlg.destroy()
                self.show_term_bar(i)                      # ★ 即加即用
                self.refresh_list()
                self.set_status("已加入术语库：%s = %s（分类=%s，必守=%s）%s"
                                % (en, z, tp, req_v.get(),
                                   "；旧译名行已降级" if r.get("demoted") else ""))

            tk.Button(dlg, text="确定", command=go, width=12).grid(
                row=4, column=0, columnspan=2, pady=8)

        def do_fill_tlk(self):
            """把**记忆库的译文回填到当前 TLK**（用户 2026-10-08 需求）。

            安全设计（继承 `rebuild_tlk_safe.py` 最关键的三条保护）：
              ① 英文源里没有的 strref（自加槽位，如那 89 个界面标签）**原样保留**；
              ② 写前**自动备份**；③ 写后**读回逐条校验**（应改的都改了、不该动的一字未动）。
            """
            if not self.doc or not self.doc.path:
                messagebox.showinfo("回填到 TLK", "先打开一个 TLK。")
                return
            tm = self._tm()
            if not tm:
                messagebox.showerror("记忆库不可用", "打不开：%s" % self.db_path)
                return
            en_path = getattr(self, "_en_source_path", None)
            if not en_path:
                messagebox.showinfo(
                    "回填到 TLK",
                    "需要**英文源 TLK** 才能按内容回填（当前没找到）。\n\n现状：%s\n\n"
                    "提示：把英文源放在同目录（如 `xxxE.tlk`）通常就能自动识别。"
                    % (self.en_info or "（未知）"))
                return
            try:
                rep = tlk_fill_service.plan_fill(self.doc.path, en_path, tm)
            except Exception as ex:                        # noqa: BLE001
                messagebox.showerror("预览失败", repr(ex))
                return
            lines = [
                "TLK：%s" % os.path.basename(self.doc.path),
                "英文源：%s" % os.path.basename(en_path),
                "",
                "会改（记忆库译文与 TLK 不同）：**%d 条**" % rep["changeable"],
                "不变（两边一致）：%d 条" % rep["same"],
                "记忆库没译文 → 保持原样：%d 条" % rep["no_tm"],
                "自加槽位（英文源里没有）→ **原样保留**：%d 条" % rep["custom_slots"],
                "",
                "写前自动备份为：%s.回填前_<时间戳>" % os.path.basename(self.doc.path),
                "写后逐条读回校验。",
            ]
            if rep["preview"]:
                lines += ["", "前 %d 条预览：" % len(rep["preview"])]
                lines += ["  #%s  %s → %s" % (c["strref"], (c["old"] or "")[:18], c["new"][:18])
                          for c in rep["preview"][:12]]
            if not rep["changeable"]:
                messagebox.showinfo("回填到 TLK", "\n".join(lines) + "\n\n（没有需要改的条目）")
                return
            if not messagebox.askyesno("回填到 TLK", "\n".join(lines) + "\n\n确定执行？"):
                return
            try:
                res = tlk_fill_service.fill_tlk(self.doc.path, en_path, tm)
            except Exception as ex:                        # noqa: BLE001
                messagebox.showerror("回填失败", repr(ex))
                return
            if res["verified"]:
                messagebox.showinfo("回填完成",
                                    "已回填 %d 条，读回校验通过 ✓\n\n备份：\n%s"
                                    % (res["written"], res["backup"] or "（未就地写，无备份）"))
            else:
                messagebox.showwarning("回填完成但有异常",
                                       "已写 %d 条；校验未通过：应改未改 %d 条 / 误改 %d 条\n\n"
                                       "备份：%s\n请把这条消息发我。"
                                       % (res["written"], len(res["bad"]),
                                          len(res["untouched_bad"]), res["backup"]))
            self.load_file(self.doc.path)                  # 重新载入，界面与文件保持一致

        def open_term_search(self):
            """术语查询窗：模糊搜术语（`TermRepo.fuzzy`），**双击把译名插入译文**。"""
            tr = self._term()
            if not tr:
                messagebox.showerror("术语库不可用", "打不开中央库：%s" % self.db_path)
                return
            w = tk.Toplevel(self)
            w.title("术语查询 — 双击结果把译名插入译文")
            w.geometry("760x430")
            tk.Label(w, text="输入英文或中文（模糊：精确 / 前缀 / 子串 / 去撇号 / 相似度）",
                     anchor="w").pack(fill="x", padx=8, pady=(8, 2))
            qv = tk.StringVar()
            tk.Entry(w, textvariable=qv, font=("Consolas", 11)).pack(fill="x", padx=8)
            lb = tk.Listbox(w, font=("Microsoft YaHei", 10))
            lb.pack(fill="both", expand=True, padx=8, pady=6)
            hint = tk.Label(w, text="", anchor="w", fg="#666")
            hint.pack(fill="x", padx=8)
            rows = []

            def do_search(*_a):
                lb.delete(0, "end")
                rows[:] = []
                q = qv.get().strip()
                if not q:
                    hint.config(text="")
                    return
                try:
                    hits = term_service.fuzzy_suggest(tr, q, limit=60)
                except Exception as ex:
                    hint.config(text="查询失败：%r" % ex)
                    return
                for t, score, why in hits:
                    rows.append(t)
                    lb.insert("end", "%.2f  %-36s = %-16s [%s/%s] %s"
                              % (score, t.en[:36], (t.zh or "")[:16], t.type,
                                 "必守" if t.required else "建议", why))
                hint.config(text="%d 条结果" % len(rows))

            def insert_pick(_ev=None):
                sel = lb.curselection()
                if not sel or not rows:
                    return
                t = rows[sel[0]]
                if self.doc and self.cur_id is not None:
                    self.editor.insert("insert", t.zh)      # 插到译文光标处
                    self.set_status("已把「%s」插入译文（记得「应用修改」）" % t.zh)
                else:
                    messagebox.showinfo("术语", "%s = %s" % (t.en, t.zh))

            qv.trace_add("write", do_search)
            lb.bind("<Double-Button-1>", insert_pick)
            tk.Button(w, text="关闭", command=w.destroy).pack(pady=(0, 8))

        def goto_id(self):
            if not self.doc:
                return
            v = simpledialog.askinteger("跳到 ID", "输入 StrRef (0 ~ %d):" % (self.doc.count - 1))
            if v is None:
                return
            k = str(v)
            if self.doc.ensure_slot(k):                    # ★ 凭空占位也归 TlkDoc
                self.q.set("")
                self.refresh_list()
            if self.tree.exists(k):
                self.tree.selection_set(k)
                self.tree.see(k)
            self.load_into_editor(k)

        # ---------- 保存 ----------
        def save_to_memory(self):
            """把【英文原文 -> 当前中文】写进翻译记忆库（标 manual，机器永不覆盖）。

            为什么需要它：本程序的手工编辑以前只写 TLK 文件，记忆库里没有 ✗ —— 下次
            拿新版 TLK 跑「一键增量」时这些手工成果不会被命中，可能被重翻或覆盖。

            ★ 旧版这里**有两个硬 bug**（`load_tm()` 第二个值是元组却当函数调；
              `TM.put` 根本没有 `note` 参数），异常又被 except 吞成弹窗 ——
              所以这个按钮**从来没成功过**（实测 `tm` 里 note 含 "tlk tool" 的条目 0 条）。
              现在走 `tlk_service.save_to_memory()`（本连接写、SQL 与 `TM.put` 语义对齐），
              并由 `l10n/selftest_gui.py` 的用例守住。
            """
            if not self.doc:
                return
            if not self.doc.modified:
                self.set_status("没有修改过的条目，无需写入记忆库")
                return
            tm = self._tm()
            if not tm:
                messagebox.showerror("记忆库不可用", "打不开翻译记忆库：%s" % self.db_path)
                return
            try:
                r = tlk_service.save_to_memory(self.doc, tm, note="tlk tool 手工编辑")
            except Exception as ex:
                messagebox.showerror("写入记忆库失败", repr(ex))
                return
            if not r["written"]:
                self.set_status("没有可写入的条目（原文空/译文空/未改动 —— 跳过 %d）"
                                % r["skipped"])
                return
            self.set_status("已写入记忆库 %d 条（手工译文，机器永不覆盖）" % r["written"])

        def save_cur(self):
            if not self.doc:
                return
            try:
                # ★ 先落盘但**不清脏标记** —— 紧接着的"写记忆库"还要靠它挑出手工改过的条目
                n = self.doc.save(clear=False)
            except Exception as e:
                messagebox.showerror("保存失败", str(e))
                return
            self.refresh_list()
            self.set_status("已保存 -> %s（%d 字节）" % (self.doc.path, n))
            if getattr(self, "auto_mem", None) is not None and self.auto_mem.get():
                self.save_to_memory()      # 顺手把手工译文写进记忆库（标 manual）
            self.doc.modified.clear()      # 到这一步才清（与旧顺序一致）

        def save_as(self):
            if not self.doc:
                return
            p = filedialog.asksaveasfilename(title="另存为", defaultextension=".tlk",
                                             filetypes=[("TLK 文件", "*.tlk")])
            if p:
                self.doc.path = p
                self.save_cur()

        def do_export(self):
            if not self.doc:
                return
            p = filedialog.asksaveasfilename(title="导出为 txt", defaultextension=".txt",
                                             filetypes=[("文本文件", "*.txt")])
            if not p:
                return
            try:
                # ★ 修掉旧实现的隐性 bug：原来调 `cli_export(self.doc.path, p)`，
                #   那里会**重新从磁盘读一遍**，于是"改了但还没保存"的内容不会出现在
                #   导出的 txt 里。现在导出**内存态**，所见即所得。
                n = self.doc.export_txt(p)
                self.set_status("已导出 -> %s（%d 字节）" % (p, n))
            except Exception as e:
                messagebox.showerror("导出失败", str(e))

        def do_import(self):
            p = filedialog.askopenfilename(title="选择要导入的 txt", filetypes=[("文本文件", "*.txt")])
            if not p:
                return
            try:
                # ★ 读文件 + 解析已下沉到 `tlk_service.import_txt`；
                #   缺 TLKCount 时用当前文档的 count 兜底（GUI 语义；CLI 那边是直接报错）。
                self.doc = tlk_service.import_txt(
                    p, fallback_count=(self.doc.count if self.doc else None))
                self.q.set("")
                self.refresh_list()
                self.set_status("已从 %s 导入 %d 条，请「另存为…」指定 TLK 输出路径"
                                % (os.path.basename(p), len(self.doc.texts)))
            except Exception as e:
                messagebox.showerror("导入失败", str(e))

        # ---------- 工具 ----------
        def do_punct(self, mode):
            if not self.doc:
                return
            name = "中文全角(。，：)" if mode == "cn" else "英文半角(. , :)"
            if not messagebox.askyesno("标点转换", "把全部条目的标点转成「%s」？\n"
                                                   "（数字如 3.5、占位符如 %%(NAME) 不会被改动）" % name):
                return
            n = self.doc.set_punct(mode)        # ★ 全量标点转换已下沉到 TlkDoc.set_punct()
            self.refresh_list()
            if self.cur_id:
                self.load_into_editor(self.cur_id)
            self.set_status("标点已转成「%s」，%d 条有变化（记得保存）" % (name, n))

        def do_ai(self):
            if not self.doc:
                return
            ids = find_untranslated(self.doc.texts)
            if not ids:
                messagebox.showinfo("AI 翻译", "没有找到未翻译的条目。")
                return
            # 先用一份副本算出记忆库能白拿多少（不花钱），让用户知道要花多少
            probe = dict(self.doc.texts)
            todo, reused = split_by_memory(probe, ids)
            if not todo:
                if not messagebox.askyesno("AI 翻译", "这 %d 条在记忆库里全都有译文。\n"
                                                       "将 0 花费直接填入，继续？" % reused):
                    return
                self.doc.texts.update(probe)
                # ⚠ 已知问题（Sprint 6 **第二批**修）：这里把**全部条目**都标脏了 ——
                #   退出前会报"未保存 N 条"的大数、写记忆库也会多扫一遍。
                #   AI 链路（网络 + SQLite 写）本批**不下沉**，届时一并改成"只标真正变化的 id"。
                self.doc.modified.update(self.doc.texts)
                self.refresh_list()
                self.set_status("已从记忆库填入 %d 条（0 花费），记得保存" % reused)
                return
            if not messagebox.askyesno("AI 翻译",
                                       "共 %d 条未翻译：\n"
                                       "  · 记忆库已有 %d 条 —— 0 花费\n"
                                       "  · 需要调用 DeepSeek API %d 条\n\n继续？"
                                       % (len(ids), reused, len(todo))):
                return
            key = os.environ.get("DEEPSEEK_API_KEY", "")
            if not key:
                key = simpledialog.askstring("API Key", "请输入 DeepSeek API Key:", show="*")
            if not key:
                return
            self.set_status("AI 翻译中… 记忆库 %d 条 + API %d 条" % (reused, len(todo)))

            def worker():
                try:
                    r = translate_with_memory(self.doc.texts, ids, key,
                                              progress=lambda m: self.after(0, self.set_status, m))
                    self.after(0, self._ai_done, r)
                except Exception as e:
                    self.after(0, lambda: messagebox.showerror("AI 翻译失败", str(e)))

            threading.Thread(target=worker, daemon=True).start()

        def _ai_done(self, r):
            self.doc.modified.update(self.doc.texts)
            self.refresh_list()
            self.set_status("完成：记忆库 %d 条（0 花费）+ API 新译 %d 条（记得保存）"
                            % (r.get("reused", 0), r.get("api", 0)))

        # ---------- 杂项 ----------
        def set_status(self, t):
            self.status.set(t)

        def show_info(self):
            if not self.doc:
                return
            self.set_status("文件 %s | languageID=%d | stringCount=%d | 有文本 %d | 已修改 %d"
                            % (os.path.basename(self.doc.path), self.doc.lang,
                               self.doc.count, len(self.doc.texts), len(self.doc.modified)))

        def show_help(self):
            messagebox.showinfo("使用说明", __doc__)

        def on_close(self):
            if self.doc.modified and not messagebox.askyesno(
                    "退出", "有 %d 条修改未保存，确定退出？" % len(self.doc.modified)):
                return
            self.destroy()

    app = App(db_path=db_path, project=project)
    if test_hook:
        # ⚠ 必须 try/finally：旧写法 `(test_hook(app), app.destroy())` 在 test_hook 抛异常时
        #   **元组求值中断、destroy 不执行** → mainloop 永不返回 → 测试直接挂死。
        #   （Tk 的 after 回调异常只走 report_callback_exception，**不会**传到调用者。）
        def _run_hook():
            try:
                test_hook(app)
            except Exception:
                import traceback
                traceback.print_exc()
            finally:
                try:
                    app.destroy()
                except Exception:
                    pass
        app.after(200, _run_hook)
    app.mainloop()
    return 0


# ============================================================ 入口
def main():
    args = sys.argv[1:]
    if not args:
        return run_gui()
    cmd = args[0]
    pos, kw = [], {}
    for a in args[1:]:
        if a.startswith("--") and "=" in a:
            k, v = a[2:].split("=", 1)
            kw[k] = v
        elif a.startswith("--"):            # 不带 = 的旗标：--dry-run / --all
            kw[a[2:]] = "1"
        else:
            pos.append(a)
    if cmd == "info" and len(pos) == 1:
        cli_info(pos[0])
    elif cmd == "export" and len(pos) == 2:
        cli_export(pos[0], pos[1])
    elif cmd == "import" and len(pos) == 2:
        cli_import(pos[0], pos[1], kw.get("count"), kw.get("lang"))
    elif cmd == "punct" and len(pos) == 2 and pos[0] in ("cn", "en"):
        cli_punct(pos[1], pos[0])
    elif cmd == "translate" and len(pos) >= 1:
        cli_translate(pos[0], kw.get("key"), "all" in kw, kw.get("tm"))
    elif cmd == "update" and len(pos) == 2:
        cli_update(pos[0], pos[1], kw.get("key"), kw.get("tm"), "all" in kw, "dry-run" in kw)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
