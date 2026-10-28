# HANDOFF · 下一步：A/B 与三个尾巴

> 2026-10-28 15:20 · 写给**下个会话**。
> 当前代码/部署状态见 `HANDOFF.md` §0；回执设计见 `SPIKE-09-combat-receipt.md`。
> 这篇只讲「接下来怎么干」，不重复已经确认过的事实。
>
> **2026-10-28 17:00 更新**：本单的 A/B 已跑完（6/6），宝箱链路**被证伪**
> （`close_map` 能关地图，但真 Chest 的 `start_event` 关图后仍 no-op），
> E9 本次未触发。结果与证据见 `RESULT-20261028-ab-chest.md`；先读那篇，别重跑。
>
> **2026-10-28 22:00 更新**：P0 已真机验证，桥接 **v8.6**（94,720 bytes，
> SHA256 `E03D1E20…`，已部署）：`open_chest` 能真开宝箱（`items_total +4`）。
> P1 只差落点：`place_item via=bag` 把 parent 设成了 **`Grid Parent`**（正确），
> 但 `EndDrag()` 会先把 transform 重置到 `startPosition` 再 AddToGrid，
> 所以 cells 仍在包外（`-6..-3`）。下一步改 `startPosition` 或 `MoveItemTowardsGrid`。
> 主背包升级后是 13 格（`GridSquare.allGrids`），不是 pouch；`Items` 是包外容器。
> 细节见 `RESULT-20261028-p0-p1.md` 与 `HANDOFF-NEXT-20261028-2200.md`。

## 0. 一句话

**工程改动不大，运行量很大。** 主要工作是跑 6 局 A/B（约 30–90 分钟），
外加三个小尾巴：宝箱链路、整理阶段兜底、玩家侧状态样本。
桥接不用再改（除非宝箱链路证伪）；**不要重新设计回执**。

## 1. 当前状态（接手先核对）

| 项 | 值 |
|---|---|
| 桥接 | v7 已部署，85,504 bytes，SHA256 `2EB092F0ABBF3FA45C9C05B6AE4279D9E7400F3D23BC3692815ADF4B986BA652` |
| 游戏 | 已关闭（不要假设它在跑） |
| 工作区 | `D:\projects\ai-player-lab`，游戏目录 `E:\steam\steamapps\common\Backpack Hero` |
| 离线测试 | `python agent\tests\test_v0.py` → ALL PASS；`bridge\tests\run-tests.ps1` → ALL PASS |
| 账本 | `data\items.json` 已清掉旧 0/0 的 HEAT/CURSED POTION、MEAL；只剩 SPOILED MILK / WOODEN SWORD |
| 回执 | 每个 ack 带 `receipt`；`--value-status` 默认关；`d-value-status` 臂已加进 `tools\ab_runs.py` |

## 2. 第一优先：A/B（先改脚本，别直接跑）

### 2.1 为什么不能直接 `ab_runs.py`

`tools\ab_runs.py` 的 `restart_game()` 调 `bph.cmd restart`，
而 agent shell **停不掉游戏**（HANDOFF §0.3）。它的 `--no-restart` 又要求
游戏先在主菜单，可一局结束后游戏停在 `dead`/`intro`/`map`，不是菜单。

### 2.2 正确做法：用 `retry_run` 代替重启

`retry_run` 已在 v7 实测：
- 死后 `ok=true changed=true`，新局 `hp=40 floor=0`；
- 局中也能重开；
- 回执带 `reset:true`，不会被误算成战斗。

所以**一局跑完（死/卡死/步数用完）→ `retry_run` → 直接跑下一局**，
不需要关游戏、不需要 `bph.cmd`。这是上一班验证它的真正用途。

实现（`tools\ab_retry.py`，已写好）：

