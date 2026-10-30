# PLAN · 道具普查（item census）· 给一个专门的会话

> 起因：存活的三个来源——①每回合给护甲的道具、②能回血/给护甲的消耗品、
> ③更复杂的稀有道具——现在 `policy` 一概看不见（治疗类 0 分，永远不会主动用）。
> 想按「续航」打分，得先有**全量道具的结构化清单**。这份是那个会话的执行计划。

## 0. 关键前提（已确认，省掉最贵的一步）

**不用解析 Unity 资产包。** 运行时就能拿到全部道具：

```
DebugItemManager.main.item2s      // List<Item2> —— 全部道具 prefab 的引用
ModItemLoader.main.modItems       // List<Item2> —— mod 加进来的（一般空）
```

依据：`ItemSpawner.GetAllValidItems()` 的 IL 第一条就是
`ldsfld DebugItemManager::main; ldfld List<Item2> DebugItemManager::item2s`，
然后按 rarity/type 过滤 —— 也就是说这张表就是游戏的**道具总池**。

顺带：`DebugItemManager` 自己有个 `exportItemJSON` 调试开关（`Start()` 里被置 false），
说明官方也导出过类似数据，但入口在 DebugMenu 里，不如我们自己读表可控。

## 1. Step 1（约 30 分钟）：桥接 `dump_item_catalog` + 枚举表

### 1.1 新只读动作

`{"type": "dump_item_catalog", "out": "runtime/item-catalog.json"}`

- 遍历 `DebugItemManager.main.item2s`（必要时拼上 `ModItemLoader.modItems`）；
- 每件 Item2 dump 下面的字段（**枚举一律存整数**，Python 侧查表）；
- **直接写文件**（`RuntimePaths` 下），不要走 ack：几百件 × 十几个数组，
  ack JSON 会有几 MB，还会拖慢 10Hz 的主循环；
- 返回一个短 ack：`{"count": N, "path": "...", "bytes": M, "ms": ...}`。

### 1.2 字段清单（照抄即可）

| 组 | 字段 |
|---|---|
| 身份 | `name`、`displayName`、`itemType[]`、`rarity`、`isStandard`、`oneOfAKindType`、`itemGroupings[]`、`spawnGroupings[]` |
| 经济 | `cost`、`originalCost`、`sigilEnergy`、`charges`、`currentCharges`、`playType` |
| 几何 | `moveArea`、`moveDistance`、`offsetOfItemShape`、`mustBePlacedOnItemType`、`mustBePlacedOnItemTypeInCombat`、`gridObject.gridPositions.Count`（占格数） |
| 效果 | `energyEffects[]`、`combatEffects[]`、`movementEffects[]`、`createEffects[]`、`modifiers[]`、`addModifiers[]`、`activeItemStatusEffects[]`、`usesLimits[]` —— 每条 effect **把它自己的所有 public 字段都 dump 出来**（类型 + 数值 + 枚举整数） |
| 文本 | `descriptions[]`（key + 用 `LangaugeManager.GetTextByKey(key)` 取到的人话；类名就是这么拼错的） |
| 适用 | `validForZones[]`、`validForCharacters[]` |

> `Item2` 上还有 `effectTotals` / `modifierTotals`，是运行时算出来的合计，
> prefab 上一般是空的 —— 可以 dump 但别指望它。

### 1.3 枚举表（一次性）

用 `tools/dnmap/Ildump.exe` 把相关枚举的**字段顺序**导出来，落成
`data/enums.json`（`{ "Item2/ItemType": ["Any","Grid",...], ... }`）。至少要：

`Item2/ItemType`（已知：22=Relic、32=Carving）、`Item2/Rarity`、`Item2/PlayType`、
`Area`、`AreaDistance`、`Length`、`StatusEffect/Type`、以及各类 effect 里的 `Type`。

### 1.4 验收

- `count` 与 `item2s.Count` 一致；
- 随机抽 5 件与游戏内描述对得上（`WOODEN SWORD` / `ROUGH BUCKLER` / `MEAL` /
  `DEBUFF POTION` / `CRISIS RING`）。

## 2. Step 2（约 30–45 分钟）：分类器 `agent/itemdb.py`

### 2.1 派生指标

```
damage, hits, block, block_per_turn, heal, max_hp_gain, energy_gain,
status_inflicted[], status_self[], cells, cost, rarity, types[]
```

### 2.2 类别（互斥的"主类别" + 可叠加的 tag）

