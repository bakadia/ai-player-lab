# RESULT · 2026-10-29 17:00 · 地图 SimpleClickItem 到底能不能捡（v8.17）

> 接 `HANDOFF-NEXT-20261029-1600.md` §3.1。上一班的结论是「`play_event` 的 `Click()`
> 收不进包，下一步要拆 Play/Click 两步」。这一班先把 IL 读干净、再把真机重跑一遍，
> 结论**反过来**了：`Click()` 一直是好的，那两次「没捡到」是两个 QuickBoost（+HP/+金币）
> 事件，本来就不产出物品。
>
> 桥接 **v8.17 → v8.18**：v8.18 是 **124,416 bytes，SHA256 `7E877A51…`**（已部署）。
> §1–§4 是 v8.17 的工作，§5 是 v8.18 的追加探针与最终结论。

## 0. 一句话

`DungeonEvent.Play()` 本来就幂等（`open` 属性当标记），是 v8.16 的 `play_event`
每次先 `RemoveEventProperty(open)` 才把「叠对象」的锅背到了游戏头上；而
`SimpleClickItem.Click()` 在真机上**是生效的**（`+5 gold`、`+5 hp` 各验证一次）。
地图上的 `SimpleClickItem` 目前只见到 `QuickBoost HP / Gold` 两种变体，
`effects` 里没有 `type=4(item)`，所以「地上掉东西」这条路径在真机上还没被走到。

## 1. IL 事实（Ildump 逐方法核对）

```
DungeonEvent.Play()
    if (GetEventProperty(open=6)) return;        // <-- 幂等标记
    if (itemsToSpawn.Count == 0) return;
    AddEventProperty(open, 0);                   // 先打标记，再生成
    Instantiate(itemsToSpawn[i], parent=Player.transform.parent)
    → 给 EventNPC / Store / Chest / Parcel / SimpleClickItem 绑 dungeonEvent

SimpleClickItem.Start()      // Play 之后**下一帧**
    dungeonEvent.GetItems(out items)             // 取事件下挂的带 Item2 的子物体
    → GetItems 会把这些孩子 SetActive(true) 且**不还原**
    → 摆到祭坛前：position + back*2 + up*0.25

SimpleClickItem.Click()
    if (dungeonEvent && dungeonEvent.IsFinished()) return;
    if (gameManager.travelling) return;
    if (isOpen) return;                          // 点过一次就进不去了
    ... animator.Play("Player_UseItem") / FinishEvent() / StartCoroutine(TakeAction())
    isOpen = true;

SimpleClickItem.TakeAction()  (协程，先 WaitForSeconds(0.4 / animator.speed))
    effect.type: 0=HP 1=EXP 2=Gold 3=地牢属性 4=item(什么都不做) 5=meta 6=生成物品
```

推论：`item` 类事件的东西是 **`Start()` 摆出来的**，`Click()` 只负责收尾（动画 +
FinishEvent + 开背包界面）。所以「东西变成 loose」发生在 **Play 之后的那一帧**，
同一帧里量 `items_loose` 永远是 0。

## 2. 真机证据（v8.17）

`tools/bph_verify_pickup.py`：`play_event{click:false}` → 等 1.5s → `diag_items` →
`play_event{play:false}` → 等 1.5s → `diag_items`/state。

### 2.1 第 1 层 d-5584（金）

```
第1步 play_event{click:false}
  played=true play_skip="" open_before=false open_after=true
  click_items_before=0 → click_items_now=1          # 新事件：Play 真的生成了一份，且只有一份
  click_items=[{name:"QuickBoost Gold Variant(Clone)", effect_count:1,
                effects:[{type:2, num:5, assigned:""}]}]
第2步 play_event{play:false}
  ok=True changed=True clicked=1 finished_after=true
变化: gold 0 → 5
RESULT effect=gold x5 item_expected=False loose_increased=False
```

### 2.2 第 1 层 d-5680（血）

```
effects=[{type:0, num:5}]           -> delta {"player_hp": 4}   （36 → 40，满血截断）
```

这两次的 `event_items` 都是 `[]`、`assigned` 都是空串 —— 事件底下**没有挂 Item2 子物体**，
`loose` 不动是正确结果。证据：`agent/tests/fixtures/pickup-20261029-sc-gold.json`、
`agent/tests/fixtures/pickup-20261029-sc-hp.json`。

（这两次是在 v8.16 上跑的，ack 因为老的「等 loot」门白等了 15 秒、回了 `ok=false`
但 `changed=true`；v8.17 修掉之后同场景回 `ok=True changed=True`。）

## 3. 这一班改了什么（v8.17）

