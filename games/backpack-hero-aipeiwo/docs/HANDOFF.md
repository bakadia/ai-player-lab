# HANDOFF · 交接给下一个智能体

> 2026-10-28 15:00 · 写给**没有任何上下文**的接手者。
> 读完这份再动手，不要去重新调查已经确认过的事。
>
> **2026-10-28 22:00 更新**：桥接 **v8.6**（94,720 bytes，SHA256 `E03D1E20…`，已部署）
> 真机验证了 `open_chest`（能开 Chest，`items_total +4`）。`place_item via=bag`
> 的 parent 已是 **`Grid Parent`**（正确），但 `EndDrag()` 会重置到 `startPosition`
> 再 AddToGrid，所以 cells 仍在包外；下一步改 `startPosition` 或 `MoveItemTowardsGrid`。
> 主背包升级后是 13 格（`GridSquare.allGrids`），`Items` 是包外容器。详见
> `RESULT-20261028-p0-p1.md` 与 `HANDOFF-NEXT-20261028-2200.md`；
> 下面 §0 的 v7 / 85,504 bytes 是 15:00 的快照。

## 0. 最新状态（2026-10-28 下午）

**上一班留的三件事都做完了；现在缺的是「用新回执跑 A/B」和「把整理阶段的偶发 no-op 钉死」。**
细节与证据见 `docs/SPIKE-09-combat-receipt.md`（回执）和 `docs/SPIKE-08-real-log-ab.md`（策略）。
桥接最终部署版：**85504 bytes，SHA256 `2EB092F0...`**（`v7`，版本号仍 `0.2.0`）。

| 能力 | 状态 |
|---|---|
| 开局 | ✅ `tools\start_run.py`，纯桥接 |
| 教程摆道具 | ✅ `place_item` |
| 地图移动 / 出口 | ✅ `move_to_room` / `enter_door` |
| 战斗 | ✅ 能打赢多场 |
| 战斗中捡掉落物 | ✅ `place_item` score=50，先捡再补刀 |
| 防御物品 | ✅ ROUGH BUCKLER 学到 7 格挡，用了 100+ 次 |
| 升级 | ✅ `Select()`×`numberToUnlock` 再 `EndLevelUp()`；`via` **必须** `select` |
| 整局跑 | ✅ 80 步级不卡（六类卡死点见 SPIKE-08 §8） |
| **死亡后开新局** | ✅ **`retry_run` 已实测**：死后 `ok=true changed=true`，新局从 `place_item` 继续 |
| **结构化战斗回执** | ✅ 每个 ack 带 `receipt`（谁打谁 / 挡多少 / 状态 / 生成物品 / 整理阶段） |
| 被动饰品 | ✅ 不再进 `legal`（`can_be_used_active=0` 硬门） |
| 整理阶段 | ⚠️ `finish_reorganize` 偶发第一次 no-op（20s 超时），重试一般能过；见 PITFALLS E9 |

### 0.1 本次（10-28 下午）改了什么

- **桥接 v2→v7 已部署**。`close_map` 三级兜底实测 `viewing_top 1→0` 成功（v2/v4/v7 各一次）。
- **`retry_run` 实测通过**：死后重开见 `data/runs/loop-20261028-133045.jsonl` 的
  `dead` → `retry ok=true changed=true` → 新局 `place_item`；局中重开也可用。
  v4 起 `retry_run`/`start_run` 的回执带 `reset:true`、`effects:[]`，避免把「旧敌人消失」
  误报成「被玩家打死 40 血」。
- **结构化回执（SPIKE-09）**：`Snapshot` 增加玩家/敌人状态、最大生命、幸运、敌人意图、
  物品自身状态/充能；`receipt` 输出 `damage`（amount/blocked/killed）、`status`、
  `max_hp`、`luck`、`item_status`、`charge`、`items:spawned`、`phase:inventory`。
- **账本/策略**：`ledger.effects` + `estimate_effects()` / `effect_score()`；
  `policy --value-status`（默认关，避免未 A/B 就改默认行为）。
