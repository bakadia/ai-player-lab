# SPIKE-06 · 地图模型复核：EventButton 不是地图节点

> 2026-10-26 15:55 · 状态：**静态证据推翻 HANDOFF §1 的前提；只读探针已部署，等实测**
> 上一份：`SPIKE-05-calibration-reorg.md`（账本校准 + 整理阶段）
> 证据来源：`runtime/inspect/types.json`（IL 反射导出，非猜测）；**尚未在真实地图上实测**

## 0. 结论先行

| 问题 | 答案 |
|---|---|
| 地图节点是 `EventButton` 吗 | **不是**。`EventButton` 是事件对话框里的选项按钮（付钱/献祭/选择） |
| 那地图节点是什么 | `DungeonEvent`（16 种类型），挂在 `DungeonRoom` 里，房间之间靠 `Door` 相连 |
| 地图是「节点列表」吗 | **不是**，是**房间网格**：`DungeonRoom.allRooms` + `DungeonPlayer.main` 走动 |
| 「打赢就停在 map」卡在哪 | 未定。**可能先停在战后奖励/升级**（`victoryItems` / `LevelUpManager`），
不是地图 |
| 动作层能直接加地图动作吗 | **不能**。`Snapshot` 没有任何地图字段，`changed` 会恒为 false |

---

## 1. 上一版的错误前提

`HANDOFF.md`（15:20 那版）§1 写的是：

> 在 `StateReader.cs` 里增加一个 `map` 段落：`mode == "map"` 时，枚举场上所有 `EventButton`，
> 把每个节点的**类型、可交互状态**写进 `state.json`……
> 地图上的节点就是它（不是独立的「地图节点」类型）。

这句话把「**访问事件时出现的按钮**」当成了「**地图上的地点**」。
静态类型完全不支持这个说法。

## 2. 证据：EventButton 是什么

字段（23 个）里关键的是这几个：

```
EventButton+Requirements requirement   // none / itemSacrifice / goldCost / itemSelect /
                                       // itemSelectNonForged / carvingSelectNonForged /
                                       // pumpkinKing / specificItemSacrifice
Int32     requiredGold
Int32     startingGold
List<Item2+ItemType> requiredItemType
List<Item2+Rarity>   requiredRarities
Boolean   sacrificeAccepted
Item2     sacrificedItem
PossibleOutcome[] possibleOutcomes     // 每个 outcome 带 probability / flavorText / actions
String    buttonText
Boolean   played
DungeonEvent dungeonEvent              // 它属于哪个事件
```

方法：`PayCosts()` / `ButtonPress()` / `ShowButtons()` / `PerfromEffect()` / `FullEffect()`。

这是一套「**对话选项**」的形状：要付多少钱、要不要献祭物品、选了之后走哪个 outcome。
它不描述「地点」，也不在地图上被摆放。

> 顺带说明上一版为什么会看错：`EventButton` 确实是「地图阶段能看到的 UI」，
> 但它出现的前提是**已经站在某个事件里**，属于「事件内部」，不是「事件的位置」。

## 3. 证据：地图真正由什么组成

### 3.1 `DungeonEvent` —— 地点/节点

```
static List<DungeonEvent> dungeonEvents        // 全量枚举，public static
DungeonEvent+DungeonEventType dungeonEventType
Boolean passable / cannotWalkAwayFrom
Int32   doorNumber
List<EventProperty> eventProperties            // cost / open / mimic / increaseCost / finished …
Int32   turnsToExpire
String  mapTextOverrideKey
```

`dungeonEventType` 的 16 个值，正好对上 wiki 里的节点分类：

```
0 Enemy   1 Elite   2 Chest   3 Store   4 Chance   5 Lock   6 Exit   7 Healer
8 EventNoNPC  9 ExitAlternate  10 Shambler  11 SimpleClickItem  12 CaveIn
13 Altar  14 Burrow  15 Parcel
```

方法：`IsFinished()` / `FinishEvent()` / `FindAllDungeonEvents()` / `GetEventPropertyValue(type)`。
`EventProperty+Type` = `finished / cost / boughtItems / mimic / randomSeed /
SimpleClickItem / open / increaseCost / selectedType`。

### 3.2 `DungeonRoom` —— 房间（地图的格子）

```
static List<DungeonRoom> allRooms       // public static
Boolean left / right / up / down        // 这个房间哪几面有门
Boolean leftBlocked / rightBlocked / upBlocked / downBlocked
Boolean isLocked / canBeSeenOnMap
DungeonEvent dungeonEvent               // nonpublic，反射读
```

方法：`GetAllVisibleRooms()` / `GetAllRooms()` / `GetRoom(Vector2)` / `MoveToRoom()`。

