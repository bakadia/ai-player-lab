# SPIKE-09 · 结构化战斗回执（SPIKE-07 §4 第二步）

> 2026-10-28 下午 · 承接 `SPIKE-08-real-log-ab.md` §7.2。
> 这篇回答一个问题：**怎么让「0 伤害 0 格挡」的物品不再被记成 0/0。**
>
> 结论：桥接的每个 ack 现在都带一个 `receipt`，用动作前后的两份 `Snapshot`
> 算出「谁打谁、挡了多少、上了什么状态、生成了什么、进了什么阶段」。
> Python 侧把回执里非伤害/护甲的效果累进账本，policy 用 `--value-status` 给它们估值。
> **不需要 Harmony patch，也不改游戏行为。**

## 0. 一句话

`delta` 回答「哪些数值变了」，`receipt` 回答「为什么变、是谁对谁做的」。
前者是给机器做校验的，后者才是给账本和策略当原料的。

## 1. 为什么必须是回执，不是调参

SPIKE-08 §7.2 的问题不是估值函数，是**输入里根本没有信息**：

- `HEAT POTION` / `CURSED POTION` 用完后 `delta` 只有
  `inventory_phase`、`items_total`、`items_loose` 这类流程字段；
- `LUCKY STAR` 的幸运是 `Player.uncommonLuck/rareLuck/legendaryLuck`，不在旧 Snapshot 里；
- `REVERSAL AMULET` 这类饰品是**被动**的，主动使用本来就什么都不发生；
- 旧账本只记 `damage_sum` / `block_sum` / `energy_sum`，上面这些全是 0。

所以要么让回执说清楚，要么策略永远只能看到 0。

## 2. 实现：一份 Snapshot 扩了哪些字段

`bridge/src/Actions.cs` 的 `Snapshot` 新增（只用于 ack 的 delta/receipt，不进 `state.json`）：

| 字段 | 内容 | 用途 |
|---|---|---|
| `PlayerStatuses` | 玩家 `Status.GetStatusEffects()` → `type:value` | 中毒/燃烧/力量/幸运… |
| `PlayerMaxHp` | `stats.maxHealth` | CANDY 这类 +最大生命 |
| `LuckUncommon/Rare/Legendary` | `Player.*Luck + *LuckFromItems` | LUCKY STAR |
| `EnemyNames/MaxHp/Statuses` | 每个敌人 | 谁被打了、上了什么状态 |
| `EnemyAttack` | 玩家回合的意图总攻击力 | 结算 `blocked` |
| `ItemNames/Charges/Statuses` | 背包内每件物品 | 「翻面」/充能类物品 |

`Differs()` 现在也逐项比较状态、最大生命、幸运、物品状态和充能。
因此**只改状态、不改血量的动作会 `changed=true`**——这是账本能记到样本的前提。
`noop` 回归仍然通过：`changed=false, delta={}`（E4）。

## 3. 回执长什么样

`bridge/src/Receipt.cs` 把差异翻译成 `effects[]`。四种真实样本：

### 3.1 打中但被格挡（真机实测）

```json
{
  "action": "use_item",
  "item": "WOODEN SWORD",
  "source": "player",
  "effects": [
    {"kind": "energy", "to": "player", "delta": -1},
    {"kind": "damage", "from": "player", "to": "enemy",
     "uid": "e-5846", "name": "Vole ", "amount": 2, "blocked": 5, "killed": false}
  ]
}
```

`amount` 是**真掉的血**，`blocked` 是被护甲吃掉的。旧 delta 只有
`enemies: -2` + `enemies_block: -5`，agent 需要自己拼；现在一句话说清。

### 3.2 敌人回合

```json
{
  "action": "end_turn",
  "source": "enemies",
  "effects": [
    {"kind": "damage", "from": "enemies", "to": "player",
     "amount": 7, "blocked": 0, "incoming": 7},
    {"kind": "energy", "to": "player", "delta": 3}
  ]
}
```

`blocked` = `min(行动前护甲, 总来袭 - 实际掉血)`；回合结束清空的护甲不会重复计数。

### 3.3 生成物品 / 进整理阶段（v6 实测，`end_turn` 触发）

```json
{
  "action": "end_turn",
  "source": "enemies",
  "effects": [
    {"kind": "items", "field": "spawned", "count": 1},
    {"kind": "phase", "field": "inventory", "from": 0, "to": 3}
  ]
}
```

这是 v6 之前完全看不见的效果：`delta` 只有 `items_in_grid`、`items_total` 的变化，
看不出「多了一件东西」和「进了整理阶段」。这次是敌人回合的诅咒触发；
`use_item` 走同一个 `WritePlayer`，所以 HEAT POTION 这类「生成诅咒 + 进整理」的消耗品也会得到同样的两条。

> v5 的 HEAT POTION 样本里 `receipt.effects=[]`，因为当时还没加 `items`/`phase`；
> 这不是「它没效果」，而是回执版本旧。v6 起补上。
>
> v7 起 `phase` 只记「进入 3（specialReorganization）」，不记胜利画面 8——
> 否则每次击杀都会给攻击物品多算一个 `phase` 效果。

### 3.4 重开一局

```json
{"action": "retry_run", "source": "player", "reset": true, "effects": []}
```

`retry_run` / `start_run` 前后不是同一个世界；不标 `reset` 会把「旧敌人消失」
误报成「被玩家打死 40 血」。这个坑是实测踩到的。

