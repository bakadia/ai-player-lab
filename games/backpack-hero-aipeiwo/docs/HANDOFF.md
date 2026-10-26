# HANDOFF · 交接给下一个智能体

> 2026-10-26 15:20 · 写给**没有任何上下文**的接手者。
> 读完这份再动手，不要去重新调查已经确认过的事。

## 0. 一句话现状

Backpack Hero 的 AI 玩家流水线已打通「读状态 → 决策 → 执行 → 校验 → 记录」，
**能无人值守打赢单场战斗**；但一离开战斗就停，**还没接地图**。

## 1. 下一步只有一个（按优先级，别的先别做）

**地图：只读探测 `EventButton`，不改任何游戏状态。**

为什么是它：验收项 A2 是「20/20 局到达 Boss」。现在打赢一场就停在 `mode=map`，
跑不完整局，后面的 A3/A5/A6 全部无从谈起。

具体做法：
1. 在 `bridge/src/StateReader.cs` 里增加一个 `map` 段落：`mode == "map"` 时，
   枚举场上所有 `EventButton`，把每个节点的**类型、可交互状态**写进 `state.json`
   （先不设计动作，先看清结构）。
2. 只读、不点、不发命令。
3. 让玩家开一局走到地图，读一次 `state.json`，确认能拿到节点列表。
4. **看清结构之后**才设计 `{"type":"choose_node","index":n}` 并实现。

`EventButton` 的已知情况：23 个字段，有 `EventButtonAction` / `PossibleOutcome` /
`PerfromEffect` 协程；地图上的节点就是它（不是独立的「地图节点」类型）。
用 `python tools/dnmap/show.py EventButton` 可以看全部字段。

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

已实现动作：`use_item` / `end_turn` / `finish_reorganize` / `skip_reorganize` / `noop`。

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

## 3. 命令速查

```powershell
# 类型地图（查类/字段/方法）
powershell -NoProfile -ExecutionPolicy Bypass -File tools\dnmap\build.ps1
python tools\dnmap\show.py EventButton

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
| `docs/REFERENCE-modes-and-map.md` | wiki 取证：挑战/地图节点分类；ModDocs 覆盖范围 |
| `docs/PITFALLS.md` | E1~E7 踩坑记录，**动手前必读** |

## 6. 开放问题（都不要在没证据时下结论）

1. `GetCombatStat` 恒返回 -1 的真实语义（这条本可当第二套独立观测做交叉校验）。
2. 背包**叠放模型**：污染物品可以压在别的物品上（`cells` 重叠），现在的数据表达不出「谁叠在谁上」。
3. `Enemy.health` 是否随掉血同步（只见过两者相等的样本）。
4. 地图/奖励阶段的完整动作集（正在做）。
5. Quick and Easy 的 `rt` 编号（还没开过）。

## 7. 需要玩家配合的事

- **开一局走到地图**（或战斗），我才能读到真实数据。
- 挑战列表要**向下滚动**才能看到 Quick and Easy。
- 换 DLL 时**先退游戏**。
- 游戏目录：`E:\steam\steamapps\common\Backpack Hero`

## 8. 别做的事

- 别为了"看起来有进展"把动作层改成猜测式调用——`changed` 校验是这套东西的核心。
- 别把 `-1`/`-999`/`"gone"` 当真实数值（都是哨兵或兜底值）。
- 别在 `.ps1` 里写中文。
- 别把任何密钥写进仓库（本地配置在 `~/.dsh/profiles/desktop/cordis.patch.yml`）。