### 3.3 `Door` —— 门（含「选下一个区域」）

```
Boolean isOpen
Int32   doorNumber
Boolean isLocked / canBeUnlocked        // nonpublic
Boolean isPresetZone
DungeonLevel+Zone presetZone
```

方法：`SetLevel(zone)` / `ConsiderWalk()` / `WalkToDoor()`。
`presetZone` + `zoneKeySprite` 说明「打完 Boss 选下一个区域」也是**门**，不是节点列表。

### 3.4 `DungeonPlayer` —— 走动的玩家

```
static DungeonPlayer main
DungeonRoom  room
DungeonEvent dungeonEvent
Boolean isMoving / locked / isMovingFromLockedDoor
Vector2 lastSpace
```

方法：`PathFromPlayerToRoom(DungeonRoom)` / `MoveToRoom(Vector2, bool)` /
`StartEvent()` / `FindReachableEvents()` / `WalkIntoEvent()`。

> 这四个加起来才是一张地图：**房间是格子，事件是格子里放的东西，门是格子之间的边，
> 玩家是走在上面的点**。把它当「节点列表 + 序号」会一路错到底。

## 4. 第二个问题：「停在 map」可能根本不是地图

`DeriveMode()` 的规则是「不是 menu / reorganize / battle，就是 map」——
**它是一个垃圾桶**。打赢之后 `battlePhase` 变 0，`gameMode` 立刻变成 `map`，
但那一刻屏幕上很可能是：

- 战利品三选一：`GameManager.victoryItems` / `rewardItems` + `OpenSelectItemDialogue(...)`
- 升级：`LevelUpManager.main.levelingUp` / `CanLevelUp()`
- 事件收尾：`GameManager.currentEventType`

所以「打赢一场就停」到底是卡在奖励还是卡在地图，**现在没有证据**。
`SPIKE-04` 那场是「打死最后一只怪 → runner 看到 mode=map → 主动退出」，
不是「游戏在等我们点地图」。

> 顺序很重要：如果先做地图动作，而游戏其实在等三选一，会得到
> 「动作发出去了、状态没变」——正好是 A4 最想抓的静默失败，而且会被误判成桥接 bug。

## 5. 本轮做的事：只读探针（已部署）

新增 `bridge/src/MapProbe.cs`，在 `state.json` 里加两块，**只读、不点、不发命令**：

```jsonc
"map": {
  "active": true,            // DungeonPlayer.main 存在
  "detail": true,            // mode=="map" 时才展开下面这些
  "zone_number": 1, "zone": 0, "zone_name": "dungeon",
  "area": "The Crypt", "dungeon_floor": 2, "dungeon_floor_name": "first",

  "player": {"room": "r123", "event": "d456", "moving": false,
             "locked": false, "from_locked_door": false, "space": [3, 1]},

  "rooms": [{"uid": "r123", "is_player": true, "locked": false, "visible": true,
             "doors":   {"left": true, "right": false, "up": true, "down": false},
             "blocked": {"left": false, "right": false, "up": false, "down": false},
             "event": "d456", "pos": [12.0, 4.0]}],

  "events": [{"uid": "d456", "type": 2, "type_name": "Chest", "finished": false,
              "passable": true, "no_walk_away": false, "door_number": -1,
              "turns_to_expire": -1, "text_key": "", "pos": [12.0, 4.0],
              "props": [{"type": 1, "name": "cost", "value": 30}]}],

  "doors": [{"uid": "o789", "open": true, "locked": false, "can_unlock": false,
             "number": 2, "preset_zone": false, "preset_zone_id": -1,
             "preset_zone_name": "", "pos": [10.0, 4.0]}],

  "event_buttons": [...],    // 用于证伪「节点 = EventButton」
  "rooms_total": 7, "events_total": 5, "doors_total": 3,
  "event_buttons_total": 0
},

"ui": {
  "leveling_up": false, "can_level_up": false,
  "victory_items": 0, "reward_items": 0, "selected_item": "",
  "current_event_type": -1, "current_event_type_name": "",
  "select_item_dialogues": 0, "select_item_finished": false
}
```

设计取舍：

- **只读**。没有新动作、没有新命令类型、`Actions.cs` 一行没动。
- **两套模型一起输出**。`events[]` 和 `event_buttons[]` 都摊平，
  用 `event_buttons_total` 的实测值来证实/证伪上一版假设，不靠嘴。
- **`detail` 只在 `mode=="map"` 展开**。战斗期间不重复枚举，避免 state.json 膨胀
  和「房间坐标每帧变 → 反复重写」。
