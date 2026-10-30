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

## 4. 结论（诚实版）

1. **机制结案**：整理阶段掉血的来源、公式、累积规则、可见性缺口全部确认，且有真机对账。
   这是 M4 那 29 点「非战斗掉血」的 100% 解释。
2. **能力上限**：能不能免伤完全取决于**那一刻主网格有没有空格**。有空格 = 免费；
   没空格 = 只能付 `2..19`，而且第 N 次更贵。
3. **没有实现的（别当成已解决）**：
   - 「网格满时要不要丢一件去换」——没做，现在的默认是**不丢**（`allow_drops=False`），
     因为丢主武器去躲 2-3 点血是净亏；但 `damage=19` 时这个判断可能是反的，需要数据。
   - 「塞进去留着诅咒值不值」——`STICKY HONEY` / `GOO` 都是 `item_type [52]` 的诅咒类
     （1 格、`onUse→Destroy`、`onTurnEnd→ItemStatusEffect(locked)`），
     塞进去是**把诅咒带在身上**，理论上可能比付 2 点血更亏。这条完全没有验证。
4. **真正值钱的推论**：既然代价由「网格外件数 × 累积计数器」决定，
   那**「让背包别在事件前塞满」**比「会塞回去」更重要——这和 M4 里
   「非战斗掉血 11.3/局」的量级是一致的。

## 5. 下一轮建议（按性价比）

1. **先测「塞 vs 不塞」的经济性**：`damage` 已知，诅咒道具的代价未知。
   最小实验：同一局里遇到 `GOO` 时交替「塞 / 不塞」，看后续 3 场战斗的掉血与可用格子。
2. **网格满时的决策**：`damage >= 6` 才考虑丢一件最低价值的非武器；
   现在有 `damage` 这个数了，这条规则可以写成纯函数并单测。
3. **让背包别塞满**：战利品配额 3 件 + `--make-room 2` 的组合会把 9 格塞爆；
   可以考虑「留 1 格余量」的摆放策略（`place.py` 里已经有 `free_squares` 的概念）。

## 6. 复现

```powershell
# 看某个时刻的特殊整理（不驱动游戏，只读 state.json）
python .scratch\hazard\watch_special.py --seconds 300 --keep 2

# 停在第一次特殊整理（游戏留在那个阶段，方便手工看）
python agent\loop.py --focus-fire --aware-block --sustain --stop-at-special 1

# 立刻发一次放置，验证这个阶段让不让放
python agent\loop.py --focus-fire --aware-block --sustain --probe-hazard
```
