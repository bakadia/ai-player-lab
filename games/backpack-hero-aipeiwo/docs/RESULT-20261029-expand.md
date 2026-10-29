# RESULT · 2026-10-29 18:00 · 扩容真相：升级本来就会给格子（v8.19）

> 接 `HANDOFF-NEXT-20261029-1700.md` §4.1「战斗存活 / 扩容（`spacesSaved` 来源）」。
> 结论：**来源一直是有的，只是我们找错了地方**。正常升级就送格子，真机 9 → 13 → 17，
> 全程没用任何测试钩子。
>
> 桥接 **v8.19**：**125,440 bytes，SHA256 `391C5E24…`**（加了只读动作 `diag_levelups`）。

## 0. 一句话

`spacesSaved` 的写入点只有三处，真正加格子的那一处是
**`Player.IncrementLevel()`：`spacesSaved += levelUps[level].rewards[0].rewardValue`**。
Purse 的升级表每一级都是 `rewardType=0(加格子)`，`rewardValue` = 4/4/3/3/3/3/3/2/2/2/2/1/1/1/1/1，
所以第 0 级给 4 格、第 1 级又给 4 格…… 之前「新开局恒为 0、只能选 0 格」的判断，
是拿**被我们自己的 `grant_spaces`/`ChangeSpaces` 钩子污染过的状态**看出来的。

## 1. 怎么找到的（`tools/dnmap/Xref.exe`，这一班新加）

`Ildump` 能看「某个方法干了什么」，但回答不了「谁写了这个字段」。所以补了一个
Cecil 交叉引用工具：

```
Xref.exe <assembly> <Type> <Member>      # 打印每个命中方法 + IL 偏移 + 读/写/调
```

```
> Xref.exe ...Assembly-CSharp.dll LevelUpManager spacesSaved
== LevelUpManager::CanLevelUp            R IL_0059 ldfld
== LevelUpManager::EndLevelUp            W IL_00de stfld        (spacesSaved = numberToUnlock，存余额)
== LevelUpManager/<LevelUp>d__39::MoveNext  R IL_01d8 / IL_0376 (numberToUnlock = spacesSaved)
== Player::IncrementLevel                R IL_00b3 / W IL_00bf  <-- 唯一的「加」
== SaveManager/<Save>d__33::MoveNext     R IL_0530 (存档)
== SaveManager/<Load>d__36::MoveNext     W IL_021b (读档)
```

`Player.IncrementLevel()` 的 IL：

```
if (level >= levelUps.Count - 1) return;
if (experience < levelUps[level + 1].xpRequired) return;
level = level + 1;
experience -= levelUps[level].xpRequired;
AddExperience(0, transform.position);
Reward reward = levelUps[level].rewards[0];
LevelUpManager.main.spacesSaved += reward.rewardValue;      // <-- 就这一行
```

调用者只有两个：`LevelUpManager.LevelUpButton()` 和 `Player.LevelUp()`。

## 2. 新的只读探针 `diag_levelups`

```
{"level":-1,"experience":0,"spaces_saved":0,"number_to_unlock":0,"can_level_up":false,
 "character":"Purse","levelups_count":16,
 "levelups":[{"index":0,"xp_required":5,"rewards":[{"type":0,"value":4}]},
             {"index":1,"xp_required":10,"rewards":[{"type":0,"value":4}]},
             {"index":2,"xp_required":25,"rewards":[{"type":0,"value":3}]}, ...]}
```

`type=0` 就是「加格子」；这张表说明至少前 16 级每级都能扩。

## 3. 真机验收（无钩子）

```
升级前: level=-1 xp=8  spaces_saved=0 number_to_unlock=0 grid=9
level_up  -> delta {"inventory_phase":[8,0], "level":[-1,0]}
          -> level=0 xp=3 spaces_saved=4 number_to_unlock=4 leveling_up=true
unlock_spaces x4 -> number_to_unlock 4→3→2→1→0（每次 clicked=1，grid 仍是 9）
finish_level_up via=end -> delta {"inventory_phase":[0,1], "grid_squares":+4}
最终: grid=13 free=7
```

`agent/loop.py` 的默认策略也改了：`--unlock-spaces` 默认 **-1 = 自动**
（升级界面里先问 `diag_levelups` 拿 `number_to_unlock`，然后一格格点）。
真机跑一整段：`grid 13 → 17`、`free 11`，一条日志：

```
[step 11] -> level_up      delta {"inventory_phase":[8,0], "level":[-1,0]}
[step 12] -> unlock_spaces   （本级可扩 4 格（spaces_saved=4））
[step 13..15] -> unlock_spaces ×3
（下一轮）finish_level_up -> grid_squares 13 → 17
```

## 4. 改动清单

| 位置 | 改动 |
|---|---|
| `tools/dnmap/Xref.cs`（新）+ `build.ps1` | 静态交叉引用工具：谁读 / 谁写 / 谁调某个字段或方法 |
| `bridge/src/Actions.cs` | 新只读动作 **`diag_levelups`**：`level` / `experience` / `spaces_saved` / `number_to_unlock` / `leveling_up` / `can_level_up` + 角色整张 `levelUps` 表 |
| `agent/loop.py` | `--unlock-spaces` 默认 **-1（自动）**；升级界面先 `diag_levelups` 拿配额再一格格点；`--grant-spaces` 的 help 改成「测试钩子，别用」 |

## 5. 验证

- `build.ps1` → 125,440 bytes；三套测试 ALL PASS
- 真机：§3 的两段（手工一次 9→13、loop 自动一次 13→17）

## 6. 下一步

1. **扩容不再卡脖子**，但「9 格包 3 件物品打 3 个敌人」的早期强度问题还在：
   现在可以正常长大，问题是**能不能活到升级**。值得跑几局看存活曲线，
   并考虑「早期多打敌人房拿经验」vs「绕开敌人保命」的取舍。
2. `loop.py` 里那条 `place_item` 重试（满包 + 地上有战利品时，`--make-room` 没生效）
   要单独查：真机上出现过「同一件 loot 连发两次 place_item 都不 changed」，最后靠走开
   放弃（然后按 E16 就没了）。
3. E16：开完箱当场整理（手工流程最容易踩）。
4. `--make-room` 默认值 A/B、「撞上敌人房打完自动继续」两笔老账。