- **全部字段单独兜底**。新代码每个读取都有 try/catch，读不到写哨兵，不让整份 state 废掉。
- **nonpublic 字段走反射**（`DungeonRoom.dungeonEvent`、`Door.isLocked`、
  `EventButton.buttonText`），只 `GetValue`，不 `SetValue`。

## 6. 实测要看什么（按顺序）

1. **打赢那一刻**（不是走到地图之后）：
   - `ui.victory_items` / `ui.reward_items` / `ui.leveling_up` 是否非空？
   - 非空 → 「停住」的根因是战后奖励，**先做奖励再谈地图**。
2. 走到地图：
   - `map.rooms_total` 与实际房间数是否一致？
   - `map.events[].type_name` 是否与看到的节点一致（战斗/宝箱/商店/出口）？
   - `map.player.room` 是否随移动更新？
3. **`map.event_buttons_total` 是不是 0**。
   - 恒 0 → 上一版假设彻底证伪，删掉那段设计。
   - 非 0 → 说明按钮确实存在，但要看它的 `event` 指向哪个 `DungeonEvent`
     （也就是「在事件里」，不是「在地图上」）。

## 7. 动手加动作之前必须先做的事

**扩 `Snapshot`。** 现在 `Snapshot` 只有：

```
PlayerHp / PlayerMaxHp / PlayerBlock / PlayerEnergy /
BattlePhase / TurnNumber / Floor / EnemyHp / EnemyBlock
```

地图动作改变的是 `DungeonPlayer.room`、`DungeonEvent.finished`、`GameManager.zoneNumber`，
**没有一个在 Snapshot 里**。后果链条：

```
动作执行 → Snapshot.Differs() 判「没变」 → changed=false → ok=false
        → run.py 连续 3 次无变化 → 判定卡死退出
```

这不是桥接 bug，是**观测范围没覆盖**（和 E4 是同一类错误的另一面：
E4 是「判据写错」，这里是「压根没观测」）。
所以顺序是：**先扩 Snapshot 与 DiffJson，再加动作**。

建议加的字段（都要能证明「真的动了」）：

| 字段 | 来源 | 为什么 |
|---|---|---|
| `RoomId` | `DungeonPlayer.main.room.GetInstanceID()` | 移动的**唯一**判据 |
| `EventFinishedCount` | `DungeonEvent.dungeonEvents` 里 `IsFinished()` 的个数 | 事件收尾 |
| `ZoneNumber` | `GameManager.zoneNumber` | 换区域 |
| `Gold` | `GameManager.goldAmount` | 开箱/买/给钱 |
| `Level` | `Player.level` | 升级 |
| `RewardCount` | `victoryItems.Count` | 三选一被消费 |

> `Gold` / `Level` 注意：这两个是「游戏自己也会改」的量，
> 加入 `Differs()` 后 `noop` 仍应为 false（noop 不会改钱），但要为此补一条回归用例。

## 8. 待确认（本轮没解决的）

1. `GameManager.currentEventType` 的默认值是 `Enemy(0)`，
   与「正在和 Enemy 事件交互」撞车，**不能单独用它判断当前事件类型**
   （探针里照常输出，但要配合 `player.event` 一起看）。
2. `GameManager.dungeonLevel` 是 `ScriptableObject` 引用，
   `currentFloor` 是否在运行时被改写**未知**。实测时和 `gm.floor` 对一下。
3. `DungeonEvent` 没有指向 `DungeonRoom` 的字段，方向是 `Room.dungeonEvent`（nonpublic）。
   探针里用反射读；将来做动作时若需要「事件→房间」，也用这个方向。
4. `Door` 没有静态列表，只能 `FindObjectsOfType`。每次轮询都扫场景，
   目前只在 `mode=="map"` 做，代价可接受；若以后要高频，得换成挂在房间下的缓存。

---

## 9. 实测结果（2026-10-26 16:20 · Purse 教学关，打赢第一只小怪后）

状态：`seq=67`、`mode=map`、`battlePhase=0`、`inventoryPhase=8`、
`floor=1`、`zone=theBramble`、`area="The Bramble"`、`hp=37/40`、`gold=0`。

