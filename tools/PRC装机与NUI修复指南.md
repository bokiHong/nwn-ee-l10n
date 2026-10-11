# PRC 装机 + NUI 点击修复指南

> 适用：无冬之夜：增强版（NWN:EE）+ PRC8 / PRC 5.0
> 本文档由 2026-10-07 的一次实战整理而来，所有结论都在这台机器上实测过。

---

## 一、这份文档解决什么

| 问题 | 症状 | 本文档的对策 |
|---|---|---|
| **PRC8 的界面上点不动** | 升级选法术、法术书、契灵、剑契、招式准备这些窗口**能正常显示**，但里面的按钮/列表点了没反应；**只有右上角的 ✕ 和收起可用** | 跑 `prc8_nui_fix.py`（一次性全局修复）|
| 不知道哪些模组会中招 | —— | 跑 `prc_nui_check.py` 体检，逐个体检给结论 |
| 以后给别的模组装 PRC | —— | 见第五节的标准流程 |

---

## 二、为什么会这样（机制，一分钟看懂）

NWN:EE 的 **NUI** 是脚本画的界面。**控件被点击时，引擎只会把事件送给"模块级事件脚本" `OnNuiEvent`**
（官方说明：<https://nwnlexicon.com/OnNuiEvent> —— *"It must be set using `SetEventScript`"*；
1.87 补丁起才能在工具集的 Module Properties → Events 里设）。

而**引擎自己画的那圈窗口边框（✕ / 收起 / 拖动）不走这条通道** —— 所以
"窗口能显示、里面点不动、只有 ✕ 能用"就是**这个问题的指纹**。

PRC8 已经准备好了总路由器 `prc_onplayernui`（按窗口 ID 分派到 14 个界面的事件脚本），
**但它需要模块把 `OnNuiEvent` 指向它。**

### 为什么 PRC8ModuleUpdater 做不到

它的行为**完全由 `.hif` 指令文件决定**，而 `PRC8.hif` / `PRC8 Pack.hif` / `PRC.hif`（PRC 5.0）
的规范头都写着 **`MinNWNVersion: 1.67`** —— NUI 是 1.87 才有的功能，所以：

- `.hif` 里只有 11 条 `module.Hak`、`module.CustomTlk` 和 **18 条老式 `module.On*` 事件**，
  **没有 `module.OnNuiEvent`**；
- 两个 `*ModuleUpdater.exe` 的二进制里连 `Nui` / `SetEventScript` 字符串都不存在。

**更关键的是**：`.hif` 的 `module.Xxx : value` 只能**改已有字段的值，不能新增字段**
（GFF 的字段表是固定结构）。所以对**老 schema** 的模组，更新器跑多少遍都补不上这一步。

### 为什么官方战役没事（实测对照）

| 模组 | `.ifo` 字段数 | 有没有 `Mod_OnNuiEvent` |
|---|---|---|
| 官方战役 `Chapter1~4 / Prelude / XP1-* / XP2_*` | 122–230 | **有**，值 = `prc_onplayernui` ✓ |
| 剑飞 Ch1 / Ch2 | 136 / 327 | **没有** ✗ |
| 官方付费模组（Kingmaker 等） | 45–113 | **没有** ✗ |

官方战役的 `.nwm` 被 **1.87+ 的 EE 工具集重新保存过**，字段槽天然存在，值也已经填好；
剑飞是 2005 年的 schema，**字段槽压根不存在**。

> 结论：**凡"老 schema + 通用更新器"的模组都会中招**（剑飞 1~5 章、多数 2020 年前的老模组、
> 未被重存的付费模组）。**与汉化译文、汉化包无关。**

---

## 三、一键修复（推荐）

修复原理：把设置语句注入 **PRC8 自己的 `prc_onenter`**（任何 PRC8 模组载入时都会执行它），
编译后放进 **`development\`**（该目录优先级高于 hak，实测有效）→ **一次修好所有模组**。

```powershell
cd C:\nwn-ee-l10n

# 1) 先干跑：编译 + 校验，但不安装
python prc8_nui_fix.py --dry-run

# 2) 正式安装（会备份同名旧文件）
python prc8_nui_fix.py

# 3) 重启游戏后，PRC8 的升级/法术书/契灵/剑契/招式准备窗口就能正常点击了
```

**期望输出**（关键两行）：

```
  退出码 0，产物 …\prc8_patch\prc_onenter.ncs（存在）
  原版常量 1359 个，新版 1360 个；新增 ['prc_onplayernui']；丢失 无
  ✓ 原有常量一个没丢，且新增了 prc_onplayernui
  ✓ 已安装 …\development\prc_onenter.ncs（468060 字节）
