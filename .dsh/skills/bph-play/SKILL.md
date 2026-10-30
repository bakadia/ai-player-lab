---
name: bph-play
description: Use when driving a Backpack Hero run for this repo (starting a run, advancing it, reading state, handling death/level-up/loot). Covers the batch interface `tools/ai_play.py`, what each digest field means, which situations require a decision, and the known failure modes.
---

# 操作手手册（Backpack Hero）

仓库：`D:\projects\ai-player-lab\games\backpack-hero-aipeiwo`。游戏在 `E:\steam\...\Backpack Hero`，桥接 mod 已部署。

## 核心原则：批处理，不是逐帧

```
python tools\ai_play.py status            # 只看（不推进）
python tools\ai_play.py play --steps 25   # 推进最多 25 步 -> 回 ai.digest.v1
python tools\ai_play.py new-run           # 死了/卡了重开
```

一次 `play` 内部跑 `agent/loop.py --max-steps N`，用的是已经验证过的几何摆放 + 启发式战斗。
**你的工作是看 digest 做决定，不是替它点每一个动作。** 一次 20–40 步比较合适：
再短就淹没在细节里，再长就错过升级/战利品这类必须拍板的点。

## digest 怎么读

| 字段 | 含义 |
|---|---|
| `mode` | `intro` / `map` / `battle` / `reorganize` / `dead` / `menu` |
| `hp` / `max_hp` | 当前血；`max_hp` 会随道具涨 |
| `floor` / `level` / `gold` | 进度 |
| `enemies[]` | 名字 + 血 + 每回合攻击力（`attacks`） |
| `bag.items[]` | 名字 / 占格 / 费用 / 每回合护甲 |
| `bag.armor_per_turn` | 全身每回合护甲合计——护甲流看这个数 |
| `bag.free` | 空格；`0` 就捡不了东西 |
| `needs[]` | **必须拍板的事**，见下 |
| `events` | 这一段的战斗数、掉血、回血、付了多少 hazard 血、丢了什么 |
| `loop.tail` | loop.py 最后几行输出，出怪事时看这里 |

## `needs` 里每一条怎么处理

- `dead` → `new-run`。别试图救，`retry_run` 是唯一出路。
- `level_up` → 升级界面开着。护甲流优先「每回合护甲 / 加格子」，武器流优先「伤害 / 攻速」。
  见 `bph-builds`。
- `loot` → 战利品还能拿 N 件。**先看 `bag.free`**：没空格就得决定丢谁（见 `bph-builds` 的定价）。
- `hazard` → 特殊整理要付血。**默认就该付**：塞回网格会把游戏卡死在
  `phase=3 / in_special_reorg=false`（4/4 局复现，见 `docs/PITFALLS.md` E25）。
- `bag_full` → 要么接受不再捡，要么丢一件换一件。
- `dialogue` → N 选 1，按 `bph-builds` 的优先级挑。

## 什么时候别用 `play`

- 想知道「为什么这步这么做」→ `python tools\ab_m4.py data\runs\ab\... --verbose` 或直接读 jsonl。
- 想改策略 → 改 `agent/policy.py` 的常量，**别**在 case 里手点。
- 想复现某局 → 日志在 `data/runs/loop-*.jsonl`，每行一个事件（`decide` / `act` / `hazard_event`）。

## 已知坑（先看再动手）

| 症状 | 原因 | 处理 |
|---|---|---|
| `play` 一启动游戏就退出（exit 53） | MelonLoader 写不了日志（沙箱/权限），不是 mod 挂了 | 用有全盘权限的方式启动游戏 |
| 卡在 `reorganize` 且 `finish` 无效 | phase=3 但 `in_special_reorg=false` | 已自动改发 `skip_reorganize`；仍卡就 `new-run` |
| 连续多次 no-change | 形态无法达成（如特殊整理塞完） | `loop` 会自己停；`new-run` |
| 战利品拿不满 | 配额 3 件、背包满 | 正常现象，别硬塞 |

细节文档：`docs/PITFALLS.md`（E23 绕圈 / E24 战利品 / E25 hazard / E26 哨兵 / E27 描述）。