| 问题 | 实测值 | 判定 |
|---|---|---|
| `event_buttons_total` | **0** | 「地图节点 = EventButton」**彻底证伪**（E8） |
| `events_total` | **11** | `DungeonEvent` 就是节点 |
| 事件类型分布 | `Enemy×5`、`Chest×1`、`SimpleClickItem×1`、`Chance×2`、`Exit×1`、`Shambler×1` | 与 wiki 的节点分类对得上 |
| 打死的那只 | `d-18140`，`finished=true` | `IsFinished()` 可直接当「已清」判据 |
| `doors_total` | **0** | 这个场景**没有 `Door` 组件**；门只是房间的 `left/right/up/down` 布尔 |
| `rooms_total` | **23**，其中 15 个 `canBeSeenOnMap` | 房间网格成立；坐标 2 单位一格、带 `.5` 偏移 |
| `map.player.room` | **null** | 没走动过时 `DungeonPlayer.room` 不赋值（见 §10） |
| `ui.can_level_up` | **true** | **打赢后的真正阻塞是升级，不是地图** |
| 屏幕 | 「胜利!」+「升级!」按钮，经验 `8/5` | 与 `can_level_up=true` 一致 |
| `inventory_phase` | **8** = `notInteractable` | 新值，别当整理阶段 |

结论：`ui` 这一段的设计是对的——它在无人值守前就把「卡在哪」指出来了。

### 9.1 真正的事件顺序

```
打死敌人 → battle_phase 1→0 → mode 变 map → 屏幕给「胜利! + 升级!」
         → 玩家点升级（LevelUpManager.LevelUpButton）→ 背包扩容 → 才能继续
```

`victory_items=0` / `reward_items=0`：这一场**没有**物品三选一，只有升级。
所以上一版担心的「三选一先于地图」在**这一场**不成立，但升级确实先于地图。

## 10. 新发现的两个坑

### 10.1 `DungeonPlayer.room` 是 null，但玩家明明在房间里

- 23 个房间都 `is_player=false`，`player.room=null`，`player.space=[1,1]`。
- 推测：`room` 只在**移动到房间时**被赋值；开局/教学是直接摆放，没走 `MoveToRoom`。
- 影响：`move_to_room` 的「起点」不能只靠 `player.room`，得用
  `transform.position` 去 `DungeonRoom.GetRoom(Vector2)` 反查，或者用 `space`。

### 10.2 `Player.level` 读出来是 `-1`

`Player` 确实有 `public Int32 level` 和 `public Int32 experience`，
但 `state.json` 里 `run.level=-1`（=读取兜底值），而 `Player.experience=8` 是对的。
在没查清之前，**不要用 `run.level`**；升级判据用 `ui.can_level_up`。

## 11. 教学关（玩家口述 + 类型确认）- `TutorialManager.main` 存在，带：
  - `tutorialSequence`（`start` / `openingMessage` / `moveToChest` / `organizeChest` /
    `openMap` / `moveToCombat` / `firstCombat` / `done` / `trulyDone`，
    另有 cr8 / tote / `purseAfterOpenMap` 分支）
  - `completedTutorials`（List\<String\>）
  - `ShowClickMe(Vector3)` / `HideClickMe()` + `clickMe` 字段
- 也就是说**教学关的进度是可直接读的**，应该立刻加进探针（`ui.tutorial_sequence`）。
- 玩家提供：教学关给 3×3 背包 + 几样道具，要求玩家摆进去；
  **引导者可以点击并弹出弹窗**。这类弹窗在对象图里没有「动作」，只能：
  1. 找到它的关闭入口（`TutorialManager` 上没有明显的方法），或
  2. 承认需要一次真实鼠标点击（与 MVP §2.3「不做鼠标模拟」冲突，要单独决策）。
- 教学关可能有**专属房间**（`tutorialRooms` / `cr8TutorialRooms` / `toteTutorialRooms` 三个 prefab 字段），
  这可能就是 `player.room=null` 的原因：教学房间不是普通 `DungeonRoom`。
  → 所以「地图动作」应当**等教学关结束**（`tutorialSequence == done/trulyDone`）再谈。
- 弹窗入口后来查到了：`TutorialPopUpManager.main.DisplayTutorial(name)` + `BasicPopUp.Close()`，
  不需要鼠标。

## 12. 第二次实测（升级 + 挑完奖励 + 走了一个房间）

两轮读数：`seq=82`（升级完、挑完道具）→ `seq=88`（在地图上走了一步）。

### 12.1 打死的敌人会从列表里消失

`events_total` 从 **11 → 10**，`d-18140`（上一场打死的那只）**整个事件没了**。

→ 「这只怪清掉了」的判据是**事件不存在**，不是 `finished=true`。
（`IsFinished()` 只在事件还活着、但已结束的瞬间有意义。）

### 12.2 世界坐标会整体平移

同一个事件在两轮里的坐标统一平移 `(-25, 0)`：

| 事件 | seq=82 | seq=88 | 位移 |
|---|---|---|---|
| `d-18236` Enemy | `[23, 2.5]` | `[-2, 2.5]` | `-25, 0` |
| `d-18220` Chest | `[28, 1.5]` | `[3, 1.5]` | `-25, 0` |
| `d-18108` Exit | `[22, 1.5]` | `[-3, 1.5]` | `-25, 0` |

