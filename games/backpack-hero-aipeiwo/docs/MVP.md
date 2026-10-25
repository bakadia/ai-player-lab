# Backpack Hero · MVP 决策

> 2026-10-25 · 状态：**已拍板，spike 进行中**（MelonLoader 已装，hello mod 待重启验证）
> 总目录：`D:\projects\ai-player-lab`（`games/backpack-hero` 是当前游戏）
> 结论先行：**MVP 不做「会打牌的 AI」，做「一条能无人值守跑完一局、每一步可验证可回放的流水线」。**
> 载体：`Quick And Easy` 模式 + `Purse`，打到第一个 Boss 为止。节奏参考：spike → MVP，不设硬期限。

---

## 0. 结论

| 问题 | 答案 |
|---|---|
| MVP 做什么 | 一条**无人值守跑完一局**的流水线：状态 → 决策 → 执行 → 校验 → 记录 → 评测 |
| 在哪跑 | `Quick And Easy`（每个 Boss 前只有一层）+ `Purse`，打到第一个 Boss |
| 先验证什么 | 进程内能不能稳定读状态、调动作（3 天 spike，go/no-go） |
| 交付什么 | `bph-bridge`（MelonLoader mod）+ `agent-core` + `bph-adapter`（Python）+ 50 局对比报告 |
| 不做什么 | 商店 / 事件 / 村庄 / 多英雄 / 剧情 / 像素感知 / LLM 内环 |
| 成功长什么样 | 20/20 到 Boss；≥50% 击杀；每个动作可校验；seed 可复现 |
| 给剑三留下什么 | 状态/动作契约、动作校验、watchdog、episode 评测、效果账本、adapter 分层 |

**一句话**：先在这个「有标准答案」的游戏里，把剑三最缺的那套**工程骨架**做对；不是比谁先把游戏打通。

---

## 1. 现场勘察

### 1.1 事实（已验证）

| 项 | 实测 | 含义 |
|---|---|---|
| 安装位置 | `E:\steam\steamapps\common\Backpack Hero` | 已安装，可直接改 |
| 版本 | build `15631088`，2025-01-05 | 约 21 个月未更新，反射目标稳定 |
| 引擎 | Unity `2021.3.22f1`，**Mono** | 有 `Assembly-CSharp.dll`，不是 IL2CPP，mod 门槛低 |
| 主程序集 | `Backpack Hero_Data\Managed\Assembly-CSharp.dll`（1.6 MB） | 可直接反编译看战斗/背包逻辑 |
| 代码 mod | MelonLoader `0.7.3`（Thunderstore 该游戏条目） | 社区已有 `MagmaCore` 等 MelonLoader 插件 |
| 官方 mod | `Mods/` + Steam Workshop，**JSON 内容包** | 改物品/内容可以；改行为不行（代码走 MelonLoader） |
| 存档 | `bph.sav`（64 B）、`bphRun0.sav`，非明文 + 有 `ES3Assembly.dll` → 推断 ES3 加密 | **不要走「解析存档」路线**，读进程内对象 |
| 反作弊 | 无 | 单机 + 官方 Workshop，没有封号面 |
| 调试入口 | 程序集含 `DebugMenu`、`ItemSpawnMenu`、`DevPunks` 控制台痕迹 | 可能能做场景注入（待验证，见 §5.1） |
| 可用模式 | `Quick And Easy`（Boss 前只有一层，-25% 敌方 HP） | 天然的「短局实验轨道」 |

### 1.2 与剑三的约束对比

| 约束 | 剑三 | Backpack Hero |
|---|---|---|
| 进程/权限 | High IL，ACE 内核反作弊 | 普通用户态，无 AC |
| 输入 | 键盘注入被过滤，最后靠 PS/2 | 不需要输入，进程内直接调方法 |
| 感知 | 纯像素，UIA 超时 | 完整对象图，反射直读 |
| 节奏 | 实时 30fps | 回合制，可暂停/加速 |
| 评测 | 「打完没打完」都说不清 | 胜负/层数/回合/伤害，全是数 |
| 知识 | 外部 API + OCR + 人肉 | 游戏内数据 + 可控实验 |
| 失败成本 | 封号/掉线/坑队友 | 重开一局 |

