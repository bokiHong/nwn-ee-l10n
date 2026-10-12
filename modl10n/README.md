# modl10n —— 无冬之夜增强版 `.mod` 批量汉化工具

把 `Swordflight Chapter One` 那次实战的全部经验（和踩过的坑）固化成的可复用流水线。

**核心能力**
- **多 mod 批量**：一个命令顺序处理任意多个 `.mod`，共用一份翻译记忆与一张术语表。
- **相同文本不重复翻译**：翻译记忆以「英文原文 sha256」为 key，跨 mod 复用；已翻过的句子再跑一次 **0 次 API 调用**（幂等，逐字节相同产物）。
- **专有名词统一**：术语表分级（必守 / 建议），译前注入提示、译后强校验，可随时审计已译内容找出漂移。
- **不破坏原文件**：GFF 最小改动回写、`.ncs` 重编译 + 字符串常量硬校验、产物读回端到端校验。
- **不需要 NWN MultiLang**（也支持它导出的 SF01.txt 作为兜底路线）。

---

## 一、快速开始

```powershell
cd C:\nwn-ee-l10n

# 0) 干跑自检（零花费）：只抽取 + 报告
python -m modl10n.pipeline --mod "…\Swordflight - Chapter Two.mod" --extract-only

# 1) 冒烟测试（先翻 50 条，确认没问题）
python -m modl10n.pipeline --mod "…\Chapter Two.mod" --limit 50

# 2) 单章全量
python -m modl10n.pipeline --mod "…\Chapter Two.mod" --workers 5

# 3) 多章批量（顺序执行，越往后复用率越高）
python -m modl10n.batch --mods "…\Chapter Two.mod" "…\Chapter Three.mod" "…\Chapter Four.mod" "…\Chapter Five.mod"
```