10 个事件全部同一位移——**地图跟着玩家平移**。

→ 绝对坐标**不能跨时刻比较**；要么记 `MapManager.map.position` 当原点，
要么只用房间之间的相对偏移。房间之间的偏移是稳定的。

### 12.3 走路**不会**给 `DungeonPlayer.room` 赋值

走了房间之后 `player.room` 仍是 `null`，23 个房间 `is_player` 全 false。
`lastSpace` 从 `[1,1]` 变成 `[0,1]`（看着像房间内局部坐标）。

→ 「我在哪个房间」必须靠 `DungeonRoom.GetRoom(playerTransform.position)` 反查。
探针 v2 已经把 `player.pos` 和 `player.room_at` 一起输出。

### 12.4 物品 10 → 4 不是 bug（玩家确认）

`seq=82` 的 10 件 = **奖励候选 + 背包原有**（玩家当时正在挑），
玩家只留了 4 件，所以 `seq=88` 是 4 件。

→ 教训：`Item2.allItems` 在**奖励/整理阶段会把候选物品也算进去**，
所以「`items_total` 变大」不等于「背包里多了东西」。
判断「哪些真的在背包里」要配合 `ui.viewing_top` / `ui.inventory_phase`。

## 13. 探针 v2（已部署 2026-10-26 17:17）

在 §5 的字段上补了六项：

| 字段 | 为什么 |
|---|---|
| `ui.viewing_top` / `viewing_top_name` | `mode=map` 时屏幕可能开的是背包；`inventory=0` / `map=1` |
| `ui.inventory_phase_name` | 8=`notInteractable` 之类，光看数字要翻表 |
| `ui.tutorial_sequence` / `_name` / `tutorial_completed` / `tutorial_disable_leveling` | 教程是脚本化的，这是权威进度 |
| `map.origin` | `MapManager.map` 的位置；把坐标归一化用 |
| `map.map_mode` | `move=0` / `createRoom=1` |
| `map.player.pos` / `player.room_at` | 走路不设 `room`，只能按坐标反查 |
| `backpack.unplaced[]` | 未摆放的物品，光有 `items_total - items_placed` 不够 |

## 14. 教程阶段被 `mode` 判成 `menu`（已修）

第三次实测（`seq=10`，玩家刚过完教程、在序章地图上走了一步）：

```json
"mode": "menu",            // ← 错的
"run": {"floor": 0},
"map": {"active": true, "detail": false,
        "zone_name": "dungeon", "area": "The Crypt",
        "dungeon_floor": 1, "dungeon_floor_name": "intro"},
"ui": {"viewing_top": 1, "viewing_top_name": "map",
       "tutorial_sequence": 8, "tutorial_sequence_name": "trulyDone",
       "tutorial_completed": 7}
```

**根因**：`DeriveMode()` 的规则是 `floor <= 0 → "menu"`，而**教程全程 `floor = 0`**。
`MapProbe` 的详情又挂在 `mode == "map"` 上，于是：

- 整段教程没有 `rooms` / `events` / `player.room_at`；
- 教程里走路**根本不改变 state 正文**（`detail=false`，地图字段不输出），
  所以 `seq` 只涨到 10，玩家走了房间也看不出来。

**修复**（已编译，等部署）：

1. `DeriveMode` 增加 `hasDungeon` 参数（`DungeonPlayer.main != null`）：
   `floor <= 0` 时返回 **`"intro"`**，不再是 `menu`。
2. `MapProbe.detail` 改成 `active && !inCombat`——
   **不再看 mode**，只要不在战斗里、地牢对象在，就展开。

**顺带确认的好消息**：

- `ui.tutorial_sequence` 一路走到 **`8 = trulyDone`**，`tutorial_completed = 7` —— 教程进度字段可用。
- `ui.viewing_top = 1 (map)` 正确反映了「地图开着」，这个字段救了「背包开还是地图开」的歧义。
- 序章区域是 `dungeon` / **The Crypt** / `intro` 层，`floor` 仍为 0；
  教程背包正好 3×3：

  | 物品 | 格子 | 备注 |
  |---|---|---|
  | WOODEN SWORD | `-1,-1  -1,0  -1,1` | 3 格 |
  | ROUGH BUCKLER | `0,0  0,1  1,0  1,1` | 4 格 |
  | SPICE | `0,-1` | **relic**（`is_relic=true`） |

## 15. `DungeonRoom.GetRoom()` 在刚切层时不可靠

实测（`seq=42`，刚走进 Exit 换到 The Crypt 第一层）：

