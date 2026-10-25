# SPIKE-05 · 尺子校准 + 整理阶段

> 2026-10-26 00:51 · 状态：**测量修复已验证，发现并补上「整理阶段」**
> 上一份：`SPIKE-04-first-battle.md`（第一场自主战斗 + 三个测量 bug）

## 0. 结论先行

| 问题 | 答案 |
|---|---|
| 三个测量 bug 修对了吗 | **修对了**。账本均值从 3.54 回到 **7.00**（木剑真值） |
| 致命一击记 0 的问题 | 已解决。这次记的是**剩余血量 4** |
| `stats` 恒 -1 是崩溃吗 | **不是**。`GetCombatStat` 调用成功、就是返回 -1 |
| 有新发现的机制吗 | **有**。污染物品会强制进入「整理阶段」，不点掉整局卡死 |
| RunType 解出来了吗 | 解出来了，`rt4` = 简单模式，含 `increaseEnemyHealth=-25` |

---

## 1. 账本校准：3.54 → 7.00

| | 样本 | damage_sum | 均值 |
|---|---|---|---|
| 修复前（SPIKE-04 那场） | 13 | 46 | **3.54** |
| 修复后 | 4 | 28 | **7.00** |

修复后 4 次采样是 `8 + 8 + 8 + 4`。最后那个 **4** 正是关键：

小蜜蜂剩 4 血被木剑打死，`delta.enemies` 里是 `"gone"`。
修复前这里记 **0**；修复后按「行动前血量」记 **4**。
均值从被低估一半，回到精确命中。

> 这也顺带验证了另一件事：`lethal` 判定生效了。
> 策略算出 `est_dmg=8 >= 剩余总血 4`，加上击杀奖励后分数 107.90，毫不犹豫地补了最后一刀。

---

## 2. 新机制：污染物品 + 整理阶段

**这一条是玩家（人类）告诉我的，不是我发现的** —— 记录清楚来源。

战斗中被怪物打中后，会生成类似「黏蜜」的道具：

```
黏蜜（STICKY HONEY）
污染
  被使用时，摧毁此物品
  你的回合结束时，被叠放的物品将被固定 +2 场战斗
```

两个关键点：

1. **这类道具能叠放在已有物品上面**。它的 `cells` 和背包里别的物品**重叠**——
   实测 `STICKY HONEY cells=['1,0']` 落在 `ROUGH BUCKLER` 的 `['0,0','0,1','1,0','1,1']` 里。
   所以「背包格子被占用」不能简单理解成一物一格。
2. **它会强制进入整理阶段**：`inventory_phase = 3 (specialReorganization)`，
   此时 `battle_phase` 可能还是 3(enemyTurn)，但游戏在等玩家摆好后点「结束整理」。

### 造成的实际问题

我的 `run.py` 在这个阶段会**卡死**：`legal` 是空的（因为判定条件是 `inPlayerTurn`），
于是「等玩家回合」一直等下去。

### 已补的支持

| 位置 | 改动 |
|---|---|
| `StateReader.DeriveMode` | `inventory_phase == 3` → `mode = "reorganize"`（优先于 battle） |
| `StateReader` legal | 整理阶段输出 `{"type":"finish_reorganize"}`，`skippableReorganization` 为真时再加 `skip_reorganize` |
| `Actions.Execute` | `finish_reorganize` → `GameManager.FinishReorganizeButton()`；`skip_reorganize` → `SkipReorganizeButton()` |
| 阶段门 | 点完后要等 `inventory_phase != 3` 才算完成，否则会提前回执 |
| `policy.py` | 这两个动作给 1.0 分（高于 end_turn 的 0），因为它是**流程阻塞点**不是战术选择 |
| `run.py` | `mode == "reorganize"` 不再当成「离开战斗」退出 |

> 入口是从元数据里查出来的：`GameManager.FinishReorganizeButton()` / `SkipReorganizeButton()`，
> 对应字段 `finishReorganizingButton` / `skipReorganizingButton`。

---

## 3. `stats` 恒 -1 的真因：它不是错误

加了诊断之后：

```
GetCombatStat(damageDealt, turn)=-1 (call ok)
```

**调用没抛异常**，就是返回 -1。所以之前那个 -1 是我的兜底值和游戏的真返回值撞了，
看起来像崩溃，其实游戏就是给了 -1（大概是「这项还没记录」的约定）。

**待办**：不能把 -1 当「读失败」。要么换个能判空的读法，要么先确认 -1 的确切语义
（比如它是不是只在 `length=combat` 且战斗结束后才有值）。

---

## 4. RunType 解码：`rt4` = 简单模式

```
runType.hardMode=False  key='rt4'  props=7
  type=10 startFromMatt        value=25
  type=3  increaseHP           value=15     ← +15 最大血量（实测 hp 40→55）
  type=6  increaseEnemyHealth  value=-25    ← -25% 敌方血量（MVP 记的那条）
  type=2  getExtraItem         value=1
  type=12 chestsContainExtra   value=2
  type=15 bonusDamage          value=10
  type=16 increaseLuck         value=25
```

对照 MVP 文档：「`Quick And Easy`（Boss 前只有一层，-25% 敌方 HP）」——
这里的 `-25%` 和 `+15 HP` 都对得上，但**没有 `oneFloorBeforeBoss(26)`**。

所以结论是：**「简单模式」不是 MVP 说的那个 Quick And Easy，但它是 MVP 指定的退路**
（原文：「确认 `Quick And Easy` 已解锁（没有就退 `Easy Mode`）」）。
菜单里还有「旅程 / 标准 / 经典马修 / 无尽美梦 / 5×7 / 5×5」，
哪个才是 Quick And Easy 还没逐个看过。

---

## 5. 踩坑：跨进程文件的瞬时拒绝

`run.py` 崩过一次：

```
PermissionError: [Errno 13] Permission denied: ...runtime\ack\995516.json
```

但紧接着用 `Get-Content` 读同一个文件，**正常**。

原因：游戏进程刚把 ack 写出来，Windows 上那一瞬间 ACL 还没完全落定，
python 正好撞上。属于**瞬时竞争**，不是权限配置错误。

修法：`client.py` 的读循环原来只 catch `FileNotFoundError` + `JSONDecodeError`，
改成 catch `OSError`（覆盖 PermissionError），继续重试到 deadline。
详见 `PITFALLS.md` E7。

> 这类问题在最开始几十次都碰不到，只有跑得快、文件刚落地时才出现。
> 提醒：**跨进程文件交接必须容忍瞬时失败**，别假设「写完了就能立刻读」。

---

## 6. 下一步

1. **部署本轮的整理阶段支持**（需要重启游戏），验证 `finish_reorganize` 真的能推进流程。
2. **地图**：现在打赢一场就停在 `mode=map`。要跑完一局必须接地图选点。
3. **`stats` 的 -1**：确认语义，否则这个「白送的交叉校验」一直用不上。
4. **背包叠放模型**：需要把「叠放」表达出来（谁叠在谁上面），否则摆包逻辑会误判空间。
