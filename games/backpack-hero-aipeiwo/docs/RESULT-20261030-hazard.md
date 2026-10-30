# RESULT · 2026-10-30 · 「整理阶段掉血」结案：特殊整理的 Hazard 公式（桥接 v8.24）

> 起因：M4 的 A/B 里，**A 组 11.3 血/局、B 组 7.0 血/局**的掉血不在战斗里发生，
> 全在 `finish_reorganize` 那一下（`-2/-3/-4/-5` 一次）。HANDOFF 里把它记成
> 「整理阶段掉血（每轮 -3~-8）是另一个大头，值得单独查」。这一查到底了。
>
> **一句话结论**：那不是 bug，是**特殊整理（`EnterSpecialOrganization`）的诅咒定价**：
> 网格外的 Hazard 道具每件扣 `k + floor + cursesSkipped`，而 `cursesSkipped` 每次累加，
> 所以同一局里是 2、3、4、5 地涨。**agent 以前连这些道具都看不见**（`backpack.items`
> 只列网格内的），只能直接付钱。桥接 v8.24 起能看见，`loop` 会先尝试塞回网格。

## 1. 机制（`GameManager` 的 IL，逐条核对）

```csharp
// 结束整理时（FinishReorganizeButton）
GetDamageForHazards(out outside, out hazards, out damage);
cursesSkipped += hazards;                       // ← 累加，所以越拖越贵
if (damage > 0) player.stats.ChangeHealth(-damage, null, false);

// GetDamageForHazards 的实际算法
候选 = itemsForSpecialReorganization
           .Select(g => g.GetComponent<Item2>())
           .Where(it => it.itemMovement != null && !it.itemMovement.inGrid);   // 只看网格外的
foreach (it in 候选)
    if (it.itemType.Contains(52)) {              // ItemType.Hazard == 52
        hazards++;
        damage += hazards + gm.floor + gm.cursesSkipped;   // 第 k 件扣 k+floor+cs
    }
```

`UpdateFinishButton()` 也调同一个方法，只是为了把「结束整理 / 会扣 N 点血」写进按钮文案，
所以这条逻辑**只在 `inSpecialReorg == true` 时**才有意义（普通整理不扣血）。

### 和真机日志的对账

| 现场 | 公式预测 | 日志 |
|---|---|---|
| 1 件 hazard、floor 1、cs 0→3 | `2, 3, 4, 5` | `-2, -3, -4, -5`（A2/A3、B1/B3 全部一致） |
| 2 件 `GOO`、floor 1、cs 0 | `(1+1) + (2+1) = 5` | `damage=5`（`--stop-at-special` 抓到） |
| 5 件叠起来（后期） | 一档一档涨 | `damage=15`、`19` |

## 2. 交付物

| 文件 | 改动 |
|---|---|
| `bridge/src/MapProbe.cs` | v8.24：`ui.special_reorg {active, skippable, curses_skipped, items_outside, hazards_outside, damage, items[{uid,name,hazard}]}`；`damage` 按上面公式自己算，不调私有方法 |
| `agent/loop.py` | `hazard_place_action()`（纯函数）+ `--probe-hazard` / `--stop-at-special` 两个调试钩子：结束整理前先把 hazard 塞回网格，`allow_drops=False` |
| `agent/tests/test_v0.py` | `test_hazard_place_before_finish`：塞一件就返回一条 `place_item`、同一 uid 只试一次、离开特殊整理清空、没 hazard 时不动 |
| `docs/PITFALLS.md` | E25（含 IL、公式、和日志的对账） |

桥接：**v8.24 / 139,264 bytes / SHA256 `18722044…`**（v8.23 是 137,728 / `E9256F65…`）。

## 3. 真机验证

### 3.1 可见性（以前完全看不到）

```
ui.special_reorg = {
  "active": true, "skippable": false, "curses_skipped": 0,
  "items_outside": 2, "hazards_outside": 2, "damage": 5,
  "items": [ {"uid":"i-15704","name":"GOO","hazard":true},
             {"uid":"i-15758","name":"GOO","hazard":true} ]
}
```

`backpack.items` 那时只有 3 件（ROUGH BUCKLER / WOODEN SWORD / MEAL），两件 `GOO` 一件都看不见。

### 3.2 放置是允许的，但有前提

- **有空位时一次就成**（`--probe-hazard`，直接发 `place_item`）：

  ```
  probe-hazard: place i-20354 ok=True changed=True err=None
  probe-hazard: after -> free=0 hazards_outside=0 damage=0
  ```

  放完 `hazards_outside` 从 1 → 0、`damage` 归零，**这一下不用扣血**。

- **网格满时一律不行**：`no fitting cell (freeGridSquares=0)`。这解释了为什么
  A/B 里那 6 局**每局都在付钱**——早期背包 9 格被 3 件道具（4+3+2）占满，永远 `free=0`。

- 顺带否掉一条弯路：`drop_item` / 拖拽在特殊整理里会被
  `StartDrag refused: inventoryPhase=3,battlePhase=3,IsComplete=True` 拒绝，
  所以「先丢一件再塞」这条路在这个阶段是走不通的。

### 3.3 修完之后的一局（`--sustain`，201 步阵亡）

`loop` 每遇到特殊整理都会先试放，日志里能逐条看到：

