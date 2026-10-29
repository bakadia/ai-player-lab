# RESULT · 2026-10-29 · 背包内 move / rotate / drop（满包能腾位了）

> 接 `RESULT-20261029-place-item.md`。`place_item` 只能在真实空格里落子，满包就没辙；
> 这一步补上 **`move_item(uid, cell)` / `rotate_item(uid, cell?, times)` /
> `drop_item(uid)`**，并在 13 格真机上跑通「移动 → 旋转 → 丢弃 → 放回」整条链。
>
> 桥接 **v8.14**：**108,544 bytes，SHA256 `8BF1CAE1…`**（已部署）。

## 0. 一句话

三个动作都是同一套机制：**先 `StartDrag()` 把物品从背包逻辑上摘下来**
（`RemoveFromGrid` 会清 `gridPositions`、`parentInventoryGrid=null`、`inGrid=false`），
再按 place_item 的顺序 `移动 transform → 改 startPosition/startRotation → EndDrag`。
`Rotate()` 本身在 `inGrid=true` 时第一行就 return，所以「先转再放」必须先 StartDrag。

13 格真机结果（`data/runs/move-rotate-drop-20261029.json`）：

| 动作 | 落点变化 | free | 说明 |
|---|---|---|---|
| move MEAL → `-1,-2` | `0,-1 1,-1` → `-1,-2 0,-2` | 4 → 4 | 只是挪位置 |
| rotate MEAL 90° | `-1,-2 0,-2` → `0,-2 0,-1` | 4 → 4 | 变竖直 1×2 |
| drop MEAL | 清空，物品到 `7,-1.35` | 4 → **6** | 变 loose（`in_grid=false`） |
| place MEAL 回来 | → `0,-2 0,-1` | 6 → 4 | 不叠、parent 仍是 Grid Parent |

## 1. 每个动作怎么做的

### 1.1 `move_item {uid, cell}`

1. 记下旧 cells（`extraFree`：这些格子在选落点时当作空——`StartDrag` 已经把它们腾出来了）。
2. `isStoppingDrag=false` → `StartDrag()`（被拒就报错，不留半截状态）。
3. 目标：给了 `cell` 就用它，否则按当前形状扫背包找第一个放得下的空格。
4. **EndDrag 之前**用 `PredictCells` 算 footprint，逐格检查「在背包里 + 空着（或属于自己旧格子）」；
   不合法就 `EndDrag` 回原位再抛错。
5. `transform.position=target` → `startPosition/startRotation` → `EndDrag` → `RefreshOccupancy()`。

失败示例（3×3 满包，移到被占的 `0,1`）：

```
ok=false ms=100
error: move_item aborted, item restored (footprint hits occupied 0,1)
cells 仍是 0,-1 / 1,-1
```

100ms 就返回——顺带修了「动作抛错还要等满 10 秒阶段门」的老毛病。

### 1.2 `rotate_item {uid, cell?, times=1}`

和 move 一样，只是 `StartDrag` 之后把 `transform.eulerAngles.z += 90*times` 再选落点。
转完放不下就回原位（`EndDrag` 用 StartDrag 抓的原始 startPosition/startRotation），
**不会把物品丢在半空**。`times` 可以一次转 180°。

### 1.3 `drop_item {uid}`

1. `StartDrag()` 摘出背包。
2. `transform.position = DropPosition()`：`maxX(背包)+6, minY(背包)-2`，保证不在任何格子上。
3. `EndDrag()` —— 注意 `AddToGrid` 在包外也会把 `inGrid=true`、`objsInGrid+1`，
   所以之后**按 `RemoveFromGrid` 的语义纠正**：
   `inGrid=false / inPouch=false / parentInventoryGrid=null / ClearGridPositions() / objsInGrid-1`。
4. 纠正后物品就是 loose（`items_loose+1`、`items_in_grid-1`），离开房间会被游戏自己的
   `DestroyAllItemsOutsideGrid` 清掉——这就是「丢弃」。

## 2. 这一步抓到并修掉的三个 bug

