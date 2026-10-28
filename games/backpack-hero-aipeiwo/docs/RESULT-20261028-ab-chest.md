# RESULT · 2026-10-28 16:00–17:00 · A/B、宝箱链路、E9、玩家侧状态

> 接手 `HANDOFF-AB-NEXT.md` 后实际跑出来的结果。只记事实和证据，不重复设计。
> 桥接仍是 v7（85,504 bytes，SHA256 `2EB092F0…`），本次**没有**改桥接。

## 1. A/B：c-full vs d-value-status（6/6 跑完）

命令（按交接单）：

```powershell
python games\backpack-hero-aipeiwo\tools\ab_retry.py `
  --arms c-full d-value-status --runs 3
```

- 16:08:39 开始，16:17:41 结束；`ab-retry-summary.json` 里 6 条全部 `ok=true, rc=0`。
- 游戏由 CUA `launch_app` 启动，停在主菜单；第 1 局由脚本调 `start_run.py`（rc=0）。
- 第 2–6 局全部走 `retry_run`：`changed=True`，耗时 1.25–1.34 s，**没有重启游戏、没有用 bph.cmd**。
- 两臂各自从 `data/items.json` 播种账本（`data/ledgers/c-full.json` / `d-value-status.json`）。
- 日志里的 `flags_detail` 已核对：c-full 的 `value_status=false`，d 的 `value_status=true`，
  其余四个 flag 两臂相同；**没有 baseline 参与**。

### 1.1 逐局

| 日志 | floor | hp | turns | 零伤回合 | 结果 | 主要物品 |
|---|---:|---:|---:|---:|---|---|
| ab-c-full-1 | 1 | 24 | 8 | 12% | 步数用完 | WOODEN SWORD×20, BUCKLER×11, MEAL×2 |
| ab-c-full-2 | 1 | 11 | 9 | 0% | 步数用完 | WOODEN SWORD×26, BUCKLER×7, MEAL×2 |
| ab-c-full-3 | 1 | 16 | 9 | 11% | 步数用完 | WOODEN SWORD×26, BUCKLER×9, MEAL×2 |
| ab-d-value-status-1 | 1 | 16 | 9 | 22% | 步数用完 | SWORD×21, BUCKLER×7, **WOODEN BLADE×6**, **CAVE FISH×1** |
| ab-d-value-status-2 | 1 | 0 | 9 | 0% | **阵亡**（79 步） | SWORD×29, BUCKLER×7, MEAL×2 |
| ab-d-value-status-3 | 1 | 23 | 8 | 33% | 步数用完 | SWORD×19, BUCKLER×12, **ROUGH WHETSTONE×1** |

### 1.2 按臂聚合（`run_report.py` 输出）

| 臂 | n | 层数(均) | 最好层 | 剩余血(均) | 零伤回合(均) | 阵亡 | BUCKLER 次数 |
|---|---:|---:|---:|---:|---:|---:|---:|
| c-full | 3 | 1.0 | 1 | 17.0 (24/11/16) | 8% | 0/3 | 27 |
| d-value-status | 3 | 1.0 | 1 | 13.0 (16/0/23) | 19% | 1/3 | 26 |

### 1.3 结论与限制

- **没有证据表明 `--value-status` 提升存活**；d 臂平均血更低、还多死 1 局。但 n=3、
  单局方差 0–24 血，**这个差异不能当结论**。
- 更关键的限制：**两臂 6 局 floor 全部停在 1**。80 步预算在 1.5–2.5 分钟内用完
  （其中 15–20 步是 `legal` 为空的纯等待），第一层的第一场战斗还没打完。
  「存活层数」这个指标本次完全饱和，A/B 实际只比较了「第一层首战打到一半时的血量」。
- **d 臂确实用上了新信息，不是只改了 flag 名**：d 的 `decision` 日志里，
  WOODEN SWORD 候选带 `effects=0.04–0.07 worth=+0.04–0.07`；d 还实际用了
  CAVE FISH / WOODEN BLADE / ROUGH WHETSTONE（c 臂从未用）。
- **但这点权重改不了决策**：候选基础分 5.5–8.0，效果项只有 0.04–0.07。
  原因在 `Ledger.estimate_effects()`：效果总和除以**该物品的全部使用次数**
  （WOODEN SWORD 样本 228 次），只有少数几次附带 `rage`，均值被摊薄到 0.05。
- 另外两条 `--value-status` 的死路：
  - `luck` 只在 `end_turn` 的回执里出现，而账本只对 `use_item` 记账 →
    `luck:*` 永远不会被学到（本次 d 账本里没有 luck 键）。
  - `max_hp` 在 6 局里一次都没出现（见 §4）。

## 2. 宝箱链路：`open_map → close_map → StartEvent` **被证伪**

### 2.1 找到的是真宝箱

- 第 1 层（floor=1）地图上找到 `d-61556`，`type_name="Chest"`，房间 `r-61012`，
  `finished=false`，`passable=true`。
- 从入口一路打到宝箱房间（途中两场战斗，最后 hp=3/40），到房间后
  `legal` 里是 `{"type":"start_event","event_type":2,"event_type_name":"Chest"}`。

### 2.2 三步实测（证据：`data/runs/chest-proof-20261028-164041.json`）

| 步骤 | 动作 | ok | changed | ms | delta | viewing_top |
|---|---|---:|---|---:|---|---|
| 起点 | 地图已开 | — | — | — | — | 1 |
| A | `start_event`（地图开着） | false | false | 408 | `{}` | 1 |
| B | `close_map` | true | **true** | 500 | `{"viewing_top":[1,0]}` | **0** |
| C | `start_event`（地图已关） | false | **false** | 400 | `{}` | 0 |

C 之后 `popups=0`、`dialogue_choices=-1`、`seq` 不变（2011→2011）；
隔 1 分钟再连发两次 `start_event` 仍是 `changed=false, ms≈410`。
**所以「close_map 恢复事件输入」对 Chest 不成立**，第三级「直接置 viewingTop」只是把
状态拨回 0，没有恢复事件入口。

### 2.3 反编译看门在哪（`Assembly-CSharp.dll`，Mono.Cecil）

`DungeonPlayer.StartEvent()` 的 IL 里**根本没有读 `viewingTop`**：

```
GetRelevantEvent(player.transform.position) -> DungeonEvent
if (ev == null) return;                       // ← 第一个门
if (Singleton.Instance.autoCloseMap) ShowInventory();
dungeonEvent = ev;
ev.Play();
StartCoroutine(WalkIntoEvent());
```

`DungeonEvent.Play()` 开头还有两个提前 return：

```
if (GetEventProperty(EventProperty.Type.open)) return;   // Type 6 == open
if (itemsToSpawn.Count == 0) return;                     // ← 第二个门
```

`IsFinished()` 看的是 `EventProperty.Type.finished`（0）；本次宝箱 `finished=false`。

而 `Chest` 自己有一整套入口（public）：`OnMouseUpAsButton()` / `OnPressStart()` /
`OpenChest()`。`OpenChest()` 里才会 `dungeonEvent.FinishEvent()`、按
`EventProperty.Type.mimic(3)` 判伪装宝箱、生成战利品。

**结论**：`StartEvent()` 不是宝箱的正确入口。宝箱 no-op 至少有两层原因：
① `GetRelevantEvent()` 没找到事件，或 ② `Play()` 因 `open` 属性 / `itemsToSpawn` 为空提前返回。
仅凭现有快照分不出是哪一层——这正好是下一步加**只读诊断 action** 的理由；
真要开箱，应该加 `open_chest` 调 `Chest.OpenChest()`（v8，先退游戏再部署）。

### 2.4 对旧结论的修正

`SPIKE-08 §8` / `HANDOFF` 里「地图开着时 `StartEvent()` 是空操作，`viewingTop` 是门」
与 IL 不符：`StartEvent()` 不读 `viewingTop`。之前观察到的 no-op 更可能是
**事件类型自己的原因**（SimpleClickItem 本来就没有 StartEvent 行为；Chest 走 Chest 入口）。
`close_map` 本身是有效的（`viewing_top 1→0` 已两次实测），但它不是宝箱的钥匙。

## 3. 整理阶段（PITFALLS E9）

- 6 局里一共 **14 次** `finish_reorganize`，**14 次全部 `changed=true`**，
  耗时 724–1991 ms，没有一次 20 s 超时。
- 所有 `inventory_phase==3` 的 decision 快照（14 个）都是
  `in_special_reorg=true, skippable_reorganization=false`——**本次没有触发 E9**
  （E9 的条件是 phase=3 且 in_special_reorg=false）。
- 唯一一次 20 s 超时是 `open_map`（胜利画面 `inventory_phase=8` 时），
  先 `level_up` + `finish_level_up` 后恢复正常，与整理阶段无关。
- 按交接单做了 loop 侧兜底（`agent/loop.py`，只影响 `--auto-retry`）：
  连续 2 次 `finish_reorganize` 仍 no-op → `retry_run` 重开，记
  `{"event":"reorg_retry", ...}`；**没有**硬置 `inventoryPhase`。
  本次 A/B 没带 `--auto-retry`，所以这条路径未被走到（也没被验证过）。
  离线测试 `agent/tests/test_v0.py` 仍 ALL PASS。

## 4. 玩家侧状态样本（尾巴三）

- **拿到了 player 侧 `status` 回执**：
  - 敌人 `end_turn` 给玩家上 `fire`：`{"kind":"status","from":"enemies","to":"player","status":"fire","delta":2,"value":2}`；
  - `CAVE FISH`：`{"kind":"status","from":"CAVE FISH","to":"player","status":"regen","delta":4,"value":4}`，
    账本记到 `status:regen:player`（count=1, sum=4）。
  - 还观察到一次归属怪异：WOODEN SWORD 击杀带火敌人后，回执把 `fire -2` 记在
    `from:"WOODEN SWORD"` 名下（`delta` 本身是对的，只是 from 语义偏了）。
- **`max_hp` 仍是 0 个样本**；`luck` 只在 `end_turn` 回执里出现
  （`luck_uncommon/rare/legendary`），且账本不记 `end_turn` → `luck:*` 学不到。
- **6 局里没有出现 HEAT POTION / CANDY**（全日志搜索无结果），所以这两件物品的
  玩家侧 `max_hp/luck` 样本仍未拿到；尾巴三只能算完成一半。

## 5. 产物

| 文件 | 内容 |
|---|---|
| `data/runs/ab/ab-c-full-{1,2,3}.jsonl` | c-full 三局原始日志（含 decision 快照） |
| `data/runs/ab/ab-d-value-status-{1,2,3}.jsonl` | d 三局原始日志 |
| `data/runs/ab/ab-retry-summary.json` | 6/6 ok 的编排结果 |
| `data/runs/ab/ab-report.json` | `run_report.py --json` 的机器可读结果 |
| `data/runs/chest-proof-20261028-164041.json` | A/B/C 三步的完整 state + ack |
| `data/runs/ab/archive-20261027/` | 10-27 的旧 ab 日志（已移出 glob，避免污染汇总） |
| `data/runs/chest-il/` | 本次反编译的 IL 片段（StartEvent/GetRelevantEvent/Play/Chest/EventProperty 枚举） |
| `.scratch/bph_drive.py` / `chest_proof.py` | 到宝箱房间的驾驶脚本与三步验证脚本（临时目录，未入库） |

## 6. 建议的下一步（按优先级）

1. **宝箱**：加只读诊断 action（输出 `GetRelevantEvent` 结果、`itemsToSpawn.Count`、
   `GetEventProperty(open)`、当前 room），确认 no-op 是哪一层；再加
   `open_chest` 调 `Chest.OpenChest()`，重新跑一次「地图开→close_map→open_chest」。
2. **A/B 加深**：80 步预算被「等回合」吃掉，floor 饱和在 1。下次要么把
   `--max-steps` 提高（例如 240），要么把 loop 的等待步计数改掉；否则 A/B 只能比血量。
3. **value-status 权重**：0.04–0.07 的效果分不可能影响决策。要么提高 `STATUS_VALUE`、
   要么用「触发时的值」而不是「全部使用次数平均」，要么先把 `luck` 记进账本
   （目前只在 end_turn 出现，永远学不到）。
4. **E9 兜底**：本次没触发，`retry_run` 兜底路径未在真机走到；下次遇到 phase=3 且
   `in_special_reorg=false` 时看 `data/runs/*.jsonl` 里的 `reorg_retry` 事件。
