---
name: bph-combat
description: Use when reasoning about a Backpack Hero fight - reading enemy intents, deciding whether to attack/block/end turn, judging lethal, or explaining why a battle was lost. Covers the turn economy, block-vs-damage math, focus fire, and the specific death causes measured on this repo.
---

# 战斗基础（Backpack Hero）

## 一回合的经济

- 每回合默认 **3 能量**，用一件道具花它标的能量（多数武器 1、盾 1、食物 0）。
- 打出的护甲**只在当回合有效**，敌方回合结算后就清零；每回合自动给甲的防具是例外（永久）。
- 因此「1 费 7 挡」和「1 费 5.4 伤」在数值上接近，但**挡只是不亏血、伤才推进战斗**。
  只有当你预计「这回合挨的打 ≥ 你还能挡的量」时，挡才比打更值。

## 三个决定性的数

1. **incoming**：敌人本回合会打进来多少（`state.enemies[].attacks[].power` 求和）。
2. **已买到的 block**：这回合已经打出去的护甲要从 incoming 里扣掉（`--aware-block`）。
3. **lethal**：能不能一击清场。能就清场——少一回合 incoming 比什么都值钱。

## 集火（focus fire）

多敌房里「每回合 incoming」是**所有敌人之和**。所以：
- 优先打「能一击打死」的那个（`--focus-fire` 的 KILL_TURNS 加成）。
- **炸弹怪**（攻击力 0、自爆 26 血）必须最优先，`attacks[].power == 0` 不代表安全。
- 残血敌人不清掉，它每回合照打——这是真机上「3 敌房一次掉 26 血」的来源。

## 实测的四种死法（按出现频率）

| 死法 | 现场 | 对策 |
|---|---|---|
| 多敌房爆发 | 一场掉 17–34 血 | 集火 + 先清能秒的 + 别在满能量时结束回合 |
| 弃掉主武器 | 为捡法杖丢了剑，之后只能挡 | `drop_ranking` 已保护「唯一攻击来源」 |
| 龟缩死循环 | 只打盾不打人，敌人永远不死 | `--aware-block` 把已买到的护甲从 incoming 扣掉，盾的分数会掉 |
| 整理阶段付血 | `finish_reorganize` 一次 2–5 | 见 `bph-play` 的 hazard 条：默认付钱，别塞 |

## 消耗品什么时候吃

- 治疗按**缺口**截断（`min(heal, max_hp-hp)`）：满血吃 = 浪费。
- 血 < 50% 且这回合不会再挨打时吃，效率最高（不会治疗溢出后被下一击吃掉）。
- 0 费食物优先吃，因为它同时**腾出格子**。

## 别做的事

- 不要在 `legal` 里还有正分动作时 `end_turn`（loop 会在全 0 分时才结束回合）。
- 不要指望「挡满就能赢」：`min(block, incoming)` 之后多出来的护甲是纯浪费。
- 不要为了挡住 1 点伤害花 1 能量——那点能量用来推进战斗更值。