- **合法性**：`StateReader` 把 `Item2.CanBeUsedActive` 从诊断提升为硬门（返回 0 不进 legal），
  被动饰品不再被用成 0/0；`state.ui` 新增 `in_special_reorg` / `skippable_reorganization`。
- **`loop.py`**：`close_map` 顺序修正——先试 `close_map`，连续两次失败才拉黑事件
  （以前一看到「地图开着 + start_event 合法」就直接拉黑，`close_map` 根本没机会跑）。

### 0.2 还没做 / 下一步

> 具体执行方案、A/B 对照选择和踩坑兜底已单独写成
> **`docs/HANDOFF-AB-NEXT.md`**；下面只留摘要，别在下一会话重新设计。

1. **用新回执跑 A/B**：对照应当是 **c-full vs d-value-status**（不是 baseline），
   每臂 3 局、独立账本。`d-value-status` = c-full + `--value-status`。
   `tools\ab_retry.py` 已写好（dry-run 通过），用 `retry_run` 代替重启，
   直接跑：`python tools\ab_retry.py --arms c-full d-value-status --runs 3`。
   细节见 `HANDOFF-AB-NEXT.md` §2。
2. **`close_map` 的「宝箱链路」还没完整验证**：只验证了 `viewing_top` 归零；
   当前可达事件是 `SimpleClickItem`，`StartEvent()` 对它本来就是 no-op，不能拿它当证据。
   要找真正的 `Chest`/`Enemy` 事件复测「地图开着 → close_map → StartEvent 成功」。
3. **整理阶段偶发 no-op**：`inventory_phase=3` 但 `inSpecialReorg=false` 时，
   `FinishReorganizeButton()` 第一行就 `return`（PITFALLS E9）。现在靠 loop 重试；
   可考虑连续失败 2 次后 `retry_run`，或加一个诊断 action 读 `itemsForSpecialReorganization`。
4. **玩家侧状态/最大生命/幸运还缺真机样本**：敌人 `rage` 状态、`items:spawned`、
   `phase` 已实测；`HEAT POTION` 那局实际是「生成诅咒 + 进整理」，没有玩家状态。

### 0.3 环境约束（这次重新确认过）

- **部署 DLL 仍要写 `E:\steam\...\Mods`**。DSH 的 workspace-write 会拒绝；
  用 `danger-full-access` 审批后 `Copy-Item` **可行**（这次就是这么部署的）。
- **启停游戏用 CUA**：`launch_app` 启动、窗口 `alt+f4` 关闭。
  `kill_app` 对「不是本 CUA runtime 启动的进程」会拒绝；`taskkill` 从 agent shell 是 Access denied。
- **`bph.ps1` 的 `dll lock` 在受限 shell 仍是假阳性**：它是 ReadWrite 打开失败。
  用「读打开」判断——读得开 = 没锁；写失败只是权限。
- **`.ps1` 纯 ASCII、csc C#5、部署前必须退游戏**（E2/E3/E6 没变）。
- 游戏目录：`E:\steam\steamapps\common\Backpack Hero`。

---


## 0.4 一句话现状（10-26 版本，保留供对照）

Backpack Hero 的 AI 玩家流水线已打通「读状态 → 决策 → 执行 → 校验 → 记录」，
**能无人值守打赢单场战斗**；一离开战斗就停，而且**停在哪已经实测确认了**：
不是地图，是**「胜利! + 升级!」**（`ui.can_level_up=true`）。

> 16:20 实测（Purse 教学关，seq=67）：`mode=map` 只是「不在战斗」的意思。
> 屏幕上给的是「升级!」。同一次实测里 `event_buttons_total=0`，
> **「地图节点就是 `EventButton`」已被彻底证伪**——节点是 `DungeonEvent`。
> 证据与数据见 `docs/SPIKE-06-map-model.md` §9。

## 1. 下一步只有一个（别的先别做）

