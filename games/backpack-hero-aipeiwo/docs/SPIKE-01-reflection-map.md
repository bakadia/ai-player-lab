# SPIKE-01 · 反射地图与环境实测

> 2026-10-25 · 状态：**状态读取的入口全部找到，动作入口待验证**
> 上一份：`docs/MVP.md`（决策）· 本文只记「实测到了什么」

## 0. 结论先行

| 问题 | 答案 |
|---|---|
| 状态能不能读 | **入口齐了**：`GameManager.main` / `Player.main` / `GameFlowManager.main` 三个 static 单例 + 三张全量表，反射直读，不用 patch |
| 动作能不能执行 | **未验证**。候选方法已定位（见 §6），但一条都还没在游戏里真调过 |
| 能不能无人值守打一场 | **未验证**。前提是先把上面两条跑通 |
| 本机能不能编译 mod | **能**。但不用 `dotnet`/`msbuild`（都不完整），用 `csc`；`bridge/build.ps1` 已跑通 |
| MelonLoader 装好了吗 | 装好了；2026-10-25 23:27 首次启动成功，`1 Mod loaded.` + `[BphBridge] BphBridge loaded.`（见 §10） | D1 上午已过 |

**一句话**：D1 下午的「定位类、dump 一场战斗」这一步，静态部分做完了；剩下的都是要开着游戏才能验的。

---

## 1. 环境实测（对 MVP §1.1 / §4 的补充与修正）

| 项 | 实测 | 影响 |
|---|---|---|
| 游戏目录 | `E:\steam\steamapps\common\Backpack Hero` 存在 | 与 MVP 记录一致 |
| MelonLoader | `0.7.3` 已释放（`version.dll` 11,057,664 B，`MelonLoader/`、`UserData/`、`Mods/` 齐） | 装好了 |
| 已部署 mod | `Mods\BphBridge.dll` 3,584 B | 已就位，但没验证过加载 |
| **加载证据** | `MelonLoader\Latest.log` **0 字节**，`Logs\26-10-25_22-54-18.log` 0 字节 | **游戏从未在装完后启动**，hello mod 仍是「待验证」 |
| 主程序集 | `Assembly-CSharp.dll` 1,621,504 B，2025-01-05 | 与 MVP 的 build 15631088 对应 |
| .NET SDK | **没有**（`C:\Program Files\dotnet\sdk` 不存在，只有 runtime） | `dotnet build` 不可用 |
| MSBuild | VS2022 Community 有 `MSBuild.exe`，但 **Roslyn 组件缺失**（`MSBuild\Current\Bin\Roslyn\Microsoft.CSharp.Core.targets` 不存在） | `msbuild BphBridge.csproj` 直接报 MSB4019，**不可用** |
| targeting pack | `.NETFramework\v4.7.2` 在 | 只是缺编译器 |
| 可用 C# 编译器 | `%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe`（14.8.10001.0，**C# 5 上限**） | 写 bridge 代码按 C# 5 约束：不用字符串插值、`?.`、表达式体成员 |
| Python | 3.11.9 | 无 `dnfile` / `dnSpy` / `ILSpy` |
| 游戏内 Mono 运行时 | `MonoBleedingEdge\bin` 不存在（无 `mono.exe`） | 别指望用 mono 跑工具，用 .NET Framework |

> 也就是说：**能编译，但只能用老 csc**。`BphBridge.csproj` 留着做参照，真正的构建入口是 `bridge/build.ps1`。
> 想换回现代工具链，需要装 .NET SDK 或 VS 的「.NET 桌面开发」工作负载——不是必须，先不折腾。

---

## 2. 反射入口（全部是 static，不需要 patch）

| 入口 | 类型 | 说明 |
|---|---|---|
| `GameManager.main` | `GameManager : MonoBehaviour` | 158 字段 / 174 方法，全局局内状态 |
| `Player.main` | `Player : MonoBehaviour` | 32 字段，玩家 AP / 等级 / 角色 |
| `GameFlowManager.main` | `GameFlowManager : MonoBehaviour` | **战斗状态机**：`battlePhase`、`turnNumber`、动作队列 |
| `Enemy.allEnemies` | `List<Enemy>` | 全场敌人（含未激活的模板？**待验证**） |
| `Status.allStatuses` | `List<Status>` | **所有战斗单位**的血量/护甲，玩家+敌人+宠物统一在这 |
| `Item2.allItems` | `List<Item2>` | 全场物品（背包 + 奖励 + 商店） |
| `GridObject.gridObjects` | `List<GridObject>`（nonpublic static） | 网格占位，能拿到精确格子 |

关键点：**`Status` 是血量的唯一真源**。`Player` 和 `Enemy` 都挂一个 `Status`（`stats` 字段），
`Status` 里有 `health` / `maxHealth` / `armor`，以及 `playerScript` / `enemyScript` 回指。
所以血量不用分别从 Player 和 Enemy 读，从 `allStatuses` 一把梭。

---

## 3. 状态映射（`bph.state.v1` 草案 → 反射路径）

