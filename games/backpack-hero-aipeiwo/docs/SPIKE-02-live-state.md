# SPIKE-02 · 在真实战斗上验状态

> 2026-10-25 23:40 · 状态：**状态读取已跑通，动作层未开始**
> 上一份：`SPIKE-01-reflection-map.md`（静态反射地图）· 本文只记「跑起来之后看到什么」
> 数据来源：`runtime/state.json`（seq 1→80）+ MelonLoader 日志里的两段 probe

## 0. 结论先行

| 问题 | 答案 |
|---|---|
| 状态能稳定读出来吗 | **能**。主菜单→开局→地图→战斗全程 80 次状态变更，无异常、无半截 JSON |
| `state.json` 可信吗 | 可信。捕获到了 HP 40→30、金币 0→6、物品 3→11、回合 0→4 |
| SPIKE-01 §7 的未知项 | 答掉 4 条，剩 2 条要再探一次（见 §5） |
| 动作层 | 没开始。下一步 |

---

## 1. 实测两段探针

### probe #1 —— 刚开局，还在选路（floor=1，地图）

```
scene=Game
gm.floor=1  gm.gold=0  gm.currentHealthAmount=0  gm.inventoryPhase=8  gm.enemyEncounter=''
gf.battlePhase=0  gf.turnNumber=0  gf.isUsingAnItem=False
pl.characterName=Purse  pl.AP=3  pl.APperTurn=3
pl.stats health=40 maxHealth=40 armor=0 maxHealthBeforeItems=40
Enemy.allEnemies=0
Item2.allItems=3
  item[0] 'WOODEN SWORD'   cost=-999 charges=0/-999 playType=0 cells=3
  item[1] 'ROUGH BUCKLER'  cost=-999 charges=0/-999 playType=0 cells=4
  item[2] 'STOPWATCH'      cost=-999 charges=0/-999 playType=0 cells=1
Status.allStatuses=1
```

### probe #2 —— 进战斗（2 只怪）

```
scene=Game
gm.floor=1  gm.gold=0  gm.currentHealthAmount=40  gm.inventoryPhase=0
gm.enemyEncounter=', RATWOLF, ROBBER RACCOON'
gf.battlePhase=3  gf.turnNumber=0        <-- enemyTurn，不是 playerTurn
pl.characterName=Purse  pl.AP=0  pl.APperTurn=3
pl.stats health=40 maxHealth=40 armor=0
Enemy.allEnemies=2
  enemy[0] 'Robber Raccoon '  e.health=25  stats.health=25  maxHealth=25  dead=False targetable=True nextAttack=no
  enemy[1] 'Ratwolf'          e.health=40  stats.health=40  maxHealth=40  dead=False targetable=True nextAttack=no
Status.allStatuses=3
```

---

## 2. 逐条回答 SPIKE-01 §7

### ✅ Q1 `Enemy.health` 和 `Enemy.stats.health` 哪个是活值

**两者一致**（25/25、40/40）。但真正被证伪的是第三个字段：

> 打完之后 `player.hp=30`，而 `gm.currentHealthAmount=40`。

所以 **`GameManager.currentHealthAmount` 不是当前血量**，它不跟着掉血走（大概是开局/上限的缓存）。
结论：

- 玩家血量只认 **`Player.main.stats.health`**
- 敌人血量用 **`Enemy.stats.health`**（和玩家同一个 `Status` 组件，语义统一）
- `Enemy.health` 暂时留作交叉校验，但**不要**当主字段
- `gm.currentHealthAmount` 从 schema 里删掉

> 待补一刀：还没见到「敌人掉血」的样本，`Enemy.health` 是否也同步更新仍未知。下次遇到残血敌人时确认。

### ✅ Q5 `Item2.allItems` 怎么区分「在背包」

`gridObject.gridPositions.Count > 0` 有效：某次状态里 `items_total=11 / items_placed=10`，
差的那 1 个是没落位的。这个判据可用。

### ⚠️ Q4 物品「能不能用」怎么算 —— 换了字段

`item.cost` 读出来是 **`-999`**，`charges` 是 **`-999`**。这两个是游戏的哨兵值（表示「不适用」），
**不是真实费用/充能**。真实数据在集合里：

```
Item2.costs : List<Item2+Cost>
Item2+Cost { type, baseValue, currentValue, displayAsX, source, costModifiers, ... }
Item2+Cost+Type : energy=0, mana=1, gold=2, canBeUsedIndirectly=3,
                  mustMeetASpecificCondition=4, sunShine=5
```

所以：
- 能量费用 = `costs` 里 `type == energy` 的 `currentValue`
- 充能 = `currentCharges`（`charges == -999` 表示不是充能类物品）
- `playType` 全部读到 0（`Active`），包括 `MANA STONE`、`SAPPHIRE` 这种明显被动的资源 ——
  **这个字段大概率不是「能不能用」的判据**，别依赖它

### ⚠️ Q2 `Quick And Easy` 怎么表示 —— 还没查

