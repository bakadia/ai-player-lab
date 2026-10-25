# SPIKE-03 · 动作层打通

> 2026-10-26 00:02 · 状态：**cmd → 执行 → ack 全链路验证通过**
> 上一份：`SPIKE-02-live-state.md`（状态读取）· 本文记「动作真的改了游戏」
> 这一份就是 MVP §5.1 的 D2 验收：一条命令能改变游戏状态。

## 0. 结论先行

| 问题 | 答案 |
|---|---|
| 动作能执行吗 | **能**。`use_item` 和 `end_turn` 都真实改变了游戏状态 |
| 能证明是「这个动作」改的吗 | **能**。每次 ack 都带前后快照的 delta，不是「没抛异常就算成功」 |
| 失败能被发现吗 | **能**。错误 uid → `ok=false` + `error`，`delta` 为空，`seq` 不动 |
| 敌人意图能读吗 | **能**，但**必须在 `playerTurn` 取**（见 §3） |
| 能无人值守打一局吗 | 还不能。缺「合法动作列表」和地图/奖励处理，见 §7 |

---

## 1. 四条实测回执（原始数据）

### 1.1 用物品 —— 最关键的证据

```
> use i-7936          # WOODEN SWORD，computed energy_cost=1

{"id":137231,"ok":true,"changed":true,"seq_before":19,"seq_after":22,"ms":706,
 "action":{"type":"use_item","uid":"i-7936","via":"consider"},
 "delta":{"player_energy":-1,"enemies":{"e-11512":-7}},
 "error":null}
```

三件事同时对上：

- 能量 **3→2**，正好等于我们从 `Item2.costs` 算出来的 `energy_cost=1`
- 敌人 **32→25**，掉 7 点
- `seq` 19→22，状态确实变了三次

> 这是「状态契约 + 动作执行 + delta 校验」第一次闭环成立。
> 也是 `Item2.costs` 那个修正（SPIKE-02 §2 Q4）的验证：如果还按 `item.cost`（-999）来算，这里就废了。

### 1.2 结束回合

```
> end-turn

{"id":160062,"ok":true,"changed":true,"seq_before":22,"seq_after":28,"ms":2829,
 "delta":{"player_energy":1,"turn":[1,2]},"error":null}
```

`turn 1→2`，能量 2→3（新回合补满 AP）。

**`ms=2829` 是这套设计的关键证据**：敌人回合要演将近 3 秒，
如果只靠「状态 400ms 没变」判断结束，会在动画间隙就回执、delta 也是残的。
加上「`battlePhase` 必须回到 `playerTurn/outOfBattle`」的阶段门之后，它老老实实等了 2.8 秒。

### 1.3 失败：不存在的物品

```
> use i999999

{"id":171253,"ok":false,"changed":false,"seq_before":28,"seq_after":28,"ms":400,
 "action":{"type":"use_item","uid":"i999999","via":"consider"},
 "delta":{},"error":"InvalidOperationException: item not found: i999999"}
```

`ok=false`、`error` 给原因、`delta` 空、`seq` 不动。**失败不会被吞成成功。**

### 1.4 noop：验证「没变化」能如实报告

```
noop ok=False changed=False delta={}
```

这条就是 §4 那个 bug 的回归用例。

---

## 2. 物品 uid 与费用（字段落地情况）

`state.json` 里现在长这样：

```json
{"uid":"i-7936","name":"WOODEN SWORD","cost":-999,"energy_cost":1,
 "usable":true,"charges":0,"max_charges":-999,"cells":["-1,-1","-1,0","-1,1"]}
```

- `uid` = `"i" + GetInstanceID()`，**跨帧稳定**，可以直接拿去发命令
- 注意 **instance id 是负数**（`i-7936`），解析时别假设是正整数
- `energy_cost` 从 `Item2.costs` 里 `type==energy(0)` 的 `currentValue` 求和，实测 1 / 1 / 0，合理
- `cost` 保留原始值 -999 只作对照，**不要用**

---

## 3. 敌人意图：必须盯 `playerTurn`

| 取样时机 | `myNextAttack` |
|---|---|
| `battlePhase=3`（enemyTurn，SPIKE-02 那次） | **null** |
| `battlePhase=1`（playerTurn，这次） | **有值** |

这次读到的意图：

```json
"attacks":[{"type":1,"power":14,"text":"","hidden":false}]
```

