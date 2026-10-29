# RESULT · 2026-10-29 · move/rotate/drop 接进策略 + swap + 按朝向搜落点

> 接 `HANDOFF-NEXT-20261029-0100.md` §7。三件事都做完了：
> **`move_item`/`rotate_item`/`drop_item` 进了策略**（`legal` 有了腾位候选、
> `loop.py --make-room N` 会腾位/丢东西）、**`swap_items` 真机能换**、
> **按朝向搜落点**（4 朝向 × 所有空格，`probe_fit` 一次拿全表）。
>
> 桥接 **v8.15**：**117,760 bytes，SHA256 `B162071B…`**（已部署，见 §6）。
> 提交：**`6540d33`**。
> `bridge/tests/run-tests.ps1`、`agent/tests/test_v0.py`、
> `agent/tests/test_backpack.py` 全 ALL PASS。

## 0. 一句话

背包几何搬到 **agent 侧**了：桥接加一个只读的 `probe_fit`，把
「每件物品 × 4 朝向 × 每个背包格子」的 footprint 一次吐出来；
`agent/backpack.py` 拿这张纯数据表做搜索（落点 / 挪 / 换 / 重排 / 丢谁），
`agent/place.py` 负责把计划按顺序发出去。桥接只加原语：
`probe_fit`、`place_item{,rot_deg}`、`move_item{,rot_deg}`、`rotate_item{,rot_deg}`、
`swap_items`。**`place_item` 的「按当前朝向扫空格」不再是唯一选择。**

## 1. 为什么这么切（几何在游戏里，搜索在 agent 里）

`PredictCells` 只有在游戏里才算得动（要 collider + transform）；如果搜索放在
C#，「4 朝向 × 所有格子 × 每件物品」每次都要现场算上百次。放到 agent 侧又有
一个格子问一次的问题。所以：

- **桥接**：一次 `probe_fit` 把整张表吐出来（只留 footprint 整块在包内的落点，
  形状相同的朝向自动去重——2×2 的盾只有 `rot 0`）。
- **agent**：`agent/backpack.py` 全是集合运算，能离线单测（真机 probe 存成
  `data/runs/probe-*.json` 当 fixture）。
- **执行**：`agent/place.py` 把计划变成一串动作，每步核对 `changed`，
  不符合就重探一次（背包和计划不一致时不会硬发）。

## 2. 具体做了什么

### 2.1 `probe_fit`（新，只读）

```
python tools\bph_act.py '{"type":"probe_fit"}'          # 全部物品
python tools\bph_act.py '{"type":"probe_fit","uid":"i-4298"}'   # 单件
```

回执 `diag` 结构：

```json
{"bag":["-1,-1", ...], "grid_squares":9, "free_squares":9,
 "items":[{"uid":"i-4214","name":"WOODEN SWORD","in_bag":false,"needs":3,"euler":0,
           "cells":["0,-4","0,-3","0,-2"],
           "rotations":[{"rot":0,"placements":[{"cell":"-1,0","cells":"-1,-1;-1,0;-1,1"}]},
                        {"rot":90,"placements":[{"cell":"0,0","cells":"-1,0;0,0;1,0"}]}]}]}
```

真机（9 格空包 + 3 件教程物品）：MEAL `needs=2` 两朝向各 6 个落点、
BUCKLER `needs=4` 只有 1 个朝向 4 个落点（2×2 对称去重）、
SWORD `needs=3` 两朝向各 3 个落点。**表和实际落点完全一致**（§3.1）。

### 2.2 `place_item` / `move_item` 现在收 `rot_deg`（绝对角度）

`rot_deg` 和 `cell` 一起用就是「把物品按这个朝向放到这个锚点」。
`place_item` 的 `cell` 处理提到「按形状扫空格」之前——以前满包时
连指定落点都会被 `no fitting cell` 拦掉。`rotate_item` 仍支持相对 `times`。

### 2.3 `swap_items {a,b,a_cell?,a_rot?,b_cell?,b_rot?}`