**所以它不是「更简单的剑三」，是「把变量控制住的剑三」**：同一套 Agent 骨架，先在这里做对，再换 adapter 搬回去。

### 1.3 它能教什么、不能教什么

| 能教（可迁移） | 不能教（别指望） |
|---|---|
| 状态契约、schema 版本化 | 实时反应、帧级时序 |
| 动作语义化、执行后校验 | 反作弊、输入注入对抗 |
| 卡死检测、超时、重试、恢复 | 3D 视角、寻路、多人社交 |
| episode 评测、随机基线、可复现 | 像素感知（第二阶段单独练） |
| 未知效果探索、效果账本 | 大世界内容量 |

### 1.4 复利的前提：一个 core，多个 adapter

如果每个小游戏都从零写「截图 → 点击 → 判断」，玩十个也不会复利，只会得到十套互不相通的脚本。
真正能囤积的是**中间那层**：

```
                 ┌──────────────┐
                 │  agent-core  │  契约 / runner / watchdog / eval / 账本
                 └──────┬───────┘
        ┌───────────────┼────────────────┐
   adapter A       adapter B         adapter C
   BPH (mod)       BPH (pixel)       JX3 (WGC)
```

BPH 的第一个 adapter 用 mod，是因为它**最便宜**：先把 core 的形状定对，再让 pixel adapter 去适配它。
反过来先写 pixel，core 会被 OCR 的偶然复杂度带偏。

---

## 2. MVP 定义

### 2.1 一句话

在 `Quick And Easy` 模式下，`Purse` 从新开局到击杀第一个 Boss，全程无人值守；每一步动作都有前置状态、预期变化、实际变化、耗时；50 局可复现，输出对比随机基线的报告。

### 2.2 做什么

- **战斗**：回合内选物品、选目标、结束回合。
- **战后**：三选一奖励、背包摆放（能放下 + 不挡路）。
- **地图**：只选战斗/宝箱；遇到商店/事件选离开。
- **异常**：死亡、卡死、超时都能正确结束并记录。

### 2.3 不做什么（硬边界）

- 商店买卖、事件分支、村庄/ meta 进度、多英雄、剧情对话。
- 像素感知、OCR、鼠标模拟。
- LLM 进内环（每回合都问模型）。
- 追求「比人类强」；先超过随机基线。

### 2.4 验收（硬指标）

| 编号 | 指标 | 门槛 |
|---|---|---|
| A1 | 桥接稳定性 | 连续 200 个动作无 seq 失配、无超时 |
| A2 | 无人值守 | 20/20 局到达 Boss，无人工干预 |
| A3 | 有效策略 | Boss 击杀率 ≥ 50%（先测随机基线，再定最终线） |
| A4 | 可观测 | 每个动作都有 expected/actual delta；静默失败漏检 = 0 |
| A5 | 可复现 | `--seed N` 重跑，动作序列一致，或差异可解释（RNG 调用点） |
| A6 | 速度 | 单局 ≤ 10 分钟（1×）；加速后目标 ≤ 4 分钟 |

> A4 是这套东西的灵魂。剑三的坑是「`SendInput` 返回 1，角色没动」；这里每次动作都必须证明状态真的变了。

---

## 3. 架构

### 3.1 三层，两个进程

```
┌─────────────────────────────┐        runtime/ 文件 IPC        ┌──────────────────────────┐
│ Backpack Hero               │  state.json  ←── 原子写 ──      │ agent-core + bph-adapter │
│  └─ BphBridge.dll (Melon)   │  cmd/*.json  ──→ 轮询 ──→       │  ├─ policy               │
│     读状态 / 算合法动作 / 执行 │  ack/*.json  ←── 结果 ──       │  ├─ runner / watchdog    │
└─────────────────────────────┘                                │  └─ eval / replay        │
                                                               └──────────────────────────┘
```

