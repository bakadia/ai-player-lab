# RESULT · 2026-10-29 · P1 place_item 打通（3×3 与 13 格都真机验收）

> 接 `HANDOFF-NEXT-20261028-2200.md` §3。结论：**`place_item` 已经能把物品真正放进
> 主背包**——`cells` 落在背包格子里、`parent=Grid Parent`、空格数下降、画面可见。
> 3×3（9 格）与升级后的 13 格都跑过，含开箱战利品。
>
> 桥接 **v8.11**：**103,424 bytes，SHA256 `721509DE…`**（部署在 Mods；v8.6 备份留在
> `Mods\BphBridge.dll.v8.6`）。`Receipt.cs` 没有改。

## 0. 一句话

`EndDrag()` 第一件事就是把 `transform.position` 拉回 `startPosition` 再 `AddToGrid`，
所以「移到目标格再 EndDrag」永远无效。**改 `startPosition` 就通了**：

```
StartDrag() -> transform.position = target -> startPosition = target -> EndDrag()
```

真机：教程 3 件（3 格剑 + 4 格盾 + 2 格饭）依次放进 3×3，`free 9→6→2→0`，
`cells` 全在 `-1..1`、`parent=Grid Parent`、背包画面满格（截图见 §2）。
随后把背包扩到 **13 格**，开箱的 3 件战利品同样进了背包（`free 3→0`）。

## 1. 根因与 IL（都在 `data/runs/il-place/`）

### 1.1 EndDrag 会重置（决定性）

`ItemMovement.EndDrag()`：

```
if (!itemType.Contains(32)) {
    transform.position = startPosition;
    transform.rotation = startRotation;
    AddToGrid(false);       // -> SnapToGrid() + TestAtPosition 占格判定
}
```

`StartDragWithoutChecks()` 末尾才写这两个字段：

```
startRotation = transform.rotation;
startPosition = transform.position;
isDragging = true;
if (inGrid) RemoveFromGrid();
```

所以正确顺序只能是 **StartDrag → 移到目标 → 改 startPosition → EndDrag**。
新增的 `via=start`（现为默认）就是这么做的，`rot=identity` 可选（默认保持当前朝向，
因为候选格形状是按当前朝向算的）。

### 1.2 形状预测曾经多算一格（第二个坑）

第一版修好 `startPosition` 后，剑预测「放得下」却落到 `-1,2`（出界）。原因在
`GridObject.GetGridPositionsAtPosition(position)`：它内部
`offset = transform.position - position`，对齐差用 `collider+offset`、格子却用
`collider-offset`，**物品离目标远时预测会多算一格**（实测 3 格剑预测成 4 格）。

修法：`Actions.PredictCells()` 先把 `transform.position` 挪到目标（offset=0，和
EndDrag→SnapToGrid 的条件一致），算完再挪回去。改完预测与落点完全一致。

### 1.3 验收门也必须换

`items_in_grid` 是 `ItemMovement.inGrid`，`AddToGrid` 在**判断格子够不够之前**就置 true，
包外（Items 容器 4,2/10,2）也 +1。现在改成
`Actions.ItemInBag(uid)`：`cells ⊆ GridSquare.allGrids` 且 `parent == Grid Parent`。
`place_item` 的 ack 只在真的进包时 `ok=true`，超时会明确写
`item not in bag (cells/parent)`。

## 2. 真机验收（3×3，P1 主目标）

`data/runs/place-20261029-3x3-and-13.json` 有完整记录。三件依次放：

| 物品 | 请求格 | 实际 cells | parent | in_bag | free |
|---|---|---|---|---|---|
| WOODEN SWORD | `-1,0` | `-1,-1` `-1,0` `-1,1` | Grid Parent | true | 9→6 |
| ROUGH BUCKLER | `0,0` | `0,0` `0,1` `1,0` `1,1` | Grid Parent | true | 6→2 |
| MEAL | `0,-1` | `0,-1` `1,-1` | Grid Parent | true | 2→0 |

