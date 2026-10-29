# RESULT · 2026-10-29 · 道具普查 Step 1：`dump_item_catalog`（v8.21）

> 目标（`docs/PLAN-item-census.md` §1）：让桥接把**道具总池**整表 dump 成结构化 JSON，
> 给下一步「续航打分」提供数据。**Step 1 完成**（里程碑 M1），Step 2/3/4 未开始。

## 1. 结论

- `DebugItemManager.main.item2s` 就是道具总池，**不用解析资产包**（计划的前提成立）。
  menu 场景下就能读：**903 件**，`mod_count=0`，一次 dump 141–188 ms。
- 整份目录 **2,806,256 bytes**，枚举表 **165 个 / 41 KB**，全部落盘。
- 每件道具都有：身份 / 经济 / 几何（含**占格数**）/ 8 类效果 / 文本 / `extra`（其余 public 字段）。
- 体检脚本 6 项全过：数量、字段完整、枚举全可映射、5 件抽样、占格分布、续航预览。

## 2. 交付物

| 文件 | 说明 |
|---|---|
| `bridge/src/ItemCatalog.cs` | 新增只读动作 `dump_item_catalog`（递归 dumper + 文件落盘 + 短 ack） |
| `bridge/src/Actions.cs` | 动作分发加一个分支 |
| `bridge/build.ps1` / `.csproj` | 新增 `UnityEngine.Physics2DModule.dll` 引用（读 `BoxCollider2D`） |
| `tools/bph_item_catalog.py` | 发动作 + 6 项体检 + `--copy` 到 `data/` |
| `tools/bph_item_enums.py` | 编译 `EnumDump.exe` 并生成 `data/enums.json` |
| `tools/dnmap/EnumDump.cs` | 从元数据**常量**导出全部枚举（Cecil，C# 5） |
| `data/item-catalog.json` | 903 件道具目录（2.8 MB，入库） |
| `data/enums.json` | 165 个枚举的 名字→整数（41 KB，入库） |

桥接：**v8.21 / 136,704 bytes / SHA256 `54476C28…`**（构建成功，仅剩老的 CS0252 警告）。

## 3. 验收数据

```text
dump: 903 件  core=903 mod=0 source=item2s  2806256 bytes  172 ms
[1] 数量: file=903  ack=903
[2] 字段缺失: 无
    文本: 888/903 件有 display_name_text（其中 7 条是 Translation not found 兜底）
    费用: 453/903 件有 costs[]（其余是纯被动）
[3] 枚举覆盖: rarity 4 / play_type 3 / move_area 29 / move_distance 4 / effect.type 50 种，未映射 0
[4] 抽样: WOODEN SWORD(3格,1费) ROUGH BUCKLER(4格,1费) MEAL(2格,0费)
          DEBUFF POTION(1格,0费) CRISIS RING(1格,无费)
[5] cells 分布: {1:588, 2:178, 3:71, 4:65, 6:1}
[6] 续航预览: 38 件带 HP/MaxHP 效果（APPLE +5、BANANAS +10、BANDAGES +3 …）
体检: 全部通过
```

效果语义抽样（可直接对照游戏）：

| 道具 | 结构化效果 |
|---|---|
| WOODEN SWORD | `combat_effects[0].effect = {type:0 Damage, value:7, target:2 enemy}` |
| ROUGH BUCKLER | `{type:1 Block, value:7, target:1 player}` |
| APPLE | `{type:2 HP, value:5}` + modifier `type:19 Destroy`（消耗品） |
| CRISIS RING | 无 combat effect，只有 modifier（`type:20 Activate` 等） |

## 4. 三个必须知道的偏差（已写进 PITFALLS E20/E21）

1. **`cost` / `originalCost` / `charges` 在 prefab 上是 `-999` 哨兵**：903 件全部 -999。
   真实费用在 `costs[]`（`type` 0=energy/1=mana/2=gold），桥接已折算成
   `energy_cost` / `mana_cost` / `gold_cost`（无则 -1）。
