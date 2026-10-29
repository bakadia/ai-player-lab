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

## 2. 现在的短板清单（按证据）

1. **多敌人房是全灭点**：三局都死在这。`--aware-block --ratio-energy` 只是把伤害摊平，
   9→13 格 + 3–6 件物品的输出打不穿 2–3 个敌人的血线。
2. **等级卡在 1**：`xpRequired` 是 5 / 10 / 25 …，但三局都没吃到第 2 级（10 xp）——
   要么战斗给的经验不够（只有 4–5 点/场），要么**死得太早**。
   这条值得单独量：一场战斗给多少 xp、一局总共能打几场。
3. **`place_item` 的几何不一致**：`cell 1,0 -> footprint hits occupied 1,-1`，
   说明 `probe_fit` 算出来的落点和桥接实际落点差一格（collider offset）。
   现在不会把局拖死了，但会白丢战利品。
4. **Chance / `EventNPC` 事件仍然只能拉黑**（PITFALLS E18），每层会少 1–2 个收益点。

## 3. 改动清单

| 位置 | 改动 |
|---|---|
| `agent/loop.py` | `deferred_ok`：`unlock_spaces` 点了格子、`place_item` 失败，都不计入 `nochange_streak`（两处 streak 判定都改） |
| `agent/loop.py` | （上一提交）`SimpleClickItem` 走 `play_event`；`start_event` 连续两次空操作 → `blocked_events.add` |
| `agent/tests/test_v0.py` | `test_current_room_event`（房间 ↔ 事件配对） |

## 4. 验证

- `test_v0.py` / `test_mapnav.py` / `run-tests.ps1` 全 ALL PASS
- 真机三局见 §0；日志 `data/runs/baseline-run{1,2,3}.txt`、`data/runs/loop-20261029-1{70*,7*}.jsonl`

## 5. 下一步（建议顺序）

1. **量经验曲线**：一局能打几场、每场给多少 xp、第 2 级要几场。这决定「主动打敌人房」
   还是「绕开保命」——三局数据看，绕开也躲不过去，不如先把 xp 拿到。
2. **多敌人房的输出策略**：现在打分是「单位能量收益」，多敌人时应该优先「先杀一个」
   （减少每回合 incoming）。`--aware-block` 只算总量，没有「集火」概念。
3. `place_item` 的落点差一格（probe 与桥接不一致）。
4. Chance / EventNPC 随机事件 UI。