每次 `ok=true changed=true ms≈500`。独立读 `state.json`：

```
items_in_grid 3 / items_loose 0 / free_squares 0 / grid_squares 9
cells 全在 -1..1，parent 全是 Grid Parent
```

背包画面：剑占左列、盾占右上 2×2、饭占底行两格，**满格可见**（本次会话截图）。

> 注：交接单写的「free 9→6」对应**第一件（3 格剑）**；三件合计 9 格，所以最终
> `free=0`。这是物品形状（3+4+2）决定的，不是叠放。

同一套动作连跑 3 局教程，落点每次都一样——可复现。

## 3. 13 格（升级扩容）

### 3.1 扩容入口不是 ChangeSpaces

- `ChangeSpaces(num)` 只是 `numberToUnlock += num`（**加配额**）；
- `Select()` = `ChangeSpaces(-1)` + `SetupSpaces()`（**花一个配额**）；
- 真正把格子并进背包的是 `GridExtension.Click()`（点一个 = 花 1 个配额 + 该格并入）。

老代码 `finish_level_up via=spaces` 把 `numberToUnlock` 当参数传给 `ChangeSpaces`，
每收一次升级就翻一倍，实测把它从 1 刷到 **256**（界面永远「选择 N 个格子」）。
已改成和 `select` 一样走 `Select()`。

### 3.2 新开局配额恒为 0

`<LevelUp>d__39` 对 rewardType=0/2 都是 `numberToUnlock = spacesSaved`，而
`spacesSaved` 只在 `EndLevelUp` 里被赋成 `numberToUnlock` —— 新开局就是 0，
界面显示「选择 0 个格子」，点哪个 `GridExtension` 都没反应。

为了把背包扩到 13 做验收，加了**测试钩子** `grant_spaces n`（只调游戏自己的
`ChangeSpaces(n)` + `SetupSpaces()`），再用 `unlock_spaces n` 点真实 GridExtension：

```
grant_spaces 4 -> unlock_spaces ×4（一次点一个）-> clear_spaces -> finish_level_up via=end
grid 9 -> 13
```

要点：

- **一次只点一个**。Click 后 `SetupSpaces` 会重建候选对象；同一帧连点会点到已销毁的
  extension（配额扣了、格子没加）。动作之间本来有 ~0.5s settle，正好让新对象注册。
- 新格子是在 **EndLevelUp 时**才真正并入的（`delta.grid_squares` 在 finish 的 ack 里
  才出现），所以 `unlock_spaces` 的 `changed` 不一定为真——`Snapshot` 已补
  `GridSquares` 字段，扩容本身也能判 changed。
- **必须清余额**：`CanLevelUp() = (xp够 || spacesSaved>0) && !levelingUp`，没花完的配额
  会让升级界面无限重开，而且每次 `LevelUpButton()` 都会 `IncrementLevel()` 把等级刷高。
  `unlock_spaces` 在配额归零时会把 `spacesSaved` 清零；另有 `clear_spaces` 手动兜底。

### 3.3 13 格真机

- 扩容后 `grid_squares=13`，13 个格子：
  `-3,-1 / -2,-1 / -1,-2 / -1,-1 / -1,0 / -1,1 / 0,-2 / 0,-1 / 0,0 / 0,1 / 1,-1 / 1,0 / 1,1`
  （这次扩容加在左侧/下方，和 10-28 手动那次的形状不同——扩容位置取决于点的哪个
  GridExtension，不是固定模板）。
- 开箱拿 4 件（BLESSED POTION / DEBUFF POTION / ROSE OF THORNS / WOODEN BLADE），
  前 3 件单格全部进包：`cells` 分别是 `0,-2` / `-1,-2` / `-3,-1`，
  `parent=Grid Parent`，`free 3→2→1→0`。
- 第 4 件 WOODEN BLADE（2 格）**没有硬塞**：`no fitting cell (freeGridSquares=0 ...)`，
  ack 明确失败。这正是新验收门的意义——不会再「默默放到包外还报成功」。
