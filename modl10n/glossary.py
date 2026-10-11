#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""术语表：专有名词（人名/地名/神祇/法术名）统一。

用法分三步：
  1. `build_candidates()` 从已有翻译记忆里挖出"独立成条的名称 -> 译名"候选；
  2. 人工/LLM 审校成 glossary.json（只收人名地名神祇法术名，不收普通名词）；
  3. 翻译时 `prompt_block()` 把"本句命中的术语"注入提示，`check()` 在译后强校验。

glossary.json 结构：
{
  "version": 1,
  "terms": [
    {"en": "Zarala", "zh": "扎拉拉", "type": "name", "note": ""},
    ...
  ]
}
"""
import os
import re
import json

# 独立成条、且是"单个大写开头的词"或"多词专名"
NAME_RE = re.compile(r"^[A-Z][A-Za-z'’\-]{2,20}$")
MULTI_RE = re.compile(r"^[A-Z][A-Za-z'’\-]+(?: [A-Z][A-Za-z'’\-]+){1,3}$")
ZH_NAME_RE = re.compile(r"^[\u4e00-\u9fff·]{2,10}$")


class Glossary(object):
    def __init__(self, terms=None):
        self.terms = []
        self._by_en = {}
        self._by_req = {}
        for t in (terms or []):
            self.add(t["en"], t["zh"], t.get("type", "name"), t.get("note", ""),
                     t.get("required", True))

    def add(self, en, zh, type_="name", note="", required=True):
        if not en or not zh:
            return
        self.terms.append({"en": en, "zh": zh, "type": type_, "note": note,
                           "required": required})
        self._by_en[en] = zh
        self._by_req[en] = required

    def __len__(self):
        return len(self.terms)

    @property
    def required_terms(self):
        return [t for t in self.terms if t.get("required", True)]

    @classmethod
    def load(cls, path):
        if not path or not os.path.exists(path):
            return cls([])
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(data.get("terms", []))

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "terms": self.terms}, f, ensure_ascii=False, indent=2)

    # ---------------------------------------------------------------- 匹配
    def _rx(self, en):
        # 单词边界必须对 <...> 之类的标记也成立，故用自定义边界
        return re.compile(r"(?<![A-Za-z])" + re.escape(en) + r"(?![A-Za-z])")

    def hits(self, text):
        """返回本句出现的术语 [(en, zh)]。"""
        out = []
        for t in self.terms:
            if self._rx(t["en"]).search(text):
                out.append((t["en"], t["zh"]))
        return out

    def prompt_block(self, text, max_terms=40):
        hs = self.hits(text)
        if not hs:
            return ""
        hs = hs[:max_terms]
        hard = [(e, z) for e, z in hs if self._by_req.get(e, True)]
        soft = [(e, z) for e, z in hs if not self._by_req.get(e, True)]
        lines = []
        if hard:
            lines.append("【固定译名·必须使用】下列词出现在原文里，译文中必须原样使用括号内的中文，不得另译：")
            lines += ["  %s = %s" % (e, z) for e, z in hard]
        if soft:
            lines.append("【建议译名】尽量沿用（若语境确实不合可微调）：")
            lines += ["  %s = %s" % (e, z) for e, z in soft]
        return "\n".join(lines)

    def check(self, src, dst):
        """译后校验：原文命中的【必守】术语，译文里是否用上了规范译名。返回未命中的 [(en, zh)]。"""
        bad = []
        for en, zh in self.hits(src):
            if not self._by_req.get(en, True):
                continue
            if zh not in dst:
                bad.append((en, zh))
        return bad

    # ---------------------------------------------------------------- 候选挖掘
    @staticmethod
    def build_candidates(pairs, min_len=2):
        """pairs: [(英文原文, 中文译文)] -> [{'en','zh'}] 候选（仅"独立成条的名称"）。"""
        out = {}
        for en, zh in pairs:
            if not en or not zh or en == zh:
                continue
            e, z = en.strip(), zh.strip()
            if not (NAME_RE.match(e) or MULTI_RE.match(e)):
                continue
            if not ZH_NAME_RE.match(z):
                continue
            out[e] = z
        return [{"en": k, "zh": v} for k, v in sorted(out.items())]
