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

- [ ] M1 `dump_item_catalog` 跑通，`runtime/item-catalog.json` 落盘（含枚举表）
- [ ] M2 分类器跑通：`unknown` 占比 < 20%，账本交叉验证偏差清单出来
- [ ] M3 `--sustain` 接进 policy + 单测（满血不浪费、濒死不乱用）
- [ ] M4 真机 A/B 2 局，掉血/楼层有数（写进 RESULT）

## 5. 坑（先记着）

- `item2s` 里是 **prefab 资产**，不在场景里：反射读字段没问题，但别调
  `GetComponentsInChildren`、别碰 `transform.parent` 之类和实例有关的东西。
- 枚举必须存整数：`Ildump` 只给字段名不给值，顺序即值（`Any=0, Grid=1, …`）。
- 文本是 language key，`LangaugeManager.GetTextByKey` 才有人话，且类名拼写就是
  `LangaugeManager`（游戏自己的错别字）。
- ack 里别塞大 JSON；写文件 + 短 ack。
- 抓完顺手把 `runtime/item-catalog.json` 复制一份到 `data/`，`runtime/` 是会变的。
