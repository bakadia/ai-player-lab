# Backpack Hero · AI 玩家试验场

剑三「小师妹」的隔壁项目：换一个小游戏，把自动玩家的**闭环、验证、评测**做对，再搬回去。

- **游戏**：Backpack Hero（Steam appid `1970580`），本机 `E:\steam\steamapps\common\Backpack Hero`
- **底座**：Unity `2021.3.22f1` / Mono / MelonLoader `0.7.3`（待装）
- **当前阶段**：方案已定，进入 spike；总目录 `D:\projects\ai-player-lab`
- **决策文档**：[docs/MVP.md](docs/MVP.md)
- **迁移目标**：`D:\projects\ai-player-lab\games\backpack-hero`

## 一句话方案

`Quick And Easy` 模式 + `Purse`，从新开局到击杀第一个 Boss，全程无人值守；每一步动作都有
「前置状态 → 预期变化 → 实际变化 → 耗时」四元组；50 局可复现，输出对比随机基线的报告。

## 为什么不是「直接让 LLM 玩」

MVP 的目标是**可验证的自动化**，不是演示效果。策略先用规则跑通闭环，LLM 只处理低频、
语义型的决策（物品解读、事件选择）。智能是第二刀，见文档第 9 节。
