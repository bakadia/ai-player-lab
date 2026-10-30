---
name: bph-builds
description: Use when deciding what a Backpack Hero run should build or keep - picking level-up rewards, choosing loot, deciding what to drop, or valuing an item. Covers the economy (7 armor = 1 energy), build archetypes, adjacency synergy, and where the numbers live.
---

# 流派与定价（Backpack Hero）

## 唯一的价格锚：7 护甲 = 1 费

- `ROUGH BUCKLER`：1 费 / 7 挡，是实测基准；账本里木剑是 1 费 / 5.4 净伤。
- **每回合自动给甲的防具**（帽子/手套/鞋子/衣服）等于每回合白赚一份格挡、还不吃能量
  → 按「3 回合」折现：`6 甲/回合 ≈ 2.57 费 ≈ 14.9 等价伤害`（木剑 5.4）。
  代码在 `agent/policy.py`：`BLOCK_PER_ENERGY=7`、`FIGHT_TURNS=3`、`per_turn_value()`。
- 所以：**一件 6 甲/回合的甲 >> 一把 7 伤/1 费的剑**。这就是护甲流成立的原因。

## 三套流派，按「背包里已经在什么」选

| 流派 | 长什么样 | 优先拿 | 忌讳 |
|---|---|---|---|
| **护甲流** | 3–4 件每回合甲 + 1 把武器 + 消耗品 | 每回合甲、加格子、相邻加甲件 | 拿第二把武器（吃能量、占格） |
| **武器流** | 2 把高伤武器 + 加攻速/暴击 | 伤害、连击（combo） | 只拿低伤多件（每件都要 1 费） |
| **消耗流** | 食物/药水多、靠续航磨 | 治疗、回能量 | 背包被消耗品占满、没格子放甲 |

**一套流派 + 几个辅助 + 若干消耗品**，不要什么都捡。判断依据看 digest 的
`bag.armor_per_turn`：已经 ≥ 6 就基本是护甲流，别再往武器上投资。

## 相邻协同（描述里写着的，别猜）

桥接 v8.25 把悬停描述抓进了 `data/item-catalog.json` 的 `description_runtime[]`，
`itemdb` 解析成 `helps_armor` / `helps_weapons` / `synergy_area`：

```
Knight's Armor : Armor items and Shields adjacent to this or diagonal to this get +N Block
Tower Shield   : Armor items to the left get +N Block to self     ← 带方向
Bronze Breastplate: Weapons adjacent to this get +N Damage
```

- 这类道具**必须贴着目标摆**才有收益（`place.synergy_bias` 已经在放置时生效）。
- 背包里没有目标时，它只值自己的那点属性——估值会低一档（`item_value(armor_pieces=)`）。
- 想让协同真正成型，光靠「新拿进来的自动挑位置」不够：**已有的甲要被挪过去**，
  这一步目前还是手工活（`plan_repack` 没带协同偏置）。

## 查一件道具值多少

```powershell
python agent\itemdb.py                      # 类别分布 / 续航榜 / 每回合收益
python agent\itemdb.py --write              # 生成 data/items-unknown.md（人工过 unknown）
python tools\bph_item_catalog.py --copy     # 重新 dump 903 件（含运行时描述）
```

- 静态数值：`data/item-catalog.json`
- 实测均值：`data/items.json`（账本，每局自动累积；`WOODEN SWORD` 已有 1000+ 样本）
- 定价公式：`agent/backpack.py::item_value`（账本 + 类别 + 每回合甲 + 相邻协同）

## 升级界面怎么选

优先级（护甲流）：**每回合护甲 > 加格子 > 最大生命 > 治疗 > 伤害**。
武器流：**伤害 > 攻速/combo > 加格子 > 最大生命**。
永远优先「每回合生效」而不是「一次性」，除非血已经见底。

## 什么时候该丢东西

- `bag.free == 0` 且战利品里有**每回合甲 / 相邻加甲件 / 治疗**：值得丢一件最低价值的。
- 不要为了捡武器丢甲；不要丢「唯一的主力输出」（`drop_ranking` 已经保护）。
- 账本没样本的东西不当作 0 分（`SENTINEL` 哨兵、未测过 ≠ 没用）。