一条动作里完成：**摘下 A（A 的旧格算空）→ B 进 A 的旧位置 → A 进 B 的旧位置**。
第 3 步不合法就回滚 B 再回滚 A，不留半截状态。踩到两个游戏机制（见 §5、PITFALLS E12/E13）：

- 不能 `StartDrag(A)` 后 `StartDrag(B)`——同一时刻只允许一件物品处于拖拽态；
- 也不能 `RemoveFromGrid(A)`——它会起协程把 `itemMovementManager.isRunning` 置真，
  同一帧的 `StartDrag` 全被拒。

最终实现用自建的 `LiftFromGrid`（只改状态）+ `AddToGrid`（public，EndDrag 的最后一步），
不依赖拖拽态。

### 2.4 `legal` 里的腾位候选（`MapProbe.WriteLegal`）

老的 2.9 段在 `free == 0` 时**一个 `place_item` 都不给**，agent 连试的机会都没有。
现在：

- 每件地上的东西都给 `place_item`，带 `needs`（要占几格）、`free`、`fits`（便宜的提示，
  `free >= needs` 只是必要条件）；
- 有东西放不下时，追加 **`drop_item`**（每件背包物品一条，带 `size`/`is_relic`/`gold`）
  和 **`move_item`/`rotate_item`**（背包物品各一条，不带 cell——桥接自己找落点）；
- 战斗分支（`StateReader`）同样补了 `needs/free/fits` + 三件套候选；
- 删掉了原来重复的第 6 段（同一批 loose 物品又列了一遍 `place_item`）。

legal 里**不展开** 4 朝向 × 所有格子（那是几百条）；具体怎么腾由 planner 算。

### 2.5 `loop.py --make-room N`

`place_item` 不再直接发给桥接，而是走 `agent/place.py`：

```
probe_fit → plan_place ─┬─ impossible : 形状本身塞不进去（丢东西也没用）→ 放弃
                        ├─ needs_drop : 还有 N 次额度 → 丢最低价值的一件 → 重探
                        └─ steps      : 先发挪/换，每一步都要 changed，最后 place_item
```

价值用账本均值（`damage + block + effects`）；**没样本的物品算 1.0 而不是 0**
（否则它们永远第一个被丢）；名字像 AMULET 的再加 3。

`--make-room 0`（默认）= 不丢东西，放不下就放弃（和以前一样）。
`policy.py` 里把 `move/rotate/swap/drop` 的分数钉成 −1：这些动作只能由腾位计划发，
战斗策略不会自己挑一次「丢东西」。

## 3. 真机验收（都是这一班实际跑出来的）

### 3.1 落点 / 朝向（9 格包，3 件教程物品）

`data/runs/probe-20261029-9cell.json` + 下面每一步的 ack：

| 步骤 | 动作 | 实际 cells | free |
|---|---|---|---|
| 放饭盒 | `place_item cell=-1,-1 rot_deg=0` | `-1,-1` `0,-1` | 9→7 |
| 放盾 | `place_item cell=-1,0 rot_deg=0` | `-1,0` `-1,1` `0,0` `0,1` | 7→3 |
| 放剑 | `place_item cell=1,0 rot_deg=0` | `1,-1` `1,0` `1,1` | 3→0 |

三件 `parent=Grid Parent`、`in_bag=true`，和 probe 表逐格一致。
`tools/bph_bag.py place` 连放三次都是这个落点。

### 3.2 rotate / move 的绝对朝向

`data/runs/primitives-20261029.json`（`tools/bph_verify_primitives.py` 产的）：

| 动作 | cells 变化 | free |
|---|---|---|
| `rotate_item rot_deg=90` | `-1,-1;-1,0;-1,1` → `-1,0;0,0;1,0`（竖→横） | 6→6 |
| `rotate_item rot_deg=0` | → `1,-1;1,0;1,1`（回到竖的，换了一列） | 6→6 |
| `move_item cell=0,0 rot_deg=0` | → `0,-1;0,0;0,1` | 6→6 |
| `place_item cell=-1,-1 rot_deg=90`（饭盒） | → `-1,-1;-1,0` | 6→4 |

`rot_deg=90` 的落点由 `FitTargets` 自己挑（没给 cell），也落在预测的位置上。

