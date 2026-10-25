# AI Player Lab · 自动玩家实验室

> 一个 core，多个 adapter。每个游戏只写适配层；决策、验证、评测、账本共用。

## 为什么

如果每个小游戏都从零写「截图 → 点击 → 判断」，玩十个也不会复利，只会得到十套互不相通的脚本。

真正能囤积的是中间那层：

```
                 ┌──────────────┐
                 │  agent-core  │  契约 / runner / watchdog / eval / 账本
                 └──────┬───────┘
        ┌───────────────┼────────────────┐
   adapter A       adapter B         adapter C
   BPH (mod)       BPH (pixel)       JX3 (WGC)
```

## 结构

```
ai-player-lab/
├── core/                    # 与游戏无关：契约、runner、验证、评测、账本
├── games/
│   └── backpack-hero/       # 第一个试验田（从 backpack-hero-aipeiwo 迁入）
├── docs/                    # 跨游戏文档：总纲、契约、路线图
├── data/                    # 跨游戏评测数据
└── tools/                   # 跨游戏小工具
```

## 当前

- 第一个游戏：**Backpack Hero**
- 阶段：**spike**（状态读取 / 动作执行 / 单场无人值守）
- 决策文档：`games/backpack-hero/docs/MVP.md`（迁移后）
- 核心契约：`core/agent_core/contract.py`

## 迁移

当前游戏目录还在 `D:\projects\backpack-hero-aipeiwo`。
一次性迁移步骤见 [`docs/00-总纲.md`](docs/00-总纲.md) 第 4 节。

## 不做什么

- 不为每个游戏写一套独立的 agent。
- 不把 LLM 放进每回合的内环。
- 不在 `core/` 里出现任何游戏名。