- **Bridge 只做三件事**：读状态、算合法动作、执行动作。**不做策略**。
- **Agent 不碰内存、不碰像素**，只认 schema。
- 以后把 Bridge 换成「像素 adapter」，Agent 一行不改——这是给剑三留的后门。
- 这和剑三的 `executor/` 是同一个形状：决策在外、执行在内；`halt` 文件也是同一套急停约定。

### 3.2 为什么用文件 IPC，不用 HTTP / 命名管道

| 方案 | 判断 |
|---|---|
| 命名管道 | **不用**：你的沙箱禁止程序开命名管道（踩坑记录 E6） |
| HTTP | 可用，但要处理端口、防火墙、URL ACL；MVP 不需要 |
| **文件队列** | **采用**：零依赖、可 `cat`、跨完整性级别、和你「执行器只通过文件通信」的约定一致 |

回合制游戏，50ms 轮询完全够；换来的是**可调试性**。

### 3.3 目录

```
ai-player-lab/                    # 总目录
├── core/                         # 与游戏无关
│   ├── agent_core/
│   │   ├── contract.py           # Observation / Action / Expect / Transition
│   │   └── verify.py             # delta 校验
│   └── tests/
├── games/
│   └── backpack-hero/            # 原 backpack-hero-aipeiwo
│       ├── README.md
│       ├── docs/MVP.md
│       ├── bridge/               # C# / MelonLoader
│       │   ├── BphBridge.csproj
│       │   └── src/
│       │       ├── BridgeMod.cs  # OnUpdate：读 cmd、执行、写 ack
│       │       ├── StateReader.cs
│       │       └── Actions.cs
│       ├── adapter/              # Python：bridge ↔ core，只翻译
│       │   ├── client.py         # 文件 IPC 客户端
│       │   └── translate.py      # bridge JSON → Observation
│       ├── agent/                # 这个游戏的策略 + 装配
│       │   ├── policy.py         # v0 规则策略
│       │   └── run.py            # 一局的入口
│       ├── runtime/              # 运行时（gitignore）
│       │   ├── state.json
│       │   ├── cmd/  ack/
│       │   └── halt              # 急停：存在即停
│       └── data/
│           ├── runs/             # JSONL 回放
│           └── items.json        # 效果账本
├── docs/
├── data/
└── tools/
```

### 3.4 状态 schema（v1 草案）

```json
{
  "schema": "bph.state.v1",
  "seq": 1287,
  "ts": 1761390000123,
  "mode": "battle",
  "run": {"hero": "Purse", "mode": "quick_and_easy", "floor": 1, "room": 4, "seed": 12345},
  "player": {"hp": 31, "max_hp": 40, "block": 0, "energy": 3, "max_energy": 3},
  "enemies": [
    {"uid": "e0", "name": "Rat", "hp": 9, "max_hp": 12, "block": 0,
     "intent": {"kind": "attack", "value": 6, "hits": 1}, "status": {}}
  ],
  "backpack": {
    "w": 5, "h": 4,
    "blocked": [[0, 0], [4, 3]],
    "items": [
      {"uid": "i1", "def": "wooden_blade", "name": "Wooden Blade",
       "x": 1, "y": 1, "w": 1, "h": 2, "cost": 1,
       "usable": true, "target": "enemy", "text": "Deal 3 damage."}
    ]
  },
  "legal": [
    {"type": "use_item", "uid": "i1", "target": "e0"},
    {"type": "end_turn"}
  ]
}
```

要点：

- `seq` 单调递增，**所有等待都以它为锚**。
- `mode` 是状态机：`menu | map | battle | reward | shop | event | gameover`。
- `legal` 由 Bridge 算，Agent 不猜合法性。
- `text` 保留原文，给 LLM/账本用；数值字段给规则用。

### 3.5 动作与回执

命令：