产物默认写到 `.mod` 同目录的 `汉化产物\`：`<名字>_zh.mod` + `<名字>.report.json`。

**参数速查**

| 参数 | 说明 |
|---|---|
| `--limit N` | 只翻「接下来 N 条未译」（可反复执行，每跑一次推进一批） |
| `--workers N` | 并发路数，默认 5 |
| `--sf01 FILE` | 路线 A：用 NWN MultiLang 导出的文本（默认路线 B：自研抽取） |
| `--outdir DIR` | 产物目录 |
| `--out FILE` | 直接指定输出文件路径（例如写回原始 mod 的文件名） |
| `--inplace` | 覆盖输入的那个 `.mod` 本身（**先自动备份**为 `<名字>.orig-backup`） |
| `--extract-only` / `--dry-run` | 只抽取 / 走完流程但不写文件（都不调 API） |
| `--no-nss` | 只汉化 GFF（物品/生物/对话/区域…），完全不动脚本 |
| `--no-ncs` | 不重编译 `.ncs`（脚本内文本不会在游戏里变中文） |
| `--no-var-assign` | 更保守：不把「字符串变量赋值」当显示文本 |
| `--punct full\|half` | 中文全角标点（默认）/ 英文半角 |
| `--tm` | 翻译记忆 sqlite 路径 |
| `--glossary` | 术语源：`*.sqlite` → `term` 表（**默认**，最新真源）；`*.json` → 冻结快照（会打印过期警告 ✗） |

**让原始文件名的 mod 变成中文**（想接着玩已有存档时用，NWN 存档是按 mod 文件名绑定的）：

```powershell
python -m modl10n.pipeline --mod "…\modules\Swordflight - Chapter One.mod" --inplace
# 原件会被备份成 "Swordflight - Chapter One.mod.orig-backup"
```

**`.mod` 体检**（专治 0 字节 / 半损坏 —— 第三方工具崩溃的典型后果）：

```powershell
python -m modl10n.check "%USERPROFILE%\Documents\Neverwinter Nights\modules"
# 输出每个 mod 的大小/条目数/结构问题/是否已汉化，并列出遗留的临时文件与备份
```

---

## 二、抽取路线：B（默认）与 A（兜底）

`.mod` 里的文本分两类，回写位置用 **locator** 精确定位（不需要文本匹配）：

| 类型 | 抽取方式 | locator |
|---|---|---|
| GFF（`.uti .utc .dlg .are .git .ifo .jrl .utp .utd .utm .ute .utt .uts .gic .utw .fac`） | 所有 `CExoLocString` 字段 | `("gff", 资源下标, 字段下标)` |
| `.nss` 脚本 | 显示文本判别（见下） | `("nss", 资源下标, 0基行号, 字面量序号)` |

**`.nss` 判别器（路线 B 的关键件）**：脚本里大量字符串是 resref / 本地变量键 / 日志标签
（`GetObjectByTag("Saman01")`、`SetLocalInt(o,"ALCOHOL",1)`、`GetTag(o) != "zararm001"`），
翻了直接坏档。做法是取该字面量「最内层包围函数名」，**只认白名单里的显示函数**：

```
FloatingTextStringOnCreature / SpeakString / ActionSpeakString / CutSpeakString /
SendMessageToPC / PopUpDeathGUIPanel / SetCustomToken / DBG_msg /
SetName / SetDescription / SendMessageToAllDMs / PrintString / MyPrintString /
WriteTimestampedLogEntry / SpeakOneLinerConversation（空字符串除外）
```

用 Chapter One 的 MultiLang 导出结果做回归验收：

| | MultiLang 路线 | 自研判别器 |
|---|---|---|
| Ch1 `.nss` 可译条数 | 293（导出 382 后过滤掉 89 条资源名） | **295** |
| 是否会把 resref / tag 当文本 | 会（需额外过滤） | **不会** |
| 是否会漏 `SetName` / `SetDescription` | 会漏 131 处 | **能抓到** |
| 未知函数（白名单外） | — | 2271 条已逐条审查，除上述两个外全是资源名/键 |

路线 A（`--sf01`）保留为兜底：如果某章的判别结果可疑，用 MultiLang 导出后 `--sf01` 重跑那一章。

---

## 三、翻译记忆（`tm.sqlite`）

```sql
tm(key, src, dst, src_mod, status, note, updated)   -- status: auto | manual | fallback | rejected
mods(name, path, units, new_tr, reused, updated)
```

- `key = sha256(英文原文 UTF-8)[:32]` —— 同一句英文永远同一句中文（**文件内 + 跨 mod + 跨章节**）。
- `status=manual` 的条目机器翻译永不覆盖（人工修订优先）。
- `status=fallback` 表示"本次没译出来、暂时保留英文"，下次会自动重试；
  **正确的空操作（如 `halquest01` 这类标识符）不会被反复重试**，这是"重复跑不花钱"的关键。
- 从 Chapter One 的 `sf_cache.json` 导入已有 3,325 条：

```powershell
python -c "from modl10n.tm import TM; TM('modl10n/tm.sqlite').import_json('sf01/sf_cache.json')"
```

---

## 四、术语表

> **术语真源是 `tm.sqlite` 的 `term` 表**（Sprint 4 起），`glossary.json` 已降级为
> **冻结快照**（2026-10-08，215 条）—— 别改它，改了不生效 ✗。
> 加/改术语请用 **`启动术语库.bat`（术语库编辑器）**，或 `python -m l10n terms …`。

**`pipeline` / `batch` 的术语来源（2026-10-11 改）**：默认读 `tm.sqlite` 的 `term` 表
（6,585 条 active）。之前固定读 `glossary.json` 快照，于是**翻译用的术语比术语库落后两个
版本、不报错只是悄悄少注入**（实测 Ch3 待译 6,640 条文本：旧快照命中 26 个术语，
新真源命中 226 个）。显式传 `.json` 仍可用（回退），但会打印过期警告 ✓。

**硬档 / 软档**（为什么不是全部硬校验）：

| 档位 | 判据 | 作用 |
|---|---|---|
| **硬档** | `required=1` 且（`type ∈ HARD_TYPES` 或术语含空格）且不在 `STOP_EN`、无同英多译 | 提示里写【固定译名·必须使用】+ **译后宽容校验** |
| **软档** | 其余 active | 只写【建议译名】，不校验 |

理由：`term` 表里混着"日常词冒充专名"（`Trade`/`Repose`/`Air`/`Failure`）与脏数据
（`Wilder = 狂念者任意`）。若全部硬校验，实测 4,000 条人工定稿译文里 **19.25%** 会被判
"术语未统一"→ 反复重试 → 白花钱 ✗；分档 + 宽容判据后降到 **3.20%**，且残留几乎全是
**真不一致**（`Hexblade` 魔剑客/魔剑士、`Armor Bonus` 盔甲加值/护甲加值）✓。

**术语未统一 ≠ 回退原文**（2026-10-11）：`Translator.best()` 只在**结构性问题**
（占位符/`<...>` 标记/换行/数字丢失）时才回退原文；只是术语没照用的话**接受译文**并记警告
（否则一条脏术语就能把整句变成英文 ✗）。术语统一交给 0 花费的事后回治。

```json
{"version": 1, "terms": [
  {"en": "Zarala", "zh": "扎拉拉", "type": "name", "note": "人名", "required": true},
  {"en": "Swordflight", "zh": "剑翔", "type": "misc", "note": "", "required": false}
]}
```

- `required=true`：译前注入「必须使用」提示，**译后校验**，没用上就重译 → 保证统一。
- `required=false`：只作为建议注入，不做校验（避免误锁，如 `Swordflight` 这类品牌名）。

**维护流程**（闭环）

```powershell
# 1) 挖候选：每翻完一章跑一次，把新出现的人名地名补进来
python -m modl10n.terms mine --tm modl10n/tm.sqlite --out term_candidates.csv