### 3.3 swap（关键：中间态）

同一份数据：

| | 换之前 | 换之后 |
|---|---|---|
| WOODEN SWORD（3 格） | `0,-1` `0,0` `0,1` | `-1,-1` `-1,0` `-1,1` |
| MEAL（2 格） | `-1,-1` `-1,0` | `0,0` `0,1` |
| free | 4 | **4** |

`ok=true changed=true`。剑接管了饭盒的旧两格（再多吃一格空地），
饭盒落到剑腾出来的地方——两件都在包里、不重叠、free 一格没多。
**这正是「B 的旧格也算空」的中间态**：如果分两条命令做，第二条一定失败
（A 还占着 B 想去的位置）。

### 3.4 满包腾位（move 路线，真机）

```
bag 9 格 / free 4，地上还有一件 4 格盾：
plan: move_item MEAL -> 0,1 rot0  ;  place_item BUCKLER -> 0,-1 rot0
执行后：free 4 -> 0，盾的 cells = 0,-1 0,0 1,-1 1,0，parent=Grid Parent
```

饭盒从中间列挪到顶行，腾出一个 2×2；盾立刻放进去。**背包 9/9 满格，
一次 drop 都没用**。这条链就是「满包也能捡战利品」的主路径。

### 3.5 整合（loop.py 自己跑）

`python agent/loop.py --max-steps 10 --make-room 1` 在一个全新局里：

```
[step 0] -> place_item i-9152  放 MEAL @-1,-1 rot0  ok=True changed=True
[step 1] -> place_item i-9110  放 BUCKLER @-1,0 rot0 ok=True changed=True
[step 2] -> place_item i-9068  放 SWORD @1,0 rot0    ok=True changed=True
[step 3] -> open_map ... 之后正常开门、进战斗
```

`--aware-block --ratio-energy --explore-threat --value-energy` 那局打赢了一场
（`hp 19`，敌人 gone），说明战斗策略没被新动作干扰。

### 3.6 负例（也是真机）

满包时用错朝向指定落点：

```
place_item MEAL cell=-1,-1 rot_deg=0
-> ok=false changed=false
   error: cell -1,-1 -> footprint hits occupied 0,-1
```

**在建任何状态之前**就被拒了（背包一格没动）。这是把 `cell` 校验提前到
`StartDrag` 之前的结果，以前是「先抬起来再失败」。

## 4. 还没验到的（下一班优先）

> **2026-10-29 16:00 更新**：第 1 条已经补上——`RESULT-20261029-mapnav.md` §2.3/§2.4
> 在真机上把 drop 分支跑通了两遍（`tools/bph_verify_drop.py` 和 `loop.py` 自己）。
> 下面保留当时的记录。

1. **drop 分支没在真机上跑到**。它需要「满包 + 又一件放不下的战利品」，
   而这一班的 9 格包只有 3 件物品（3+4+2=9 刚好满），凑不出第 4 件：
   地图上最近的宝箱房间（`r-5830`）一直不可见，走过去的路上还撞进战斗并阵亡。
   本轮用**离线测试**覆盖了这条链：
   `test_place_with_room_drops_lowest_value`（满包 → 丢 ROCK → 再放，动作序列
   `probe_fit, drop_item, probe_fit, place_item` 全部断言）和
   `test_plan_needs_drop`。`drop_item` 本身在真机上反复验过（§3.2 之前先把 3 件全丢了，
   free 4→7→9、物品变 loose）。
2. **swap 的等形互换**只在离线测过；真机这次是 3 格 ↔ 2 格。
   桥接的 `swap_items` 本身不限形状，是 planner 的 `plan_swap` 先只放了等体积。
3. **repack（从零重排）** 只有离线测试；真机没跑到碎片化到那一步的局面。
4. 战斗里捡战利品（`StateReader` 那条 legal）没真机验过。

## 5. 这一次抓到的两个机制（详见 PITFALLS E12/E13）