> ⚠️ **这一节是 10-26 的历史版本，里面的待办早就做完了。**
> 以 §0 为准；§1 只保留下来说明「当时是怎么一步步推进的」。

**在真实游戏里验证五个地图动作能不能用、`changed` 能不能判对。**

动作层第一批已经写完并部署（`bridge/src/Actions.cs`），但**只在代码层面验证过**，
还没在真实地图上跑过一次。

### 1.1 已实现的动作（都是桥接进程内调用，不是模拟点击）

| 动作 | 入口 | 阶段门（不满足不算完成） |
|---|---|---|
| `open_map` | `GameManager.ShowMap(force)` | `viewingTop == 1` |
| `move_to_room` | `DungeonRoom.MoveToRoom()`（**不是** `PathFromPlayerToRoom`） | 玩家停下且最近房间 == 目标 |
| `start_event` | `DungeonPlayer.StartEvent()`（对 Exit 无效） | settle |
| `enter_door` | `Door.OnMouseUpAsButton()` | 世界签名变 + `!locked` |
| `finish_event` | `DungeonEvent.FinishEvent()` | 世界签名变 + `!locked` |
| `level_up` | `LevelUpManager.LevelUpButton()` | settle |
| `finish_level_up` | `LevelUpManager.EndLevelUp()` | `levelingUp == false` |
| `close_popup` | 所有 `BasicPopUp.Close()` | 弹窗数 == 0 |
| `place_item` | `StartDrag` → 移到空格子 → 反射 `EndDrag` | `items_in_grid` 变大 |

`place_item` 的细节（往背包里放东西）见 `SPIKE-06` §20，那是目前唯一能保住掉落物的路径。

CLI 也能直接发：

    python games\backpack-hero-aipeiwo\adapter\client.py open-map
    python games\backpack-hero-aipeiwo\adapter\client.py move r12345
    python games\backpack-hero-aipeiwo\adapter\client.py start-event
    python games\backpack-hero-aipeiwo\adapter\client.py level-up
    python games\backpack-hero-aipeiwo\adapter\client.py close-popup

整局循环（地图 + 战斗 + 升级，战斗策略复用 `policy.py`）：

    python games\backpack-hero-aipeiwo\agent\loop.py --max-steps 200

### 1.2 `Snapshot` 已经扩过（否则动作必被判失败）

新增 9 个字段：`RoomId` / `ViewingTop` / `InventoryPhase` / `Gold` / `Level` /
`ItemsTotal` / `ItemsPlaced` / `EventCount` / `PopupCount`。

**验证要点**：

- 发一条 `noop` 必须返回 `changed=false, delta={}`（E4 的回归用例）；
- 发一次 `open_map` 必须 `changed=true`，而且 `delta` 里出现 `viewing_top:[0,1]`。

### 1.3 实测已知（省得重查）

- 教程全程 `floor=0`，`DeriveMode` 现在返回 **`intro`**，不再当成 `menu`；
  地图细节按 `active && !inCombat` 展开，不再挂在 `mode=="map"` 上。
- **死亡没有专门状态**：挂掉时游戏仍是 `mode=battle`、`battle_phase=3`、`legal=[]`。
  现在 `DeriveMode` 先判 `Status.health<=0` 返回 **`dead`**。
- **`end_turn` 会被 reorganize 打断**：敌人回合里掉诅咒 → `inventory_phase=3`。
  `PhaseIsPlayerOrIdle()` 已把「整理阶段」也算成可收尾，不再假超时 15 秒。
- `DungeonRoom.GetRoom()` 在刚切层时对「坐标完全相等」的房间也返回 null →
  探针改成自己按曼哈顿距离找最近房间（`room_at` + `room_dist`）。
- 打死的敌人是**整个事件消失**，不是 `finished=true`。
- 地图跟着玩家平移：坐标要 `pos - origin` 才稳定。
- `ui.viewing_top`（`inventory`/`map`）才是「背包还是地图」的判据，`mode` 不是。
- `tutorial_sequence` 是持久化的，重玩教程时一开始就是 `trulyDone`。