# 2) 体检：分【必守·需要修】与【建议·仅供参考】两栏
python -m modl10n.terms audit --tm modl10n/tm.sqlite --glossary modl10n/glossary.json

# 3) 修（推荐）：零 API 调用就地替换译名，只动名字、不动句子
python -m modl10n.terms fix --tm modl10n/tm.sqlite --glossary modl10n/glossary.json --dry-run
python -m modl10n.terms fix --tm modl10n/tm.sqlite --glossary modl10n/glossary.json
#    替换表：modl10n/term_variants.json，格式 {错误译法: 规范译法}
#    回订是迭代的：fix → audit，若仍有残留变体就补进替换表再 fix（长变体自动优先）
#    修过的条目标为 status=manual，机器翻译永不覆盖

# 3') 修（备选）：标为 rejected 交给模型重译（会花钱，且可能改动句子）
python -m modl10n.terms audit --tm modl10n/tm.sqlite --glossary modl10n/glossary.json --fix

# 4) 重新打包：TM 改完直接重跑，0 次 API 调用
python -m modl10n.pipeline --mod "…\Chapter One.mod"
```

### 润色：人工修改已有译文（`review`）

**关键前提**：译文只存在 `tm.sqlite` 里，打包是从记忆库取译文的 ——
**直接改导出的 txt 不会生效**，必须回导记忆库（之后重新打包是 0 花费）。

```powershell
# 导出审校表（TSV / 带 BOM，Excel 不乱码；一行 = 一种不重复文本）
python -m modl10n.review export --mod "…\Chapter One.mod" --out 审校表.tsv
#   只要一部分：--limit 200 --offset 200 / --fname ".dlg" / --filter 关键词 / --kind nss

# 只改「现译」列，存成 UTF-8；key 与「英文原文」列不可动（会被检测并跳过）

python -m modl10n.review apply --file 审校表.tsv --dry-run     # 先看会改几条
python -m modl10n.review apply --file 审校表.tsv               # 回导（标 status=manual，机器永不覆盖）

python -m modl10n.pipeline --mod "…\Chapter One.mod" --yes      # 重新打包，0 调用