```
player.pos = [-3, 1.5]
rooms 里 r-15954 的坐标就是 [-3, 1.5]   ← 完全相等
但 DungeonRoom.GetRoom(pos) -> null
且 DungeonPlayer.room -> null          ← 切层后没走动过
```

教程里同一个调用是成功的，说明它依赖「当前层已注册」之类的内部状态。

**改法**：不用游戏的方法，自己在 `DungeonRoom.allRooms` 里找
**曼哈顿距离最近且 < 0.6** 的房间（格子 1 单位，阈值安全），
输出 `player.room_at` + `player.room_dist` 便于校验。

## 16. 动作层第一批（已实现并部署）

| 动作 | 调用入口 | 阶段门（必须满足才算完成） |
|---|---|---|
| `open_map` | `GameManager.ShowMap(force)` | `viewingTop == 1` |
| `move_to_room` | `DungeonPlayer.PathFromPlayerToRoom(room)` | 玩家停下且最近房间 == 目标 |
| `start_event` | `DungeonPlayer.StartEvent()`（当前房间事件） | settle |
| `level_up` | `LevelUpManager.LevelUpButton()` | settle |
| `close_popup` | 所有 `BasicPopUp.Close()` | 弹窗数 == 0 |

`Snapshot` 新增 9 个字段（否则这些动作全会被判成「没变化」）：

```
RoomId / ViewingTop / InventoryPhase / Gold / Level /
ItemsTotal / ItemsPlaced / EventCount / PopupCount
```

`legal` 在地图/序章阶段会输出：

```
close_popup（有弹窗时）
start_event（当前房间有 DungeonEvent 时，带 event_type_name）
level_up（CanLevelUp() 为真时）
open_map（viewingTop != 1 时）
move_to_room × N（看得见、没锁、非当前房间）
enter_door（当前房间是 Exit/ExitAlternate 且场上有 Door 时）
```

## 17. 实测：`move_to_room` / `enter_door` 的正确答案

### 17.1 `PathFromPlayerToRoom` 是死路，`DungeonRoom.MoveToRoom()` 才对

- `DungeonPlayer.PathFromPlayerToRoom(room)`：发出去 **20 秒 state 一格没动**，纯算路不执行。
- `DungeonRoom.MoveToRoom()`（房间自己的方法，点房间走的就是它）：**804ms 成功**。

```
delta = {"room": ["r224572", "r224552"]}
```

`move_to_room` 现在默认走这条，另有 `via=press/closest/consider/path` 四个备选。

### 17.2 `start_event` 对 Exit 无效，出口是 `Door.OnMouseUpAsButton()`

```
start_event -> ok=false changed=false delta={}   （StartEvent 什么都不做）
enter_door  -> ok=true  changed=true            （门被点开了）
```

`Door` 的可见性：`ConsiderWalk()` / `StartWalk()` 是 **private**，
public 的只有 `OnMouseUpAsButton()` / `OnPressStart()` / `WalkToDoor()`（协程）。
所以 `enter_door` 默认调 `OnMouseUpAsButton()`，参数 `via=press|walk` 备用。

### 17.3 出门是「演出」，必须用世界签名当阶段门

点门之后立刻回执了（`ms=501`，`delta={"inventory_phase":[1,8]}`），
但**真正切层还要 ~5 秒**（`floor 0 -> 1`、`rooms 6 -> 19`）。
那段时间里 state 只变了两次，settle(0.4s) 一到就提前回执了。

修法：`enter_door` / `finish_event` 增加 `WaitWorldChange` 阶段门，
执行前记下 `World.Signature()`（`floor|roomCount|eventCount|roomId`），
**签名变了才算完成**。同时把 `enter_door` 的最小超时抬到 15s。

### 17.4 负的 InstanceID 被当成「读失败」

`WriteRef()` 原来写的是 `if (id < 0) 写 null`，但这个游戏的 `GetInstanceID()`
**经常是负数**（物品 `i-7936`、房间 `r-9056`）。于是：

- `map.player.room_at` 在 The Crypt 一直是 `null`（房间 id 是负的）；
- `legal` 里的 `enter_door.door` 也一直是 `null`。

改成 `int.MinValue` 当哨兵后就能正常输出了。
**教训**：负数在这里是合法 id，不是错误码。

### 17.5 物品模型实测确认

教程开局：

```
items_total 3 / items_placed 3 / items_in_grid 0 / items_loose 3
backpack.items  = []                                   ← 背包是空的
backpack.unplaced = [MEAL, ROUGH BUCKLER, WOODEN SWORD] ← 全在地上
```

走一个房间后：

```
delta = {"items_loose": -3, "items_total": -3, "items_placed": -3}
```