### 1.4 部署状态

| 文件 | 状态 |
|---|---|
| `bridge/src/World.cs` | 新增：最近房间 / 房间 uid / 弹窗数 / 事件数 |
| `bridge/src/MapProbe.cs` | 探针 + 地图阶段 `legal` |
| `bridge/src/Actions.cs` | 五个动作 + Snapshot 扩字段 |
| `Mods\BphBridge.dll` | 已部署，62464 字节，20:07 |
| 旧 DLL 备份 | `bridge/bin/Release/BphBridge.deployed-backup.dll` |


## 2. 已经确认过的事实 —— 不要再调查

### 状态入口（都是 public static 单例，反射直读，不用 patch）

| 入口 | 用途 |
|---|---|
| `GameManager.main` | floor / gold / inventoryPhase / 各种按钮 |
| `Player.main` | `stats`(血量) / `AP`(能量) / `characterName` |
| `GameFlowManager.main` | `battlePhase` / `turnNumber` / `GetCombatStat` |
| `Enemy.allEnemies` / `Status.allStatuses` / `Item2.allItems` | 全量枚举 |

- **血量只认 `Status.health`**。`GameManager.currentHealthAmount` 被证伪（不理变化）。
- `Item2.cost` / `charges` 是 `-999` 哨兵，**真实费用在 `Item2.costs`**
  （`energy`/`mana`/`gold`，官方 ModDocs 已确认）。
- 敌人意图 `Enemy.myNextAttack` **只在 `battlePhase==1`(playerTurn) 有值**，enemyTurn 是 null。
- `GameFlowManager.GetCombatStat(...)` 调用**成功但恒返回 -1**，语义未明（见 §6 开放问题）。
- 物品 uid 用 `GetInstanceID()`（可能是负数，如 `i-7936`）；敌人 uid 同理 `e-12345`。

### 动作层（已经能用）

协议：`runtime/state.json` ← bridge 原子写；`runtime/cmd/{id}.json` 写命令；
`runtime/ack/{id}.json` 收回执（含 `ok/changed/delta/ms/error`）；`runtime/halt` 急停。

已实现动作：`use_item` / `end_turn` / `finish_reorganize` / `skip_reorganize` / `noop`，
以及地图阶段的 `open_map` / `move_to_room` / `start_event` / `enter_door` /
`finish_event` / `level_up` / `close_popup`（见 §1.1）。

游戏启停 + 部署用 `tools\bph.cmd`（`status` / `stop` / `start` / `restart`），
**不要再手动拷 DLL**。它判断「游戏在不在跑」用的是 **DLL 文件锁**——进程枚举在
受限会话里会看不到游戏（PITFALLS E2），文件锁才可靠。

**开一局用 `tools\start_run.py`**（纯桥接，不模拟鼠标）：

    python games\backpack-hero-aipeiwo\tools\start_run.py

它按实测出来的唯一正确序列走：主菜单→`MenuManager.StartGameButton()`（快速开始），
角色选择→`MenuManager.GoAdventuring()`（去冒险吧）。
**别在主菜单直接调 `GoAdventuring`**——会绕过角色选择，开出一个带
`TESTING SWORD` / 500 金币的调试局。

窗口默认放到**副屏**（远程桌面常见需求），要改：`bph.cmd start -Display primary`。


三个设计要点，别推翻：
- **`changed` 必须用 `Snapshot.Differs()` 逐字段比对**，不许用 `diff != "{}"`（历史 bug，见 PITFALLS E4）。
- **结束回合有阶段门**：要等 `battlePhase` 回到 playerTurn/outOfBattle 才算完成，
  否则会在敌人回合动画间隙误判（实测 `ms` 2.8~3.2s 才是正常值）。
- **逆境/整理阶段要单独识别**：`inventory_phase==3` 时必须发 `finish_reorganize`，
  否则整局卡死（中污染/诅咒会进这个阶段）。

### 模式与挑战（重要，容易搞错）