```json
{"id": 1288,
 "action": {"type": "use_item", "uid": "i1", "target": "e0"},
 "expect": {"enemy_hp": {"uid": "e0", "delta_max": -1}, "energy_delta": -1},
 "timeout_ms": 5000}
```

回执：

```json
{"id": 1288, "ok": true, "seq_before": 1287, "seq_after": 1289,
 "delta": {"enemy_hp": {"e0": -3}, "energy": -1},
 "ms": 412, "error": null}
```

### 3.6 一次动作的时序（核心）

1. Agent 读 `state(seq=n)`，选 `action`，写 `cmd/{id}.json`。
2. Bridge 在 `OnUpdate` 轮询到命令，**在主线程**执行。
3. 等状态稳定：`seq > n` 且 `mode` 按预期迁移（或仍在 `battle`）。
4. 写 `ack/{id}.json`，附 `delta` 与耗时。
5. Agent 校验 `delta` 是否满足 `expect`：
   - 满足 → 继续。
   - 不满足 → `assert_fail`：存前后快照，**重试一次**；再失败 → 结束本局并告警。
6. 任何一步超过 `timeout_ms` → `stuck`：截图 + dump + 重启游戏（可选）。

> 这套「执行 → 等状态 → 验 delta → 重试/中止」就是剑三执行器缺的那一层。

### 3.7 策略 v0（规则）

```
score(action) =
    damage_dealt            * 1.0
  + block_gained            * (incoming > 0 ? 0.8 : 0.1)
  + lethal_bonus            (击杀: +100)
  - energy_cost             * 0.1
  + status_value            (中毒/力量等，来自效果账本)
```

- 每回合取最高分；没有正分动作就 `end_turn`。
- 未知物品：HP > 50% 且能量够时**试一次**，把观测写进 `data/items.json`。
- 战后奖励：稀有度 + 与现有 tag 的协同；未知物品优先（探索）。
- 背包摆放：先 first-fit；有空间时按邻接加成做局部交换（v1.1 再上求解器）。

### 3.8 效果账本（小而关键）

每次 `use_item` 记录：物品 def、使用前/后（敌方 HP、我方 block、能量、状态），聚合出
`mean_damage / mean_block / variance / samples`。

- 解决「800+ 物品，效果是代码不是数据」的问题：**不靠读代码，靠实验**。
- 这就是剑三「技能效果未知 → 打一次记一次」的同款能力。
- 物品数量先只覆盖前 30 个常见的；未知的保守探索。

---

## 4. 关键技术选择

| 选择点 | 采用 | 理由 | 备选 |
|---|---|---|---|
| Mod 加载器 | **MelonLoader 0.7.3** | 该游戏现成、Mono、社区在用 | BepInEx（也能用，但没现成条目） |
| 状态读取 | **反射只读** | 不 patch 游戏逻辑，风险最低 | Harmony patch（等反射不够再上） |
| 动作执行 | **优先调高层方法** | 最稳、最快、不带 UI 噪声 | `EventSystem` 合成点击 → OS 点击 |
| IPC | **文件队列** | 沙箱友好、可调试 | HTTP（后续可选） |
| 策略 | **规则 v0** | 可解释、可复现、迭代快 | LLM（低频）/ 搜索（v2） |
| 加速 | `Time.timeScale` + `Application.runInBackground` | 50 局要过夜 | patch 动画协程 |

**一个待验证的坑**：MelonLoader 的 `Mods/` 和游戏官方 `Mods/` 同名。前者放 `.dll`，后者放
modpack 文件夹；大概率能共存，但 spike 第一天要确认，必要时用 `--melonloader.basedir` 挪走。

---

## 5. 计划

### 5.1 三天 spike（先做这个，go/no-go）

