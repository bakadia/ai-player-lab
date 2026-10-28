# SPIKE-08 · 真机日志上的战斗策略 A/B（SPIKE-07 §4 补丁 + 两条补充）

> 2026-10-27 晚 · 承接 `SPIKE-07-combat-ab.md`（合成局面）。
> 这篇只回答一个问题：**SPIKE-07 §4 的两行补丁，能不能修掉真机上「ROUGH BUCKLER 整局 0 次使用」。**
> 结论先行：**不能。** 补丁本身是对的，但它作用的位置在「未知物品」分支之后，
> 而真机的卡点正好在「未知物品」分支里。另外补了一条致命 bug 和一个操作约束。

## 0. 结论

| 改动 | 真实 27 个决策里改变了几个 | buckler 用了几次 |
|---|---|---|
| `aware_block`（incoming 减已买护甲） | 1 / 27 | 0 |
| `ratio_energy`（按能量归一化） | **0 / 27** | 0 |
| 两条一起（= SPIKE-07 §4 原文） | 1 / 27 | 0 |
| 再加 `explore_threat` | 10 / 27 | **9** |

- `ratio_energy` 在真实背包上是**空操作**：所有物品都是 0 费或 1 费，除以 `max(1, energy)` 不变。
- `aware_block` 唯一改变的那一个决策是「护甲已经 18 点了，别再加一面盾」——对的，但和存活无关。
- **buckler 一次都不用的原因不是估值算错，是它根本进不了估值。**
  `score_action` 在 `ledger.estimate_damage(name) is None` 时直接返回 `EXPLORE_SCORE=0.5`，
  后面所有格挡逻辑都不会执行。0.5 永远打不过已知的剑（~5.9），于是永远不会被试，
  于是永远学不到数值——**账本冷启动死锁**。

## 1. 方法：先把回放做对

仓库里没有「把日志重放成决策序列」的工具，直接拿 `data/items.json`（终态账本）去回放
会**偷看未来**：SPOILED MILK 是 seq=170 才第一次被用、才进账本的，用它去评 seq=47 的局面
等于开卷考试（第一次回放因此把 `current` 只复现到 24/27）。

正确做法是**按 loop.py 的记账逻辑增量重建账本**：

1. 从终态账本里减掉本局所有 `use_item` 的贡献，得到「本局开始前」的账本；
2. 顺着日志走，每遇到一条 `act` 就按 `loop.py` 同样的规则（`_damage_from` / `player_energy` /
   `enemies_block`）把样本写进账本、把 `explored` 记上物品名；
3. 每到一条 `decision` 就用**当时**的账本 + `policy.choose` 重新选一次。

这样 `baseline`（开关全关）能 **27/27 复现真实日志**，回放才可信。
脚本：`.scratch/replay_real_policy.py`（临时，暂未入库）。

## 2. 为什么「按威胁探索」必须存在

用真实局面反推：假设 buckler 的格挡 B 是已知的，它在死亡回合（hp=2、来袭=7）
对**任意 B ≥ 3** 都会赢过剑——因为 `_block_value` 在「来袭 ≥ HP」时有 2 倍紧急系数。
也就是说**估值函数没问题，缺的是知识**。

而在非致命的回合（hp=40、来袭=9），`block_weight=1.0` 时 7 点格挡得 6.9 分，
也压过剑的 5.83——用户要的「先 1 费套盾、剩下 2 费打输出」在模型里是自洽的。
**前提还是那个 7 得先进账本。**

所以补的是：

```python
# 没到必死，但场上有威胁：按「它最多能替我挡掉多少」估
prior = EXPLORE_THREAT_WEIGHT * min(incoming, EXPLORE_THREAT_CAP)   # 0.8 × min(incoming, 10)
```

来袭 9 时给 7.2 > 5.83 → 会去试；来袭 0 时给 0 → 不会白试。
`0 < hp <= incoming`（下一击必死）时改给 `SURVIVAL_PRIOR=40`（高于普通输出、低于
`LETHAL_BONUS=100`，所以「能一击清场」仍然优先于探索）。

常量大小关系是有意的，别随手改：
`LETHAL_BONUS(100) > LOOT_SCORE(50) > SURVIVAL_PRIOR(40) > 普通输出(~6)`。
把 `SURVIVAL_PRIOR` 设成 50 会和 `LOOT_SCORE` 打平，胜负取决于 `legal` 的顺序——踩过。

## 3. 四个开关（`policy.Flags`，默认全关 = v0.1 行为）