三件地上的战利品被游戏销毁了（`DestroyAllItemsOutsideGrid`）。
**所以 `items_placed`（有 gridPositions）绝对不能当「在背包里」用。**

## 18. 「战利品消失」的真相：掉落物在战斗结束的瞬间被清掉

玩家反馈「战斗结束后的奖励不知道为啥没了」。用新加的 `agent/watch.py`
（每次 seq 变化落一条记录，见 §19）抓到了完整过程：

```
13:48:25 seq=200 battle  loose=1  grid=3  vic=0  rew=0   ← 掉落物已经在地上
13:48:27 seq=207 battle  loose=1  grid=3  vic=0  rew=0   ← 还能拿
13:48:28 seq=211 map     loose=1  grid=3  vic=0  rew=0   ← 战斗刚结束，还在
13:48:28 seq=212 map     loose=0  grid=3  vic=0  rew=0   ← 同一秒内被销毁
```

对应 `run.py` 的日志：

```
act end_turn  -> delta {"items_total": +1, "items_loose": +1, ...}
                 （敌人回合里掉了东西）
act use_item  -> delta {"battle_phase": [1,0], "items_total": -1, "items_loose": -1, ...}
                 （补最后一刀，战斗结束，掉落物一起没了）
```

结论：

1. **普通战斗没有「战后三选一」**——`victory_items` / `reward_items` /
   `select_item_dialogues` 全程都是 0，两次战斗都一样。
2. 所谓的「奖励」就是**战斗中掉在地上的物品**（`items_loose`）。
3. 战斗一结束，地上物品立刻被 `DestroyAllItemsOutsideGrid` 清掉。
   **必须在战斗**结束前**把它放进背包**，否则必丢。

对 agent 的含义：现在的 `policy.py` 完全无视 `items_loose`，
所以每次战斗都会白丢掉落物。要拿就得在战斗内完成「放进背包」——
而摆放目前没有公开 API（`Item2` 只有 `FindGridSpaces()` 这类查询），
这是下一个真正的难点（要么找到程序入口，要么走一次真实拖拽）。

## 19. 新工具 `agent/watch.py`

`state.json` 只保留最后一帧，奖励/升级/弹窗这类界面出现几秒就没了，
事后读一次无法回答「当时弹没弹」。

    python games/backpack-hero-aipeiwo/agent/watch.py --seconds 3600

每次 `seq` 变化写一条到 `data/state-log/*.jsonl`，只记会瞬变的字段：
`viewing_top / inventory_phase / leveling_up / can_level_up / victory_items /
reward_items / select_item_dialogues / selected_item / popups / items_in_grid /
items_loose / legal 类型列表`。

> 这次就是用它在同一秒内抓到 `loose 1 -> 0`，否则完全看不出发生过什么。

## 20. 摆放战利品：`StartDrag → 移到空格子 → EndDrag`（已打通）

### 20.1 先失败的一条路

第一次用 `Item2.FindGridSpaces()` 当目标位置，五条路全挂：

```
stopdrag/drag/gm/snap -> no free grid space (FindGridSpaces=0)
look                  -> timeout
```

**`FindGridSpaces()` 对「还在地上的物品」返回 0 个**——它只在物品已经和
某个背包网格建立关联时才算得出来。不能当起点。

### 20.2 正确的起点：`GridSquare.allGrids`

```csharp
static List<GridSquare> GridSquare.allGrids   // public static，背包的每个格子
Boolean containsItem                          // public
Boolean isPouch                               // public
```

摆放 = **拖拽的三步**：

1. 在 `GridSquare.allGrids` 里挑一个 `!isPouch && !containsItem` 的格子，取它的世界坐标；
2. `ItemMovement.StartDrag()`（public）；
3. `itemMovement.transform.position = 格子坐标`；
4. 反射调 private 的 `ItemMovement.EndDrag()`。

实测（教程三件，各 ~500ms，全部成功）：

```
drag ok=True changed=True delta={"items_in_grid": 1, "items_loose": -1}
```

阶段门：**`items_in_grid` 必须变大**，否则回执 `(item did not enter the grid)`。

放好之后：

```
WOODEN SWORD  ['-5,-1','-5,0','-5,1']
ROUGH BUCKLER ['-4,0','-4,1','-3,0','-3,1']
MEAL          ['3,-1','4,-1']            ← 被放到了另一片格子区
legal 里 place_item 消失
```

> 注意 MEAL 落点和剑/盾不在一起（x=3 而不是 -5）。游戏允许放到背包的不同区域，
> 这不影响判定——`items_in_grid` 和 `GetAllItemsInGrid()` 都认。

### 20.3 已接进战斗循环