# 不想手改、想让它重译某几句：
python -m modl10n.review retranslate --file 审校表.tsv --filter 关键词
```

实测：改「阿萨比钥匙」一行 → `acdoorkey001.uti` 与 `asabicave001.git` 两处同时生效
（因为按内容哈希去重）；`apply` 后重新打包只多出 72 字节，读回校验 0 不一致。

### 可视化编辑器（`汉化\mod tool.py`，润色首选）

双击 `汉化\启动mod编辑器.bat` 开窗：左边列全部**不重复文本**，点一条即可对照英文改译文，
**Ctrl+S 保存到记忆库**，菜单「重新打包」把改动打进 `.mod`。功能与安全约定：

- 同 `review` 的语义：改的是**记忆库**（标 `status=manual`，机器永不覆盖），
  程序**不直接改写 `.mod`**，打包复用经过验证的 `pipeline`（原子写 / 自动备份 / 读回校验）。
- 打包用**离线占位翻译器** —— GUI 按钮**任何情况下都不调用 API**（未译条目保持英文）。
- 支持搜索、只看未译/待重译、只看已改、资源名筛选，并提示「该文本出现 N 处，改一次全部生效」。
- 也可当 CLI 用：`info` / `export` / `apply` / `repack [--outdir|--out|--inplace]`。
- 实测：编辑器改一条 → 保存 → 打包，产物与命令行流程**逐字节相同**。

### 术语表怎么选词（踩过的坑）

- **不要收与普通名词冲突的词**。实测 `Helm`（神祇赫尔姆）会被 `Helm of Awareness`
  （警觉头盔）误命中；`Bane`、`Shar` 这类也有类似风险。非要用就填 `required: false`。
- **不要把普通名词塞进来当"统一"**（`Bag=袋子`、`Ghoul=食尸鬼`）—— 这类词在不同语境
  本就该有不同译法，钉死只会制造错误。
- 只收 **人名 / 地名 / 神祇 / 法术与器物专名**，投入产出最高。
- `required: true` 的词译后会被强校验，模型没照用就要重译 → **必守词越多，越可能多花钱**。
- 中英对照写法（`扎拉拉（Zarala）`）是**有意保留**的，回订时不会被动到
  （替换只在译文里完全找不到规范译名时生效）。

```powershell
# 看某个 mod 会命中哪些术语（不翻译）
python -m modl10n.terms scan --glossary modl10n/glossary.json --mod "…\Chapter Two.mod"
```

---

## 五、回写与验证（安全机制）

### GFF
按 locator 定位字段，**先把当前字段文本与抽取时的原文核对**（不符则不覆盖并报警），
再把新 `CExoLocString` 追加到 FieldData 末尾、更新该字段指针；
Struct / Field / Label / FieldIndices / ListIndices 等其余字节完全不动。

### `.nss`
按 0 基行号 + 字面量序号定位，核对原文后替换，代码字符一个不动。

### `.ncs`（关键）
`.mod` 里 `.nss` 是源码，**游戏运行时只读 `.ncs`**。所以改完 `.nss` 必须重编译：

1. 逐个文件编译（一个失败不影响其他），并自动补 **include 闭包**
   （`#include` 是编译期内联，库文件里的译文要让引用它的脚本一起重编译才生效）。
2. **硬校验门**：`nwnsc -d` 反汇编新旧 `.ncs`，比较字符串常量多重集必须等于
   「原常量按译文表映射」的结果（按 UTF-8 前 100 字节归一化，因为 `nwnsc -d` 会把常量截断到 128 字节）。
   **不通过就保留原始 `.ncs`** —— 宁可这部分保持英文，也绝不悄悄丢内容。

### 产物读回校验
把生成的 `.mod` 当黑盒重新抽取全部文本，逐条与预期比对，并检查 ERF 结构
（资源不越界、不重叠、键表一致）。**不一致必须是 0**。

---

## 六、依赖与路径

