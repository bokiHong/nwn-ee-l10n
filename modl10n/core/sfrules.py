#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享规则：可翻译性判定、换行处理、译文校验、标点规范化。"""
import re

# ---------------------------------------------------------------- 不可翻译的字面量
# .nss 里形如资源名 / 脚本常量的字符串（如 "NW_IT_MPOTION009"、"NW_ATTACK_MY_TARGET"）
# 一旦翻译会直接破坏脚本逻辑，必须原样保留。
NONTEXT_RE = re.compile(r"^[A-Za-z0-9_]+$")

# 占位符 / 标记
TOKEN_RE = re.compile(r"</?[A-Za-z][^>]*>|%\([^)]*\)|%[sdif]|\$\{[^}]*\}")


def is_nontext_literal(text, resrefs=frozenset()):
    """判断 .nss 字符串字面量是否"不该翻译"（资源引用 / 常量名）。"""
    s = text.strip()
    if not s:
        return True
    if s.lower() in resrefs:
        return True
    if NONTEXT_RE.match(s):
        return True
    return False


# ---------------------------------------------------------------- 换行处理
NL = "\\n"          # SF01 文本中用两个字符表示换行（GFF 真换行 / .nss 转义）


def split_lines(text):
    return text.split(NL)


def join_lines(parts):
    return NL.join(parts)


# ---------------------------------------------------------------- 译文校验
_CN_DIGIT = "零一二三四五六七八九"


def _cn_int(n):
    """0..999999 的简体中文数字（标准写法）。"""
    if n == 0:
        return "零"
    units = ["", "万", "亿"]

    def _four(x):
        s, zero = "", False
        for i, u in enumerate(["", "十", "百", "千"]):
            d = x % 10
            x //= 10
            if d == 0:
                zero = True
            else:
                s = _CN_DIGIT[d] + u + ("零" if (zero and s) else "") + s
                zero = False
        return s

    out, i = "", 0
    while n:
        part = n % 10000
        if part:
            out = _four(part) + units[i] + out
        n //= 10000
        i += 1
    return out


def _cn_number_variants(n):
    """阿拉伯数字 n 的**常见中文写法**（"数字是否丢失"的豁免用）。

    ★ 2026-10-11 加：中文里 `Level 2` 写「二层 / 第二层」、`10000 gold` 写「一万金币」
      都是正常译法，但原判据只比对阿拉伯数字 → 会误判"丢失数字" → 整条**回退成英文** ✗
      （实测：Ch4/Ch5 共 6 条因此变英文）。命中任一候选写法即算保留 ✓。

    ⚠ 刻意**不覆盖数字 1** —— 「一」在中文里太常见（"一个"、"一起"），豁免它会把
      "真丢了数字 1" 也放过 ✗。`Part 1` 这类只能人工处理或另想办法。
    """
    out = set()
    try:
        v = int(n)
    except (TypeError, ValueError):
        return out
    if v < 2 or v > 999999:
        return out
    s = _cn_int(v)
    out.add(s)
    if s.startswith("一十"):
        out.add(s[1:])            # 「十二」比「一十二」更常用
    if v == 2:
        out.add("两")
    if v % 10 == 2 and v > 10:
        out.add(_cn_int(v - 2) + "两")     # 22 -> 「二十二」的「两」变体（保守）
    return out


def validate(src, dst):
    """返回问题列表（空列表=通过）。"""
    problems = []
    if not dst or not dst.strip():
        problems.append("译文为空")
        return problems
    if src.count(NL) != dst.count(NL):
        problems.append("换行标记 \\n 数量不一致: 原文 %d, 译文 %d" % (src.count(NL), dst.count(NL)))
    miss = set(TOKEN_RE.findall(src)) - set(TOKEN_RE.findall(dst))
    if miss:
        problems.append("丢失标记: " + ", ".join(sorted(miss))[:120])
    # 数字串必须保留（1d6 / +2 / DC 15 / 20% 等）。三条豁免（实测都是误报）：
    #   ① 序数 1st/2nd/3rd/4th…：中文写「第一/第五」是正常译法，不强求数字原样出现；
    #   ② 千分位 2,000：中文写成 2000 也正常，先去掉千分位逗号再比较；
    #   ③ 中文数字写法：`Level 2`→「二层」、`10000`→「一万」（见 `_cn_number_variants`）；
    #   ④ 百分数 `100%`→「百分之百」。
    src_n = re.sub(r"(?<=\d),(?=\d\d\d)", "", src)
    sd = []
    for m in re.finditer(r"\d+", src_n):
        if src_n[m.end():m.end() + 2].lower() in ("st", "nd", "rd", "th"):
            continue
        sd.append(m.group(0))
    dd = set(re.findall(r"\d+", dst.replace(",", "")))
    lost = []
    for n in sd:
        if n in dd:
            continue
        if any(x in dst for x in _cn_number_variants(n)):
            continue
        if re.search(r"(?<!\d)" + re.escape(n) + r"\s*%", src_n) and "百分之" in dst:
            continue
        lost.append(n)
    if lost:
        problems.append("可能丢失数字: " + ", ".join(sorted(set(lost))[:8]))
    return problems