- `Quick and Easy` 是**挑战(Challenge)**，不是模式。流程：快速开局 → 角色 → 挑战列表（**要滚动**）。
- 判别方法：`runProperties` 里有没有 `oneFloorBeforeBoss(26)`。
- 已跑过的两局都不是 Quick and Easy：`rt1`=Standard、`rt4`=Easy Mode。
- 挑战效果与 `runProperties` **一一对应**（7 条对 7 条已验证）。

### 地图 / 战后（**静态证据**，结构可信、取值未实测）

| 入口 | 用途 |
|---|---|
| `DungeonPlayer.main` | `room`(当前房间) / `dungeonEvent` / `isMoving` / `PathFromPlayerToRoom()` / `StartEvent()` |
| `DungeonRoom.allRooms` | 房间网格；`left/right/up/down` + `*Blocked` 是门位；`isLocked` / `canBeSeenOnMap` |
| `DungeonEvent.dungeonEvents` | 地图上的「节点」，16 种 `dungeonEventType`（Enemy/Chest/Store/Lock/Exit…） |
| `Door`（只能 FindObjectsOfType） | 门；`isOpen` / `doorNumber` / `presetZone`（选下一区域也是门） |
| `GameManager.zoneNumber` / `dungeonLevel` | 区域与楼层；`dungeonLevel` 是 ScriptableObject，`currentFloor` 是否运行时改写未知 |
| `GameManager.victoryItems` / `rewardItems` | **战后三选一**的战利品列表 |
| `LevelUpManager.main` | `levelingUp` / `CanLevelUp()`，战后升级界面 |

- **`EventButton` 不是地图节点**，是事件对话框里的选项按钮（`requirement`=goldCost/itemSacrifice…）。
  上一版交接搞错了，`SPIKE-06` 有完整证据。
- `DeriveMode()` 的 `map` 是个**垃圾桶**：只要不是 dead/menu/intro/battle/reorganize
  就都叫 map，所以战后奖励界面也会被标成 `map`。**别拿 mode 判断「现在是不是地图」**。

## 3. 命令速查

```powershell
# 类型地图（查类/字段/方法）
powershell -NoProfile -ExecutionPolicy Bypass -File tools\dnmap\build.ps1
python tools\dnmap\show.py DungeonEvent
python tools\dnmap\show.py --search Dungeon

# 编译 bridge（不能用来部署）
powershell -NoProfile -ExecutionPolicy Bypass -File games\backpack-hero-aipeiwo\bridge\build.ps1

# bridge 单元测试
powershell -NoProfile -ExecutionPolicy Bypass -File games\backpack-hero-aipeiwo\bridge\tests\run-tests.ps1

# agent 离线测试（不需要游戏）
$env:PYTHONIOENCODING='utf-8'
python games\backpack-hero-aipeiwo\agent\tests\test_v0.py

# 读状态 / 发命令
python games\backpack-hero-aipeiwo\adapter\client.py state
python games\backpack-hero-aipeiwo\adapter\client.py use <uid>
python games\backpack-hero-aipeiwo\adapter\client.py end-turn

# 自动打一场
python games\backpack-hero-aipeiwo\agent\run.py --max-steps 60
```

## 4. 环境约束（都是踩过坑才摸清的）

- **没有 .NET SDK**；VS2022 有 MSBuild 但**缺 Roslyn**，`msbuild` 不可用。
  唯一能编译的是系统自带 `csc`（C# **5** 上限）。所以 `.cs` 里不能用字符串插值、`?.`、
  表达式体成员、`nameof`、元组、`out var`。
- 编译必须 `/noconfig /nostdlib+ /codepage:65001`，并引用**游戏自己的 BCL**
  （`Managed\mscorlib.dll` + `netstandard.dll`），不能用系统 .NET Framework 的。
- **`.ps1` 必须纯 ASCII**。Windows PowerShell 5.1 按 GBK 解码无 BOM 文件，
  中文注释会吞掉下一行代码（PITFALLS E6，我踩过两次）。
