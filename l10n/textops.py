#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯文本规则（标点转换 / 规范化 / 可译性判定）—— 从 `tlk tool.py` 原样搬来。

**零 IO、零 tk、零网络。** GUI 与 CLI 共用同一份实现。

⚠ 三个"名字像、语义完全不同"的东西，别混：

| 函数 | 用途 | 语义 |
|---|---|---|
| `normalize()` | 写记忆库前的**译文清洗** | 全角→半角 + NFKD 去组合符（**会改变文本**） |
| `keys.norm()`（在 `l10n/keys.py`） | 翻译记忆的 **key 规范化** | 统一换行 + 去首尾空白（**只为匹配**） |
| `needs_translation()` | 这条还要不要翻 | 委托 `modl10n.tlk.needs_translation`（**先 norm**） |

另外 `find_untranslated()` 用的是**原始文本**判据（`mostly_ascii` + `[A-Za-z]{2,}`），
与 `needs_translation` **不等价** —— 对"首尾带空白的英文"可能差几条。两者各自保留
原语义，谁都不许"顺手统一"。
"""
import re
import unicodedata

_TAG_RE = re.compile(r"<[^>]*>")


# ============================================================ 标点：半角 -> 全角
def _is_cjk(ch):
    if not ch:
        return False
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF      # 汉字
            or 0x3000 <= o <= 0x303F   # 中文标点
            or 0xFF00 <= o <= 0xFFEF)  # 全角符号


def _next_nonspace(s, i):
    j = i + 1
    while j < len(s) and s[j] == " ":
        j += 1
    return s[j] if j < len(s) else ""


def _prev_nonspace(s, i):
    j = i - 1
    while j >= 0 and s[j] == " ":
        j -= 1
    return s[j] if j >= 0 else ""


def _pair_quotes(s):
    out, op = [], True
    for ch in s:
        if ch == '"':
            out.append("“" if op else "”")
            op = not op
        else:
            out.append(ch)
    return "".join(out)


def _cn_plain(s):
    """半角 -> 中文全角（只在中文语境；数字/英文/占位符/纯英文不受影响）。

    转成全角标点后会顺手吃掉紧跟的半角空格，符合中文排版习惯。
    """
    s = re.sub(r"(?<=[\u4e00-\u9fff])\.\.\.", "……", s)
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", s))
    out, open_parens = [], 0
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        prev = s[i - 1] if i > 0 else ""
        nxt = s[i + 1] if i + 1 < n else ""
        nsp = _next_nonspace(s, i)
        psp = _prev_nonspace(s, i)
        drop = False
        if ch == ".":
            if _is_cjk(prev) or (prev.isdigit() and nxt in ("\r", "\n", "")):
                ch, drop = "。", True
        elif ch == ",":
            if _is_cjk(prev) or (has_cjk and _is_cjk(nsp)
                                 and not (prev.isdigit() and nxt.isdigit())):
                ch, drop = "，", True
        elif ch == ":":
            if _is_cjk(prev) or (has_cjk and (_is_cjk(nsp) or prev.isdigit())):
                ch, drop = "：", True
        elif ch == ";":
            if _is_cjk(prev) or (has_cjk and _is_cjk(nsp)):
                ch, drop = "；", True
        elif ch == "!":
            if _is_cjk(prev):
                ch, drop = "！", True
        elif ch == "?":
            if _is_cjk(prev):
                ch, drop = "？", True
        elif ch == "(":
            if has_cjk and (_is_cjk(psp) or _is_cjk(nsp)):
                ch = "（"
                open_parens += 1
                if out and out[-1] == " ":
                    out.pop()
        elif ch == ")":
            if open_parens > 0:
                ch = "）"
                open_parens -= 1
        out.append(ch)
        if drop and nxt == " ":
            i += 1          # 吞掉全角标点后的半角空格
        i += 1
    return _pair_quotes("".join(out))


_EN_MAP = {"。": ".", "，": ",", "、": ",", "：": ":", "；": ";", "（": "(", "）": ")",
           "！": "!", "？": "?", "“": '"', "”": '"', "‘": "'", "’": "'",
           "…": "...", "—": "-", "－": "-", "　": " ", "～": "~"}


def _en_plain(s):
    for a, b in _EN_MAP.items():
        s = s.replace(a, b)
    return re.sub(r"……", "...", s)


def _with_tags(text, fn):
    """跳过 `<...>` 标签，只对标签**之间**的文本做处理。"""
    tags = _TAG_RE.findall(text)
    if not tags:
        return fn(text)
    parts = _TAG_RE.split(text)
    res = fn(parts[0])
    for i, t in enumerate(tags):
        res += t + fn(parts[i + 1])
    return res


def to_cn_punct(text):
    """半角标点 -> 中文全角（保留 `<...>` 标签内内容）。"""
    return _with_tags(text, _cn_plain)


def to_en_punct(text):
    """中文全角标点 -> 英文半角。"""
    return _with_tags(text, _en_plain)


# ============================================================ 规范化（写库前清洗）
_PUNCT = {"。": ".", "．": ".", "，": ",", "、": ",", "：": ":", "；": ";",
          "（": "(", "）": ")", "！": "!", "？": "?", "“": '"', "”": '"',
          "‘": "'", "’": "'", "…": "...", "—": "-", "–": "-", "―": "-",
          "【": "[", "】": "]", "《": "<", "》": ">", "「": '"', "」": '"',
          "『": '"', "』": '"', "〜": "~", "‧": "-", "\xad": "-"}


def _norm_text(s):
    for a, b in _PUNCT.items():
        s = s.replace(a, b)
    out = []
    for ch in s:
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:
            ch = chr(o - 0xFEE0)
        elif o == 0x3000:
            ch = " "
        out.append(ch)
    return "".join(c for c in unicodedata.normalize("NFKD", "".join(out))
                   if not unicodedata.combining(c))


def normalize(s):
    """写记忆库前的译文清洗（**会改变文本**，不是 `keys.norm`）。"""
    return _with_tags(s, _norm_text)


# ============================================================ 可译性判定
def mostly_ascii(s):
    """非空白字符里 ASCII 占比 > 0.9。"""
    core = [c for c in s if not c.isspace()]
    return bool(core) and sum(1 for c in core if ord(c) < 128) / len(core) > 0.9


def find_untranslated(texts):
    """找出"以 ASCII 为主且有 2+ 连字母"的 id（GUI 的"只看未译"用它）。

    ⚠ 与 `modl10n.tlk.needs_translation` **不等价**（后者先 `norm` 去首尾空白），
    这里用的是原始文本 —— 保持原语义，别替换。
    """
    return [i for i, t in texts.items()
            if mostly_ascii(t) and re.search(r"[A-Za-z]{2,}", t)]


def needs_translation(text):
    """委托 `modl10n.tlk.needs_translation`（TLK 流程用，判据会先 norm）。"""
    from modl10n.tlk import needs_translation as _nt
    return _nt(text)
