# RESULT · 2026-10-29 · 房间图导航 + 满包 drop 真机验收

> 接 `RESULT-20261029-planner.md` §4.1（唯一没在真机跑到的：`--make-room` 的 drop 分支）。
> 这一班补上两件事：**房间图导航**（`legal` 会列走不到的房间，一格一格走才行）
> 和 **drop 分支的真机验收**（两条独立的路径都跑通了）。
>
> 桥接 **v8.16**：**118,784 bytes，SHA256 `ED512364…`**（已在 Mods 里）。
> 提交：`379d968`（导航 + `play_event` + 验收工具，`tests/test_mapnav.py` 合入）。

## 0. 一句话

`map.rooms[]` 里的 `pos/doors/blocked` 本来就是一张完整的邻接表——用它做 BFS，
`legal` 里那些「走不到的房间」就不再是陷阱；一次只走一格、点不到就朝目标探一格，
迷雾（`visible=false`）也不挡路。到了宝箱，`open_chest` 掉 4 件，
满包时 `place_with_room` 真的**丢了一件再放进去**。

## 1. 房间图（`agent/mapnav.py`）

```python
g = RoomGraph.from_state(state, bad_edges)
g.path(cur, target, allow_invisible=True)   # 最短路（含起终点）
g.next_hop(cur, target)
g.first_step(cur, target, legal_rooms, avoid=danger)   # 下一次 move_to_room 发哪格
g.reachable(cur)
g.describe(cur)                             # 人看的邻接表
```

连通规则（两边都要成立才算一条边）：

```
a 在 d 方向有门 && !a.blocked[d] && b 在反方向有门 && !b.blocked[反] && 两边都没锁
```

`pos` 是半格坐标（y 常见 .5），相邻房间差 1.0，所以先 `round(2)` 再当 key。

两个实战要点：

- **迷雾**：新楼层一开始大部分房间 `visible=false`（`legal` 里也就点不到）。但 doors
  数据是全的，所以长距离路径按全图算；路径上的下一格如果点不了，`first_step` 就
  从「能点到的邻居」里挑离目标最近的那个，先探一格——走过去之后新的房间就会 reveal。
  真机上这一条把「站在 19 间房里不知道往哪走」变成 3–10 步走过去。
- **绕开敌人**：`avoid` 里放 `Enemy/Elite/Shambler` 房间。绕不开时（这一层只有一条路）
  自动退回最短路——真机 `r-5528` 就是必经的敌人房，硬着头皮走。

`loop.py` 的地图分支现在：可达性 = 房间图（不是 `legal`）→ 目标房间照旧按事件优先级选
→ `first_step` 只发一格 → 走不通就把这条边记进 `bad_edges`（下次 BFS 绕开）。
`retry_run` 之后把这些学习到的状态一起清空。

## 2. 真机验收

### 2.1 导航

| 场景 | 结果 |
|---|---|
| 第 0 层：`open_map` → 3 格 → 出口 → `enter_door` | `r224572 → r224552 → r224984 → r225596`，floor 0→1 |
| 第 1 层：走向宝箱 `r-5356`（10 格） | 前 3 格顺利，第 4 格 `r-5528` 是敌人房 → 进战斗 → 工具退出（可重入） |
| 打完再跑 | `r-5370 → r-5384 → r-5398 → r-5356`，3 格到达 |

以前对着远处房间直接发 `move_to_room` 会 30 秒超时；现在同样一段路是 0.8–1.1 秒一格。

**顺带发现两个硬约束**（PITFALLS E14）：地图**关着**的时候 `legal` 里根本没有
`move_to_room`（只有 `open_map`）；而且远处房间要等地图开着才 reveal。

### 2.2 宝箱

```
open_chest ok=true changed=true
delta = {"items_total": 4, "items_placed": 4, "items_loose": 4,
         "luck_uncommon": 3.6, "luck_rare": 1.8, "luck_legendary": 0.54}
```

掉了 4 件：`BLUEFIN`(1) `CURSED POTION`(1) `DEBUFF POTION`(1) `CRISIS RING`(?)。

### 2.3 drop 分支 #1：`tools/bph_verify_drop.py`

```
start : free=1, in_grid=3, loose=3
CURSED POTION 直接放进去           -> free=0
捡 DEBUFF POTION（free=0）:
  腾位：丢 BLUEFIN（1 格，剩余 0 次）
  放 DEBUFF POTION @-1,-1 rot0     -> ok=True changed=True
after : free=0, in_grid=4, loose=2
RESULT ok=True dropped=['BLUEFIN'] in_bag=True
```

被丢的 `BLUEFIN` 出现在 `unplaced` 里（变 loose），`DEBUFF POTION` 的
`cells=["-1,-1"] parent=Grid Parent in_bag=true`。**free 一格没变**——丢 1 格、放 1 格。
证据：`data/runs/drop-make-room-20261029.json`。

### 2.4 drop 分支 #2：`loop.py` 自己走的同一条链