# ---------------------------------------------------------------- 标点规范化
_PUNCT_MAP = {
    "。": ".", "．": ".", "，": ",", "、": ",", "：": ":", "；": ";",
    "（": "(", "）": ")", "！": "!", "？": "?", "“": '"', "”": '"',
    "‘": "'", "’": "'", "…": "...", "—": "-", "–": "-", "―": "-",
    "【": "[", "】": "]", "《": "<", "》": ">", "「": '"', "」": '"',
    "『": '"', "』": '"', "〜": "~", "‧": "-",
}
_TAG_RE = re.compile(r"<[^>]*>")


def _half(s):
    for a, b in _PUNCT_MAP.items():
        s = s.replace(a, b)
    out = []
    for ch in s:
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:
            out.append(chr(o - 0xFEE0))
        elif o == 0x3000:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def normalize_punct(s, mode="full"):
    """mode="full" 保留中文全角标点；mode="half" 全部转英文半角（保护 <...> 标签内字节）。"""
    if mode != "half":
        return s
    tags = _TAG_RE.findall(s)
    if not tags:
        return _half(s)
    parts = _TAG_RE.split(s)
    res = _half(parts[0])
    for i, t in enumerate(tags):
        res += t + _half(parts[i + 1])
    return res


def escape_nwscript(s):
    """把译文写成合法的 nwscript 字符串字面量内容（不处理 \\n：它本身就是我们要保留的转义）。"""
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            nxt = s[i + 1] if i + 1 < len(s) else ""
            if nxt in ("n", "t", "\\", '"'):
                out.append(c + nxt)
                i += 2
                continue
            out.append("\\\\")
            i += 1
            continue
        if c == '"':
            out.append('\\"')
            i += 1
            continue
        if c == "\n":
            out.append("\\n")
            i += 1
            continue
        if c == "\r":
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def unescape_sf(text):
    """SF01 表示 -> 真实文本（仅 \\n）。"""
    return text.replace(NL, "\n")


def escape_gff(s):
    """GFF 文本 -> 统一表示：反斜杠先转义，真实换行再转成两字符 \\n。

    先转义反斜杠是为了可逆：有些 GFF 文本本身含字面量反斜杠（例如 `";\\n`），
    不转义的话回写时会和换行标记混淆（Swordflight Ch2 就有一例）。
    """
    return s.replace("\\", "\\\\").replace("\n", NL)


def unescape_gff(s):
    """escape_gff 的逆运算。"""
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nx = s[i + 1]
            if nx == "n":
                out.append("\n")
                i += 2
                continue
            if nx == "\\":
                out.append("\\")
                i += 2
                continue
        out.append(c)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------- 标记遮蔽
# 形如 <his/her>、<race>、<man/woman> 的引擎变量看起来"像内容"，模型很容易把它翻译掉
# （例如 <his/her> -> <他/她>），那样游戏的变量替换就失效了。做法：翻译前把这些标记换成
# 私有区占位符（模型不会去改它），译文回来后再换回原标记。
PH_L, PH_R = "\ue000", "\ue001"


def mask_tokens(text):
    """返回 (遮蔽后的文本, {占位符: 原标记})。"""
    mapping = {}
    out = text
    for i, tk in enumerate(dict.fromkeys(TOKEN_RE.findall(text))):
        ph = "%s%d%s" % (PH_L, i, PH_R)
        if tk in out and ph not in out:
            out = out.replace(tk, ph)
            mapping[ph] = tk
    return out, mapping


def unmask_tokens(text, mapping):
    for ph, tk in mapping.items():
        text = text.replace(ph, tk)
    return text


def has_placeholders(text):
    return PH_L in text