| 依赖 | 默认位置 |
|---|---|
| `nwnsc.exe`（NWScript 编译器） | `Documents\Neverwinter Nights\hak\nwnsc.exe` |
| NWN:EE 安装目录 | 从 `nwn.ini` 的 `NWMFiles=` 反推（也自动找到过 `D:\Program Files (x86)\Steam\steamapps\common\Neverwinter Nights`） |
| DeepSeek API Key | 环境变量 `DEEPSEEK_API_KEY`，或 `--api-key`，或 `modl10n/.api_key`（或 `sf01/.api_key`） |

包结构：

```
modl10n/
  core/erf.py         ERF/MOD 归档读写（打包时未改动资源自动回落原始字节）
  core/gff.py         GFF 读写（最小改动回写）
  core/sf.py          SF01.txt 解析（路线 A 用）
  core/sfrules.py     共享规则：可译性判定、换行转义、译文校验、标点、标记遮蔽
  core/classify.py    .nss 显示文本判别器
  extract.py          抽取（路线 B/A）→ 带 locator 的单元
  tm.py               翻译记忆（SQLite，线程安全）
  glossary.py         术语表
  translator.py       DeepSeek 翻译 + 分级兜底 + 术语注入/校验
  ncs.py              .nss→.ncs 重编译 + 硬校验注入
  pipeline.py         单 mod 全流程 CLI
  batch.py            多 mod 批量 CLI
  terms.py            术语表挖掘/审计/扫描 CLI
```

---

## 七、实测数据

| 项目 | 结果 |
|---|---|
| Ch1 用新工具回归重跑 | 5,732 单元 / 去重 3,326；**新增翻译 8 条**；读回校验 5,732 条 **0 不一致**；`.nss` 293 处、`.ncs` 注入 160 个 |
| Ch1 零花费幂等复跑 | **本次待译 0 条**，产物逐字节相同 ✅ |
| **Ch1 术语回订**（2026-10-07） | `terms fix` 零 API 调用修掉 **62 条**漂移（孟农/曼农→门农、邦格罗→邦格洛、扎拉娜→扎拉拉、拉默格→拉穆尔格、塔尔沙→塔沙尔、马拉萨→马拉萨尔、流沙客栈/酒馆→流沙旅店、阿斯比→阿萨比、克林沙尔→克伦沙尔、克兰根丁/格丁→克兰盖丁、谢尔卡尔/谢卡尔→舍卡尔、奎里克斯/希娅/西亚→奎里希亚…）；审计**必守漂移归零**；重新打包 0 次调用、读回校验 0 不一致；**归因校验 65 处变更 100% 可由替换表解释、0 处夹带改动** |
| Ch2 冒烟（50 条 + 复用 Ch1 记忆） | 单元 29,573 / 去重 11,911；免费复用 **7,535 个单元实例**（Ch1 的 540 个去重文本）；读回校验 **0 不一致** |
| Ch2 全量预估 | 需新译约 11,403 条，同比 Ch1 约 8 倍调用量 / 10 倍字符量 |
| 五章共享记忆节省 | 逐章各翻一遍 36,599 次 → 共享记忆 27,138 次（**省 26%**） |
| 术语回订 vs 重译 | 就地替换 **0 次调用**（重译需按句数付费，且可能改坏原本没问题的句子） |

# ============================================================
#  TLK 汉化（PRC8 这类模组）：按内容存记忆库 + 更新后只翻增量
# ============================================================
# 一次性：把已有的中英对照（英文导出 + 中文导出）存进同一个 tm.sqlite
python -m modl10n.tlk import-txt .\prc8.txt .\prc8_zh.txt
#   · 按【英文原文内容哈希】存 -> 跨版本、跨项目都能命中，且不会错位
#   · 默认跳过记忆库里已存在的 key（不覆盖你润色过的条目）
#   · 「中文与英文相同」的缩写代码/占位符也收录（以后不再白花钱、也不会被改坏）

