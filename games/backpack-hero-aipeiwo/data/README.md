# data/

这个游戏的数据资产。

## `items.json` — 效果账本

**这是实验产出，不是官方数据。** 由 `agent/ledger.py` 在打的过程中自动累积：

```json
{"items": {"WOODEN SWORD": {"samples": 4, "damage_sum": 28, "block_sum": 0}},
 "note": "...", "schema": "bph.items.v1"}
```

| 字段 | 含义 |
|---|---|
| `samples` | 用了多少次 |
| `damage_sum` | 累计实际打掉的血（击杀按剩余血量算） |
| `block_sum` | 累计给自己加的护甲 |
| `absorbed_sum` | 累计被对方格挡吃掉的量（有才写） |

要注意的：

- **样本少的时候别当真值用**。4 次采样得到 7.00 只是「看起来对」，波动很大。
- 键是 `displayName`，跨局稳定；`instance id` 每局都变，不能当键。
- 名字带尾随空格，存之前已 `strip()`。
- 已知偏差：被格挡的那一刀 `damage_sum` 记 0，`absorbed_sum` 记真实吸收量。
  所以 `damage_sum / samples` **不是**「这件物品的平均伤害」，而是
  「打在有护甲目标上的平均净输出」。要算理论伤害得 `(damage_sum + absorbed_sum) / samples`。

## `runs/` — 每局回放

`run.py` 写的 JSONL，一行一个事件（`decide` / `act` / `stuck` / `leave_battle`）。
**不进 git**（`.gitignore` 里有 `games/*/data/runs/`），因为它增长快且可重跑。

> `*.bak` 一律不进仓库——那是调试时的临时备份。