| 天 | 做什么 | 产出 |
|---|---|---|
| D0（10 分钟） | 确认 `Quick And Easy` 已解锁（没有就退 `Easy Mode`）；备份 `Assembly-CSharp.dll`；关云存档 | 环境就绪 |
| D1 上午 | 装 MelonLoader；跑通 hello mod；确认官方 `Mods/` 不冲突 | 游戏能启动，日志里有自己的输出 |
| D1 下午 | 用 dnSpy/ILSpy 反编译 `Assembly-CSharp.dll`；定位 `GameManager` / `Player` / `Enemy` / 背包 / 回合状态；dump 一场战斗 | `state.json` 样例（哪怕字段不全） |
| D2 上午 | 写 `cmd → 调 UseItem/EndTurn → ack`；验证 HP/能量真的变 | 一条命令能改变游戏状态 |
| D2 下午 | 如果直接调被 UI 守卫挡住：改 `EventSystem` 合成点击；两条路选一条 | 动作层结论（哪种可行） |
| D3 | 随机合法动作自动打一场；重复 20 场 | 胜率 + 失败模式清单 + **一页 go/no-go** |

**spike 只回答三个问题**：

1. 状态能不能稳定、完整地读出来？
2. 动作能不能可靠地执行，并且观测到变化？
3. 一场战斗能不能无人值守打完？

三个都是 yes → 进 MVP；任何一个 no → 换路线（见 §7），别硬撑。

### 5.2 十天 MVP

| 天 | 做什么 | 验收 |
|---|---|---|
| D4–5 | schema v1 + Bridge 加固（原子写、seq、超时、halt、日志） | A1 |
| D6–7 | 战斗策略 v0 + expect/verify + watchdog | 单场稳定；assert_fail 可见 |
| D8–9 | 奖励/摆放/地图；整局跑通 | A2 |
| D10–11 | 50 局评测 + 随机基线 + 报告 | A3/A5/A6 |
| D12–13 | 修 top 3 失败模式；缓冲 | 失败率下降 |
| D14 | demo（60s GIF/录屏）+ 一页结论文档 | 可展示 |

> 时间按业余精力走，不设硬期限：spike 的退出条件是三个问题都有答案；MVP 的退出条件是 A1–A6。
> 如果某周只能投 5 小时，就只推一个验收项，别并行。

---

## 6. 风险与预案

| 风险 | 概率 | 影响 | 预案 |
|---|---|---|---|
| 动作方法有 UI 状态守卫，直接调无效 | 中 | 高 | 退 `EventSystem` 合成点击；再退窗口化 OS 点击 |
| 反射字段分散/命名混淆，读不全 | 中 | 中 | 先写「对象图 dump」工具；schema smoke test；锁 build |
| 物品效果太杂，策略不会用 | 高 | 中 | 只覆盖前 30 个常见物品；效果账本；未知保守探索 |
| 动画/协程慢，50 局跑不完 | 中 | 中 | `timeScale`；patch `WaitForSeconds`；先跑 10 局 |
| 范围蔓延到商店/村庄 | 高 | 高 | 非目标写死；`--mode` 白名单；一周只加一个系统 |
| 游戏更新破坏反射 | 低 | 中 | 锁 build `15631088`；备份 `Assembly-CSharp.dll` |
| 存档/云同步干扰多局 | 低 | 低 | 每局用独立 profile 或先关云存档 |

---

## 7. 备选方案（以及为什么不是 MVP）

| 方案 | 练到什么 | 为什么不作 MVP |
|---|---|---|
| **纯像素 agent**（截图 + OCR + 鼠标） | 感知/输入，最像剑三 | 80% 预算花在 OCR；没有 ground truth，调不动。放第二阶段 |
| **背包布局求解器**（离线） | 组合优化，AI 含量最高 | 只覆盖一个决策点，没有闭环；放 v1.1 |
| **LLM 直接玩** | demo 效果好 | 慢、贵、不可复现；只做解说/低频策略 |
| **RL 端到端** | 纯策略 | 样本效率低、局太长、没有仿真器 |

### 为什么像素路线要放第二阶段，而不是不做

因为这里有一个剑三给不了的东西：**Bridge 就是像素管线的标准答案**。