# 看新版 TLK 有多少能白拿（0 花费、不写文件）
python -m modl10n.tlk status  <新版TLK>
python -m modl10n.tlk fill    <新版TLK> <输出TLK>      # 只用记忆库填，0 花费

# 一键增量汉化（在 tlk tool.py 里，因为翻译提示词与 API 调用在那）
python "tlk tool.py" update   <新版TLK> <输出TLK> [--dry-run] [--key=sk-xxx] [--tm=路径]
#   先吃记忆库（0 花费）-> 只翻没命中的 -> 外科式写出 TLK（保留索引表其余字段）-> 读回校验

# 查「同英文、不同译法」的术语不一致
python -m modl10n.tlk conflicts .\prc8.txt .\prc8_zh.txt --csv .\prc8_术语不一致.csv

# 术语统一（0 花费）：生成决策表 -> 人工审 -> 改记忆库 -> 重建 TLK
python prc8_术语统一.py                       # 生成/刷新 决策表（含 D&D 官方惯例修正）
python -m modl10n.tlk unify --table .\prc8_术语统一表.tsv --dry-run
python -m modl10n.tlk unify --table .\prc8_术语统一表.tsv
python -m modl10n.tlk rebuild <旧中文TLK> prc8.txt <新TLK>
#   决策表列：英文术语 / 规范译法 / 被替换的变体 / 说明 / 范围 / 保护
#   范围=仅词条：只改该词条本身（英文本身是常用词时用，如 Command/Orcish）
#   保护：替换前先遮蔽的片段（如 Vile 的「邪恶」不能改到规则术语「邪恶阵营」）
#   实测：891 条改动、同一英文多译法 128 组 -> 0 组
```

实测（模拟 PRC 更新：200 条改写 + 50 条全新）：新版需中文 29,676 条，
**记忆库命中 29,426 条（99.2%，0 花费）**，真正需翻译 **250 条**，读回校验 0 不一致。
具体步骤与背景见保姆手册的「10. TLK 汉化与增量更新」一节。

---

## 八、已知限制

### ⚠ 不要用 NWN MultiLang 的 "Import to" 写 .mod

实测事故（2026-10-07）：MultiLang 导入时的流程是「**先打开目标文件并截断 → 再写入**」，
而它在写入前碰到了空指针崩溃（`Access violation ... Lesen von Adresse 00000000`），
结果目标 `.mod` 被留成 **0 字节**，原始内容全丢（靠会话存档副本才恢复）。

崩溃的可复现条件：`Export from` 指向的 mod 与 `Import to` 不是同一个（资源索引对不上），
且文本里源语言槽为空（例如从已汉化 mod 导出时 `#str engM:` 是空的）+ "Prepare languages"
里 English 未勾选 → 工具读源语言字符串取到空指针。

而且就算不崩也没用：MultiLang **只搬文本、不重编译 `.ncs`**，脚本里的对白/提示仍是英文，
写进哪个语言槽也不确定。**请改用本工具的 `--inplace`**（并且本工具是原子写：
先写临时文件再 `os.replace` 替换，从结构上不可能留下 0 字节文件）。

1. `s_library001/002` 这类**纯 include**（没有 `main`）nwnsc 不产出 `.ncs`；它们的译文通过
   引用它们的脚本一起重编译而生效。
2. 个别脚本因**模块自带的旧 include 与当前基座冲突**无法编译（Ch1 有 3 个、Ch2 有 1 个），
   这些脚本将保留原始 `.ncs`（脚本内文本保持英文），报告里会列出。
3. 「字符串变量赋值」类（`text = "长句"`）会自动当显示文本处理并在报告里标出
   （Ch2 有 19 条），建议抽查；确属键值的可用 `--no-var-assign` 整体关掉。
4. 术语表只覆盖 Chapter One 出现过的人名地名；Ch2~Ch5 的新名字要靠 `terms mine` 迭代补入。
5. `nwnsc -d` 把字符串常量截断到 128 字节，硬校验只比较前 100 字节。
