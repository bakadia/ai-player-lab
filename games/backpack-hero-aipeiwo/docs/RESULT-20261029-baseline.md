# RESULT · 2026-10-29 19:00 · 存活基线（3 局，v8.19 + loop 修复）

> 扩容修好之后，量一下「现在到底能打多深」。
> 协议：`retry_run` 重开 → 摆 3 件教程物品 → `loop.py --max-steps 260
> --max-seconds 1800 --aware-block --ratio-energy --make-room 1`（扩容默认自动）→ 打到死。
> 三局都是**同一套参数、同一份账本**。

## 0. 结论

**扩容不再是瓶颈，瓶颈是「活到第二次升级」。** 三局全部死在第 1–2 层、**等级始终是 1**、
背包都已经扩到 **13 格**（说明扩容路径每局都正常跑完了一次）。

| # | 最深层 | 步数 | 等级 | 背包 | 结局 |
|---|---|---|---|---|---|
| 1 | **2** | 193 | 1 | 13 格 / 死时 6 件 | 战斗内 hp 2→0，剩 1 敌 |
| 2 | 1 | 106 | 1 | 13 格 / 死时 7 件 | 战斗内 hp→0，剩 1 敌 |
| 3 | 1 | 148 | 1 | 13 格 / 死时 3 件 | **整理阶段**被打死，剩 1 敌 |

共同点：都是「多敌人房 + 硬打」，死的时候往往**只剩 1 个敌人**。也就是差一点点。

## 1. 这轮顺手修掉的两个「自己把自己判死」

跑基线时 loop 两次在「明明在推进」的时候 `判定卡死，停`。都是同一类错误：
**预期内的 `changed=False` 被算进了卡死计数**。

| 动作 | 现象 | 修法 |
|---|---|---|
| `unlock_spaces` | 每点一个 GridExtension，`grid_squares` 要等 `finish_level_up` 才 +N，所以每次都是 `ok=False changed=False`；点 4 次正好凑满 `MAX_CONSECUTIVE_NOCHANGE=4` | `deferred_ok`：`diag.clicked>0` 就不计卡死 |
| `place_item` | 两件放不进去的战利品（`cell 1,0 footprint hits occupied 1,-1`）各失败两次 → 4 次 no-change → 判死 | 同上：摆放失败是**可恢复**失败（换格/记 explored），不计卡死 |

修好之后同一个开局才跑到 193 步、floor 2（修之前 31 步就"卡死"在地图上）。

## 2. 经验 / 战斗曲线（实测一局，工具 `tools/bph_xp_curve.py`）

给 state 加了 `run.experience` / `run.xp_next`（后者来自 `levelUps[level+1].xpRequired`），
再用一个**纯读 state.json** 的采样器跟 loop 并行跑（不发命令，不抢队列）。

这一局打了 **6 场**（第 6 场死）：

| # | 层 | 敌人 | 掉血 | xp | 之后 |
|---|---|---|---|---|---|
| 1 | 1 | 2（Muskrat Brigand / Ratwolf） | 6 | **7** | map |
| 2 | 1 | 2（Bearded Dragon / Hermit Crab Bomb） | 14 | **7** | reorganize |
| 3 | 1 | 1（Bearded Dragon） | 2 | **6** | map |
| 4 | 1 | 2（Sparrow Knave / Dove Deacon） | 4 | **7** | reorganize |
| 5 | 1 | 1（Sparrow Knave） | 5 | **5** | map |
| 6 | 2 | 3（Gremlin / Ratwolf ×2） | 4 → 0 | 0 | **dead** |

**读数**：

- **一场 5–7 xp**，跟敌人数量弱相关（2 敌=7、1 敌=5–6）。
- 升级门槛：`5 / 10 / 25 / 50 …`。这一局总共拿到 **32 xp**（5 场），
  只走到 **level 1 + 余 17/25** —— 也就是说**一局大约能升 2 级就到头**。
- **HP 是硬约束**：40 点血，5 场共掉 **31 点**，没有任何回血手段（MEAL 只有 5）。
  按这个速率，**第 5–6 场必死**，而第 2 级要等到再赢 4–5 场。
- 3 敌人房是死因（第 6 场），2 敌人房掉 4–14 血，1 敌人房只掉 2–5 血。

**推论**：

1. **「绕开多敌人房、专挑 1 敌人房」在数学上更划算**：xp/血比高一个量级
   （1 敌：5–6 xp / 2–5 血；2–3 敌：7 xp / 4–14 血，还可能死）。
2. 想靠升级变强，得先把**每场掉血**压下来（集火先杀一个 / 更好的盾 / 回血），
   而不是多打几场。
3. 升级本身的收益也不小：第 0 级给 4 格，但**不会回血**，所以「撑到升级」才是关键。

## 3. 现在的短板清单（按证据）

1. **多敌人房是全灭点**：三局都死在这。`--aware-block --ratio-energy` 只是把伤害摊平，
   9→13 格 + 3–6 件物品的输出打不穿 2–3 个敌人的血线。
2. **等级卡在 1 的原因查清了**（§2）：每场 5–7 xp 是够的，问题是**血不够撑到第 3 级**。
3. **`place_item` 的几何不一致**：`cell 1,0 -> footprint hits occupied 1,-1`，
   说明 `probe_fit` 算出来的落点和桥接实际落点差一格（collider offset）。
   现在不会把局拖死了，但会白丢战利品。
4. **Chance / `EventNPC` 事件仍然只能拉黑**（PITFALLS E18），每层会少 1–2 个收益点。

## 4. 改动清单

| 位置 | 改动 |
|---|---|
| `agent/loop.py` | `deferred_ok`：`unlock_spaces` 点了格子、`place_item` 失败，都不计入 `nochange_streak`（两处 streak 判定都改） |
| `agent/loop.py` | （上一提交）`SimpleClickItem` 走 `play_event`；`start_event` 连续两次空操作 → `blocked_events.add` |
| `bridge/src/StateReader.cs` | state 增加 `run.experience` / `run.xp_next`（升级曲线统计用） |
| `tools/bph_xp_curve.py` | 新：纯读 state.json 的战斗/经验采样器（`mode` 进出 battle 判定场次，跨级也能算 xp） |
| `agent/tests/test_v0.py` | `test_current_room_event`（房间 ↔ 事件配对） |

## 5. 验证

- `test_v0.py` / `test_mapnav.py` / `run-tests.ps1` 全 ALL PASS
- 真机：§0 三局（基线）+ §2 一局（经验曲线，`data/runs/xp-curve-run1.jsonl`）
- 日志 `data/runs/baseline-run{1,2,3}.txt`、`data/runs/loop-20261029-1{70*,7*}.jsonl`

## 6. 下一步（建议顺序）

1. **「少挨打」优先于「多打场」**：按 §2 的 xp/血比，先把每场掉血压到 2–3，
   比多打两场更值。具体两条：
   - **多敌人集火**：现在打分是「单位能量收益」，没有「先杀一个」的概念；
     2–3 敌人时应该把「能一击必杀的那个」优先（每回合 incoming 立刻少一截）。
   - **敌人房选择**：`mapnav` 的 `avoid` 目前只按「是敌人房」一刀切；
     可以按「敌人数 / 血量」给风险分，优先 1 敌人房。
2. `place_item` 的落点差一格（probe 与桥接不一致）。
3. Chance / EventNPC 随机事件 UI（每层 1–2 个收益点）。
4. 回血：只有 MEAL/PoULTICE 之类少量食物；升级不回血。值得看商店/宝箱里食物的权重。