| schema 字段 | 反射路径 | 类型 | 置信度 |
|---|---|---|---|
| `mode` | `GameFlowManager.main.battlePhase` | enum | 高（值见 §4） |
| `mode`（非战斗细分） | `GameManager.main.inventoryPhase` | enum | 中，语义待确认 |
| `run.floor` | `GameManager.main.floor` | int | 高 |
| `run.hero` | `Player.main.characterName` | enum，`Purse = 1` | 高 |
| `seq` / 回合 | `GameFlowManager.main.turnNumber` | int | 高 |
| `player.hp` | `Player.main.stats.health` | int | 高 |
| `player.max_hp` | `Player.main.stats.maxHealth` | int | 高 |
| `player.block` | `Player.main.stats.armor` | int | 高 |
| `player.energy` | `Player.main.AP` | int | 高 |
| `player.max_energy` | `Player.main.APperTurn` | int | 高 |
| `enemies[]` | `Enemy.allEnemies` 过滤 `!dead` | — | 高 |
| `enemy.name` | `enemy.displayName` | string | 高 |
| `enemy.hp` / `max_hp` / `block` | `enemy.stats.health` / `.maxHealth` / `.armor` | int | 高 |
| `enemy.intent` | `enemy.myNextAttack.attacks[]` | `List<Enemy+Attack>` | 中，字段语义待验 |
| `backpack.items[]` | `Item2.allItems`（需再按父网格过滤） | `List<Item2>` | 中 |
| `item.name` | `item.displayName` | string | 高 |
| `item.cost` | `item.cost`（原始值 `originalCost`） | int | 高 |
| `item.charges` | `item.currentCharges` / `item.charges` | int | 高 |
| `item.text` | `item.descriptions` | `List<string>` | 高 |
| `item.cells` | `item.gridObject.gridPositions` | `List<Vector2Int>` | 高 |
| `item.usable` | `item.playType` / `item.costs` / `item.currentCharges` | 组合判断 | **待设计** |

相对 MVP §3.4 草案的两处修正：

1. **背包不要用 `x/y/w/h`**，用 `gridPositions`（占格列表）。物品是异形的，矩形表达会失真。
2. `enemy.hp` 走 `enemy.stats.health`，**不要**用 `enemy.health`——后者也存在（`Int32 health`），
   大概率是模板/初始值，哪个是活值待验证（见 §7）。

---

## 4. 枚举（已导出确切数值）

**`GameFlowManager+BattlePhase`** — 战斗状态机，`mode` 的主来源：

| 名 | 值 |
|---|---|
| `outOfBattle` | 0 |
| `playerTurn` | 1 |
| `playerAction` | 2 |
| `enemyTurn` | 3 |
| `petTurn` | 4 |

**`GameManager+InventoryPhase`** — 背包交互态：

| 名 | 值 | 名 | 值 |
|---|---|---|---|
| `locked` | 0 | `inCombatMove` | 5 |
| `open` | 1 | `clickItem` | 6 |
| `choose` | 2 | `placePet` | 7 |
| `specialReorganization` | 3 | `notInteractable` | 8 |
| `dead` | 4 | | |

**`Character+CharacterName`**：`Any=0, Purse=1, Satchel=2, Tote=3, CR8=4, Pochette=5` → MVP 用 `Purse`。

**`Item2+PlayType`**：`Active=0, Combo=1, Movable=2`。

**`Item2+Effect+Target`**（决定动作目标合法性）：`unspecified=0, player=1, enemy=2, allEnemies=3,
everyone=4, reactiveEnemy=5, allFriendlies=6, adjacentFriendlies=7, friendliesInFront=8,
friendliesBehind=9, statusFromItem=10, frontmostFriendly=11, backmostFriendly=12, truePlayer=13,
everyoneButPochette=14`。

---

## 5. 白送的东西（不用自己算的指标）

| 想要 | 直接读 |
|---|---|
| 本回合/本场伤害输出 | `GameFlowManager.main.GetCombatStat(damageDealt, length)` |
| 受伤 | `...GetCombatStat(damageTaken, length)` |
| 用了几件物品 | `...GetCombatStat(itemsUsed, length)` |
| 已结束？ | `GameFlowManager.main.AnyEnemiesAlive()` |

`GameFlowManager+CombatStat+Type`：`itemsUsed=0, damageTaken=1, carvingsUsed=2, boardsCleared=3,
specificItemUsed=4, cursesReceived=5, damageDealt=6, invincibleHitsTaken=7`。

这条对验收 A4（每个动作有 expected/actual）很关键：**游戏自己就在记 delta**，
我们只要在动作前后各读一次 `GetCombatStat`，就能白拿一份独立于我们自己观测的交叉校验。

---

## 6. 动作入口候选（**全部未验证**）

| 动作 | 候选 | 备注 |
|---|---|---|
| 用物品 | `GameFlowManager.ConsiderUse(Item2, bool dontShowNegative)` | 最像「语义动作」的入口 |
| 选物品 | `GameFlowManager.SelectItem(Item2, List<Item2+Cost>)` / `DeselectItem()` | 可能只改 UI 选中态 |
| 推进效果 | `PerformActionEffect()` → `CompleteAction()` | 协程/队列驱动，需搞清 `isUsingAnItem`、`actionFinished` 的时序 |
| 结束回合 | `Player.NextTurn()` / `GameFlowManager.AutoEndTurn()` | 还有 `GameManager.main.endTurnButton` 做后备 |
| 开始战斗 | `GameFlowManager.StartCombat()` | spike 里构造战斗用 |