| 主类别 | 判定 |
|---|---|
| `sustain` | 有 `heal` 或 `max_hp_gain`（**这次的靶心**） |
| `defense` | 有 `block`；`block_per_turn` 再打 tag `per_turn` |
| `attack` | `damage > 0` |
| `energy` | 只改能量/费用 |
| `status` | 只上状态（`poison/bleed/heat…`） |
| `relic` | `itemType` 含 `Relic(22)`、`oneOfAKindType != none` |
| `utility` | 其它（钥匙、地图、商店类） |
| `unknown` | 判不出来 → 进 `data/items-unknown.md` 人工过 |

**别试图自动解释稀有道具的条件触发**（modifier / scripted trigger）：
原样 dump + 描述文本，先按文本关键词给弱标签即可。

### 2.3 交叉验证（重要）

`data/items.json`（账本）里已经有实测均伤/均格挡，拿目录里的静态值跟它比：
偏差 > 30% 的全列出来 —— 这既是验收分类器，也是发现「账本被少数样本带偏」的机会。

## 3. Step 3（约 30 分钟）：接进 `policy` + 真机 A/B

1. **续航打分**（新 flag `--sustain`，默认关）：
   `heal 值 = min(heal, max_hp - hp) × SUSTAIN_WEIGHT`，
   满血时治疗=0 分（和 `_block_value` 的 `min` 逻辑一致）；
   `block_per_turn` 按「预计还剩几回合」折现，别按回合数无脑乘。
2. **丢弃优先级**：`place_with_room` / `choose_drop` 现在按名字价值，改成按分类
   （`sustain`/`relic` 不丢；`attack` 里低于均值的先丢）。
3. **三选一 / 商店**：`pick_dialogue` 有候选名之后按类别挑（现在固定拿第 0 个）。
4. **A/B**：`bph_xp_curve.py --stop-on-dead` 各 2 局，看「每场掉血 / 最深楼层 / 等级」。

## 4. 会话内的里程碑（做完就打勾）

- [x] M1 `dump_item_catalog` 跑通，`runtime/item-catalog.json` 落盘（含枚举表）
- [x] M2 分类器跑通：`unknown` 占比 < 20%，账本交叉验证偏差清单出来
- [x] M3 `--sustain` 接进 policy + 单测（满血不浪费、濒死不乱用）
- [x] M4 真机 A/B 3 局 vs 3 局，掉血/楼层有数（`docs/RESULT-20261030-ab-m4.md`：
      层数 1.3→1.7、回血 1/3 局→3/3 局；但每场掉血 7.6→7.9，仍在方差内）

## 5. 坑（先记着）

- `item2s` 里是 **prefab 资产**，不在场景里：反射读字段没问题，但别调
  `GetComponentsInChildren`、别碰 `transform.parent` 之类和实例有关的东西。
- 枚举必须存整数：`Ildump` 只给字段名不给值，顺序即值（`Any=0, Grid=1, …`）。
- 文本是 language key，`LangaugeManager.GetTextByKey` 才有人话，且类名拼写就是
  `LangaugeManager`（游戏自己的错别字）。
- ack 里别塞大 JSON；写文件 + 短 ack。
- 抓完顺手把 `runtime/item-catalog.json` 复制一份到 `data/`，`runtime/` 是会变的。

## 6. Step 1 已完成（2026-10-29，v8.21）——数据长什么样，照这个写 Step 2

产物：

| 文件 | 内容 |
|---|---|
| `runtime/item-catalog.json` / `data/item-catalog.json` | **903 件**道具，2,806,256 bytes |
| `data/enums.json` | **165 个枚举**（真值，来自元数据常量），41 KB |
| `bridge/src/ItemCatalog.cs` | 只读动作 `dump_item_catalog`（含通用递归 dumper） |
| `tools/bph_item_catalog.py` | dump + 体检（数量/字段/枚举/抽样/占格/续航预览） |
| `tools/bph_item_enums.py` | 由 `tools/dnmap/EnumDump.exe` 生成 `data/enums.json` |

实测（menu 场景，`source=item2s`、`mod_count=0`、141–188 ms）：

- `count = 903`，和 `DebugItemManager.item2s.Count` 一致；
- 抽样 5 件全对：WOODEN SWORD / ROUGH BUCKLER / MEAL / DEBUFF POTION / CRISIS RING；
- 占格分布 `1格 588 / 2格 178 / 3格 71 / 4格 65 / 6格 1`（木剑 3 格、圆盾 4 格）；
- 453 件有 `costs[]`（有费用），其余 450 件是纯被动；
- `effect.type` 出现 50 种，全部能在 `data/enums.json` 里查到。

