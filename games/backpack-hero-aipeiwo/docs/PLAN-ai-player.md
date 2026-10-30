# PLAN · AI 操作手 / 主播插件（M1 已落地）

> 目标（2026-10-30 用户定的方向）：**让 AI 去操作**——先把流派、装备评分、战斗常识
> 沉淀成 skills 和可调用脚本，再让一个会话能自动打完一局，最后做一个「能当主播」的插件，
> 让小师妹（现有鲸鱼娘宠物人格）直播玩游戏。
>
> 粒度决策：**批处理监督**（代码执行、模型每 20–40 步看一次 digest 决策），
> 不是逐动作模型控制——一局 100–400 步，逐动作会把 10 分钟浪费在模型延迟上，且不可复现。

## 0. 已经跑通的（M1）

| 层 | 是什么 | 在哪 |
|---|---|---|
| **手**（执行） | 几何摆放 + 启发式战斗 + 状态机，10Hz、确定性 | `agent/loop.py` / `policy.py` / `backpack.py` / `place.py` |
| **接口**（批处理） | `status` / `play --steps N` / `new-run`，返回 `ai.digest.v1` | `tools/ai_play.py` |
| **知识**（skills） | 操作手手册 / 流派与定价 / 战斗基础 | `.dsh/skills/bph-{play,builds,combat}/SKILL.md` |
| **数据** | 903 件道具 + 运行时描述（752 件）+ 账本 | `data/item-catalog.json` / `data/items.json` |

digest 一次大概长这样（40 步一批）：

```
mode battle  floor 1  hp 20/40  lv 0  gold 6
enemies [('Muskrat Brigand',30,[7]), ('Ratwolf',40,[8])]
bag [ROUGH BUCKLER(4), WOODEN SWORD(3), TOY MACHETE(2), SLATS SHIELD(2), ...] free 0
needs ['bag_full: 背包满了，再捡就要丢东西/换装备']
events {'battles': 2, 'hp_lost': 17, 'hazard_paid': 3, 'refused_swap': ['MEAL(要丢 SHIV)']}
```

`needs` 是「必须拍板」的钩子：`dead` / `level_up` / `loot` / `hazard` / `bag_full` / `dialogue`。
没有 `needs` 就继续 `play`，有就按 skill 里的规则处理。

## 1. 剩下的四层

```
L4 会话（autoplay）    一个 agent session：读 skill → 循环 play/决策 → 打完一局
L5 直播（plugin）       DSH client 插件「直播间」面板 + 鲸鱼娘解说
L6 人格（pet）          复用 @linxin666/dsh-pet 的 whale-girl，不做新形象
```

### 1.1 直播插件怎么接（技术草图）

DSH 里 UI 是 **client 插件**（Slot + React），游戏状态在**磁盘上**
（`runtime/state.json` 10Hz + `data/runs/loop-*.jsonl` 事件流）。所以分两半：

- **host 半边**（Node）：定时读 `state.json`（节流到 2Hz）+ tail 最新 jsonl，
  归一成一个小对象，通过 cordis service / event 推给 client：
  `bph.live = {floor, hp, max_hp, bag, enemies, last_events[], phase}`。
- **client 半边**（React）：一个 panel 槽位渲染
  - 血条 / 楼层 / 金币 / 背包网格（每格标出占格与每回合甲）
  - 敌人意图（攻击力合计 = 本回合 incoming）
  - 事件滚动条（"第 3 场赢了，掉了 8 血" "付了 3 血拒绝诅咒"）
  - 解说气泡：鲸鱼娘头像 + 一句模板文案（可低频换成模型生成）

**关键约束**：面板不能直连桥接（`runtime/cmd` 是命令目录，不是订阅），
只能读文件——所以 host 半边是「文件 → service」的适配器，2Hz 足够。

### 1.2 解说文案怎么来

| 档位 | 做法 | 成本 |
|---|---|---|
| 模板 | 事件 → 一句话（20 条模板覆盖 90% 情况） | 0 |
| 模型（低频） | 每场战斗结束 / 每次升级，用 digest 生成 1 句 | 每局 ~30 次调用 |
| 模型（高频） | 每 5 秒按状态生成 | 不建议，纯烧钱 |

建议先做模板 + 「每场战斗一次模型」的混合：模板保证不哑场，模型负责有梗。

## 2. 里程碑

| # | 内容 | 验收 |
|---|---|---|
| **M1** ✅ | `ai_play.py` 批处理 + 3 个 skill | 会话能 play → 读 digest → 处理 needs → 继续（已实测） |
| **M2** ✅ | `autoplay`：无人值守打完多局 | 2 局全程无人工；见 §2.1 |
| **M3** | host 半边 `bph.live`（文件→service） | 面板能实时看到血/楼层/背包，延迟 < 1s |
| **M4** | client 直播间面板 + 事件滚动 | 在现有 GUI 里能看一整局不动手 |
| **M5** | 鲸鱼娘解说（模板 + 低频模型） | 每场战斗后有话说，且不胡说（事实来自 digest） |
| **M6** | 一局完整直播跑通 | 无人值守 1 小时，画面/解说/数据都在 |

### 2.1 M2 实测（2026-10-30）

`python tools\ai_play.py autoplay --runs 2 --steps 120 --minutes 32 --stall-limit 2`

| 局 | 结局 | 最深层 | 等级 | 战斗 | 掉血/场 | 付的 hazard 血 |
|---|---|---|---|---|---|---|
| 1 | 卡死被自动重开 | 2 | 2 | — | — | — |
| 2 | 阵亡 | 2 | 1 | 8 | 6.25 | 0 |
| 合计 | 2 局无人值守 | 均 2.0 | — | 22 | 6.55 | 34 |

**两个发现**：

1. **卡死判据必须是「进度指纹」而不是 `seq`**：`seq` 每帧自增，run 1 在地图上来回走时
   它一直在变、floor/hp/level 一动不动 —— 用 `seq` 判会永远判不出卡死。
   改成 `(mode, floor, hp, level, battles)` 后，2 段无进展立刻抓到并重开。
2. **floor 2 的地图空转仍然存在**（run 1 就是这样卡住的）：不是 E23 那种两房间互踢，
   更像是「图上有房间但走不过去」的老问题在 floor 2 变体。下次要单独查
   `mapnav` 在「可达但 legal 点不到」时的兜底。
3. **hazard 付血累计 34**：默认 `--hazard-policy skip` 下，一次特殊整理 2–5 血、越拖越贵，
   和 E25 的公式一致 —— 这笔钱现在还是净支出，等「塞」的卡死问题修了才谈得上省。


## 3. 风险与对策

| 风险 | 对策 |
|---|---|
| 面板读 10Hz 文件把 UI 拖卡 | host 侧节流 2Hz + 只在 `seq` 变化时推 |
| 模型解说编造事实 | 文案只允许引用 digest 里的字段；模板兜底 |
| 直播用的局和 A/B 数据混在一起 | `data/runs/live/` 单独目录，`ab_*` 工具只读 `ab*` |
| 自动打完一局仍然会死（现在最好 floor 2） | M2 的验收设成「死在 floor ≥2 且全程无人工」，不追求通关 |
| 游戏窗口被前台抢焦点 | 已有 `bph.ps1` 的窗口归位；面板不需要焦点 |

## 4. 现在就能做的下一件事

**M2**：把「一局打完」变成脚本 —— 在 `ai_play.py` 上加 `autoplay`（循环 play + 自动处理
`level_up` / `loot` / `hazard` / `dead`），跑通后再接 M3 的面板。
这一步不需要新插件，纯 Python，当天能验收。
