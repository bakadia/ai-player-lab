# 参考 · 模式与地图（wiki 来源）

> 2026-10-26 · 来源：Backpack Hero Wiki（下方逐条给出链接）
> 这份是**外部知识**，用来校正我们自己的猜测；代码结构仍以实测为准。

## 0. 结论先行

| 发现 | 影响 |
|---|---|
| **Quick and Easy 是「挑战(Challenge)」，不是「模式」** | MVP 文档的措辞要改；操作流程是「快速开局 → 选角色 → 选挑战」 |
| 菜单里只显示 7 个挑战，实际有 20+ | 之前看不到 Quick and Easy 是因为**列表要滚动**，不是没有 |
| 挑战效果和 `runProperties` **一一对应** | 可以直接用 `runProperties` 判断当前是哪个挑战 |
| Easy Mode ≠ Quick and Easy | 我们跑的 `rt4` 是 Easy Mode，是 MVP 指定的**退路**而非正选 |
| 地图节点在代码里是 `EventButton` | wiki 给不了代码结构，这一步还得自己探 |

---

## 1. 纠正：Quick and Easy 属于「挑战」

Wiki 原文（[Quick and Easy](https://backpackhero.wiki.gg/wiki/Quick_and_Easy)）：

> This **challenge** has the following effects:
> - There is only one floor before each boss
> - +25% each time you earn XP
> - +25% Luck
> - -25% Enemy health

而 [Challenges](https://backpackhero.wiki.gg/wiki/Challenges) 页写得明确：

> **Challenges** are game modes that change the way the game plays. ...
> **Challenges are only available in Quick Run.**

所以真实流程是：

```
主菜单 → 快速开局(Quick Run) → 选角色(钱包鼠) → 选挑战(Challenges) → 开始
```

MVP 文档里写的「`Quick And Easy` 模式」是**概念混用**：它有「模式」的语义，
但游戏里的落点是「快速开局这个模式 + 一个叫 Quick and Easy 的挑战」。

我们之前那局选的是「简单模式」= **Easy Mode**，是另一个挑战。

---

## 2. 挑战效果 ↔ `runProperties` 一一对应（强证据）

| 挑战 | Wiki 效果 | 实测 `runProperties` |
|---|---|---|
| [Standard](https://backpackhero.wiki.gg/wiki/Standard) | 只有「从 Matthew 开始」 | `rt1` props=**1**：`startFromMatt(10)` ✅ |
| [Easy Mode](https://backpackhero.wiki.gg/wiki/Easy_Mode) | 从 Matthew 开始 / +15% 最大血量 / -25% 敌方血量 / 每场普通战斗多拿 1 件 / 普通宝箱多 2 件 / 所有武器 +10% 伤害 / +25% 幸运 | `rt4` props=**7**：`startFromMatt=25`、`increaseHP=15`、`increaseEnemyHealth=-25`、`getExtraItem=1`、`chestsContainExtra=2`、`bonusDamage=10`、`increaseLuck=25` ✅ |
| [Quick and Easy](https://backpackhero.wiki.gg/wiki/Quick_and_Easy) | Boss 前只有一层 / +25% XP / +25% 幸运 / -25% 敌方血量 | **没开过**，预期 props≈4 |

**7 条对 7 条、数值全中**，所以 `RunTypeManager.Instance.runType.runProperties`
就是「当前挑战的效果清单」，可以直接拿来做判别。

### 怎么认出 Quick and Easy

```python
props = {p["type"]: p["value"] for p in run_properties}
is_quick_and_easy = props.get(26) is not None   # oneFloorBeforeBoss
```

`26 = oneFloorBeforeBoss`（见 `RunType+RunProperty+Type`）。
Easy Mode 没有这一条 —— 这也是「`rt4` 不是 Quick and Easy」的判定依据。

> 另外一个可以顺手用的：`rt1` = Standard，只有 `startFromMatt`。

---

## 3. 地图结构（[Dungeon](https://backpackhero.wiki.gg/wiki/Dungeon)）

### 区域（Zone）

3 幕，每幕两个区域，**开局和每次打完 Boss 后选下一个区域**：

| 幕 | 二选一 |
|---|---|
| Act 1 | [The Crypt](https://backpackhero.wiki.gg/wiki/The_Crypt) / [The Bramble](https://backpackhero.wiki.gg/wiki/The_Bramble) |
| Act 2 | [Deep Cave](https://backpackhero.wiki.gg/wiki/Deep_Cave) / [Enchanted Swamp](https://backpackhero.wiki.gg/wiki/Enchanted_Swamp) |
| Act 3 | [Magma Core](https://backpackhero.wiki.gg/wiki/Magma_Core) / [Frozen Heart](https://backpackhero.wiki.gg/wiki/Frozen_Heart) |

### 节点类型（按代码里的 `EventButton` 分）

| 类别 | 内容 |
|---|---|
| 战斗 | 普通战斗、Boss（每幕末，或 Quick and Easy 下每层末） |
| 宝箱 | [Chests](https://backpackhero.wiki.gg/wiki/Chests)：4 件物品**可以全拿**，只要背包装得下；有需要钥匙的锁箱；有寄居蟹伪装成宝箱、开箱即进战斗 |
| 白给 | Small Bonuses（经验花 / 金币堆 / 安娜雕像） |
| 特殊 | Magic Font、Visions of Danger |
| 商店 | Dungeon Shop 及其它 NPC 交易 |
| 事件 NPC | 炼金术师、铁匠、治疗者、赌轮盘的鼹鼠、流浪战士……二十多个，含角色专属 |

对 MVP 的用处：MVP §2.2 说「地图只选战斗/宝箱；遇到商店/事件选离开」——
现在有了明确的分类依据，就看 `EventButton` 上暴露的是哪一类。

---

## 4. wiki 给不了什么

**它不给代码结构。** 具体到我们要动手的部分：

- 地图节点在 C# 里是 `EventButton`（23 个字段，带 `EventButtonAction`、
  `PossibleOutcome`、`PerfromEffect` 协程），不是独立的「地图节点」类型
- 怎么枚举场上的 `EventButton`、怎么安全触发（是调协程还是走 onClick）、
  怎么在地图上区分「战斗」和「商店」——**都得自己探**

所以下一步不变：**读 `EventButton` → 分类 → 加 `choose_node` 动作**。
只是现在知道该期待哪几类节点了，不会漏。

---

## 5. 待办（因这次搜索新增）

1. **把 MVP 文档的措辞改掉**：`Quick And Easy` 是挑战不是模式，
   操作路径是「快速开局 → 角色 → 挑战」。
2. **正式跑一局 Quick and Easy**：挑战列表要滚动，选带 `oneFloorBeforeBoss` 的那个，
   跑完确认 `runProperties` 里出现 `type=26`。
3. 从那以后 baseline 换成 Quick and Easy；Easy Mode 的账本数据仍可复用（物品效果与挑战无关）。
