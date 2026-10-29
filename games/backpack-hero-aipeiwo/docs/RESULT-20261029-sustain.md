# RESULT · 2026-10-29 · 续航 Step 3：`--sustain`（M3 代码完成，A/B 未收敛）

> 目标（`docs/PLAN-item-census.md` §3）：把「回血 / 每回合护甲 / 稀有道具」接进 policy，
> 让 40 血能撑过第 5–6 场。**代码 + 单测完成**；真机 A/B 被两个别的问题打断，
> 结论只到「方向对、样本不够」。

## 1. 交付物

| 文件 | 改动 |
|---|---|
| `agent/policy.py` | `Flags.sustain` / `sustain_weight`；**有效治疗**计分 `min(heal, max_hp-hp)`；`reward_score` / `pick_reward`（N 选 1 按类别挑） |
| `agent/backpack.py` | `item_value(..., db)` 按类别加分；`drop_ranking` 不丢 `sustain`/`relic`，**唯一的攻击来源也不丢** |
| `agent/place.py` | 腾位前比价：要丢的东西比要捡的还值钱 → 放弃（`place_not_worth`） |
| `agent/loop.py` | `--sustain` 开关、`ItemDB` 装载、`pick_dialogue` 用 `ui.dialogue_items`、地图防绕圈（E23） |
| `bridge/src/MapProbe.cs` | `ui.dialogue_items[]`（N 选 1 的候选名）→ 桥接 **v8.22 / 137,216 bytes / `7E9749A1…`** |
| `agent/tests/test_v0.py` | 3 个新用例：续航计分（含**账本无样本**的食物）、奖励挑选、腾位保护 |

## 2. 单测覆盖（都过了）

```text
PASS sustain scoring   # 满血 0 分；20/40 时 +10；缺口 3 时截断到 3；max_hp=-1 不给分；
                       # 账本里没样本的 SALAMI 也能拿到 +20（这条是 A/B 打出来的 bug）
PASS reward pick       # 满血拿剑，残血拿膏药
PASS drop keeps sustain# 香蕉（续航）与唯一武器都不进丢弃候选；只剩它们时才退回
```

## 3. 真机 A/B：被两件事打断，样本不够

原本想「`--focus-fire` vs `--focus-fire --sustain`」各 2 局。实际跑出来的是：

| # | flags | 步数 | 最深层 | 等级 | 结局 | 备注 |
|---|---|---|---|---|---|---|
| 1 | focus_fire + sustain | 68 | 1 | 1 | 第 1 场战斗死亡 | **为捡 ELECTRIC WAND 丢了 WOODEN SWORD**，之后只能挡 |
| 2 | focus_fire + sustain | 101 | 1 | 1 | 第 2 场死亡 | 只用盾挡、不出剑（龟缩死循环），每回合 3 次格挡 |
| 3 | focus_fire + aware_block（对照） | 183 | **2** | **2** | 3 敌房 hp 2→0 | 8 场战斗，每场掉血 **3.6** |
| 4 | + sustain（修完弃牌/龟缩后） | 175 | 2 | 2 | 4 敌房 hp 1→0 | 仍没吃到任何回血（背包里没食物） |
| 5 | 同上，接着 #4 的残局重跑 | 98 | 2 | 2 | 3 敌房 hp 2→0 | **地图来回走 85 步**（E23），修完才继续 |

三个真问题（都不是 `--sustain` 本身）：

1. **弃牌自杀**：`--make-room` 会为了捡东西丢掉主武器 → 已加两道保护：
   唯一的攻击来源不丢；要丢的东西比要捡的值钱就放弃。
2. **龟缩死循环**：格挡 7 > 剑 5.4，能量全喂盾，永远打不死敌人。`--aware-block`
   （已有的开关）把「已经买到的护甲」从 incoming 里扣掉，第三次格挡就掉到 0 分，
   剑才会出手。#3 的 8 场 3.6 血/场就是这么来的。
3. **地图绕圈 85 步**（E23）：目标房间图上可达、`legal` 上点不到，`first_step`
   退化成「A↔B 互为最近邻居」。已加 12 步 / 同房间 3 次的上限，超了就拉黑换目标。

## 4. 结论

**`--sustain` 已经在真机上触发并真的回血**（2026-10-29 21:1x）：

```
[step 186] mode=battle hp=32
  -> use_item CANDY APPLE  (score=8.50 CANDY APPLE:
     sustain=8/10hp(hp=32/40) worth=+8.00 (unknown item))
     ok=True changed=True delta={"player_hp": 8, ...}
[step 187] mode=battle hp=40
```

- 这条**账本里没有样本**（`unknown item`）——修之前它会在 `est is None` 分支直接
  返回 0.5 探索分，永远不会被吃。
- 有效治疗按缺口截断（10 → 8），`delta.player_hp=8` 和算出来的完全一致。

这段 A/B 真正挖出的是四个死因（弃牌、龟缩、绕圈、**战利品环节根本没跑**，E24）。
前三个让单局活不过 2 层；第四个让「回血」这条路在真机上根本不存在——接上战利品后
每场 7 件、配额 3 件，`--sustain` 才有东西可吃。

仍缺的是**统计意义上的 A/B**：单局方差太大（3.6 / 2.6 / 40 血/场都出现过），
至少每组 3 局才谈得上比较；现在只证明「机制通了、数值对」。

## 5. 下一轮怎么做（建议）

1. **A/B 改成同图层对照**：两组都用 `--aware-block --focus-fire --make-room 2`，
   只差 `--sustain`；每组至少 3 局。
2. **每回合护甲**（`block_per_turn`）还没接进打分：它只在丢东西时算了价值。
   下一步可以给「每回合 6 甲」的道具一个「预计还剩几回合」的折现分。
3. 整理阶段掉血（每轮 -3~-8）是另一个大头，值得单独查（污染/诅咒机制）。