> **2026-10-28 15:20：脚本已经写好并 dry-run 通过**
> （`python tools\ab_retry.py --arms c-full d-value-status --runs 3 --dry-run`）。
> 它按下面的逻辑工作，但没有在真机上端到端跑完 6 局——第一次跑请盯前两局。
>
> ```powershell
> python games\backpack-hero-aipeiwo\tools\ab_retry.py `
>   --arms c-full d-value-status --runs 3
> ```
>
> 脚本会在每臂第一局前从 `data\items.json` 播种独立账本；之后每局用 `retry_run`
> 拿干净局，不重启游戏。A/B 结果写到 `data\runs\ab\ab-<arm>-<n>.jsonl`，
> 汇总写到 `ab-retry-summary.json`。

实现逻辑：

1. 读 `runtime/state.json`：
   - `mode=menu` → 调 `tools\start_run.py` 进第一局；
   - 已在局内 → 直接用（或先 `retry_run` 拿一个干净局）。
2. 对每个 arm / 每个 run：
   - 每臂第一局前 `shutil.copyfile(data\items.json, data\ledgers\<arm>.json)`（臂内累积）；
   - 第 1 局之外：`bridge.act({"type":"retry_run"}, timeout_ms=30000)`，
     断言 `changed=true` 且新 state 在 `intro/map/battle`；
   - 跑 `loop.py --ledger <arm> ...flags`，把 `loop-*.jsonl` 改名为 `ab-<arm>-<n>.jsonl`；
   - 失败就记录，不要整批停。
3. 跑完打印 `run_report.py` 的命令；结果在 `data\runs\ab\ab-retry-summary.json`。

`retry_run` 失败时的兜底：CUA `launch_app` 重开 + `start_run.py`。
不要退回 `bph.cmd restart`。

### 2.3 臂怎么选：**不要 baseline vs d**

`d-value-status` = c-full + `--value-status`。拿 baseline 对比它，
会把 `aware_block/ratio_energy/explore_threat/value_energy` 的效果混进来，
无法回答「回执估值有没有用」。

正确对照：

```
c-full          = --aware-block --ratio-energy --explore-threat --value-energy
d-value-status  = c-full + --value-status
```

跑 **c-full vs d-value-status，每臂 3 局**（共 6 局）。baseline 只作为历史参照，
不参加这次归因。

### 2.4 执行

```powershell
# 1) 启动游戏（CUA；不要从 agent shell 直接 Start-Process）
#    cua_driver_native__start_session -> launch_app E:\...\Backpack Hero.exe
# 2) 跑 A/B（脚本待写；下面是一局的手工版）
$env:PYTHONIOENCODING='utf-8'
python games\backpack-hero-aipeiwo\tools\start_run.py
python games\backpack-hero-aipeiwo\agent\loop.py --max-steps 80 --max-seconds 900 `
  --ledger games\backpack-hero-aipeiwo\data\ledgers\c-full.json `
  --aware-block --ratio-energy --explore-threat --value-energy