## 4. 账本怎么接

`agent/ledger.py` 新增：

- `record_use(..., receipt=...)` —— 只把「非伤害/非护甲」的效果写进 `effects`；
  伤害和护甲仍按 delta 记，避免同一件事记两遍。
- `estimate_effects(name)` —— 每个键的每次使用平均值。
- `effect_score(name)` —— 所有效果绝对值的和，给 policy 用的粗估值。

键的格式：

```
status:BURN:enemy        对敌状态，每次 +N
status:regen:player      对己状态
item_status:reverseHourglass   物品自身状态
max_hp / luck:rare / gold / heal
items:spawned            使用了会生成物品
phase:inventory:3        进入了整理阶段
```

老账本没有 `effects` 字段时全部返回 0/空，行为与 v0.1 一致。

> 2026-10-28 下午清掉了 `data/items.json` 里旧桥接测出的三条 0/0
> （`HEAT POTION` / `CURSED POTION` / `MEAL`）：它们不是「没效果」，
> 是当时量不到。留着会让新策略以为「已知且没用」，再也不会去试。

## 5. policy：`--value-status`

`Flags.value_status` 默认关。打开后：

```python
eff = ledger.effect_score(name)          # 每个状态点/属性点算 1 分
if eff > 0:
    value += STATUS_VALUE * eff          # STATUS_VALUE = 1.0
```

为什么默认关：1 分只是「让它不再恒为 0」，不是经过 A/B 验证的权重。
下一步应该用 `--value-status` 跑 A/B，而不是直接把它设成默认。

`effect_score` 只累加方向明确的正面效果（`status` / `max_hp` / `luck` / `item_status` /
`heal` / `gold`）。`items:spawned` 和 `phase:inventory` 照样记进账本供复盘，
但不参与打分——生成的不一定是好东西，进整理阶段通常是代价。

## 6. 顺手修掉的两个「假合法」

### 6.1 被动饰品不再进 legal

`StateReader` 以前只按 `playType == Active` 判合法，于是 `REVERSAL AMULET`
（Accessory，被动）和 `LUCKY STAR` 这类没有主动费用的饰品也会进 `legal`。
用一次什么都不发生，账本记 0/0，正好是 SPIKE-08 §7.2 的另一半原因。

现在把 `Item2.CanBeUsedActive(...)` 从「诊断字段」提升为**硬门**：
返回 0 时 reason=`cannot_use_active`，不进 legal；返回 -1（调用失败）才退回手写规则。
传的是物品自己 `costs` 的拷贝，不会改游戏状态。实测 `WOODEN SWORD` / `MEAL` /
`ROUGH BUCKLER` 仍然 `can_be_used_active=1`，没有被误杀。

> 注意：这不能替代「效果模型」。被动饰品本来就不该被使用，回执也救不了
> 「把它当主动物品用」这个错误——两条要一起修。

### 6.2 整理阶段诊断

`state.ui` 新增 `in_special_reorg` / `skippable_reorganization`。
原因见 `PITFALLS.md` E9：`inventory_phase=3` 但 `inSpecialReorg=false` 时，
`FinishReorganizeButton()` 会在第一行直接 return，表现为 20 秒超时。

## 7. 真机证据

`data/runs/loop-20261028-142800.jsonl`（v5）：

```
seq=147  use_item WOODEN SWORD -> e-5846: amount=2 blocked=5
seq=...  end_turn -> player amount=7 blocked=0 incoming=7
seq=...  use_item HEAT POTION -> delta {items_total:+1, inventory_phase:0->3}，receipt.effects=[]（v5 旧版）
```

`data/runs/loop-20261028-143513.jsonl`（v6）：

```
end_turn -> items:spawned=1, phase:inventory 0->3
```

`loop-20261028-133045.jsonl`（v2）：`retry_run` 死后重开 `ok=true changed=true`，
随后新一局从 `place_item` 正常继续。

## 8. 局限

1. **按动作聚合，不是逐帧事件流**。同一动作里多段伤害会合并成一条；
   要做「第 1 段被闪避、第 2 段暴击」得用 Harmony patch `Status.Attack`。
2. **敌人回合的攻击者是聚合的**：`from:"enemies"`，不分是哪个敌人打的。
   玩家侧的目标是准的（每个敌人一条）。
3. **状态名依赖枚举**：`StatusEffect.Type.ToString()`；枚举名变了账本键也会变。
4. **`rage` 这类受击反应会被记成攻击物品的效果**。数值很小（0.01~0.03），
   目前不影响决策；要精确区分「物品主动效果」和「受击反应」需要事件流。
5. 玩家侧状态/最大生命/幸运的**真机样本还没收集到**（只验证了敌人状态和物品生成）；
   代码路径与敌人状态共用 `WriteStatuses`，等一局 HEAT POTION / CANDY 就能补上。

## 9. 复现

```powershell
# 编译 + 部署（部署要管理员；agent 侧用 danger-full-access 或人工）
powershell -NoProfile -ExecutionPolicy Bypass -File games\backpack-hero-aipeiwo\bridge\build.ps1

# agent 测试（不需要游戏）
$env:PYTHONIOENCODING='utf-8'
python games\backpack-hero-aipeiwo\agent\tests\test_v0.py

# 带状态估值跑一局
python games\backpack-hero-aipeiwo\agent\loop.py --max-steps 80 --value-status
```
