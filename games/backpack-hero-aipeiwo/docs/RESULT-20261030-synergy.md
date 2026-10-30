# RESULT · 2026-10-30 · 装备描述 + 相邻协同（护甲流真正能被「摆」出来）

> 用户提醒（2026-10-30）：「有些装备的效果是可以增强护甲，或者护甲摆放位置不同会有
> 不一样的效果，我估计你得把鼠标放到装备上 OCR 看看描述，或者你后台能抓到描述。」
>
> **不用 OCR**：描述是 `ModTextGen` 的**静态方法**在运行时拼出来的，桥接直接调用就能拿到。
> 拿到之后，「相邻护甲 +N 格挡」这类规则第一次变成了结构化数据，
> 并且真的影响到了落点选择（真机日志：`协同：挨着 3 件目标`）。

## 1. 描述是怎么拿到的

`data/item-catalog.json` 里的 `descriptions[]` 有 833 件是空的（PITFALLS E20 时代就知道了），
真正的人话在：

```csharp
ModTextGen.GenerateCombatEffectDescription (Item2 item, Item2.CombattEffect e, bool reload) : string
ModTextGen.GenerateMovementEffectDescription(Item2 item, Item2.MovementEffect e, bool reload) : string
ModTextGen.GenerateModifierDescription       (Item2 item, Item2.Modifier mod, bool reload) : string
ModTextGen.GenerateAddModifierDescription    (Item2 item, Item2.AddModifier m, bool reload) : string
```

四个都是 **static**（我先按实例调，编译器直接报 CS0176 才发现），`reload=false` 表示
「只要字符串、别重建 UI」。桥接 v8.25 在 `dump_item_catalog` 里对每件道具把这几句拼起来，
写进 `description_runtime[]`：

```
903 件里有 752 件拿到了描述
Knight's Armor : Adds /x Block to self
                 Armor items and Shields adjacent to this or diagonal to this get /x Block
Tower Shield   : Adds /x Block to self
                 Armor items to the left get /x Block to self          ← 带方向
Bronze Breastplate: Armor items adjacent ... get /x Block
                    Weapons adjacent to this get /x Damage
```

`/x` 是游戏用来顶替数值的占位符（数字是运行时按状态算的），
所以**只解析「谁 / 哪个区域 / 加什么」**，数值仍用 modifiers 里已经算好的
`support_block` / `support_damage`。

## 2. 解析成结构化字段（`itemdb.ItemStats`）

| 字段 | 含义 | 例 |
|---|---|---|
| `helps_armor` | 给「护甲」加成 | Knight's Armor |
| `helps_weapons` | 给「武器」加成 | Bronze Breastplate |
| `synergy_area` | `adjacent` / `adjacent+diagonal` / `left` / `right` / `row` | Tower Shield = `left` |
| `synergy_block` / `synergy_damage` | 加成量与种类（来自 modifiers） | Knight's Armor = 2 格挡 |

全库：**20 件给护甲加成、115 件给武器加成**。

## 3. 用在两处

### 3.1 估值：旁边有甲，这件才值钱

`backpack.item_value()` 新增 `armor_pieces` 参数（`drop_ranking` 会数背包里几件护甲传进来）：

```python
if armor_pieces > 0 and st.helps_armor and st.synergy_block > 0:
    value += per_turn_value(st.synergy_block * min(armor_pieces, 4))
```

Knight's Armor（+2 格挡给相邻护甲）在背包里有 2 件甲时多值 `per_turn_value(4) ≈ 9.3` 分
——比它自己的 6 甲/回合（14.9）再加一半。**同一件道具，有没有甲在旁边是两个价。**

### 3.2 摆位：贴着要加成的目标放

`place.synergy_bias(bag, name, db)` 返回一个「这个落点挨着几件目标」的函数，
交给 `backpack.plan_place(..., bias=...)` → `_sort_placements` 排序时优先贴着目标；
方向性协同（`left` / `right` / `above` / `below`）只算那个方向，
`adjacent+diagonal` 才算八个方向。

真机证据（`data/runs/synergy/run2.console.txt`）：

```
放 KNIGHT'S SHIELD @-1,0 rot0 -> ok=True changed=True ms=1405
  （place KNIGHT'S SHIELD directly）  协同：挨着 3 件目标
```

## 4. 验证

- 单测：
  - `test_synergy_bias_prefers_armor_neighbors`：贴着护甲 = 1、隔两格 = 0、包里没护甲 → `None`；
  - `test_armor_priced_above_sword`、`test_reward_pick` 覆盖护甲定价（上一轮）。
  四套测试全 ALL PASS。
- 真机 2 局（`data/runs/synergy/`，都停在 floor 1）：偏置生效 1 次（上面的日志），
  另一局没有出现「相邻加成」类道具，属于样本问题。

桥接：**v8.25 / 140,288 bytes**（v8.24 是 139,264）。

## 5. 局限（别当成解决了）

1. **只影响「新拿进来的东西」**：已经摆在包里的道具不会为了协同去挪位置。
   真机上要挪就得走 `move_item` / `swap_items` 重排，`plan_repack` 目前不带 bias。
2. **`/x` 的数值来自 modifiers 的静态值**，如果某件道具的加成是「按周围件数动态变」
   （描述里带 `for each`），现在只能按静态值算一次。
3. **没有「护甲边际递减」**：`min(block, incoming)` 决定第 4、5 件甲的收益会掉，
   现在 `armor_pieces` 上钳到 4 件，是拍的。
4. 描述里 151 件是空的（`ERROR: DESCRIPTION FOR MODIFIER COULD NOT BE GENERATED`
   这类占位也在其中），这部分的协同关系仍然只能靠数值猜。

## 6. 下一轮

1. **重排让协同落地**：给 `plan_repack` 加同一套 bias，让「已经有甲在旁边」的道具
   在背包碎片化/换装时被挪到正确位置（现在只在放置时选点）。
2. **按描述把「for each」类效果估出来**（周围件数 × 每件加成），这类才是护甲流的核心。
3. 用描述文本做一层「流派分类」（护甲流 / 武器流 / 消耗流），
   让 `reward_score` 能判断「这件是不是我这套流派要的」。