按 MVP §4 的判据：**先试直接调高层方法**；被 UI 守卫挡住再退 `EventSystem` 合成点击。

配套的「忙/闲」判据（等状态稳定用）：
`GameFlowManager.main` 的 `isUsingAnItem` / `actionFinished` / `isCheckingEffects` / `endingTurn`，
外加 `battlePhase` 迁移。这决定了 `seq` 该怎么生成。

---

## 7. 还没答的问题（按优先级）

> 2026-10-25 23:40 已在真实战斗上实测过一轮，Q1/Q5/Q6 有答案、Q4 换了字段，
> 见 `SPIKE-02-live-state.md`。下表保留原始记录。

1. **`Enemy.health` 和 `Enemy.stats.health` 哪个是活值？** —— 读错就是 A4 全面误报。
2. **`Quick And Easy` 怎么表示？** `RunTypeManager.Instance.runType` + `RunType.runProperties`
   里 `RunType+RunProperty+Type` 是枚举，得看有哪些值。
3. **结束回合的确切调用点**，以及 `battlePhase` 的迁移时序（决定 `seq` / `timeout_ms`）。
4. **物品「能不能用」怎么算** —— `costs` / `currentCharges` / `playType` 三者关系。
5. **`Item2.allItems` 里哪些在背包、哪些在奖励界面** —— 靠 `parentInventoryGrid` 还是 `gridObject.gridPositions` 非空？
6. **`Enemy.allEnemies` 是否含未交战/模板敌人** —— 过滤条件得先确认。

前 5 条都要开着游戏验，靠静态分析到头了。

---

## 8. 工具

`tools/dnmap/`：把 .NET 程序集的类型/字段/方法/属性导成 JSON + TSV，用来定位类。
不依赖 SDK，用系统 `csc` + MelonLoader 自带的 `Mono.Cecil`。

```powershell
# 重新导出（装配路径默认指本机 Backpack Hero）
powershell -NoProfile -ExecutionPolicy Bypass -File tools\dnmap\build.ps1

# 查询
python tools\dnmap\show.py GameManager
python tools\dnmap\show.py GameManager --grep hp
python tools\dnmap\show.py --search Enemy          # 按类型名模糊找
```

产物落在 `games/backpack-hero-aipeiwo/runtime/inspect/`（已 gitignore，不进仓库）。

`bridge/build.ps1`：用 `csc` 编 `BphBridge.dll`，因为本机 `dotnet`/`msbuild` 都不完整。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File games\backpack-hero-aipeiwo\bridge\build.ps1
# 加 -Deploy 顺便拷进游戏的 Mods/
```

> 本机 PowerShell 执行策略是 `Restricted`，`.ps1` 必须带 `-ExecutionPolicy Bypass -File` 才能跑。

---

## 9. 复现命令

```powershell
# 导出类型地图
powershell -NoProfile -ExecutionPolicy Bypass -File tools\dnmap\build.ps1
# -> assembly : Assembly-CSharp 0.0.0.0
# -> types    : 1219 (failed 0)

# 编译 bridge
powershell -NoProfile -ExecutionPolicy Bypass -File games\backpack-hero-aipeiwo\bridge\build.ps1
# -> built: BphBridge.dll (3584 bytes)

# 确认 MelonLoader 有没有真跑过
Get-Item 'E:\steam\steamapps\common\Backpack Hero\MelonLoader\Latest.log' | Select-Object Length,LastWriteTime
# -> Length = 0  => 从未启动
```

---

## 10. hello mod 验收（2026-10-25 23:27 通过 ✅）

修掉 `MelonGame` 开发者名（见 `PITFALLS.md` E1）后，日志：

```
[23:27:35.849] Melon Assembly loaded: '.\Mods\BphBridge.dll'
[23:27:35.849] SHA256 Hash: '33969E4D...'
[23:27:35.867] BphBridge v0.0.1  by ai-player-lab
[23:27:35.867] 1 Mod loaded.
[23:27:35.878] [BphBridge] BphBridge loaded. game=Backpack Hero
```

`1 Mod loaded` + 我们自己 `OnInitializeMelon` 打的那行都出来了。
**MVP §5.1 的 D1 上午到此完成。**

---

## 11. 下一步（可执行）

1. ~~启动一次游戏，确认 `Latest.log` 里有 `BphBridge loaded. game=Backpack Hero`~~ ✅ 已完成。
2. 把 `BridgeMod.cs` 写成真的 `OnUpdate` 循环：读 `GameManager.main` / `Player.main` /
   `GameFlowManager.main`，原子写 `runtime/state.json`，带 `seq`。
3. 用一个 `dump` 命令把 §3 的每一行都真实读一遍，对不上的就地修正——**这一步能一次性验掉 §7 的 1、5、6**。