同一场战斗，Bridge 读到的 `hp=31 / energy=3 / 敌人 intent=6` 是 ground truth；像素 adapter 读出来
多少，就能算出 OCR/ROI 的**逐字段误差**。这是剑三最缺的「带答案的感知评测」。
所以第二阶段不是重复劳动，是**用标准答案校准感知**。

---

## 8. 给剑三留下的资产

| BPH 里练的 | 剑三对应 | 复用形态 |
|---|---|---|
| `Observation / Action / Transition` 契约 | 游戏状态契约 | 换 adapter，runner 不动 |
| `act → 等状态 → 验 delta` | 按键后校验（追踪栏/血条/坐标） | 同一套 assertion |
| watchdog / 超时 / 重试 / 急停 | 卡死、过图、掉线 | 同一套 runner |
| episode 指标 + 随机基线 + 回放 | 任务成功率/耗时 | 同一套 eval |
| 效果账本 | 技能/副本机制知识 | 同一套 `items.json` → `skills.json` |
| Bridge ↔ 像素 adapter | WGC + ROI + OCR | 同一接口两种实现 |

**搬过去要改的只有两处**：adapter（状态从哪来）和动作原语（怎么按键/点击）。其余全是资产。

---

## 9. 第二阶段（如果想继续）

1. **盲打模式**：同一 Agent，换像素 adapter；用 Bridge 的 ground truth 量 OCR/ROI 的误差
   （HP、能量、物品数、敌人 intent、延迟）。这是剑三感知工作的预演。
2. **布局求解器**：把背包当 2D bin packing + 邻接加成；模拟退火 / CP-SAT；目标函数是
   「每点能量的期望伤害」。输出布局给 Agent，或做成给人用的「摆包顾问」。
3. **LLM 策略**：只在「看不懂的物品/事件」上调用，带工具（查效果账本、查 wiki）。
   低频、可缓存、可回放；不要放进每回合的内环。
4. **接回 DSH**：Agent 稳定后包成 DSH 的 tool/skill；DSH 只负责下发目标和收战报，不进内环。

### 智能阶梯（给「AI 含量」一个坐标）

| 级别 | 策略 | 何时做 |
|---|---|---|
| L0 | 随机合法动作 | 基线，必须有 |
| L1 | 贪心启发式 | **MVP** |
| L2 | 一步前瞻 + 效果账本 + 敌人 intent | v1.1 |
| L3 | 状态克隆 + 搜索（MCTS/beam） | 需要存档/克隆 spike |
| L4 | 学习策略 / LLM 战略层 | 有数据之后 |

---

## 10. 拍板记录

| 日期 | 决定 |
|---|---|
| 2026-10-25 | 目标：**闭环/评测**（不走纯视觉） |
| 2026-10-25 | **允许装 MelonLoader**（单机、自己的账号、可回滚） |
| 2026-10-25 | **不设硬期限**，按业余精力推进；以验收项而不是日历推进 |

下一步：迁移到 `D:\projects\ai-player-lab\games\backpack-hero`，然后开始 spike。

---

## 附录：证据与命令

- 版本/构建：`E:\steam\steamapps\appmanifest_1970580.acf` → `buildid 15631088`
- Unity：`UnityPlayer.dll` → `2021.3.22f1`
- 程序集：`Backpack Hero_Data\Managed\Assembly-CSharp.dll`，字符串含 `ModLoader`、`ModItemLoader`、
  `WorkshopModpack`、`DebugMenu`、`ItemSpawnMenu`、`DevPunks`、`GameManager`、`Enemy`、`Item2`
- 存档：`bph.sav` 64 B、`bphRun0.sav`，头部非明文 → ES3 加密
- 官方 mod 文档：<https://github.com/BackpackHero/ModDocs>（JSON 内容包）
- MelonLoader 该游戏条目：<https://thunderstore.io/c/backpack-hero/p/LavaGang/MelonLoader/>
- 游戏模式：<https://backpackhero.wiki.gg/wiki/Game_Modes>（`Quick And Easy` 无需解锁）