| 开关 | 作用 | 单独开有没有用 |
|---|---|---|
| `aware_block` | `incoming` 减去 `player.block` | 有用（止损），但不解决探索 |
| `ratio_energy` | 分数 `/= max(1, energy)` | **在真实背包上是空操作**；SPIKE-07 已说单独开更差 |
| `explore_threat` | 未知物品按威胁估值 / 致命时给生存先验 | **这条才真正让防御物品出现** |
| `value_energy` | 把 `+N 能量` 折成分数 | 回能量的消耗品全靠它才可见 |

两个实现细节：

- **`player.block` 是 `Status.armor`，拿不到时 `StateReader` 写 `-1` 哨兵。**
  SPIKE-07 §4 原文是 `int(state["player"].get("block") or 0)`，`-1` 会原样传进去，
  变成 `incoming - (-1)` = 来袭 +1。已改成 `max(0, ...)`。
- **`LETHAL_BONUS` 不参与能量归一化。** 连奖励一起除以能量，会让 3 费的必杀（100/3=33）
  输给 1 费的普通输出。所以 `_score_parts` 把「价值」和「一次性奖励」分开返回。
  这也是和 `ab_combat.py`（照 SPIKE-07 原样，奖励也除）**唯一的口径差异**。

能量折算的口径：`energy_value` = 背包里已知攻击物品的最高「均伤/费用」，
把 `+2 能量` 折成「够我再打两下最强的攻击」。只认**正**的净收益——
普通攻击物品的 `player_energy: -1` 是花掉的费用，已经由 `ENERGY_WEIGHT` 计过，
记进账本会重复扣一次。

## 4. 一个必须修的账本 bug

`loop.py` 原来不管动作成没成，只要 `use_item` 就记一条账本样本：

```python
if item_name:
    ledger.record_use(item_name, ...)   # 以前：失败也记
```

一次**失败/无效**的使用会被永久记成 `0/0` 均值，等于把「没测到」当成「测出来是 0」，
物品再也翻不了身——对「靠探索学数值」的策略是致命的。
现在只有 `ack.ok and ack.changed` 才记账本（`--record-unverified` 可恢复旧行为），
但 `explored` 仍然记上，避免同一件东西反复试。

## 5. 操作约束（踩到了）

**`tools\bph.cmd restart` 在 agent 的 shell 里跑不通。**
游戏跑在用户的交互会话里，agent 的 shell 看不到那个进程（PITFALLS E2 的同一个现象），
`bph.ps1` 于是走到 `taskkill /IM` 并失败：`cannot stop the game from this shell
(session/integrity)`。CUA 驱动能看到窗口（`Backpack Hero.exe`），但它拒绝
kill 不是自己启动的进程。

结论：**agent 无法自己重启游戏**。可用的替代：

- 打死一局后，**先看死亡界面能不能直接开出新局**（`MenuManager.main` 在局内是 null，
  死亡界面是否已加载菜单待实测）；
- 否则需要人工 `bph.cmd restart`（或关掉重开）。

这直接影响 A/B 的组织方式：**每臂独立账本**（`--ledger`），否则先跑的臂把物品学进
共享账本，后面的臂等于开卷考试。

## 6. 升级界面的死循环（同批修掉）

`loop.py` 的 `level_attempt` 以前是**全局计数、从不归零**：
第 1 次升级用 `via=select`（有效），第 2 次就滑到 `spaces`，之后一直是 `end`——
而 `end` 正是 HANDOFF §0 记的「只调 EndLevelUp 会无限重开」。

修法：`level_attempt` 改成**同一次升级内**累加，界面真的没了才归零；
`via` 固定 `select`（实测唯一有效），重试只增加 `times`，不再降级到已知坏的路径；
`--level-up-via` 留作对照。

## 7. 真机 A/B 结果（进行中）

每臂独立账本（`--ledger data\ledgers\<arm>.json`，每次从 `data\items.json` 播种），
指标由 `tools\run_report.py --dir data\runs\ab --glob "ab-*.jsonl"` 汇总。

| 臂 | n | 存活层数 | 剩余血量 | 存活回合 | 零伤害回合 | buckler 使用 | 结果 |
|---|---|---|---|---|---|---|---|
| baseline | 2 | 1 | 0 / 0 | 3 / 5 | 0% / 0% | **0** | 2/2 阵亡 |
| c-full | 2 | 1 | 0 / 19 | 10 / 13 | 0% / 15% | **9 / 11** | 1/2 阵亡 |