```
（特殊整理：先把 STATIC 塞回网格，否则结束整理扣 2 血）
（特殊整理：先把 GOO 塞回网格，否则结束整理扣 15 血）   ×5
（特殊整理：先把 GOO 塞回网格，否则结束整理扣 19 血）   ×2
```

**但这局仍然在付钱**：`place_item` 全返回 `no fitting cell (freeGridSquares=0)`。
也就是说「先塞」这个动作现在是**尽力而为**：有空格就省下血，没空格照付。

## 4. 实验①：「塞」vs「不塞」的经济性 —— 结论是**塞不可用**

用 `--hazard-policy {place,skip,alternate}` + `tools/ab_hazard.py` 跑了 6 局
（`data/runs/ab-hazard*`），共 11 条特殊整理事件：

| 类别 | 条数 | 情况 |
|---|---|---|
| 背包满（`free=0`），塞不了 | **7** | `place_item` 报 `no fitting cell (freeGridSquares=0)`，只能付钱 |
| 有空位（`free=3`），塞成功 | 4 | `ok=True changed=True`，`hazards_outside` 归零、`damage` 归零 |
| 塞成功的后续 | **4/4 卡死** | `inventory_phase=3` + `in_special_reorg=false`，`finish` 与 `skip` 都 no-op、`seq` 冻结 |

卡死现场（`data/runs/ab-hazard2/run2.console.txt`，塞完 `STICKY HONEY` 之后）：

```
[step 72] mode=reorganize hp=27 in_grid=4 loose=0
  （phase=3 但 in_special_reorg=false：finish 一定 no-op，改发 skip_reorganize（E9））
  -> skip_reorganize
     ok=False changed=False ms=20083 error=timeout ... (inventoryPhase still reorganize)
  ... 连发 4 次，seq 一直是 4125，判定卡死
```

**所以「塞」现在不是可用选项**：省下 2-4 点血，代价是整局报废（比付血差得多）。
默认值因此改成 **`--hazard-policy skip`**（付钱、道具被销毁，继续跑）。
`place` / `alternate` 保留为实验开关，打开时会打印一行警告。

> 机制上「塞」确实有效（`damage` → 0 是真的），坏在**塞完这个整理阶段不会自己结束**。
> 推测是 `AddToGrid` 绕过了游戏自己的特殊整理完成流程（正常是拖拽 → `EndDrag` →
> 游戏把 `inSpecialReorg` 置 false 并推进 phase）。这条没验证，属于下一轮的活。

### 顺带修掉的一个真 bug（E9 变体）

原来的兜底只在 `in_special_reorg == true` 时才发 `skip_reorganize`；可塞完 hazard 之后
状态是 `in_special_reorg == false` 而 phase 仍是 3 —— 兜底根本不触发，白白 4×20s 超时。
现在 `phase=3 && !in_special_reorg` 时直接发 `skip_reorganize`（虽然这个死状态它也是 no-op，
但至少不会在别的场合白等 80 秒）。

## 5. 结论（诚实版）

1. **机制结案**：整理阶段掉血的来源、公式、累积规则、可见性缺口全部确认，且有真机对账。
   这是 M4 那 29 点「非战斗掉血」的 100% 解释。
2. **代价是硬的**：能不能免伤只看**那一刻主网格有没有空格**。有空格才谈得上塞，
   没空格就只能付 `2..19`，而且第 N 次更贵。
3. **「塞」这条路现在是堵的**（实验①的结论）：塞成功 4/4 会把游戏卡进
   `phase=3 / in_special_reorg=false` 的死状态，整局报废。所以默认付钱。
   **「省血」和「能继续跑」目前二选一，而后者更重要。**
4. **真正值钱的推论没变**：代价由「网格外件数 × 累积计数器」决定，
   所以**「别在事件前把背包塞满」**仍然是最大的杠杆——这和 M4 里
   「非战斗掉血 11.3/局」的量级一致，也是唯一还没试过的方向。

## 6. 下一轮建议（按性价比）

1. **先修「塞完卡死」**，否则①这条路白修：正常流程是拖拽 → `EndDrag` →
   `GameManager.MoveItemTowardsGrid`（private）→ 游戏自己收尾。我们走的是
   `AddToGrid`，很可能跳过了收尾。`Actions.cs` 里 `place_item` 已经有 `via=drag`
   分支，值得拿特殊整理的道具试一次 `via=drag`，看 phase 会不会正常推进。
2. **网格满时的决策**：`damage >= 6` 才考虑丢一件最低价值的非武器；
   现在有 `damage` 这个数了，这条规则可以写成纯函数并单测。
3. **让背包留 1 格余量**：战利品配额 3 件 + `--make-room 2` 会把 9 格塞爆
   （实测 11 条事件里 7 条 `free=0`）。`place.py` 里已经有 `free_squares`，
   加一条「保留 1 格」的摆放约束即可，而且**这条不依赖①**，现在就能做。

## 7. 复现

```powershell
# 看某个时刻的特殊整理（不驱动游戏，只读 state.json）
python .scratch\hazard\watch_special.py --seconds 300 --keep 2

# 停在第一次特殊整理（游戏留在那个阶段，方便手工看）
python agent\loop.py --focus-fire --aware-block --sustain --stop-at-special 1

# 立刻发一次放置，验证这个阶段让不让放
python agent\loop.py --focus-fire --aware-block --sustain --probe-hazard
```