`type=1` 是 `Enemy+Attack+Type.block` —— 它准备**挡 14**，不是攻击。
所以那一回合玩家没掉血是合理的，不是我们读错了。

`text` 为空是正常的：这个怪的攻击没有单独描述文案，得靠 `type` + `power` 组合判断。

---

## 4. 修掉的 bug：`changed` 曾经恒为真

第一版 `noop` 回执是：

```json
{"ok":true,"changed":true,"delta":{"enemies":{}}}
```

什么都没做，却报「有变化」。原因：`changed` 写成了 `DiffJson() != "{}"`，
而 `DiffJson()` **无条件**写出 `"enemies":{}` 这个键，于是判断恒真。

**为什么这个 bug 很致命**：A4 要求「每个动作都有 expected/actual delta，静默失败漏检 = 0」。
`changed` 恒真 = 任何动作都被判成功 = 这套东西最想防的那类失败被请回了门里。

修法：新增 `Snapshot.Differs()` 逐字段比对，作为 `changed` 的**唯一**判据；
`DiffJson()` 的敌人 delta 改成先收集、非空才写键。代码注释里写死了「不许再用 diff != {}」。

---

## 5. 运行模式：这局不是 Quick And Easy

探针读到：

```
runType.hardMode=False  key='rt1'  props=1
  runProp[0] type=10 value=0        # 10 = startFromMatt
```

`RunType+RunProperty+Type` 一共 62 个值，和 MVP 相关的是：

| 值 | 名字 | 含义 |
|---|---|---|
| 6 | `increaseEnemyHealth` | 敌方血量（负数就是减免，MVP 说的 -25%） |
| 26 | `oneFloorBeforeBoss` | Boss 前只有一层 |
| 31 | `startingBackpackSize` | 起始背包尺寸 |

这局只有 `startFromMatt`，**没有 `oneFloorBeforeBoss`** → 不是 Quick And Easy。

不影响动作层验证，但如果要按 MVP 的设定跑 50 局，得在开局选 `Quick And Easy`。
判别方法：`RunTypeManager.Instance.runType.runProperties` 里是否有 `type==26`。

> 待确认：`runProperties` 可能只装「相对 `standardRunType` 的差异」，
> `RunTypeManager` 里还有个 `standardRunType` 字段。要下结论得两个都读。

---

## 6. 现在的完整能力

```
Python (agent)                     游戏进程 (BphBridge)
  client.py state   ──read──▶      runtime/state.json     (原子写，带 seq)
  client.py use/end-turn ──write──▶ runtime/cmd/{id}.json
                                    └─ OnUpdate 轮询 → 执行 → 等稳定
  ack ◀──────────────write──────    runtime/ack/{id}.json (delta + ms + error)
  client.py halt on  ──touch──▶     runtime/halt            (存在即拒执行)
```

| 能力 | 状态 |
|---|---|
| 读状态 | ✅ 1219 类型地图 + 实测字段 |
| 读意图 | ✅ 限 `playerTurn` |
| 用物品 | ✅ 带 delta 校验 |
| 结束回合 | ✅ 阶段门防误判 |
| 失败可见 | ✅ `ok/error/delta` 三元组 |
| 急停 | ✅ `halt` 文件 |
| 合法动作列表 | ❌ 还没做（`legal` 字段目前不存在） |
| 地图 / 奖励 / 商店 | ❌ 还没做 |

---

## 7. 下一步

1. **`legal` 动作列表**：MVP §3.4 要求由 Bridge 算，Agent 不猜合法性。
   现在 `usable` 只是个 `energy_cost <= energy` 的粗判，要升级成完整判断
   （`costs` 全类型、`currentCharges`、`mustBePlacedOnItemType` 等）。
2. **战斗闭环**：`while 有敌人: 选最优动作 or end_turn`。这一步跑通就等于 D3 的「随机合法动作打一场」。
3. **地图 / 奖励 / 三选一**：`mode` 现在只有 `menu/map/battle`，要补 `reward`。
4. **评测**：`GameFlowManager.GetCombatStat(damageDealt/damageTaken)` 已经在，接上就能出数。

> 提醒：每次改 mod 都要退游戏再覆盖 DLL（`PITFALLS.md` E2）。
> 下一轮建议一次性把「legal 列表 + 战斗闭环」写完再重启，别一格一格挪。