2. **`Item2.gridObject` 在 prefab 上是 null**：按计划读 `gridPositions.Count` 会得到 903 个 -1。
   改成沿子物体找 `GridObject` → `BoxCollider2D`，按 size/offset 折算（1 格 = 1 世界单位），
   得到上面的 `cells` 分布；输出里同时留了 `collider_shapes[]` 原始 w/h/offset。
3. **`descriptions[]` 833 件为空**：物品描述是运行时按 effect 拼的；有文本的 70 件里
   7 条是 `Translation not found for key ...` 兜底串，不能当真文本。

顺带修了 `tools/bph.ps1` 的假阳性：没有写权限的 shell 里
`File.Open(ReadWrite, None)` 抛 `UnauthorizedAccessException`，被当成「dll 被占用」，
导致游戏已关还拒绝部署（PITFALLS E22）。

## 5. 复现

```powershell
# 1) 枚举表（不需要游戏）
python tools\bph_item_enums.py

# 2) 目录（游戏要在跑；menu 或局内都行）
python tools\bph_item_catalog.py --copy

# 部署（改了桥接才需要）
powershell -NoProfile -ExecutionPolicy Bypass -File .\bridge\build.ps1
tools\bph.cmd stop ; tools\bph.cmd start
```

## 6. Step 2：分类器 `agent/itemdb.py`（M2）

```text
道具总数 903；类别（互斥）:
  attack 212 (23.5%)  utility 157 (17.4%)  support 118 (13.1%)  relic 95 (10.5%)
  status  91 (10.1%)  defense  87 ( 9.6%)  energy   56 ( 6.2%)
  unknown 54 ( 6.0%)  sustain  33 ( 3.7%)
unknown 占比 6.0%（目标 < 20%）
```

- 标签：`rare=351, support=234, consumable=170, relic=113, reactive=65, aoe=35,
  per_turn=32, curse=28, self_harm=21`。
- 续航榜：POULTICE / SALAMI 20，SUNTAN LOTION 12，BANANAS/CANDY APPLE 等 10。
- 每回合护甲：PLATE ARMOR 8，STUMP HAZARD 7，BIRD CHANT / KNIGHT'S ARMOR 6。
- 交叉验证（样本 ≥5 的 3 件）：WOODEN SWORD -23%（净伤害口径，见下）、
  ROUGH BUCKLER +1%、MEAL 无伤害。偏差 >30% 的 0 条。

三个实现上的关键决定：

1. **`modifiers[]` 不是自己的效果**。它改的是别的道具（骑士甲自己 6 甲/回合，
   另给相邻 Armor/Shield +2）。第一版混算 → 337 件都成 support、骑士甲 8 甲/回合。
   拆开后新增 `support` 类别和 `support_damage` / `support_block`。
2. **`damage_active` vs `damage`**。`reactive`（onKill / whenAttacked / onTurnStart）
   的伤害不能当成「用一下就打」；Step 3 打分要用 `damage_active`。
3. **`data/items.json` 记的是净伤害**，所以木剑 -23% 是正常的（被格挡的部分在
   `absorbed_sum`）。护甲类对得上（+1%），说明分类器没把数值抄错。

测试：`python agent/tests/test_itemdb.py`（9 项 ALL PASS，含枚举锚点、名字容错）。

## 7. 下一步（Step 3 / 4，还没开始）

1. **Step 3 `--sustain` 接进 policy**（默认关）：
   - 治疗：`heal = min(stats.heal, max_hp - hp) × SUSTAIN_WEIGHT`，满血 0 分；
     用 `damage_active` / `block_active` 的那种「立刻结算」口径，别把 `onKill` 的回血当随时可用。
   - 每回合护甲（`block_per_turn`）按「预计还剩几回合」折现，别按回合数无脑乘。
   - 丢弃优先级：`sustain` / `relic` 不丢；`attack` 里低于均值的先丢；`support` 看是否还有人可辅。
   - 三选一 / 商店：`pick_dialogue` 有候选名后按类别挑（现在固定第 0 个）。
   - 单测：满血不浪费、濒死不乱用、0 费食物优先于 1 费攻击。
2. **Step 4 真机 A/B**：`bph_xp_curve.py --stop-on-dead` 各 2 局，
   对比「每场掉血 / 最深楼层 / 等级」，结果写进新的 RESULT。