1. **`containsItem` 误清**：最初写的 `ClearStaleOccupancy` 会把「物品刚挪过去的格子」也清掉，
   rotate 之后 `free_squares` 从 4 变成 5（多报一格）。改成 `RefreshOccupancy()`：
   以所有 `inGrid` 且有 `gridPositions` 的物品为唯一事实，重算全部格子的 `containsItem`。
   `place/move/rotate/drop` 之后都调。
2. **drop 之后 `Item2.gridObject` 可能是 null**：形状算不出来，`place_item` 就退化到
   「盲放第一个空格」，实测把 MEAL 叠到了 WOODEN SWORD 的格子里（两个物品都声称占 `-1,-1`）。
   修法：`ResolveGridObject()`——必要时调 `ItemMovement.GetGridObject()` 补建；
   **补不出来就直接报错**，不再盲放。
3. **错误要等 10 秒**：`Actions.Execute` 抛错后，`TickActions` 还在等阶段门。
   现在 `pc.Error != null` 直接 `FinishPending`，失败 100ms 返回，重试/换格快了一个数量级。

## 3. 另外：一次自己造成的事故（已修复，记录在 PITFALLS E11）

批量改 `Actions.cs` 时用了
`(Get-Content -Raw) -replace ... | Set-Content -Encoding UTF8`。
PS 5.1 用 GBK 读无 BOM 的 UTF-8 源码，**整个文件的中文注释变 mojibake，171 行注释和下一行代码被合并**
（`//` 把后面的 `RefreshOccupancy();` 吞了；编译不报错，只是少执行一句）。

恢复方式：用 .NET `Encoding.GetEncoding(936)` 建字符→字节逆表转回 UTF-8，
123 行从 HEAD 恢复，96 行手写重写；最后**逐方法对比 IL**：

```
methods old/new 62/62
only old: ClearStaleOccupancy(...)
only new: RefreshOccupancy()
body differs: MoveRotateDropAction, DropItemAction   （只有这两个调用了被替换的方法）
```

除预期改动外 IL 完全一致 —— 代码没被改坏，坏的只是注释，已经全部补回。
教训：**改源码只用 edit/write，不要用 PowerShell 文本管道**。

## 4. 改动清单

| 文件 | 内容 |
|---|---|
| `bridge/src/Actions.cs` | `move_item`/`rotate_item`/`drop_item` + `MoveRotateDropAction`/`DropItemAction`、`ResolveGridObject`、`RefreshOccupancy`、`FitTargets`、`DropPosition`、`Snapshot.BagSig` |
| `bridge/src/BridgeMod.cs` | move/rotate 等 `ItemInBag`；drop 等 `WaitDropped`；抛错立即回执 |
| `bridge/build.ps1` | 未改 |
| `docs/PITFALLS.md` | 新增 E11（PowerShell 转码事故） |
| `data/runs/move-rotate-drop-20261029.json` | 四步的完整 diag/cells/free |

## 5. 验证

- `bridge/tests/run-tests.ps1` → ALL PASS；`agent/tests/test_v0.py` → ALL PASS
- 构建 108,544 bytes；部署 SHA256 一致（`8BF1CAE1…`）
- 真机：3×3 满包时 move 到占用格 → 100ms 失败且物品回原位；
  13 格上 move/rotate 各一次（free 不变）、drop（free +2、变 loose）、
  place 回来（free -2、无重叠、parent=Grid Parent）

## 6. 还没做的

1. **策略接入**：`legal` 里还没有 move/rotate/drop，`loop.py` 也不会自动「腾位」。
   下一步建议加 `--make-room N`：`place_item` 报 `no fitting cell` 时，按账本/费用挑一件
   最低价值的背包物品 `drop_item`，再重试放。
2. **swap**：两件物品互换位置（现在 move 的目标必须整块空着，或只占自己的旧格子）。
3. **`rotate_item` 的落点**：只按当前形状挑；如果要「转 90° 才放得下」，得让调用方指定 cell
   或做一个小搜索（4 个朝向 × 所有空格）。