```

**撤销**（恢复 PRC8 原版行为）：

```powershell
python prc8_nui_fix.py --uninstall
```

### 注入的内容长这样（保守版）

```nwscript
// 仅当模块自己没设过时才接管，绝不覆盖别的模组自己的 NUI 处理
if (GetEventScript(GetModule(), EVENT_SCRIPT_MODULE_ON_NUI_EVENT) == "")
{
    SetEventScript(GetModule(), EVENT_SCRIPT_MODULE_ON_NUI_EVENT, "prc_onplayernui");
}
```

> `EVENT_SCRIPT_MODULE_ON_NUI_EVENT` 的值是 **3021**（来自游戏目录 `ovr\nwscript.nss`）。

---

## 四、体检：哪些模组会中招

```powershell
cd C:\nwn-ee-l10n
python prc_nui_check.py                       # 扫默认位置（用户 modules + 官方战役/付费模组）
python prc_nui_check.py "D:\某个模组目录"        # 扫你指定的目录
```

输出三类结论：

| 结论 | 含义 |
|---|---|
| **正常 ✓** | `.ifo` 里已有 `Mod_OnNuiEvent = prc_onplayernui`（官方战役就是这种）|
| **字段不存在（老 schema）** | 装了 PRC 就等着点不动；**全局补丁已装的情况下会被自动接管** ✓ |
| **值=<别的脚本>** | 模组自带 NUI 处理。全局补丁**不会覆盖**，需要让那个脚本把 PRC8 的窗口转发给 `prc_onplayernui` |

---

## 五、给一个模组装 PRC 的标准流程

1. **确认 PRC 文件到位**：`hak\prc8_*.hak`（或 PRC 5.0 那套）已在 `Documents\Neverwinter Nights\hak\` 下
   （**注意：hak 放在子目录里不会被加载**，比如 `hak\prc5.0\` 就是惰性的）。
2. **用 `PRC8ModuleUpdater.exe` 处理目标模组**（照 PRC 自带教程：勾选模组 → 应用）。
   它会加 hak、设自定义 TLK、设 18 个老式模块事件 —— **但它不会（也无法）设 NUI 事件**。
3. **跑体检**：`python prc_nui_check.py "<模组目录或文件>"`
   - 显示"正常 ✓" → 完事；
   - 显示"字段不存在" → 确认 `development\prc_onenter.ncs` 在（跑第三节的修复即可）；
   - 显示"值=<别的脚本>" → 需要转发（见第六节）。
4. **进游戏验证**：随便打开一个 PRC8 的 NUI 窗口（升级选法术最方便）点一下。

---

## 六、常见问题

**Q：装了补丁，重启游戏后还是点不动？**
1. 确认 `python prc8_nui_fix.py --dry-run` 能成功编译（失败会打印 nwnsc 的报错）；
2. 确认文件确实在 `Documents\Neverwinter Nights\development\prc_onenter.ncs`；
3. 确认该模组**真的挂了 PRC 的 hak**（体检工具会显示"挂PRC?"一列）；
4. 确认是**重启游戏后**测的（脚本只在载入时执行）。

**Q：模组自己用了 NUI，PRC8 的窗口还是点不动？**
它的 `OnNuiEvent` 已被占用。在它的事件脚本里加一段转发即可：

```nwscript
if (NuiGetWindowId(oPlayer, nToken) == "PRC_XXX")   // PRC8 的窗口 ID
{
    ExecuteScript("prc_onplayernui");
    return;
}
```

**Q：`NuiCreate(..., sEventScript)` 是什么？**
该参数可以**按窗口**单独指定事件脚本（比模块级更外科）。PRC8 的窗口创建时没传，
所以要么走模块级事件，要么改 PRC8 的十几个 view 脚本。

**Q：以后换机器 / 重装游戏怎么办？**
需要重新装：①PRC 本体 + hak；②`python prc8_nui_fix.py`（装 NUI 补丁）。
`development\prc_onenter.ncs` 与 `汉化\prc8_patch\` 都是可再生的，不用备份。

---

## 七、名词表

| 名词 | 说明 |
|---|---|
| `development\` | 用户目录下的开发目录，**资源优先级高于 hak**（本补丁就放这里）|
| `hak\` | 模组资源包目录；**子目录不会被加载** |
| `override\` | 全局覆盖目录（PRC8 的 `ocfix_*` 脚本也在这里）|
| `module.ifo` | 模组主信息（GFF 格式）：模块名、描述、挂的 hak、18/22 个模块事件脚本…… |
| `Mod_OnNuiEvent` | **本文档的主角**：模块级 NUI 事件脚本字段。老模组没有这个字段槽 |
| `OnNuiEvent` | 事件名；NUI 控件点击时触发 |
| `prc_onplayernui` | PRC8 的总路由器：按窗口 ID 分派给各 `prc_nui_*_event` |
| `.hif` | PRC 更新器的指令文件（`PRC8.hif` / `PRC.hif`），声明要挂哪些 hak、设哪些事件 |

---

## 八、本次实测记录（供追溯）

- 剑飞 Ch1/Ch2：`.ifo` 无 `Mod_OnNuiEvent` → 修复前点击全死、修复后用户实测**恢复正常** ✓
- 官方战役 13 个 `.nwm`：本来就带 `prc_onplayernui` ✓
- `Contest Of Champions 0492.mod` / `Neverwinter Chess.mod`：同样老 schema，**被同一个全局补丁覆盖** ✓
- 验收数据：补丁编译后字符串常量 **1359 → 1360**，只新增 `prc_onplayernui`、**丢失 0** ✓
- 相关工具（都在 `汉化\` 下）：`prc8_nui_fix.py`（修复）、`prc_nui_check.py`（体检）