```
drop_for_room                                    # 丢 CURSED POTION（1 格，最便宜）
act place_item i-13578 ok=True  place CRISIS RING directly
place_no_room BLUEFIN   (free=0, 额度用完了)
place_no_room CURSED POTION (free=0, 额度用完了)
```

也就是说：`legal` 给出 `place_item{needs,free,fits:false}` + `drop_item` 候选 →
`try_place` 判定 `needs_drop` → 丢一件 → 重探 → 放进去；额度（`--make-room 1`）用完后
剩下的战利品明确放弃（而不是死循环）。证据：`data/runs/loop-20261029-151413.jsonl`、
`data/runs/state-drop-verified-20261029.json`。

### 2.5 `legal` 的实际样子（满包时）

```json
{"type":"place_item","uid":"i-13578","name":"CRISIS RING","needs":1,"free":0,"fits":false}
{"type":"drop_item","uid":"i-4248","name":"ROUGH BUCKLER","size":4,"is_relic":false,"gold":0,"why":"make_room"}
{"type":"move_item","uid":"i-4248","name":"ROUGH BUCKLER","size":4,"why":"make_room"}
{"type":"rotate_item","uid":"i-4248","name":"ROUGH BUCKLER","times":1,"why":"make_room"}
...
```

四件背包物品各有一条 `drop_item`，前三件还各带 `move_item`/`rotate_item`。

## 3. 这一班踩到的（PITFALLS E14/E15）

1. **E14 · `legal` 不是可达性**：`move_to_room` 会列出走不到的房间（30 秒超时）；
   地图**关着**时 `legal` 里只有 `open_map`，没有 `move_to_room`；远处房间要地图开着
   才 reveal。→ 用 `mapnav` 的房间图，走路前先 `open_map`，一次只走一格。
2. **E15 · SimpleClickItem 不能靠 `start_event`**：`start_event` 对它 ok=false；
   得 `DungeonEvent.Play()` + `SimpleClickItem.Click()`。而且 `Play()` **每调一次就多
   生成一份**（真机连调三次，`spawned_click_items` 从 2 涨到 4）。`Click()` 在这个环境里
   **没有**把东西收进背包（`loose_before=0 → loose_after=0`，`in_grid` 不变），
   所以地图上这类东西暂时还捡不到——新加的 `play_event` 只把它生成出来。

## 4. 改动清单

| 文件 | 内容 |
|---|---|
| `agent/mapnav.py` | 新：`Room`/`RoomGraph`（注解、BFS、`first_step`、`reachable`、`describe`、`bad_edges`、`avoid`） |
| `agent/loop.py` | 地图分支用房间图判可达 + 一次一格 + `bad_edges` 学习；retry 清空 |
| `agent/tests/test_mapnav.py` | 新：直线/墙、上锁/迷雾、坏边绕行、可见但不可达、**真机 19 间整层地图**（`fixtures/state-map-floor1.json`） |
| `bridge/src/Actions.cs` | 新 `play_event`（`DungeonEvent.Play` + `SimpleClickItem.Click`，带 diag） |
| `tools/bph_nav.py` | 新：`show` / `go <room|event:Kind>` / `exit` |
| `tools/bph_verify_drop.py` | 新：满包 → 开箱/捡物 → `place_with_room` 丢一件再放（可重入，打不过就下次接着来） |
| `agent/tests/fixtures/state-map-floor1.json` | 真机整层地图（19 间，含 doors/blocked/事件） |
| `data/runs/drop-make-room-20261029.json` | drop 分支的完整证据（含丢/放两个 ack） |
| `data/runs/state-drop-verified-20261029.json` | 验收后的完整 state（4 件在包、2 件 loose） |
| `data/runs/state-map-20261029.json` | 导航用的整层 state |

## 5. 验证

- `python agent/tests/test_mapnav.py` → ALL PASS（含真机整层）
- `python agent/tests/test_v0.py` → ALL PASS（内含 `test_backpack` 12 条）
- `powershell -File bridge/build.ps1` → 118,784 bytes；部署后 SHA256 `ED512364…`
- 真机：§2.1–§2.5

## 6. 下一步

1. **`play_event` 的 Click 为什么没收东西**：`SimpleClickItem.Click()` 的 IL 里
   `TakeAction()` 是协程（要几帧），而且 `Click` 开头有 `isOpen`/`travelling` 的门。
   要么分两步（先 `play`，下一帧再 `click`），要么直接读 `SimpleClickItem` 上的
   `Item2` 去调 `gameManager` 的入包接口。
2. **战斗存活**：这一班两次都是「走去宝箱 → 必经敌人房 → 硬打」。
   当前 9 格包 + 3 件物品打 3 个敌人会输（`incoming 26`/回合）。
   扩容来源（`spacesSaved`）没解决前，这个上限一直在。
3. `--make-room` 的默认值现在是 0（不丢）。要不要在「包满且地上有 loot」时默认开 1，
   需要一次 A/B 再说。