- 最终 `state`：`grid_squares 13 / free 0 / items_in_grid 6 / items_loose 0`，
  6 件的 `cells` 全在 13 格内、`parent` 全是 `Grid Parent`。
  存档：`data/runs/state-v87-bag13-20261029.json`。

## 4. 已知坑（这次踩到的）

1. **`containsItem` 会滞后**：消耗掉的物品留下的格子仍报 occupied。13 格那局
   `free=3` 时实际有 4 个空格（第 4 件 2 格仍放不下，因为需要连续两格）。
   `SquareOpen` 同时看 `containsItem` 和 `TestForItem`，只能缓解。
2. **地图视觉关不掉**：`viewing_top` 能 1→0，但地图那层动画不一定收起（背包被盖住）。
   这是老问题，验收时以 `state` + diag 为准；3×3 那次地图是关着的，所以截图干净。
3. 不可再用的 `via` 分支（look/gm/stopdrag/snap/parent_snap）保留着做对照，但都不是
   正确路径；默认 `via=start`。

## 5. 改动清单

| 文件 | 内容 |
|---|---|
| `bridge/src/Actions.cs` | `place_item via=start`（改 startPosition）、`PredictCells`、`ItemInBag`/`BagCellSet`/`CellOfSquare`/`CountFreeSquares`/`CountGridSquares`、`cell` 指定落点、`diag_place`、`unlock_spaces`、`grant_spaces`（测试钩子）、`clear_spaces`、`Snapshot.GridSquares`、`finish_level_up via=spaces` 修正 |
| `bridge/src/BridgeMod.cs` | `place_item` 验收门换成 `ItemInBag(uid)`；超时提示写明 cells/parent |
| `agent/loop.py` | `--unlock-spaces N` / `--grant-spaces N`；扩容一次点一个；`MAX_LEVEL_ATTEMPTS 6→12`；ack 里读 `diag` |
| `tools/bph_act.py` | 新增：发任意动作并打印 ack/diag（调 place_item/diag 用） |
| `tools/dnmap/Ildump.cs` | 新增：Mono.Cecil IL dumper（`Ildump.exe <asm> <type> [methods]`） |
| `bridge/build.ps1` | 未改（曾试加 Physics2DModule 引用，CS1070 不认，已回退；collider 诊断改反射） |
| `data/runs/place-20261029-3x3-and-13.json` | 本轮全部落点/验收数据 |
| `data/runs/state-v87-bag13-20261029.json` | 13 格 + 6 件物品的完整 state |
| `data/runs/il-place/` | EndDrag/StartDrag/SnapToGrid/LevelUp/GridExtension 等 IL |

## 6. 验证

- `bridge/tests/run-tests.ps1` → ALL PASS
- `agent/tests/test_v0.py` → ALL PASS
- 构建：`powershell -File bridge/build.ps1` → 103,424 bytes；部署 SHA256 与构建一致
  （`721509DE…`）
- 真机：3 局教程摆 3 件（每局 `free 9→0`、`in_bag=true`）；一局扩到 13 格后开箱摆 3 件
  （`free 3→0`）；满包时第 4 件被明确拒绝。

## 7. 建议下一步

1. **13 格之后的常规化**：`grant_spaces` 是测试钩子，正常升级应该走游戏自己的
   「选格子」奖励；需要搞清 `spacesSaved` 的正常来源（或确认这版的 0 是游戏 bug）。
2. **背包满时的 move/rotate/drop**：13 格满后第 4 件只能丢；需要 `move_item(uid, cell)`、
   `rotate_item(uid)`、`drop_item(uid)`（`GridObject.SnapToGrid` + `startPosition` 同一套）。
3. **`containsItem` 滞后**：`free_squares` 偶尔少报 1，清理逻辑要先重算或按 `TestForItem`。
4. **地图视觉**：`close_map` 的按钮路径没真正触发动画，截图验收要留意遮挡。
