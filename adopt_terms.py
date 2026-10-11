#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""C：把规则书的译名批量落到 PRC8 记忆库（只处理"英文原文恰好等于该术语"的条目 ✓）。

判定原则（我替你判断 ✓）：
  * **采用**：标准 D&D 专名（职业/进阶职业/流派/招式/法术学派/灵能系/核心规则术语）✓
  * **跳过**：上下文相关的通用词（requirements / moderate / electricity / material /
    duration / concentration / intelligence / knowledge / equipment / treasure …）✗

安全：只改 src 恰好等于该术语的条目（术语条目 ✓），改前备份记忆库，写完标 status=manual ✓。
用法：python adopt_terms.py [--apply]
"""
import os
import re
import sys
import time
import shutil
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
TM = os.path.join(HERE, "modl10n", "tm.sqlite")
BACKUP_DIR = os.path.join(HERE, "..", "tmp", "备份")

ADOPT = {
    # —— 九剑（ToB）——
    "crusader": "天诛之剑", "swordsage": "贤者之剑", "warblade": "军道之剑",
    "desert wind": "漠风", "devoted spirit": "虔心", "diamond mind": "钢魂",
    "setting sun": "暮日", "shadow hand": "影手", "stone dragon": "石龙",
    "tiger claw": "虎爪", "white raven": "白鸦",
    "counter charge": "反制冲锋", "step of the wind": "风之步",
    "stone bones": "石铸身躯", "burning blade": "红莲之剑", "martial spirit": "武技之魂",
    "hunter's sense": "狩猎嗅觉", "sudden leap": "猛跃", "shadow sun ninja": "影日忍者",
    "bloodstorm blade": "暴血之刃",
    # —— 职业 / 进阶职业 ——
    "hexblade": "魔剑客", "factotum": "杂学士", "beguiler": "欺愚者", "virtuoso": "表演家",
    "alienist": "异形学家", "contemplative": "虔心祈者", "shugenja": "修验者",
    "swashbuckler": "游荡剑客", "thrallherd": "奴役者", "unseen seer": "无踪先知",
    "sublime chord": "超凡之弦", "fist of zuoken": "汝欧肯之拳", "war mind": "战魂",
    "sanctified mind": "圣魂", "frost mage": "冰霜法师", "frostrager": "冰霜狂怒者",
    "dirgesinger": "哀伤咏叹者", "stormlord": "风暴领主", "blighter": "枯萎者",
    "killoren": "麒灵", "warforged": "战俑", "changeling": "半变形怪", "duskblade": "暮刃",
    "dragon shaman": "龙之萨满", "shadowcaster": "暗影秘使", "githzerai": "吉斯泽莱人",
    "illithid slayer": "灵吸怪杀手", "mind blade": "念刃", "mindblade": "念刃",
    # —— 法术学派 / 灵能系 ——
    "abjuration": "防护系", "conjuration": "咒法系", "divination": "预言系",
    "enchantment": "附魔系", "evocation": "塑能系", "illusion": "幻术系",
    "necromancy": "死灵系", "transmutation": "变化系",
    "clairsentience": "心灵预言系", "metacreativity": "心灵创造系",
    "psychokinesis": "心灵转化系", "psychometabolism": "心灵自塑系",
    "psychoportation": "心灵传送系", "psionic powers": "心灵异能",
    # —— 核心规则术语 ——
    "bonus feat": "奖励专长", "martial discipline": "流派", "two-weapon fighting": "双武器攻击",
    "saving throw": "豁免检定", "temporary hit points": "暂时生命值",
    "nonlethal damage": "非致死伤害", "ability modifier": "属性调整值",
    "weapon special ability": "武器附魔", "immediate action": "直觉动作",
    "freedom of movement": "行动自如", "spell turning": "法术反转",
    "protection from energy": "防护能量伤害", "reserve feats": "法储专长",
    "heritage feats": "血脉专长", "exalted feats": "崇善专长",
}


# 长句里的旧译名（从审计样例与用户截图里确认过的 ✓）—— 逐处替换，带英文护栏 ✓
VARIANT_FIXES = [
    ("hexblade", "咒剑士", "魔剑客"),
    ("beguiler", "诡计师", "欺愚者"), ("beguiler", "诡术师", "欺愚者"),
    ("war mind", "战争心智", "战魂"), ("war mind", "战斗心灵", "战魂"),
    ("fist of zuoken", "祖奥肯之拳", "汝欧肯之拳"), ("fist of zuoken", "佐肯之拳", "汝欧肯之拳"),
    ("sublime chord", "崇高和弦", "超凡之弦"),
    ("diamond mind", "钻石心灵", "钢魂"),
    ("swordsage", "剑道家", "贤者之剑"),
    ("shadowcaster", "暗影法师", "暗影秘使"),
    ("warblade", "战刃", "军道之剑"),
    ("crusader", "十字军", "天诛之剑"),
    ("desert wind", "沙漠之风", "漠风"),
    ("devoted spirit", "虔诚灵", "虔心"),
    ("stone bones", "石骨", "石铸身躯"),
    ("shadow hand", "暗影之手", "影手"),
    ("setting sun", "落日", "暮日"),
    ("martial spirit", "武道之魂", "武技之魂"),
]


def main():
    apply = "--apply" in sys.argv
    con = sqlite3.connect(TM)
    rows = con.execute("SELECT rowid, src, dst FROM tm WHERE status!='rejected'").fetchall()
    plan = []
    for rid, s, d in rows:
        key = (s or "").strip().lower()
        if key in ADOPT and (d or "").strip() != ADOPT[key] and (d or "").strip():
            plan.append((rid, s.strip(), d.strip(), ADOPT[key]))
    print("  采用清单 %d 个术语；命中的「术语条目」%d 条（只改这类 ✓）" % (len(ADOPT), len(plan)))
    uniq = sorted({(p[1], p[2], p[3]) for p in plan})
    print("\n  抽样 20 条改动：")
    for s, d, n in uniq[:20]:
        print("     %-26s %-14s -> %s" % (s[:26], d[:14], n))
    if not apply:
        print("\n  预览（未改）。加 --apply 执行。")
        return 0
    os.makedirs(BACKUP_DIR, exist_ok=True)
    bak = os.path.join(BACKUP_DIR, "tm_%s.sqlite" % time.strftime("%Y%m%d_%H%M%S"))
    shutil.copy(TM, bak)
    print("\n  已备份 -> %s" % os.path.basename(bak))
    for rid, s, d, n in plan:
        con.execute("UPDATE tm SET dst=?, status='manual', note='规则书术语统一(C)' WHERE rowid=?",
                    (n, rid))
    con.commit()
    print("  ✅ 术语条目改动 %d 条" % len(plan))
    # ② 长句里的旧译名：逐处替换（**不跳过"已含新名"的行** ✓ —— 那是 A 的残留原因 ✗）
    total = 0
    for en, old, new in VARIANT_FIXES:
        rx = re.compile(r"(?<![A-Za-z])" + re.escape(en) + r"(?![A-Za-z])", re.I)
        cnt = 0
        for rid, src, dst in con.execute(
                "SELECT rowid, src, dst FROM tm WHERE status!='rejected'").fetchall():
            if not dst or old not in dst or new in dst:
                continue
            if not rx.search(src or ""):
                continue
            con.execute("UPDATE tm SET dst=?, status='manual', note='规则书术语统一(C-变体)' WHERE rowid=?",
                        (dst.replace(old, new), rid))
            cnt += 1
        con.commit()
        total += cnt
        if cnt:
            print("     %-18s %-10s -> %-10s %d 条" % (en, old, new, cnt))
    print("  ✅ 变体替换合计 %d 条" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
