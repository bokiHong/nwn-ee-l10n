<div align="center">

# NWN:EE 汉化工作台

**《无冬之夜：增强版》模组汉化的完整流水线**

翻译记忆 · 术语治理 · TLK 回填 · 位置索引 · 影响面预览 · 零花费增量

![License: MIT](https://img.shields.io/badge/code-MIT-yellow.svg)
![License: CC BY-SA 4.0](https://img.shields.io/badge/data-CC%20BY--SA%204.0-lightgrey.svg)
![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)

</div>

---

## 这是什么

一套把 NWN:EE 模组汉化从「手工翻文本」变成**可复用、可量化、可验收**的工具链。

它主要解决一件具体的事：**模组更新之后，怎么只翻新增的那几句，其余一句钱都不再花？**

本项目最初为解决下面这三个痛点而写（维护者原话）：

> 「这三个工具目前各自为政，代码逻辑和数据结构相互独立。TLK 的 ID 冲突无法在 MOD 中直接解决，术语库无法实时检查 TLK 和 MOD 的翻译，导致增量更新极其痛苦。」

根因被定位为**同一个问题**：翻译记忆库只有「原文 ↔ 译文」，缺少**位置层** ——
所以「这句话在哪个文件的哪个字段」无从查起，也就谈不上精准增量。

现在它已经在真实项目上跑通：**PRC8 全量汉化（30,168 条文本）** 与
**Swordflight Chapter One / Two**（约 44,000 条翻译记忆），术语表 6,500+ 条。

---

## 核心能力

| 能力 | 说明 |
| --- | --- |
| **翻译记忆** | 以**英文原文的内容哈希**为 key（不是序号），跨模组 / 跨章节 / 跨游戏复用。已覆盖过的文本重跑 **0 次 API 调用**。 |
| **术语治理** | 分级术语表（必守 / 参考）+ 译前注入 + 译后强校验 + **术语违规自动发现**（`命中必守术语但译文没用规范译名`）+ 零调用就地回订。 |
| **位置索引** | 三层模型：容器 → 资源 → 单元。每个可译出现都有 `locator`（机器回写用）与 `field_path`（人看）。索引层**可随时从源文件重建**。 |
| **影响面预览** | 任何「改一处、动多文件」的操作，**先出预览**：改多少条、涉及哪些模组、要不要重建、预计耗时、是否花钱。**预览与执行共用同一段查询逻辑**。 |
| **TLK 槽位账本** | 解决「TLK 的 ID 冲突」：谁占用了哪个 strref、用途是什么、哪些槽位**重建时绝不能清空**。 |
| **产物读回校验** | 打包后逐条读回比对，残留必须为 0；`.ncs` 替换前过**字符串常量多重集硬校验**，不通过就保留原版。 |
| **人工润色闭环** | 可视化编辑器或审校 TSV → 回导记忆库 → 重新打包，**0 花费**。 |

---

## 快速开始

需要 **Windows**（GUI 用 tkinter）+ **Python 3.8 或更高**。其余全部是标准库，
只有「调用 API 翻译」这一步需要 `pip install requests`。

```bash
# 1) 两个仓库都要克隆：代码 + 公开数据（术语表 / 翻译记忆库）
git clone https://github.com/bokiHong/nwn-ee-l10n.git
git clone https://github.com/bokiHong/nwn-ee-l10n-data.git

# 2) 建库并导入公开数据（幂等，可反复跑）
cd nwn-ee-l10n
python tools/bootstrap.py --data ../nwn-ee-l10n-data/data

# 3) 跑验收 —— 应当全绿
python -m l10n.selftest
```

看到这样的输出就成了：

```
库现状：term 13412 条（active 6580 / candidate 6832）、tm 43887 条、term_variant 77 条
库文件：.../modl10n/tm.sqlite（30.2 MB）

下一步：python -m l10n.selftest      # 跑验收，确认库可用
```

然后双击启动图形界面（全部是 ASCII 名的 `.bat`，转发给中文名脚本，避开 cmd 的编码坑）：

| 双击这个 | 打开什么 |
| --- | --- |
| `启动汉化工作台.bat` | ⭐ **统一启动器** —— 四个工具的总入口（推荐从它开始） |
| `启动mod编辑器.bat` | **MOD Tool** —— `.mod` 汉化的可视化编辑器 |
| `启动tlk编辑器.bat` | **TLK Editor** —— TLK ↔ 记忆库对账、回填、槽位账本 |
| `启动术语库.bat` | **术语库编辑器** —— 术语增删改查、违规检查、体检、一键重建产物 |
| `启动翻译流水线.bat` | **翻译流水线** —— 唯一会花钱的入口（带三道安全闸） |

> 验收脚本只会跳过「需要你本机私有文件」的检查（模组本体、游戏 TLK 槽位账本、
> 部分术语库用例），**不会因此判失败**。实测三档环境：
> 只克隆代码（未导入数据）**42 项全过** → 导入公开数据包后 **70 项全过** →
> 完整环境（真实模组 + PRC8 TLK）**102 项全过**。

---

## 本仓库不含什么（**请先读**）

本仓库**刻意不含**任何第三方游戏或模组的原始文件。这不是疏漏，是法律边界：

| 不含 | 为什么 |
| --- | --- |
| ❌ 模组本体（`.mod` / `.erf` / `.hak`） | 版权归各自作者。PRC8、Swordflight 这类社区模组**本来就是免费下载的**，但「免费」≠「可以再分发」—— 请自行从原发布页下载 |
| ❌ 游戏原始文件（`dialog.tlk`、`.bif`、`.key`） | 版权归 Beamdog / 原版权方 |
| ❌ D&D 规则书语料 | 第三方版权内容，只用于本地术语校对 |
| ❌ 第三方汉化包本体 | 如 `NWN_EE_zh_CN_Unicode`（`version.dll` 钩子）是别人做的 |
| ❌ API 密钥 | `.gitignore` 第一行就堵死了 `.api_key` |
| ❌ 本机路径与备份 | 你的 `tmp/`、数据库快照、个人目录一概不进来 |

**你要自己准备**：一份合法的 NWN:EE 游戏副本 + 你打算汉化的模组的正版文件。

`data/` 目录里只有两类东西：**术语表**（译名对照）和**翻译记忆库**
（英文原文 ↔ 中文译文）。英文原文的权利归模组作者，中文译文由本项目维护者创作。
若你是权利人，认为这些内容侵犯了你的权益，请开 Issue，我们会立即移除。

---

## 四个工具 + 统一启动器

### 0. 汉化工作台（`汉化工作台.py` / `启动汉化工作台.bat`）

**统一启动器**：把下面四个工具收在一个窗口里，并做**上下文联动**（选好模组 / 项目后，
带着它打开对应工具）。所有业务逻辑都在 `l10n/services/` 里，GUI 只是壳 ——
所以合并风险可控、四套 GUI 测试能守住"行为不变"。

### 1. MOD Tool（`mod tool.py`）

`.mod` 汉化的主界面：打开模组 → 自动抽取可译文本 → 表格里逐条编辑 →
标「待重译」交给 API → 保存回记忆库 → 一键打包成 `_zh.mod`。
内置**影响面预览**与**失败注入保护**（写库失败时绝不吞掉你的改动）。

### 2. TLK Editor（`tlk tool.py`）

面向 `.tlk` 文件：加载中英文 TLK、做 `TLK ↔ 记忆库` 对账（一致 / 不一致 /
未入库 / 无英文对照 四种状态）、回填译文、认领与校验**槽位账本**。
回填前会备份，且**自加槽位一字不动**。

### 3. 术语库编辑器（`术语库编辑器.py`）

术语的增删改查 + 分级 + 分类筛选 + 排序，外加四件治理工具：
**术语违规检查**（列出「英文命中必守术语、但译文没用规范译名」的位置，可逐条采纳 /
双击即采纳）、**术语表体检**（六类判据查脏数据）、**一键重建产物**、
**批量可撤销**。改译名走 `TermRepo.set_zh()`，旧译名自动降级为 rejected，
**不会留下第二条 active**。

> 实测：违规从 22701 处经八招降噪 + 多轮治理降到 **16 处**；术语表体检 70 → **10 处**。

### 4. 翻译流水线（`翻译流水线.py` / `启动翻译流水线.bat`）

**唯一会花钱的入口**，所以配了三道安全闸：`--run` / `--txt <导出>` / `--key sk-xxx --yes`
缺一即拒；默认只估价。另有**花费账本**记录每次调用的估算与实付。

---

## 命令行速查

所有命令都在仓库根目录下执行。

### 统一工作台 `python -m l10n`

| 命令 | 作用 |
| --- | --- |
| `python -m l10n status` | 看库里各项目的进度 |
| `python -m l10n scan --project <名> --mod <文件>` | 登记容器的资源清单 |
| `python -m l10n extract --project <名> --mod <文件>` | 抽取可译单元 → `unit` 表 |
| `python -m l10n diff [--project <名>]` | 算出还要翻哪些（只读） |
| `python -m l10n plan [--project <名>]` | **花钱前的影响面与花费预估**（只读，不调 API） |
| `python -m l10n terms --action <动作>` | 术语库：`import` / `export` / `search` / `validate` / `index-terms` |
| `python -m l10n tlk --action <动作> --tlk <文件>` | TLK 槽位账本：`claim` / `list` / `allocate` / `baseline` / `verify` |
| `python -m l10n pack` | 打包产物（0 花费） |
| `python -m l10n translate` | **唯一会花钱**的 CLI：默认只估价，真跑需 `--run --txt <导出> --key sk-xxx --yes`（缺一即拒） |
| `python -m l10n.db --check` / `--migrate` | 检查 / 幂等升级数据库结构 |
| `python -m l10n.selftest [1-8]` | 验收（跑单个数看某一层） |
| `python -m l10n.tlk_audit` | TLK ↔ 记忆库对账，导出 CSV 供人工拍板 |

### 汉化流水线 `python -m modl10n`

| 命令 | 作用 |
| --- | --- |
| `python -m modl10n.pipeline --mod <文件> --tm <库>` | **单模组全流程**：抽取 → 翻译 → 回写 → 打包 → 校验 |
| `python -m modl10n.batch --mods <通配符...>` | 多模组批量（共用一份记忆库与术语表） |
| `python -m modl10n.review export --out <tsv>` | 导出审校表（8 列 TSV，改「现译」列即可） |
| `python -m modl10n.review import --file <tsv>` | 回导审校结果（0 花费） |
| `python -m modl10n.rename --old <旧> --new <新> --dry-run` | 术语统一（**先出影响面预览**，去掉 `--dry-run` 才执行） |
| `python -m modl10n.check <文件>` | 只读体检 |
| `python -m modl10n.tlk <子命令>` | TLK 相关：增删改查 / 重建 / 回填 / 应用术语 |
| `python -m modl10n.terms <mine\|audit\|scan\|fix>` | 术语挖掘 / 审计 |

### 辅助工具 `tools/`

| 脚本 | 作用 |
| --- | --- |
| `python tools/bootstrap.py --data <数据包>/data` | 建库 + 导入公开数据 |
| `python tools/export_data.py --db <库> --out <目录>` | 从你的库导出可分发数据（维护者用） |
| `python tools/prc8_nui_fix.py` | 修 PRC8 的 **NUI 点击失效**（见下） |
| `python tools/prc_nui_check.py` | 体检各模组的 NUI 事件脚本绑定情况 |
| `python rebuild_tlk_safe.py [--apply]` | **安全重建 TLK**：保留英文导出里没有的自加槽位 |

---

## PRC8 的 NUI 点击失效（一个值得单说的修复）

**现象**：装了 PRC8 的老模组里，PRC8 窗口能正常显示，但**所有控件点击都没反应**。

**根因**：NWN:EE 里 NUI 控件的点击只会送到模块的 `OnNuiEvent` 事件脚本，
而 PRC8 需要模块自己把路由器挂上：

```nwscript
SetEventScript(GetModule(), EVENT_SCRIPT_MODULE_ON_NUI_EVENT, "prc_onplayernui");
```

这个事件是 1.87 补丁才加进模块属性的。2005 年代的老模组
（例如剑飞，`Mod_MinGameVer` 只有 1.68）的 `module.ifo` 里**根本没有这个字段**。

**修法**：把那一行注入 PRC8 自己的 `prc_onenter`（任何 PRC8 模组载入时都会执行它），
用 `nwnsc` 重编译后放进 `Documents\Neverwinter Nights\development\` ——
该目录优先级高于 hak，于是**一次修好所有模组**，不用逐个改 `.mod`。

```bash
python tools/prc8_nui_fix.py --dry-run   # 只编译校验
python tools/prc8_nui_fix.py             # 安装（幂等，自动备份）
python tools/prc8_nui_fix.py --uninstall # 卸载
```

---

## 数据与许可

| 内容 | 位置 | 许可 |
| --- | --- | --- |
| 代码（Python / GUI / CLI） | 本仓库 | **MIT**，见 [`LICENSE`](LICENSE) |
| 数据（术语表 / 翻译记忆库） | [`nwn-ee-l10n-data`](https://github.com/bokiHong/nwn-ee-l10n-data) | **CC BY-SA 4.0**，见 [`LICENSE-DATA`](LICENSE-DATA) |

**为什么拆两个仓库**：数据的版权风险与代码不同。万一有模组作者提出异议，
只需归档数据仓库，工具仓库不受影响。

`modl10n/tm.sqlite`（中央库）是你的**本机数据**，不进 Git。
公开数据通过 `tools/bootstrap.py` 导入。

---

## 设计要点

这些是本项目踩坑后固化下来的原则，改代码时请一并遵守：

1. **`tm` 表只读（译文的唯一真源）** —— 译文只存在 `tm` 表，
   `unit` 表只存「位置 + 源文哈希」。术语只写 `term` 表，
   `glossary.json` 降级为**导出快照**（绝不自动覆盖）。
2. **索引层必须能从源重建** —— 删掉 `unit` / `resource` / `container`
   后重跑 `scan` + `extract`，必须得到**完全相同**的 `uid` 与 `src_key`（已有验收用例）。
3. **预览与执行必须同源** —— 影响面预览和执行**共用同一个查询函数**，
   不许拿预览的返回值当执行的开关（踩过「鸡生蛋」坑：预览不查空 src 行 → 0 条 → 执行也查不到）。
4. **双 key 兜底** —— 历史上两条流程的 key 算法不一致（`norm` vs `raw`）。
   任何「这句有没有译文」的判断都必须走 `l10n/keys.py` 的 `keys_for()`，两个 key 都试。
5. **宁可保持英文，也绝不悄悄丢内容** —— `.ncs` 替换前过字符串常量多重集硬校验，不通过就保留原版。
6. **失败必须可见** —— 写库失败时绝不 `edits.clear()`（否则改动「看起来已保存」然后丢失）。

---

## 常见问题

**Q：我能直接用它汉化别的模组吗？**
可以。`python -m modl10n.pipeline --mod <你的.mod>` 加上你自己的 API Key 即可。
详细保姆级教程见 [`modl10n/新手上手.md`](modl10n/新手上手.md)。

**Q：翻译要花钱吗？**
只有「记忆库里没有的新文本」才调 API。已有的重复文本 **0 花费**。
用 `python -m l10n plan` 可以在花钱前看到预估花费。

**Q：我的库在哪？**
`modl10n/tm.sqlite`。备份用 SQLite 的在线 `backup()` API（不是复制文件，避免漏掉 WAL）。

**Q：找不到我的游戏目录？**
设环境变量 `NWN_HOME` 指向 NWN:EE 用户目录
（默认 `%USERPROFILE%\Documents\Neverwinter Nights`）。

**Q：跑验收时一堆「跳过」正常吗？**
正常。依赖你本机私有文件（商业模组、游戏 TLK）的检查会跳过，
设 `L10N_TEST_MOD` / `NWN_TLK` 等环境变量即可让它们跑起来。

---

## 致谢

- 术语译名参考了《龙与地下城 3.5 版》中文规则书译名与中文社区通用译法
- 感谢 PRC8 Consortium、Swordflight 系列等模组的作者
- 感谢 NWN:EE 中文社区与汉化包的作者们

---

<div align="center">

**如果这个项目帮到了你，给个 ⭐ 就是最好的支持。**

</div>
