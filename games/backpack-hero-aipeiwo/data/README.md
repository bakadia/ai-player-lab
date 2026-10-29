# data/

这个游戏的数据资产。

## `items.json` — 效果账本

**这是实验产出，不是官方数据。** 由 `agent/ledger.py` 在打的过程中自动累积：

```json
{"items": {"WOODEN SWORD": {"samples": 4, "damage_sum": 28, "block_sum": 0,
                            "effects": {"status:rage:enemy": {"count": 3, "sum": 3}}}},
 "note": "...", "schema": "bph.items.v1"}
```

| 字段 | 含义 |
|---|---|
| `samples` | 用了多少次 |
| `damage_sum` | 累计实际打掉的血（击杀按剩余血量算） |
| `block_sum` | 累计给自己加的护甲 |
| `absorbed_sum` | 累计被对方格挡吃掉的量（有才写） |
| `energy_sum` | 累计净回能量（有才写） |
| `effects` | 结构化回执里的非伤害效果；键形如 `status:BURN:enemy`、`max_hp`、`items:spawned`、`phase:inventory:3`，值为 `{count, sum}` |

要注意的：

- **样本少的时候别当真值用**。4 次采样得到 7.00 只是「看起来对」，波动很大。
- 键是 `displayName`，跨局稳定；`instance id` 每局都变，不能当键。
- 名字带尾随空格，存之前已 `strip()`。
- 已知偏差：被格挡的那一刀 `damage_sum` 记 0，`absorbed_sum` 记真实吸收量。
  所以 `damage_sum / samples` **不是**「这件物品的平均伤害」，而是
  「打在有护甲目标上的平均净输出」。要算理论伤害得 `(damage_sum + absorbed_sum) / samples`。
- `effects` 只记「delta 看不见」的东西（状态/最大生命/幸运/物品自身状态/生成物品/整理阶段）；
  伤害和护甲不重复记。没有 `effects` 的老条目一律当「没测过」，不是「测出来是 0」。

## `item-catalog.json` — 道具总池（静态目录）

`tools/bph_item_catalog.py` 从游戏里 dump 出来的**全量道具清单**（903 件，2.8 MB），
源头是 `DebugItemManager.main.item2s`（也就是 `ItemSpawner.GetAllValidItems()` 用的出生池）。
不是实验数据，是版本快照；换游戏版本要重抓。

每件道具的字段（snake_case 是给 `agent/itemdb.py` 的契约）：

| 组 | 字段 |
|---|---|
| 身份 | `name`（prefab 名）、`display_name`、`display_name_text`（人话）、`item_type[]`、`rarity`、`one_of_a_kind`、`item_groupings[]`、`spawn_groupings[]`、`play_type` |
| 经济 | `energy_cost` / `mana_cost` / `gold_cost`（由 `costs[]` 折算，无则 -1）、`cost` / `original_cost` / `charges`（**prefab 上是 -999 哨兵**）、`sigil_energy` |
| 几何 | `cells`（占格数）、`grid_positions[]`、`collider_shapes[]`（w/h/ox/oy）、`move_area`、`move_distance`、`offset_of_item_shape` |
| 效果 | `combat_effects[]` / `energy_effects[]` / `movement_effects[]` / `create_effects[]` / `modifiers[]` / `add_modifiers[]` / `active_item_status_effects[]` / `uses_limits[]` / `costs[]` —— 每项是带 `"$type"` 的原始数据对象（枚举存整数） |
| 文本 | `descriptions[]`：`{key, text}`；833 件为空（描述是运行时按 effect 拼的） |
| 其它 | `extra`：`Item2` 上其余 public 字段（`effectTotals` / `modifierTotals` / `isStandard` 等） |

> 枚举整数去 `data/enums.json` 查。已知坑：`cost` 是 -999 哨兵（E20）、
> `gridObject` 在 prefab 上是 null（E21）。

## `enums.json` — 枚举真值表

`tools/bph_item_enums.py` 用 `tools/dnmap/EnumDump.exe` 从 `Assembly-CSharp.dll`
的元数据**常量**导出（不是按字段顺序猜的），当前 **165 个枚举**：

```json
{"schema": "bph.enums.v1", "assembly": "Assembly-CSharp",
 "types": {"Item2+ItemType": {"Any": 0, "Grid": 1, ..., "Relic": 22, "Carving": 32}}}
```

用法：`{v: k for k, v in types["Item2+Effect+Type"].items()}` 得到 `整数 -> 名字`。

## `items-unknown.md` — 分类器判不出来的道具

`python agent/itemdb.py --write` 生成：分类器（`agent/itemdb.py`）在 prefab 上
找不到任何 `Effect`、也没有可解析的 modifier 的道具（当前 54 件，集中在
Accessory / Component / Core）。它们的数字效果在 `ScriptedTrigger` / 组件脚本里，
**不适合硬猜**；这张表是给人过一遍的，过完再决定补别名还是归 `utility`。

## `runs/` — 每局回放

`run.py` / `loop.py` 写的 JSONL，一行一个事件（`decide` / `act` / `map` / `dead` / `retry` / `stuck` / `end`）。
`act` 事件带 `receipt`（结构化战斗回执，见 `docs/SPIKE-09-combat-receipt.md`）。
**不进 git**（`.gitignore` 里有 `games/*/data/runs/`），因为它增长快且可重跑。

> `*.bak` 一律不进仓库——那是调试时的临时备份。