- **部署 DLL 要先退游戏**（进程映射住文件会拒绝覆盖，PITFALLS E2），
  且写入 `E:\steam\...\Mods\` 超出工作区，需要提权。
- 本机执行策略 `Restricted`，跑 `.ps1` 必须带 `-ExecutionPolicy Bypass -File`。

## 5. 文档地图（细节都在这里）

| 文件 | 内容 |
|---|---|
| `docs/MVP.md` | 决策文档（注意：其中 Quick and Easy 的措辞已被 `REFERENCE-modes-and-map.md` 纠正） |
| `docs/SPIKE-01-reflection-map.md` | 静态反射地图、状态入口 |
| `docs/SPIKE-02-live-state.md` | 真实战斗上的状态实测 |
| `docs/SPIKE-03-live-actions.md` | 动作层打通、四条回执原文 |
| `docs/SPIKE-04-first-battle.md` | 第一场自主战斗 + 三个测量 bug |
| `docs/SPIKE-05-calibration-reorg.md` | 账本校准 3.54→7.00、整理阶段 |
| `docs/SPIKE-06-map-model.md` | **地图模型复核**：EventButton 不是节点；房间/事件/门/玩家四件套；探针字段表；Snapshot 缺口 |
| `docs/SPIKE-07-combat-ab.md` | 用户写的战斗决策离线 A/B（合成局面；注意它假设「每件物品都已知道数值」） |
| `docs/SPIKE-08-real-log-ab.md` | 真机日志回放、四个开关、A/B 数据、六类卡死点、桥接缺口 |
| `docs/SPIKE-09-combat-receipt.md` | **回执现状**：结构化战斗回执、账本 effects、`--value-status`、被动饰品合法性 |
| `docs/HANDOFF-AB-NEXT.md` | **下一步执行单**：A/B 用 `retry_run` 代替重启、c-full vs d-value-status、宝箱链路、整理兜底 |
| `docs/REFERENCE-modes-and-map.md` | wiki 取证：挑战/地图节点分类；ModDocs 覆盖范围 |
| `docs/PITFALLS.md` | E1~E10 踩坑记录，**动手前必读**（E8=UI 控件；E9=整理阶段 no-op；E10=被动饰品） |

## 6. 开放问题（都不要在没证据时下结论）

1. `GetCombatStat` 恒返回 -1 的真实语义（这条本可当第二套独立观测做交叉校验）。
2. 背包**叠放模型**：污染物品可以压在别的物品上（`cells` 重叠），现在的数据表达不出「谁叠在谁上」。
3. `Enemy.health` 是否随掉血同步（只见过两者相等的样本）。
4. 地图/奖励阶段的完整动作集（探针已就位，等实测结构）。
5. Quick and Easy 的 `rt` 编号（还没开过）。
6. 打赢之后**第一件事**到底是奖励还是地图（`ui.victory_items` 的实测值决定顺序）。
7. `DungeonEvent` 要不要当作「可达性图」来做决策（房间连通性 + 门是否开），
   还是简单「就近走」就够——**先看数据再定**，别提前设计。

## 7. 需要玩家配合的事

- **开一局，打赢第一场后先别急着继续**：让我读一次 `state.json`（看 `ui` 段），
  这一步决定后面先做奖励还是先做地图。
- 然后再走到地图（或随便点开一个事件），再读一次（看 `map` 段）。
- 挑战列表要**向下滚动**才能看到 Quick and Easy。
- 换 DLL 时**先退游戏**。
- 游戏目录：`E:\steam\steamapps\common\Backpack Hero`

## 8. 别做的事

- 别为了"看起来有进展"把动作层改成猜测式调用——`changed` 校验是这套东西的核心。
- 别把 `-1`/`-999`/`"gone"` 当真实数值（都是哨兵或兜底值）。
- 别在 `.ps1` 里写中文。
- 别把任何密钥写进仓库（本地配置在 `~/.dsh/profiles/desktop/cordis.patch.yml`）。
