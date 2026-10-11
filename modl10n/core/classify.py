#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`.nss` 显示文本判别器。

路线 B 的核心：不用 NWN MultiLang，自己判断一个字符串字面量是不是"给玩家看的文本"。

规则（从 Ch1 的 MultiLang 导出结果反推，并收紧为白名单，宁少勿错）：
    取该字面量"最内层的包围函数调用名"，只有名字在 DISPLAY_FUNCS 里才算显示文本。
    用白名单而非黑名单是刻意选择：脚本里大量字符串是 resref / 本地变量键 / 日志标签
    （GetObjectByTag("Saman01")、SetLocalInt(o,"ALCOHOL",1)、CreateItemOnObject("nw_wmgwn002")、
     GetTag(o) != "zararm001"），一旦误翻就直接坏档。

实现要点：
    * 先把源码做"代码骨架"：注释内容、字符串内容全部替换为等长空格（位置不变），
      只在骨架上追踪括号深度，避免注释/字符串里的括号干扰。
    * 从字面量位置向前找最内层未闭合的 '('，取紧邻其左侧的标识符——跨行调用自然支持。
"""
import re

# 紧邻 '(' 左侧的标识符
IDENT_TAIL = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*$")
# `变量名 =` 或 `变量名 +=` 结尾（用于识别字符串变量赋值）
ASSIGN_TAIL = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\+)?=\s*$")
# 纯标识符（资源名 / 常量 / 键）
IDENT_ONLY = re.compile(r"^[A-Za-z0-9_]+$")

# 被 MultiLang 认定为"显示"的函数（括号内为 Ch1 实测导出次数）
DISPLAY_FUNCS = {
    "FloatingTextStringOnCreature",   # 121
    "SpeakString",                    # 110
    "ActionSpeakString",              # 41
    "CutSpeakString",                 # 24
    "SendMessageToPC",                # 21
    "PopUpDeathGUIPanel",             # 7
    "SetCustomToken",                 # 3
    "DBG_msg",                        # 2
    # 玩家可见的名称/描述（MultiLang 漏了，实测 Ch1~Ch5 共 131 处）
    "SetName",                        # 105
    "SetDescription",                 # 26
    # 同族、Ch1 未出现或未激活，但语义一致，一并纳入
    "SendMessageToAllDMs",
    "PrintString",
    "WriteTimestampedLogEntry",
    "MyPrintString",
    "SpeakOneLinerConversation",      # 空字符串（对话 resref）会被单独排除
}

# 参数是资源名/数据键的函数：显式排除（白名单已经能挡住，这里是双保险 + 可读性）
NEVER = {
    "GetLocalInt", "SetLocalInt", "GetLocalFloat", "SetLocalFloat",
    "GetLocalString", "SetLocalString", "DeleteLocalString",
    "GetLocalObject", "SetLocalObject", "GetLocalLocation", "SetLocalLocation",
    "GetObjectByTag", "GetNearestObjectByTag", "GetWaypointByTag", "GetTag",
    "CreateObject", "CreateItemOnObject", "CutCreateObject", "ActionCreate",
    "ExecuteScript", "ActionStartConversation", "CutBeginConversation",
    "AddJournalQuestEntry", "RemoveJournalQuestEntry", "GetJournalQuestExperience",
    "PlaySound", "Get2DAString", "SetCampaignInt", "SetCampaignString",
    "JumpToObject", "JumpToLocation", "GetLocation", "SetMapPinEnabled",
    "GetStringLeft", "GetStringRight", "GetStringByStrRef",
    "SetUserDefinedItemEventPrefix", "HasItem", "GetItemPossessedBy",
    "DestroyObject", "ActionEquipItem", "ActionGiveItem", "SetLocked",
    "SetEncryptionKey", "ActionSpeakStringByStrRef",
}


def code_skeleton(text):
    """把注释内容、字符串内容替换成空格，保持字符位置与换行不变。"""
    out = list(text)
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\":
                # 转义：把两个字符都抹掉（保留长度）
                out[i] = " "
                if i + 1 < n and text[i + 1] != "\n":
                    out[i + 1] = " "
                i += 2
                continue
            if c == '"':
                in_str = False
                i += 1
                continue
            if c != "\n":
                out[i] = " "
            i += 1
            continue
        if c == '"':
            in_str = True
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


def enclosing_call(before_skeleton, maxlen=8000):
    """before_skeleton = 该字面量之前的代码骨架。返回最内层包围调用的函数名（None=不在调用参数里）。"""
    seg = before_skeleton[-maxlen:]
    depth = 0
    i = len(seg) - 1
    while i >= 0:
        c = seg[i]
        if c == ")":
            depth += 1
        elif c == "(":
            if depth == 0:
                m = IDENT_TAIL.search(seg[:i])
                return m.group(1) if m else None
            depth -= 1
        i -= 1
    return None


def classify_file(text, display_funcs=None, never=None, var_assign=False):
    """返回 [(lineno, lit_index, body, func, decision)]；lineno 为 0 基。

    var_assign=True 时，额外把"赋给字符串变量的、且不像资源名/标识符的字面量"也算显示文本
    （例：`string sPPf="Your criminal behavior has brought..."` —— 变量后面会送给显示函数）。
    这类条目在报告里会被单独标出来供人工确认。
    """
    from .sf import parse_nwscript_literals
    funcs = DISPLAY_FUNCS if display_funcs is None else display_funcs
    nev = NEVER if never is None else never
    lines = text.split("\r\n") if "\r\n" in text else text.split("\n")
    skel_lines = [code_skeleton(ln) for ln in lines]
    out = []
    prefix = ""
    for ln, line in enumerate(lines):
        skel = skel_lines[ln]
        for li, (s0, e0, body) in enumerate(parse_nwscript_literals(line)):
            if not body:
                continue
            fn = enclosing_call(prefix + skel[:s0])
            if fn in nev:
                decision = "skip:data-func"
            elif fn in funcs:
                if fn == "SpeakOneLinerConversation" and body.strip() == "":
                    decision = "skip:empty-conv"
                else:
                    decision = "translate"
            elif fn is None and var_assign and ASSIGN_TAIL.search(skel[:s0]) \
                    and not is_ident_like(body):
                decision = "translate:var-assign"
            elif fn is None:
                decision = "skip:no-call"
            else:
                decision = "skip:unknown-func"
            out.append((ln, li, body, fn, decision))
        prefix += skel + "\n"
    return out


def is_ident_like(body):
    s = body.strip()
    return bool(s) and bool(IDENT_ONLY.match(s))


def extract_nss_display(text, **kw):
    """只返回要翻译的字面量：[(lineno, lit_index, body)]。"""
    return [(ln, li, b) for ln, li, b, _f, d in classify_file(text, **kw)
            if d.startswith("translate")]