**方向明确，但别当结论**：两臂都停在第一层，所以「存活层数 / 剩余血量」区分度不够；
真正拉开的是「存活回合 3/5 → 10/13」和防御物品使用 0 → 20 次。
每局遭遇随机，n=2 只能算定性证据。

### 7.1 真机上可复现的三段证据（`ab-c-full-1.jsonl`）

```
seq=2566  hp=31 incoming=8   ROUGH BUCKLER: explore against the threat
                             (prior=6.40 = 0.8×min(8,10))   6.40 > 木剑 5.88  -> 试了一次
seq=2618  hp=29 incoming=17  ROUGH BUCKLER: est_block=7.00 eff_block=7.00  6.90 > 木剑 5.81
seq=2621  hp=29 incoming=10(=17-7)   已买的 7 点护甲被扣掉（aware_block 生效）
```

最有力的一次（`loop-20261028-000226.jsonl`，5 血、25 点来袭）：

```
hp=5  incoming=18(=25-7)   ROUGH BUCKLER eff_block=14.00 -> 13.90   （必死翻倍）
hp=5  incoming=11(=25-14)  ROUGH BUCKLER eff_block=14.00 -> 13.90
end_turn -> player_hp -4, player_block -21
```

**5 血挨 25 点只掉 4 点**：连叠两面盾把必死局面捞回来了。

### 7.2 还解决不了的：效果不可见的物品

`REVERSAL AMULET` / `HEAT POTION` / `LUCKY STAR` / `CANDY` 用过一次后账本记成 `0/0`，
之后再不会被选中。它们的效果（增益、状态、翻面）不产生快照可见的变化，
按「不记未验证样本」的规则被跳过 → `explored` 里变成 `already explored` → 恒 0 分。
这需要 SPIKE-07 §4 第二步的结构化回执才能解决，不是打分能修的。

> **2026-10-28 下午：已由 `SPIKE-09-combat-receipt.md` 处理。**
> 回执现在会给出 `status` / `max_hp` / `luck` / `item_status` / `items:spawned` /
> `phase:inventory`，账本记 `effects`，policy 用 `--value-status` 估值。
> 另外发现两类「假不可见」：`REVERSAL AMULET`/`LUCKY STAR` 本质是**被动饰品**，
> 不该进 `legal`（v7 起用 `CanBeUsedActive` 挡掉）；`HEAT POTION` 实测是
> 「生成一件物品 + 进整理阶段」，回执用 `items:spawned` + `phase` 记录。

## 8. 真机踩到的六个卡死点（都已在 `loop.py` 兜住）

| 现象 | 根因 | 处理 |
|---|---|---|
| 升级界面无限重开 | `level_attempt` 是全局计数，第 2 次起滑到已知无效的 `via=end` | 改成「同一次升级内计数」，`via` 固定 `select` |
| 胜利界面卡住 | 屏幕上有「升级!」但 `can_level_up=false`，`start_event` 无动作 | `inventory_phase==8` 时直接发 `level_up` |
| 宝箱开不了 | **地图开着时 `StartEvent()` 是空操作**，而地图关不掉（`ShowMap` 两种 force 都无效） | 新增桥接 `close_map`；失败则拉黑该事件换目标 |
| 两个房间来回走 | 事件永远「未完成」，每轮都被重选 | 事件拉黑 + `arrivals>=2` 也拉黑 |
| 走进死路 30 秒×4 | 游戏把「实际走不到」的房间也列进 `legal` | 移动失败 → `bad_rooms` |
| 同一物品连撞 4 次 | 游戏把「当前用不了」的物品也列进 `legal` | 本回合失败 → `bad_uids`（只在同回合剔除） |

**共同教训：`legal` 不等于「现在真能做」。** 桥接的合法集是「名义合法」，
真机上有三类假阳性（不可达房间、不可用物品、被 UI 挡住的事件）。
agent 必须自己记住失败过的目标，不能假设再试一次就会成功。

### 8.1 第七个卡死点（10-28 下午补充）

`inventory_phase=3` 但 `GameManager.inSpecialReorg=false` 时，
`FinishReorganizeButton()` 第一行直接 `return`，`finish_reorganize` 表现为 20 秒超时；
连续 4 次就把 loop 判死。偶发时重试一次能过，持续 false 时只能 `retry_run` 重开。
详见 `PITFALLS.md` E9；`state.ui` 现在带 `in_special_reorg` 便于先判这个字段。