| 位置 | 改动 |
|---|---|
| `bridge/src/Actions.cs` `play_event` | 新增 `play` / `click` 两个开关；**不再** `RemoveEventProperty(open)`（Play 自带幂等）；只点 `isOpen=false` 的 SCI；diag 增加 `played/play_skip/open_after/finished_after/click_items_before/click_error` |
| `bridge/src/Actions.cs` | diag 增加 `event_items[]`（事件下挂的 Item2）与 `click_items[]`（SCI 的 `isOpen`/激活状态/effects/assigned item 详情） |
| `bridge/src/Actions.cs` | 新只读动作 **`diag_items`**：所有 `Item2` 分为 `active` / `inactive` 两段（`FindObjectsOfType` 看不到未激活的，用 `Resources.FindObjectsOfTypeAll` + `scene.IsValid()` 过滤） |
| `bridge/src/Actions.cs` | 抽出 `FindEventForAction()` / `ClickItemsOf()`；新增 `PlayEventExpectsLoot()`（effect 里有没有 `type=4`） |
| `bridge/src/BridgeMod.cs` | `play_event` 的 ack 门只在真有 item effect 时才等 loot（否则 QuickBoost 白等 15 秒还回 `ok=false`） |
| `tools/bph_verify_pickup.py` | 新：两步验收 + effect 类型解释 + hp/gold/level/loose 变化表 + 证据 JSON |

v8.18 追加：

| 位置 | 改动 |
|---|---|
| `bridge/src/Actions.cs` | 新只读动作 **`diag_events`**：整层事件 + `itemsToSpawn` 的 prefab 元数据（名称、是否带 SCI、SCI 的 effects、带几个 Item2 子物体）；`diag_event` 的每条事件也带上同样的 `items_to_spawn_detail` 与 `room` |
| `tools/bph_events.py` | 新：把 `diag_events` 打成表，`item?` 列直接标出「这格会不会掉物品」 |
| `tools/bph_asset_scan.py` | 新：扫 `Backpack Hero_Data` 里的字符串（prefab 名只在资源里，不在 dll 里） |

## 4. 验证

- `bridge/build.ps1` → 122,880 bytes；部署后 SHA256 `B24BA2F9EDAD37216EC718846E0AA2EBCE90E9BD5D7185536F9029FE3AEF2B06`
- `run-tests.ps1` / `test_v0.py` / `test_mapnav.py` 全 ALL PASS
- 真机 §2.1 / §2.2（v8.16 + v8.17 各一轮）

## 5. 继续追：`type=4(item)` 的 SimpleClickItem 到底存不存在（v8.18）

为了不用「一格格走过去撞」，v8.18 加了两个只读探针：

- **`diag_events`**：整层所有 `DungeonEvent` 一览。关键是读
  `itemsToSpawn` 里 **prefab 本体**（不 Play、不实例化）上的 `SimpleClickItem.effects`
  ——有 `type=4` 才会往地上摆物品，`0/1/2` 就是 QuickBoost（血/经验/金币）。
- **`tools/bph_events.py`**：把上面那份 JSON 打成一张表（`item?` 列）。
- **`tools/bph_asset_scan.py`**：直接扫 `Backpack Hero_Data/*.assets` 里的字符串
  （prefab 名不在 `Assembly-CSharp.dll` 里，只在序列化资源里）。

真机 + 资源两边对上了：

```
floor 1 全层 10 个事件（diag_events）：
  d-5602  type 11  [QuickBoost HP Variant]     hp x5
  d-5586  type 11  [QuickBoost XP Variant]     exp x5
  其余：Chest / Door / EventNoNPC / 三队敌人

资源扫描（sharedassets1.assets）里 SimpleClickItem 的 prefab 族只有 6 个：
  QuickBoost HP Variant / QuickBoost XP Variant / QuickBoost Gold Variant
  + 各自的 QuickBoost Cursed HP/XP/Gold Variant
```

**结论**：这个 build 里地图上的 `SimpleClickItem` **只有 QuickBoost（+血/+经验/+金币）
这一族**，没有「掉物品」的变体。所以「地图上的东西收不进包」这个任务前提本身不成立——
地图上的物品一直是**地上的 loose item**（教程开局那 3 件、宝箱掉的那 4 件都是），
走的是早已验收过的 `place_item` 通道。

## 6. 下一步

1. ~~找 `type=4(item)` 的 SimpleClickItem~~ —— 见 §5，这个 build 里没有这一族。
   以后要「捡地图上的东西」，入口是 loose item，不是 `play_event`。
2. **宝箱里的东西必须在同一个房间收完**（PITFALLS E16）：这一班开了个宝箱掉 4 件
   （LEATHER CAP / LIQUID ARMOR / BOWBLADE / APPRENTICE STAFF），往前走两格就全没了
   （游戏自己的 `DestroyAllItemsOutsideGrid`）。`loop.py` 开完箱应该当场整理。
3. 战斗存活 / 扩容（`spacesSaved` 来源）—— 仍是主线。
4. `--make-room` 默认值 A/B、「撞上敌人房打完自动继续」两笔老账。