# 3) 下一局
python -c "import sys;sys.path.insert(0,r'games/backpack-hero-aipeiwo/adapter');from client import Bridge;print(Bridge(r'games/backpack-hero-aipeiwo/runtime').act({'type':'retry_run'},timeout_ms=30000))"
# 4) 报告
python games\backpack-hero-aipeiwo\tools\run_report.py --dir games\backpack-hero-aipeiwo\data\runs\ab --glob "ab-*.jsonl"
```

看什么：存活层数、剩余血量、存活回合、零伤回合占比、BUCKLER 使用次数；
另外从 `act.receipt.effects` 里数 `status:` / `max_hp` / `luck` / `item_status`
的出现次数，确认 `d` 臂真的用上了新信息（不是只改了 flags 名字）。

## 3. 尾巴一：close_map 的宝箱链路

现在只证明了 `close_map` 能把 `viewing_top` 从 1 拨到 0；
**没有证明「地图开着 → close_map → StartEvent 成功」**。
上一班试的 `SimpleClickItem` 对 `StartEvent` 本来就是 no-op，不能当证据。

复现步骤：

1. 进一局，到 floor ≥ 1，打开地图；
2. 在 `state.map.events` 里找 `type_name="Chest"` 且对应房间 `visible=true`、
   `legal` 里有 `move_to_room` 的；
3. 走到那个房间（如果走不到，先探相邻房间把地图揭开）；
4. 手动 `open_map`（把 `viewing_top` 弄成 1）；
5. `start_event` —— 预期 no-op（记录 ack）；
6. `close_map` —— 预期 `viewing_top 1→0`；
7. 再 `start_event` —— **预期 changed=true / 弹出宝箱对话**。

如果第 7 步仍然 no-op：说明第三级「直接置 viewingTop」只改了状态、没恢复事件输入，
需要看 `DungeonPlayer.StartEvent()` 和 `DungeonEvent` 的其它门（例如 `eventButton`、
`RandomEventMaster`、`viewingEvent`），不要继续调 `close_map`。
这一步只读/单动作验证，不要改 loop 的拉黑逻辑——现在 loop 的顺序已经改成「先 close_map，
两次失败才拉黑」。

## 4. 尾巴二：整理阶段兜底

现状：`inventory_phase=3` 但 `inSpecialReorg=false` 时，
`FinishReorganizeButton()` 第一行 return（PITFALLS E9）。
偶发一次重试能过；连续 false 会 20s × N 超时，最后被 loop 判卡死。

两个可选方案（按推荐顺序）：

1. **loop 侧兜底（最小改动）**：连续 2 次 `finish_reorganize` 失败后，
   若 `--auto-retry`，调 `retry_run` 重开，记 `{"event":"reorg_retry"}`。
   优点：不改桥接、不碰游戏清理逻辑；缺点：丢掉当前局。
2. **桥接侧诊断/强制**：加一个只读 action 输出
   `inSpecialReorg / itemsForSpecialReorganization / itemsReplacedBySpecialReorgItem`；
   确认是「游戏把 inSpecialReorg 清了」还是「本来就不是同一种整理」。
   **不要**直接置 `inventoryPhase=0` 跳过 `ClearAreas.../RemoveItemsOutsideGrid/ChangeCurse`。

做完任一方案都要重新部署（v8）并跑一次会触发整理阶段的对局。

## 5. 尾巴三：玩家侧状态样本

回执的玩家侧 `status` / `max_hp` / `luck` 代码路径与敌人侧共用 `WriteStatuses`，
敌人 `rage` 已实测；但玩家侧还缺一个真机样本。
拿到 HEAT POTION / CANDY / 带 buff 的消耗品时，确认 ack 里有：

```json
{"kind":"status","to":"player","status":"...","delta":N,"value":N}
```

以及账本 `effects` 出现 `status:...:player` / `max_hp` / `luck:...`。
如果用了却还是没有，先看 `receipt.effects` 是否为空，再查
`Snapshot.PlayerStatuses` / `PlayerMaxHp` / `Luck*` 是否读到了值。

## 6. 不要重做的事

- 不要重新设计/重写 `Receipt.cs`；伤害/格挡/状态/生成物品/整理阶段都已覆盖。
- 不要恢复「被动饰品进 legal」；`CanBeUsedActive=0` 是硬门。
- 不要用 `diff != "{}"` 判 changed；E4。
- 不要把 `retry_run` 的回执当战斗回执；它带 `reset:true`。
- 不要在 `.ps1` 里写中文；E6。
- 不要从 agent shell 直接启动游戏；用 CUA。
- 不要用 `bph.ps1` 的 `dll lock` 判断游戏在不在跑；受限 shell 是假阳性，用读打开。

## 7. 相关文件

| 文件 | 用途 |
|---|---|
| `docs/HANDOFF.md` | 总体状态、能力表、环境约束 |
| `docs/SPIKE-09-combat-receipt.md` | 回执字段、账本键、真机样本、局限 |
| `docs/PITFALLS.md` | E9（整理阶段 no-op）、E10（被动饰品） |
| `tools/ab_runs.py` | 现有 A/B 脚本（restart 路径对 agent 不可用；臂定义可复用） |
| `tools/ab_retry.py` | **本次新增**：用 `retry_run` 代替重启的 A/B 编排；dry-run 通过，未端到端跑过 |
| `data/runs/loop-20261028-133045.jsonl` | `retry_run` 死后重开的证据 |
| `data/runs/loop-20261028-142800.jsonl` | `amount/blocked`、`end_turn` 回执证据 |
| `data/runs/loop-20261028-143513.jsonl` | `items:spawned` + `phase` 回执证据 |