- `state.legal` 在**玩家回合**里会给地上的物品发 `place_item`（带 uid/name）；
- `policy.py` 给 `place_item` **50 分**——高于普通攻击、低于击杀（100），
  也就是「能捡就先捡，但不耽误补刀」；
- 摆放失败会把 `place:<uid>` 记进 `explored`，同一件东西不会反复试；
- `run.py` 失败时打一行 `放弃摆放 ...`。

这样掉落物在战斗结束前就会被收进背包，不再被 `DestroyAllItemsOutsideGrid` 清掉。

## 21. 死亡态与两个「假超时」

### 21.1 死亡没有专门状态，只能靠 `hp<=0`

实测挂掉那一刻（`seq=101`）：

```json
"mode": "battle", "battle_phase": 3, "hp": 0,
"inventory_phase": 0, "legal": []
```

游戏并没有把状态切成「dead」——`inventory_phase` 是 `locked(0)` 而**不是** `dead(4)`，
`battle_phase` 还停在 enemyTurn。所以：

- `DeriveMode()` 现在**先判 `Status.health <= 0` → `"dead"`**，再判 reorganize/battle；
- `run.py` 也加了 `mode == "dead"` 的出口（原来只靠 `hp<=0`，两者都留着）。

### 21.2 `end_turn` 的假超时：reorganize 会打断回合

实测一次 `end_turn` 等了 **15 秒**然后报：

```
error=timeout after 15070ms (battlePhase not player/idle)   ← 但 changed=True
```

真相：敌人回合里掉出**诅咒/污染**物品，`inventory_phase` 直接 `0 -> 3`（进整理阶段），
而 `battlePhase` 还停在 3。`PhaseIsPlayerOrIdle()` 只认 `battlePhase 0/1`，于是干等。

修法：**`inventoryPhase == 3` 也算「可以收尾」**。整理阶段本来就是一个独立的等待点，
`finish_reorganize` 会接手。

### 21.3 `enter_door` 回执早于新层加载

只等「世界签名变了」时，`floor` 一变就回执（`ms≈2400`），
但那一刻 `rooms` 还是上一层的 6 个、`DungeonPlayer.locked=true`，真正加载完要 ~6 秒。

修法：世界签名之外再加一条 **`WorldReady()`**（`!isMoving && !locked`）。

> 这两个都是「阶段门不够严」的同一类问题：状态确实变了，但没变完。

## 22. 开局：主菜单那几个按钮就是 public 方法

想「脚本开一局」，第一反应是模拟鼠标——**这个环境里不行**：游戏窗口常开在副屏，
前台焦点被任务栏/聊天窗口抢走，`SendInput` 点不到，`PostMessage` 又不被 Unity 认。

翻 `MenuManager` 才发现按钮全是现成方法：

| 界面 | 方法 |
|---|---|
| 主菜单「快速开始」 | `MenuManager.StartGameButton()` |
| 角色选择「去冒险吧」 | `MenuManager.GoAdventuring()` |
| 主菜单「继续快速游戏」 | `MenuManager.LoadGameAndContinueSave()` |
| 故事模式 | `MenuManager.StartGameButton()`（同一个入口） |

`MenuManager.main` 是 public static，所以 `tools/start_run.py` 一条命令就能进教程：

```
主菜单 --StartGameButton()--> 角色选择 --GoAdventuring()--> 教程（6 房间 / 3 件道具 / 金币 0）
```

### 22.1 踩过的坑

1. **在主菜单直接调 `GoAdventuring()`** 会绕过角色选择，开出一个
   **带 `TESTING SWORD`×3、`NUCLEAR DART`、500 金币、55 个房间的调试局**。
   能跑，但不是正常局，别拿它当 baseline。
2. **`NewCharacterSelector.StartGame()` / `ChooseCharacter()` / 按钮 onClick 都不顶用**：
   分别表现为「没反应」「没反应」「关掉角色选择但不开局」。
   只有 `MenuManager.GoAdventuring()` 在角色选择界面里是有效的。
3. **程序化点「快速开始」之后，`menu.character_selector` 要几十毫秒才变 true**。
   脚本如果在 ack 之后立刻读 state，会以为没弹出来，于是又点一次「快速开始」，
   结果**角色选择界面无限重开**。修法：ack 之后等一下再读，且一旦看到角色选择就
   只按「去冒险吧」。

### 22.2 相关工具

- `tools/start_run.py`：开局（`--via quickstart|story|continue`）。
- `tools/bph.ps1`：启停 + 部署 + **把窗口摆到指定屏**
  （`-Display auto|primary|secondary`，默认 `auto` = 有副屏就放副屏）。