`RunTypeManager.Instance.runType.runProperties` 这次没探到。下次补。

### ❌ Q3 结束回合的调用点 —— 没开始

属动作层，见 §6。

### ✅ Q6 `Enemy.allEnemies` 是否含模板敌人

不含。地图上 `allEnemies=0`，进战斗后正好 2 只且 `dead=False`。`dead` 过滤有效。

---

## 3. 新发现（没预料到的）

### 3.1 intent 这次读不到

两段探针里 `nextAttack=no`（`Enemy.myNextAttack` 为 null）。

**大概率是取样时机问题**：probe #2 恰好打在 `battlePhase=3`（`enemyTurn`），
敌人已经在出招，意图自然清空了。玩家该看意图的时候是 `battlePhase=1`（`playerTurn`）。

下一次探针改成 **在 `playerTurn` 且敌人存活时** 触发。意图相关的东西：

```
Enemy.myNextAttack        : PossibleAttack   <- 主字段
Enemy.myOverrideIntention : PossibleAttack
Enemy.possibleAttacks     : List<PossibleAttack>   <- 候选池，不是当前意图
Enemy+PossibleAttack.attacks : List<Enemy+Attack>
Enemy+Attack { type, currentPower, description, hideIntention, powerRange, isNumeric }
Enemy+Attack+Type : attack=0, block=1, poison=2, spikes=3, regen=4, summon=5, pass=6,
                    heal=7, vampire=8, hazard=9, burn=10, dodge=11, slow=12, weak=13,
                    rage=14, haste=15, selfDestruct=16, ... steal=19, freeze=20
```

### 3.2 `scene` 只能区分「主菜单 / 游戏内」

主菜单 `scene=MainMenu`（此时三个单例全是 null），进入游戏后**地图和战斗都是 `scene=Game`**。
所以：

- `scene=MainMenu` → `mode=menu`，可靠
- 进游戏后 **`mode` 必须靠 `battlePhase`**，`scene` 帮不上忙

`battlePhase=0`(outOfBattle) 在地图和结算界面都会出现，目前 `DeriveMode` 把
「`outOfBattle` + `floor>=1`」判成 `map`，这是**启发式**，等遇到奖励/商店界面再收窄。

### 3.3 几个小坑

| 现象 | 说明 |
|---|---|
| `enemy displayName` 有尾随空格（`'Robber Raccoon '`） | 比较名字前要 `Trim()` |
| `gm.enemyEncounter` 头上有逗号（`', RATWOLF, ROBBER RACCOON'`） | 是字符串拼接的产物，解析要跳空项 |
| `pl.level` 第一段探针读到 -1、后来是 0 | 开局瞬间字段还没初始化，读不到属正常 |
| `Item2.cost` / `charges` = -999 | 哨兵值，别当真值用 |

---

## 4. 对 `bph.state.v1` 的修正（下一版 schema v2）

| 字段 | v1 现状 | 改成 |
|---|---|---|
| `player.hp` | `stats.health` ✅ | 不变 |
| `player.gm_health_amount` | 输出了 | **删掉**，会误导（恒等于 40） |
| `player.max_hp` | `stats.maxHealth` ✅ | 不变 |
| `backpack.items[].cost` | `item.cost` → 恒 -999 | **改读 `item.costs` 里 `type==energy` 的 `currentValue`** |
| `backpack.items[].charges` | `item.charges` → -999 | `currentCharges` + 用 -999 判「非充能物品」 |
| `backpack.items[].usable` | 没实现 | 由 `costs` 的 energy 费用 vs `player.energy` 算，**不靠 `playType`** |
| `enemies[].hp` | `stats.health` ✅ | 不变，`raw_health`（`Enemy.health`）降级为调试字段 |
| `enemies[].attacks` | 读不到（null） | 改在 `playerTurn` 取样 |
| `run.mode` | `Quick And Easy` 未取 | 补 `RunTypeManager.Instance.runType` |
| `mode` | 启发式 | 保留启发式 + 已暴露原始 `battle_phase`/`inventory_phase`，不急着定死 |

---

## 5. 还没答的（按优先级）

1. **玩家回合时的敌人意图**（探针改到 `battlePhase==1` 触发）
2. **`Quick And Easy` 的表示**（`RunTypeManager` + `RunType.runProperties`）
3. **结束回合 / 用物品的调用点** —— 动作层，最关键
4. `Enemy.health` 会不会跟着掉血（需要残血样本）

---

## 6. 下一步

1. 把 probe 触发条件从「第一次见到敌人」改成「`battlePhase==1` 且有敌人」，补 intent。
2. 补 `item.costs` 的读取，算出真实的能量费用和「能不能用」。
3. 然后才动动作层：`runtime/cmd/*.json` → 执行 → `runtime/ack/*.json`。

> 注意：每次改 mod 都要**先退游戏**再覆盖 `Mods\BphBridge.dll`，
> 进程映射住的文件覆盖会失败（`PITFALLS.md` E2）。