三处**和计划原文不一样**，Step 2 必须照实际数据写（也记进了 PITFALLS E20/E21）：

1. **`cost` / `originalCost` / `charges` 在 prefab 上全是 `-999` 哨兵**。
   真实费用看 `costs[]`；桥接已折算成 `energy_cost` / `mana_cost` / `gold_cost`
   （没有就是 `-1`）。次数看 `uses_limits[]`。
2. **`Item2.gridObject` 在 prefab 上是 null**，占格数是桥接从子物体
   `BoxCollider2D` 的 `size`/`offset` 折出来的（`collider_shapes[]` 里有原始 w/h/offset）。
3. **`descriptions[]` 833 件是空的**：物品描述是运行时按 effect 拼的。
   有描述的那 70 件里，7 条 `GetTextByKey` 返回的是
   `Translation not found for key ...` 兜底串，别当成真文本。

捷径（Step 2 会用到）：

- 通用 dumper 给每个数据对象打了 `"$type"`（`Effect` / `Modifier` / `Trigger` / `Cost` …），
  所以 `itemdb.py` 可以**递归找 `$type == "Effect"`**，不用为每种效果写一套解析。
- 效果的语义 = `type`（见 `Item2+Effect+Type`）+ `value` + `target`；
  `modifiers[].trigger.trigger` 是触发时机（`onTurnStart` / `onUse` / `constant` …），
  「每回合给护甲」就靠这一层区分。
- 已实测的形状样例：木剑 `combat_effects[0].effect = {type:0(Damage), value:7, target:2(enemy)}`，
  圆盾 `{type:1(Block), value:7, target:1(player)}`，苹果 `{type:2(HP), value:5}`。

## 7. Step 2 也已完成（`agent/itemdb.py`）——分类器实际长这样

`python agent/itemdb.py [--write]` 输出类别分布 / 续航榜 / 每回合收益 / 账本交叉验证，
`--write` 生成 `data/items-unknown.md`。测试：`python agent/tests/test_itemdb.py`。

**实际分布（903 件）**：

| 类别 | 件数 | 占比 |
|---|---|---|
| attack | 212 | 23.5% |
| utility | 157 | 17.4% |
| support | 118 | 13.1% |
| relic | 95 | 10.5% |
| status | 91 | 10.1% |
| defense | 87 | 9.6% |
| energy | 56 | 6.2% |
| unknown | 54 | **6.0%**（目标 <20%） |
| sustain | 33 | 3.7% |

**计划外新增 `support` 类别**：`modifiers[]` / `addModifiers[]` 改的是**别的**道具
（例：KNIGHT'S ARMOR 自己每回合 6 甲，另给相邻 Armor/Shield +2）。
第一版把它们算进自己的输出，导致 337 件都成了 support、骑士甲变成 8 甲/回合；
拆开后才对。`ItemStats` 因此多了 `damage_active`（只算 onUse/onCombo 这类**立刻结算**的）、
`support_damage` / `support_block`（给别人的）、以及 `reactive` 标签。

**账本交叉验证**（样本 >= 5 的只有 3 件）：

| 物品 | 样本 | 静态伤 | 实测伤 | 偏差 | 静态挡 | 实测挡 | 偏差 |
|---|---|---|---|---|---|---|---|
| WOODEN SWORD | 623 | 7.0 | 5.41 | **-23%** | 0 | 0 | - |
| ROUGH BUCKLER | 178 | 0 | 0 | - | 7.0 | 7.04 | **+1%** |
| MEAL | 5 | 0 | 0 | - | 0 | 0 | - |

木剑 -23% 不是分类器错：账本记的是**净伤害**（被格挡吃掉的部分另记 `absorbed_sum`）。
所以这条交叉验证能证明「护甲类完全对得上」，伤害类要按 `(damage+absorbed)/samples` 再比。

**Step 3 要注意的两件事**：

1. 续航不能只看名字：`sustain` 33 件里 20 件是 0 费食物（heal 2–20），
   但 GEMSTONE HEART（heal 8）挂在 `onKillNonSummonEnemy` 上，不是想用就用。
2. `unknown` 54 件集中在 Accessory/Component/Core——它们的数字效果在
   `ScriptedTrigger` / 组件脚本里，prefab 上确实没有 `Effect`；`data/items-unknown.md` 里可人工过。