1. `RemoveFromGrid()` 在 `GameFlowManager.isCheckingEffects` 时会起
   `RemoveAtEnd()` 协程，把 `itemMovementManager.isRunning` 置真 →
   同一帧 `StartDrag` 全被拒。改成自建 `Actions.LiftFromGrid()`。
2. **同一时刻只能拖一件**，而且 `EndDrag` 不清 `GameManager.draggingItem`
   （要等游戏自己的 `Update`）。所以一条动作里最多 `StartDrag` 一次；
   第二次落位用 `Actions.PlaceLifted()`（`transform.position` + public `AddToGrid`）。
   回滚同理，走 `Actions.RestoreItem()`（摘格子 → AddToGrid）。

两个坑都是靠「把 `StartDrag` 的每一道门打进错误信息」才定位的
（`Actions.StartDragBlocked`）——以前的报错只有 `isDragging=false`。

## 6. 改动清单

| 文件 | 内容 |
|---|---|
| `bridge/src/Actions.cs` | `probe_fit`（`ProbeFitAction`）、`place_item` 的 `rot_deg` + 提前校验 `cell`、`move/rotate` 的 `rot_deg`、`swap_items`（`SwapItemsAction`/`LiftFromGrid`/`PlaceLifted`/`RestoreItem`/`StartDragBlocked`）、`OccupiedCells`/`CheckFootprint`/`SpacesNeeded`/`IsRelicItem`/`ItemGoldCost`、`move/rotate` 的校验改用 `gridPositions` |
| `bridge/src/BridgeMod.cs` | `swap_items` 的 `WaitInBag` 门 |
| `bridge/src/MapProbe.cs` | `WriteLootAndRoom`：`place_item{needs,free,fits}` + `drop_item`/`move_item`/`rotate_item` 候选；删掉重复的第 6 段 |
| `bridge/src/StateReader.cs` | 战斗分支同样带 `needs/free/fits` + 腾位候选 |
| `agent/backpack.py` | 新：probe 数据模型 + `plan_place`/`plan_swap`/`plan_repack`/`choose_drop` + 字符画 |
| `agent/place.py` | 新：`probe_bag` / `place_with_room`（探针→计划→腾位→放，带 `drops_left`） |
| `agent/loop.py` | `--make-room N`、两处 `place_item` 改走 `place_with_room` |
| `agent/policy.py` | `move/rotate/swap/drop` 分数 −1（只能由腾位计划发） |
| `agent/tests/test_backpack.py` | 新：12 个用例（解析/直接放/按朝向/挪一件/只能丢/不可能/互换/重排可执行/丢谁/真机 probe/丢后放/零额度） |
| `agent/tests/test_v0.py` | 加一条「腾位动作不进战斗决策」，并把 `test_backpack` 一起跑 |
| `tools/bph_bag.py` | 新：`show` / `plan` / `place [--make-room N]`（和 loop 同一条链） |
| `tools/bph_verify_primitives.py` | 新：真机跑 drop×3 → place → rotate×2 → move → place → swap，逐步断言并落盘 |
| `data/runs/probe-20261029-9cell.json` | 真机 probe（离线测试的 fixture；入库副本在 `agent/tests/fixtures/probe-20261029-9cell.json`） |
| `data/runs/primitives-20261029.json` | 上面每一步的 ack + cells + free |
| `data/runs/state-pre-loop-20261029.json` | 跑 loop 之前的完整 state（满包 + 3 敌人） |

## 7. 验证

- `powershell -File bridge/build.ps1` → 117,760 bytes
- `powershell -File bridge/tests/run-tests.ps1` → ALL PASS
- `python agent/tests/test_v0.py` → ALL PASS（含 `test_backpack` 的 12 条）
- 真机：§3.1–§3.6
- 部署：`Mods\BphBridge.dll` = 117,760 bytes（游戏退出后覆盖）

## 8. 下一班第一步

先读 `HANDOFF-NEXT-20261029-1400.md` §7。最想补的是 §4.1：
**真机把 drop 分支跑通**（满包 + 第 4 件战利品）。
最快的做法：进一层有 Chest 的楼层，先把包塞满再开箱，然后
`python tools\bph_bag.py place <loot> --make-room 1`。
