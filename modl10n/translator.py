#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""翻译器：DeepSeek 批量翻译 + 校验 + 分级兜底 + 术语表 + 翻译记忆。

这是把 Swordflight Chapter One 那次实战里踩到的坑全部固化下来的版本：
  * 内容哈希做 key（同一句英文只翻一次，跨 mod 复用）
  * 批量 JSON 翻译，失败逐条降级
  * 校验：换行标记 \\n 数量、<...>/%(...) 标记、数字串必须保留
  * 分级兜底：批量 → 标记提示重译 → 遮蔽重译 → 逐行 → 回退原文
      - "标记提示"专门治 <his/her> 这类引擎变量被模型翻译掉的毛病
      - "遮蔽"用私有区占位符，注意模型可能把占位符"脑补"成 <CUSTOM0>，所以不是首选
  * 术语表：命中术语注入提示 + 译后强校验
  * 指数退避重试；每条即时落盘
"""
import os
import re
import sys
import json
import time
import random
import threading
import collections
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .core import sfrules as R
from .tm import key_of

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

BATCH_CHARS = 3500
BATCH_ITEMS = 25
MAX_TOKENS = 8192
TEMPERATURE = 0.25
MAX_RETRIES = 8
SINGLE_RETRY = 3

SYSTEM_PROMPT = (
    "你是资深游戏本地化译者，负责把《无冬之夜：增强版》(Neverwinter Nights: Enhanced Edition) "
    "模组（D&D 费伦背景）的英文文本翻译成简体中文。\n"
    "严格遵守：\n"
    "1. 只翻译给出的文本本身，输出中不要任何解释、注释、引号包裹或多余文字。\n"
    "2. 原样保留所有标记与占位符，一个字符都不能改：<CUSTOM0>、<FirstName>、<his/her>、<race>、"
    "<StartCheck>、</Start>、%(NAME) 等尖括号/百分号标记。\n"
    "3. 原样保留数字与骰子表达式：1d6、2d4+2、+2、DC 15、20%、-1 等。\n"
    "4. 文本里的 \\n 是两个字符表示的换行标记，必须原样保留，数量和位置都不能变。\n"
    "5. 人名、地名、神祇、法术、专有名词采用通行的 D&D 简体中文译名"
    "（如 Tyr=提尔、Calimshan=卡林杉、Faerun=费伦、Waterdeep=深水城）；无通行译名的音译。\n"
    "6. 语气忠于原文：NPC 对话口语化，物品描述保持书面语，规则文本用规范术语。\n"
    "7. 用简体中文，不要夹杂英文。"
)


def read_api_key(explicit=None):
    if explicit:
        return explicit
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if k:
        return k
    for p in (os.path.join(os.path.dirname(os.path.abspath(__file__)), ".api_key"),
              os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "sf01", ".api_key")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            continue
    return ""


class Translator(object):
    def __init__(self, api_key, glossary=None, punct="full", mask_batch=False,
                 model=MODEL, base_url=BASE_URL):
        self.key = api_key
        self.glossary = glossary
        self.punct = punct
        self.mask_batch = mask_batch
        self.model = model
        self.base_url = base_url
        self.warnings = []
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- HTTP
    def _post(self, messages, json_mode=True, max_tokens=MAX_TOKENS):
        url = self.base_url + "/chat/completions"
        headers = {"Authorization": "Bearer " + self.key, "Content-Type": "application/json"}
        body = {"model": self.model, "messages": messages,
                "temperature": TEMPERATURE, "stream": False}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if max_tokens:
            body["max_tokens"] = max_tokens
        last = None
        for attempt in range(MAX_RETRIES):
            try:
                r = requests.post(url, headers=headers, json=body, timeout=300)
                if r.status_code == 200:
                    return r.json()["choices"][0]["message"]["content"]
                if r.status_code == 401:
                    raise RuntimeError("API Key 无效(401)")
                if r.status_code == 402:
                    raise RuntimeError("API 余额不足(402)")
                last = RuntimeError("API %d: %s" % (r.status_code, r.text[:200]))
            except requests.exceptions.RequestException as e:
                last = e
            if attempt < MAX_RETRIES - 1:
                wait = min(90.0, (2 ** attempt) + random.uniform(0, 1.5))
                print("  [重试 %d/%d] %.1fs: %s" % (attempt + 1, MAX_RETRIES - 1, wait, last), flush=True)
                time.sleep(wait)
        raise RuntimeError("重试耗尽: %s" % last)

    # ---------------------------------------------------------------- 提示词
    def _sys(self):
        return SYSTEM_PROMPT

    def _hint(self, text):
        """本句命中的术语 -> 提示片段。"""
        if not self.glossary:
            return ""
        return self.glossary.prompt_block(text)

    # ---------------------------------------------------------------- 批量
    def translate_batch(self, pairs):
        """pairs: [(key, text)] -> {key: 译文}"""
        payload, maps, hints = {}, {}, []
        for k, t in pairs:
            if self.mask_batch:
                m, mp = R.mask_tokens(t)
                if mp:
                    payload[k] = m
                    maps[k] = mp
                    continue
            payload[k] = t
        gl = {}
        for k, t in pairs:
            b = self._hint(t)
            if b:
                gl[k] = b
        user = ("把下面 JSON 对象里每个值翻译成简体中文，键保持不变，返回同样键集合的 JSON 对象。"
                "值里的 \\n 是换行标记，必须原样保留。只输出 JSON：\n"
                + json.dumps(payload, ensure_ascii=False))
        if maps:
            user += ("\n\n注意：值里形如 %s数字%s 的片段是必须原样保留的变量占位符，"
                     "不要翻译、改动或删除。" % (R.PH_L, R.PH_R))
        if gl:
            user += "\n\n【术语要求】\n" + "\n".join(
                "  key=%s:\n%s" % (k, v) for k, v in list(gl.items())[:12])
        messages = [{"role": "system", "content": self._sys()},
                    {"role": "user", "content": user}]

        def fix(out):
            if maps:
                out = {k: R.unmask_tokens(v, maps.get(k, {})) for k, v in out.items()}
            return out

        for attempt in range(SINGLE_RETRY):
            raw = self._post(messages, json_mode=True)
            try:
                data = json.loads(re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", raw.strip()))
                if isinstance(data, dict):
                    out = {str(k): str(v) for k, v in data.items()}
                    missing = [k for k, _ in pairs if k not in out]
                    if not missing:
                        return fix(out)
                    messages[-1] = {"role": "user",
                                    "content": user + "\n\n上次缺少这些键，请补齐："
                                               + ", ".join(missing)}
            except (ValueError, TypeError) as e:
                messages[-1] = {"role": "user",
                                "content": user + "\n\n上次不是合法 JSON（%s），请只输出 JSON。" % e}
        raise RuntimeError("批量 JSON 重试失败")

    # ---------------------------------------------------------------- 单条各档
    def _single(self, text, extra=""):
        user = ("把下面这段英文翻译成简体中文，只输出译文本身。"
                "必须原样保留所有 <...> 标记、%(...) 占位符、数字和 \\n 换行标记：\n\n" + text)
        h = self._hint(text)
        if h:
            user += "\n\n" + h
        if extra:
            user += "\n\n【特别注意】" + extra
        return self._post([{"role": "system", "content": self._sys()},
                           {"role": "user", "content": user}], json_mode=False).strip()

    def _hint_tokens(self, text):
        """把 <...> 标记逐条列出，要求原样保留（治 <his/her> 被翻译）。"""
        toks = collections.Counter(R.TOKEN_RE.findall(text))
        if not toks:
            return self._single(text)
        listed = "\n".join("- %s   （必须在译文中原样出现 %d 次）" % (tk, n)
                           for tk, n in toks.items())
        user = ("把下面这段英文翻译成简体中文，只输出译文本身。\n"
                "特别注意：下列片段是游戏引擎的变量标记，**必须逐字符原样出现在译文中**，"
                "出现次数也要一致；绝对不要翻译它们、不要换成中文、不要改大小写或斜杠：\n"
                + listed + "\n\n原文：\n" + text)
        h = self._hint(text)
        if h:
            user += "\n\n" + h
        return self._post([{"role": "system", "content": self._sys()},
                           {"role": "user", "content": user}], json_mode=False).strip()

    def _masked(self, text):
        masked, mp = R.mask_tokens(text)
        if not mp:
            return self._single(text)
        user = ("把下面这段英文翻译成简体中文，只输出译文本身。\n"
                "重要：文本里形如 %s数字%s 的片段是**必须原样保留的变量占位符**，"
                "不要翻译、不要改动、不要删除、不要移动。\n"
                "另外保留所有数字和 \\n 换行标记：\n\n" % (R.PH_L, R.PH_R) + masked)
        return R.unmask_tokens(
            self._post([{"role": "system", "content": self._sys()},
                        {"role": "user", "content": user}], json_mode=False).strip(), mp)

    def _by_lines(self, text):
        parts = R.split_lines(text)
        out = []
        for p in parts:
            out.append(p if not p.strip() else self._single(p))
        return R.join_lines(out)

    def _problems(self, src, dst):
        probs = R.validate(src, dst)
        if self.glossary:
            for en, zh in self.glossary.check(src, dst):
                probs.append("术语未统一: %s 应为 %s" % (en, zh))
        return probs

    def best(self, key, text, log=None):
        """分级兜底，返回 (译文 or None, 档位标签, 问题列表)。

        ★ 2026-10-11：**"术语未统一"不再导致回退原文** ✗ —— 术语表里可能有脏数据或
          写法差异（`Wilder = 狂念者任意`、`Nentyar Hunter` 对"南提亚 猎人"），
          为它把整条译文丢掉、让用户看到半英文章节，代价远大于收益。
          所以循环里顺手留下"**只有术语问题、没有结构问题**"的候选，最后兜底接受它 ✓
          （零额外 API 调用；术语统一交给 0 花费的事后回治：rename / 违规检查）。
          **结构性问题**（`R.validate`：占位符、`<...>` 标记、换行、数字丢失）照旧必须拒绝 ✓。
        """
        stages = (("标记提示", self._hint_tokens), ("遮蔽", self._masked), ("单条", self._single))
        last_probs = []
        term_only = None          # (候选译文, 档位) —— 只有术语问题、结构没问题
        for tag, fn in stages:
            for _ in range(SINGLE_RETRY):
                try:
                    cand = R.normalize_punct(fn(text), self.punct)
                except RuntimeError as e:
                    self._warn(key, tag + "失败", str(e), text)
                    break
                probs = self._problems(text, cand)
                if not probs:
                    return cand, tag, []
                last_probs = probs
                if term_only is None and not R.validate(text, cand):
                    term_only = (cand, tag)
                if log:
                    log("   [%s] 未过校验：%s" % (tag, probs))
        try:
            cand = R.normalize_punct(self._by_lines(text), self.punct)
            if not self._problems(text, cand):
                return cand, "逐行", []
            if term_only is None and not R.validate(text, cand):
                term_only = (cand, "逐行")
        except RuntimeError as e:
            self._warn(key, "逐行失败", str(e), text)
        if term_only is not None:
            cand, tag = term_only
            if log:
                log("   [%s] 仅术语未统一 → 接受译文（不回退原文）" % tag)
            self._warn(key, "术语待统一", "; ".join(last_probs)[:300], text)
            return cand, tag + "·术语待统一", last_probs
        return None, "", last_probs

    def _warn(self, key, kind, detail, src=""):
        with self._lock:
            self.warnings.append({"key": key, "kind": kind, "detail": str(detail)[:300],
                                  "src": src[:200]})

    # ---------------------------------------------------------------- 主循环
    def run(self, pending, tm, src_mod=None, workers=5, progress=True):
        """pending: [(key, text)]。返回统计 dict。把结果写进 tm。"""
        stats = collections.Counter()
        if not pending:
            return stats
        batches, cur, curlen = [], [], 0
        for item in pending:
            cur.append(item)
            curlen += len(item[1])
            if curlen >= BATCH_CHARS or len(cur) >= BATCH_ITEMS:
                batches.append(cur)
                cur, curlen = [], 0
        if cur:
            batches.append(cur)
        total = len(pending)
        lock = threading.Lock()
        done = [0]

        def accept(key, src, dst, tag=""):
            tm.put(key, src, dst, src_mod, status=("fallback" if tag == "回退原文" else "auto"))
            with lock:
                done[0] += 1
                i = done[0]
                stats["done"] += 1
                if tag:
                    stats["via_" + tag] += 1
            if progress:
                print("[%d/%d] %5.1f%% %-14s %s" %
                      (i, total, i * 100.0 / total, tag or "批量",
                       src[:40].replace("\n", " ")), flush=True)

        def log(msg):
            if progress:
                print(msg, flush=True)

        def handle(batch):
            try:
                res = self.translate_batch(batch)
            except RuntimeError as e:
                print("  批失败（%s），转逐条。" % e, flush=True)
                res = {}
            for k, t in batch:
                tr = res.get(k)
                if tr is not None:
                    cand = R.normalize_punct(tr, self.punct)
                    if not self._problems(t, cand):
                        accept(k, t, cand)
                        continue
                cand, tag, probs = self.best(k, t, log)
                if cand is not None:
                    accept(k, t, cand, tag)
                    continue
                self.warnings.append({"key": k, "kind": "回退原文",
                                      "detail": "所有档位均未过校验: %s" % probs,
                                      "src": t[:200]})
                accept(k, t, t, "回退原文")

        print("并发 %d 路，共 %d 批，待译 %d 条。" % (workers, len(batches), total))
        errors = []
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = [ex.submit(handle, b) for b in batches]
            for fu in as_completed(futs):
                # 关键：必须取回结果，否则工作线程里的异常会被静默吞掉（曾经因此白花钱）
                try:
                    fu.result()
                except Exception as e:
                    errors.append(repr(e))
                    print("  [批处理异常] %r" % (e,), flush=True)
        tm.commit()
        stats["warnings"] = len(self.warnings)
        if errors:
            stats["batch_errors"] = len(errors)
        return stats
