# 踩坑记录 · Backpack Hero

> 一条一条攒。每条都要能回答「怎么发现的 / 为什么 / 以后怎么办」。
> 按发现顺序编号，引用时写 `E1`。

## E1 · MelonGame 开发者名写错，mod 静默不加载

**怎么发现的**：装好 MelonLoader、`Mods\BphBridge.dll` 也在位，启动后日志只有一行
`0 Mods loaded.`，没有报错、没有异常，控制台一切正常。

**真相**：往上翻日志其实说了，只是不明显：

```
[23:07:30.866] Melon Assembly loaded: '.\Mods\BphBridge.dll'
[23:07:30.883] 'BphBridge v0.0.1' is incompatible:
[23:07:30.883] - BphBridge is only compatible with the following Games:
[23:07:30.883]     - 'Backpack Hero' by Jaspel
```

而游戏真实身份是：

```
Game Name: Backpack Hero
Game Developer: TheJaspel
```

我们写的是 `[assembly: MelonGame("Jaspel", "Backpack Hero")]`，第一个参数（开发者）
少了 `The`。DLL 本身被正常读取、SHA256 都打了，只是兼容性检查没过。

**为什么坑**：MelonLoader 把「加载失败」降级成「不兼容」，日志级别是普通信息，
结尾又只有一句 `0 Mods loaded`。看起来像「没扫描到文件」，实际是「扫到了但被拒」。

**以后怎么办**：

- `MelonGameAttribute` 的签名是 `.ctor(String developer, String name)`，**顺序是开发者在前**，
  用 `tools/dnmap/show.py MelonGameAttribute` 可以随时确认。
- 开发者的准确值只信日志里的 `Game Developer:` 那一行，别猜（`Jaspel` 是游戏作者名，
  不等于 Unity 里登记的 company）。
- 排查顺序固定为：文件在不在 → `Melon Assembly loaded` 有没有出现 → 有没有
  `is incompatible` → 最后才怀疑代码。

## E2 · 覆盖 `Mods\*.dll` 报 user-mapped section open

**怎么发现的**：改好代码重新编译，往 `Mods\` 覆盖时失败：

```
The requested operation cannot be performed on a file with a user-mapped section open.
```

**为什么**：MelonLoader 把 mod 程序集作为内存映射节载入，进程不退出就一直持有映射。
注意此时**任务管理器/`Get-Process` 可能看不到进程**（如果从受限会话里查），
别被「查不到进程」骗过去——文件锁才是可靠证据。

**以后怎么办**：

- 部署 mod 前先确认游戏真的退了；有疑问就试着独占打开目标文件探一下锁。
- 用 `cua_driver_native__list_windows` 按窗口标题确认游戏在不在，比查进程稳。
- 覆盖失败**不要**去删文件，同样会被拒绝；老老实实退游戏。

## E3 · 本机没有可用的现代 C# 工具链

**怎么发现的**：`dotnet --list-sdks` 空；`msbuild BphBridge.csproj` 报 MSB4019，
提示 `MSBuild\Current\Bin\Roslyn\Microsoft.CSharp.Core.targets` 不存在。

**为什么**：装了 VS2022 Community，但没装「.NET 桌面开发」工作负载（缺 Roslyn 编译器）；
`C:\Program Files\dotnet` 只有 runtime 没有 SDK。

**以后怎么办**：

- 构建走 `bridge/build.ps1`（系统自带 `csc`，**C# 5 上限**）。
- 写 bridge 代码时禁用：字符串插值 `$""`、`?.`、表达式体成员 `=>`、`nameof`、
  元组、`out var`。
- 想用新语法就装 .NET SDK 或 VS 的 C# 工作负载——不是当前阻塞项，先不装。

## E4 · 「有没有变化」用 diff 字符串判空，恒为真

**怎么发现的**：动作层第一次跑通，发了个 `noop`（什么都不做），回执是
`{"ok":true,"changed":true,"delta":{"enemies":{}}}`。

**为什么**：`changed` 写成了 `DiffJson() != "{}"`，而 `DiffJson()` 无论有没有变化
都会写出 `"enemies":{}` 这个键，所以判断永远为真。

**为什么这很致命**：验收项 A4 要求「每个动作都有 expected/actual delta，
静默失败漏检 = 0」。`changed` 恒真意味着**任何动作都被判成功**——
正是这套东西最想防的那类失败。

**以后怎么办**：

- 「有没有变」必须用结构化比对（`Snapshot.Differs()` 逐字段），**不许**拿序列化结果判空。
- 生成 delta 时，**先收集、非空才写键**，别写空容器占位。
- 每个动作都要留一个 `noop` 回归用例：`noop` 必须回 `changed=false, delta={}`。

## E5 · PowerShell 里 `\"` 不是转义，字符串会提前结束

**怎么发现的**：`git commit -m "... \"enemies\":{} ..."` 报
`fatal: options '-m' and '-c' cannot be used together`——明明只传了 `-m`。

**为什么**：PowerShell 用反引号 `` ` `` 转义，`\"` 实际是「反斜杠 + 字符串结束符」。
字符串在那里就断了，后面的内容变成独立参数，其中某些被 git 当成了选项。

**以后怎么办**：

- 多行/含引号的提交信息用 here-string：`$msg = @' ... '@` 再 `git commit -m $msg`。
- 或者把信息写进文件，用 `git commit -F <file>`。
- 同类问题：`.ps1` 里别写非 ASCII 注释（见 E6）。

## E6 · 无 BOM 的 UTF-8 PowerShell 脚本被按 GBK 解码

**怎么发现的**：往 `bridge/build.ps1` 里加了几行中文注释后，构建突然报
`fatal error CS2007: 无法识别的选项 "/"`——一个和注释毫无关系的错误。
更早一次同样现象，报的是 `Unexpected token ')'`。

**为什么**：Windows PowerShell 5.1 读没有 BOM 的 `.ps1` 时会用本地 ANSI 代码页（中文系统是 GBK）。
UTF-8 的中文字节被当成 GBK 解码后，可能吞掉后面的换行符或引号，
于是**下一行代码被并进注释**——`$cscArgs = @(...)` 整行消失，变量根本没赋值。

**以后怎么办**：

- 构建/测试脚本一律 **纯 ASCII**（`bridge/build.ps1`、`tests/run-tests.ps1` 都是）。
- 写完脚本可以验一下：`(Get-Content -Encoding Byte x.ps1 | Where-Object {$_ -gt 127}).Count` 应为 0。
- 中文说明写在 `.md` 和 `.cs` 里；`.cs` 靠 csc 的 `/codepage:65001` 保证按 UTF-8 读。

## E7 · 跨进程文件刚写完就读，会瞬时 PermissionError

**怎么发现的**：`run.py` 崩过：

```
PermissionError: [Errno 13] Permission denied: ...runtime\ack\995516.json
```

但紧接着用 `Get-Content` 读同一个文件，**一切正常**。

**为什么**：游戏进程刚把 ack 文件写出来，Windows 上那一瞬间安全描述符还没完全落定，
python 正好撞上。是**瞬时竞争**，不是权限配错、也不是文件锁。

**为什么坑**：它出现得极不规律——之前几十次都正常，只有跑得快、文件刚落地时才撞上。
而且报的是 PermissionError，很容易让人去查 ACL，方向全错。

**以后怎么办**：

- 跨进程文件交接，读的一方要**容忍瞬时失败**：`FileNotFoundError` 和
  `PermissionError` 一样当「还没好，再等等」处理，统一用 `OSError` 捕获。
- 判据：**同一个文件手动能读、程序刚读失败** → 基本就是瞬时竞争，别去动权限。
- 写的一方用「临时文件 + 原子替换」只是必要条件，不能保证读者那一瞬间一定成功。

## E8 · 用「类型名像不像」代替「它在场景里是什么角色」

**怎么发现的**：交接文档一度断言「地图节点就是 `EventButton`」，依据是
wiki 说「地图节点在代码里是 EventButton」，而 `EventButton` 确实有 23 个字段、
带 `PossibleOutcome` 和 `PerfromEffect` 协程——名字和「节点」对得上。

后来把字段签名逐条看下来：

```
requirement = goldCost / itemSacrifice / itemSelect / carvingSelectNonForged
requiredGold / requiredItemType / requiredRarities / sacrificeAccepted
possibleOutcomes[] / buttonText / chosenOutCome / played
PayCosts() / ShowButtons() / PerfromEffect()
```

这是**事件对话框里的选项按钮**（付钱、献祭、选一件），不是地图上的地点。
真正的地图是 `DungeonEvent`（16 种类型）+ `DungeonRoom` + `Door` + `DungeonPlayer`。

**为什么坑**：`EventButton` 确实会「在地图阶段出现」，所以观察到的现象和结论不矛盾；
但它是**已经在事件内部**才出现的 UI，不是「事件的位置」。一旦当成节点，
下一步就会设计出不存在的动作（`choose_node(index)`），整条线索作废。

**以后怎么办**：

- 区分「世界对象」和「UI 控件」，先看三样：
  - 有没有**位置/归属**字段（`transform` / `room` / `position` / `doorNumber`）；
    UI 控件一般只有 `buttonText` / `onClick` / `possibleOutcomes` / `requirement`。
  - 有没有 `static List<T>` 全量枚举——世界对象常见，UI 控件一般没有。
  - 它在什么条件下被实例化：跟着**场景/房间**生成，还是跟着**对话框**生成。
- 「这个类型在地图阶段能看到」≠「这个类型就是地图的内容」。
- 结论落到文档时标明**证据等级**：静态签名 / 运行时实测。
  只有前者时，措辞必须是「疑似」，并且下一步要设计一个能证伪它的实测。

## E9 · `inventory_phase=3` 但 `inSpecialReorg=false`，`FinishReorganizeButton()` 是空操作

**怎么发现的**：一局在战斗中用 `CURSED POTION` 后进入整理阶段（`inventory_phase=3`、
`specialReorganization`），loop 连发 4 次 `finish_reorganize`，每次都是
`ok=false changed=false ms=20000 error=timeout (... inventoryPhase still reorganize)`。
屏幕上的「结束整理」按钮看得见、点得动，但点完没反应。

**真相**：反编译 `GameManager.FinishReorganizeButton()`（用 MelonLoader 自带 Mono.Cecil
写了个临时 IL dumper）后看到第一段：

```
if (!inSpecialReorg && inventoryPhase != choose(2) && !limitedItemReorganize) return;
```

也就是说，**方法自己会先看 `inSpecialReorg` 这个布尔**；`inventory_phase==3` 并不是它的判据。
当游戏因为某种原因把 `inSpecialReorg` 清成 false 却仍停在 phase 3 时，方法直接 return，
既不报错也不改变状态。UI 按钮绑的就是这个方法，所以点按钮同样没用。

**为什么坑**：state 只暴露 `inventory_phase`，看起来「已经识别对了」，但真正的门是另一个字段；
20 秒超时像「按钮没点到 / 动画没结束」，很容易往输入或时序方向查。

**以后怎么办**：

- `state.ui` 现在同时输出 `in_special_reorg` 和 `skippable_reorganization`（v7 起）。
  先看 `in_special_reorg` 是不是 false，再怀疑别的。
- 偶发一次 no-op 很常见（本次 80 步里第一次 20s 超时、第二次 1.7s 成功）；
  loop 的重试是有效的。
- 如果连续多次 false，不要继续点：直接 `retry_run` 重开（本次实测能恢复），
  或者加一个只读诊断 action 把 `itemsForSpecialReorganization` 打出来再决定。
- 不要用「直接置 `inventoryPhase=0`」硬修——`FinishReorganizeButton()` 还负责
  `ClearAreasForSpecialReorganization`、`RemoveItemsOutsideGrid`、`ChangeCurse` 等清理，
  跳过会留下脏状态。

## E10 · 被动饰品进了 `legal`，用一次 0/0，账本永远翻不了身

**怎么发现的**：`REVERSAL AMULET` / `LUCKY STAR` 这类饰品被 `use_item` 后
`ack.ok=false changed=false`；按「只记已验证样本」的规则不记账本，
但 `explored` 已记上，之后永远 0 分。`TUNIC` 也出现过同样的 `ok=false`。

**真相**：旧 `StateReader` 的合法性只看 `playType == Active(0)` 和 `is_relic`。
被动饰品的 `playType` 也是 Active（0），但 `Item2.CanBeUsedActive(...)` 返回 0：
它没有主动费用/条件，主动使用本来就什么都不发生。
`REVERSAL AMULET` 的 wiki 效果是「这一行每有一个 Hazard，相邻武器 +1 伤害」——纯被动。

**为什么坑**：这不是「效果不可见」，是「这个物品根本不该被使用」。
回执能说清「用了但什么都没发生」，但正确做法是**不让它进 legal**。

**以后怎么办**：

- v7 起 `CanBeUsedActive` 返回 0 是硬门（reason=`cannot_use_active`），不再进 legal；
  返回 -1（调用失败）才退回手写规则。
- 传的是物品自己 `costs` 的拷贝；不要传空表——空表会让所有物品都返回 false
  （上一班踩过，`/r` 的注释还留着）。
- 判断「被动/主动」不能只看 `playType`；`Item2.ItemType` 的 Accessory(6)/Relic(22)
  只能当辅助信号，最终以游戏自己的 `CanBeUsedActive` 为准。

## E11 · 用 PowerShell 管道改源码，整个文件的中文注释被转码毁掉

**怎么发现的**：想批量替换 `Actions.cs` 里一个函数名，图省事写了

```powershell
(Get-Content .\src\Actions.cs -Raw) -replace 'OldName\(', 'NewName(' | Set-Content .\src\Actions.cs -Encoding UTF8
```

改完编译照过（代码行是 ASCII），但文件里所有中文注释变成 `锛?` 之类的乱码，
而且 **171 行注释和下一行代码被合并成一行**——`// 注释` 把后面的
`RefreshOccupancy();` 吞掉，运行时不报错、只是少执行一句。

**真相**：Windows PowerShell 5.1 的 `Get-Content -Raw` 在没有 BOM 时按**系统 ANSI
（这里是 cp936/GBK）**解码。源码是 UTF-8 无 BOM，于是每个汉字被拆成两个 GBK 字符；
写回时又按 UTF-8 编码，等于「UTF-8→GBK→UTF-8」双重转码。
行尾的中文经常以 3 字节 UTF-8 字符结尾，GBK 解码把最后 1 个字节和后面的 `\r\n`
一起吃成 `?`，换行就没了。

**为什么坑**：

- 编译不报错：乱码只在注释里，被吞掉的代码行也「合法」（变成注释的一部分）。
- 尺寸/哈希仍然能对上（同一份源码两次构建本来就不保证字节相同），容易以为没事。
- 没有 undo：`Set-Content` 直接覆盖原文件。

**以后怎么办**：

- **改源码只用编辑工具的 `edit`/`write`**，不要用 PowerShell 文本管道/`-replace` 落盘。
- 非要命令行处理，必须显式编码：
  `Get-Content -Encoding UTF8` + `Set-Content -Encoding UTF8`（PS5.1 仍会加 BOM），
  或者用 Python：`open(..., encoding='utf-8')`。
- 已经毁了可以救：用 .NET `Encoding.GetEncoding(936)` 建「字符→原始字节」逆表，
  把乱码文本转回字节再按 UTF-8 解码；行尾被吃掉的换行按「`?` + 缩进」补回去。
  本次就这么恢复的，并用「逐方法比 IL」证明代码没变（除预期改动外完全一致）。

## E12 · `RemoveFromGrid()` 会把 `itemMovementManager.isRunning` 置真，同帧的 `StartDrag` 全被拒

**怎么发现的**：`swap_items` 的第一步用 `RemoveFromGrid(A)` 把 A 从背包里摘下来
（A 的旧格于是算空），紧接第二步 `StartDrag(B)` 却每次都被拒。原来的报错只说
`StartDrag refused: isDragging=false`，完全看不出原因；把判定链路的每个字段都打进
错误信息之后才看到：

```
swap aborted (i-4198: StartDrag refused: inventoryPhase=1,battlePhase=0,
IsComplete=False,isCheckingEffects=True,isStoppingDrag=False,isDragging=False,
isRunning=True,isCheckingForPuzzle=False)
```

**真相**：`ItemMovement.StartDrag()` 的 IL 门是
`isStoppingDrag / GameManager.dead / GameFlowManager 为 null /
(!IsComplete() && inventoryPhase != 3) / itemMovementManager.isRunning /
isCheckingForPuzzle / GameManager.draggingItem != null`。
`RemoveFromGrid()` 在 `GameFlowManager.isCheckingEffects == true` 时会走
「启动 `RemoveAtEnd()` 协程」那条分支，协程把 `ItemMovementManager.isRunning` 置真，
于是同一帧里再也抬不起第二件物品。

**以后怎么办**：

- 「把物品从背包里摘下来」不要调 `RemoveFromGrid`，用 `Actions.LiftFromGrid()`：
  只做必要状态（`ClearGridPositions` + `parentInventoryGrid=null` +
  `inGrid/inPouch=false` + `objsInGrid-1`），不起协程。
- 任何「一个动作里要挪两件物品」的地方，都先看一眼 `isRunning`／`draggingItem`。

## E13 · 同一时刻只能拖一件；`EndDrag` 不会清 `draggingItem`

**怎么发现的**：`swap` 修好 E12 之后仍然失败在第三步——
`StartDrag refused: ...,draggingItem=Wooden Sword Variant(Clone)`。
也就是说**上一步的 EndDrag 已经跑完了，`draggingItem` 还指着那件物品**。
它是在游戏自己的 `Update` 里（隔帧）才被清掉的，所以：

- 分成两条命令的 `move_item` / `place_item` 连续做没问题（中间隔了十几帧）；
- 同一条命令里连做两次 StartDrag 一定失败。

**以后怎么办**：

- 一条动作最多 `StartDrag` 一次。其余落位用 `Actions.PlaceLifted()`：
  `transform.position = target` 之后直接调 public 的 `AddToGrid(false)`
  （`EndDrag` 内部也就是这一步：SnapToGrid + 占格判定 + 设 parent + `objsInGrid+1`）。
- 回滚同理：`Actions.RestoreItem()` 走「摘格子 → AddToGrid」的手工路径，
  不要再依赖 StartDrag/EndDrag。
- 报错时把 `draggingItem` 的名字打出来，否则「谁占着拖拽态」根本看不出来。

## E14 · `legal` 不是可达性：`move_to_room` 会列出走不到的房间

**怎么发现的**：想去 10 格外的宝箱，对着 `legal` 里的 `move_to_room` 直接发，
每次都等满 30 秒超时（`timeout after 30076ms (not arrived at r-5858)`）。
另外还有两个连带现象：

- **地图关着时** `legal` 里只有 `open_map`，一个 `move_to_room` 都没有；
- 远处房间要**地图开着**才 reveal（`canBeSeenOnMap`），关掉之后又只剩脚下两格。

推论：`move_to_room` 的合法集合 ≠ 图上的邻接关系。真机数据里 `rooms[]` 的
`pos/doors/blocked/locked/visible` 是**完整**的（连迷雾房间都有 doors），
所以可达性应该用房间图算，动作层面再和 `legal` 求交集。

**以后怎么办**：

- 用 `agent/mapnav.py`：`RoomGraph.from_state(state)` → `path/first_step`。
- **一次只发一格**，看 `room_at` 真的变了再走下一格；失败就把这条边记进 `bad_edges`。
- 走路前确保 `viewing_top == 1`（必要时先发 `open_map`）；到了事件房间再 `close_map`。
- 迷雾里的下一格点不了时，朝目标方向挑一个「能点到的邻居」先探过去（`first_step` 就是干这个的）。

## E15 · 地图上的 SimpleClickItem：**`Click()` 是好的**，坑在「它到底产出什么」和「什么时候量」

> v8.17 更正。原来的结论（`start_event` 无效 + `Click()` 收不进包）前半句对、后半句是
> **误判**：当时点的那两个 `SimpleClickItem` 根本不是「掉东西」的事件。

**IL 事实**（`Ildump` 逐方法核对，v8.17）：

```
DungeonEvent.Play()        if (GetEventProperty(open)) return;   // open=6，幂等
                           if (itemsToSpawn.Count == 0) return;
                           AddEventProperty(open, 0);            // 先打标记再生成
                           ... Instantiate(itemsToSpawn[i]) ...  // 绑 SimpleClickItem.dungeonEvent
SimpleClickItem.Start()    dungeonEvent.GetItems(out items)      // 取事件底下挂着、带 Item2 的子物体
                           → GetItems 会对带 Item2 的孩子 SetActive(true)（且不还原）
                           → 把它们摆到祭坛前（back*2 + up*0.25）
SimpleClickItem.Click()    if (dungeonEvent && IsFinished()) return;
                           if (gameManager.travelling) return;
                           if (isOpen) return;                   // 点过一次就不会再进去
                           ... FinishEvent() + StartCoroutine(TakeAction()) + isOpen = true
TakeAction()               协程，先等 0.4/animator.speed 秒，再按 effect.type 分发：
                           0=HP 1=EXP 2=Gold 3=地牢属性 5=meta 6=生成物品
                           **4=item：什么都不做**（东西是 Start() 摆出来的，靠玩家拖进包）
```

**真机证据**（`tools/bph_verify_pickup.py`，两层楼四个事件）：

| 事件 | effect | 点下去之后 |
|---|---|---|
| d-5680 `QuickBoost HP Variant` | `0 / +5` | `player_hp 36 → 40`（满血截断） |
| d-5696 `QuickBoost Gold Variant` | `2 / +5` | `gold 0 → 5` |
| d-5584 `QuickBoost Gold Variant` | `2 / +5` | `gold 0 → 5`，ack `ok=True changed=True` |

也就是说 `Click()` 一点问题都没有。**这层楼上的 SimpleClickItem 全是 `QuickBoost <属性>`
变体**（HP/Gold），`effects` 只有一条、`assigned` 是空串、`event_items` 是空数组——
它们本来就不会往地上掉东西，`loose 0 → 0` 是正常结果，不是失败。

**为什么坑**：

1. **effect 类型不同，产出去向完全不同**。只有 `type=4(item)` 才是「东西摆到地上」，
   而那一份是 `Start()` 干的（Play 之后**下一帧**），不是 `Click()`。
2. **同一帧量不到**。`Play()` 实例化的对象这一帧还没跑 `Start()`，那时 `items_loose`
   必然是 0。要看结果必须**隔一两帧再读 state**（`--settle`）。
3. **`Play()` 本来幂等，是老代码把它破了**。`Play()` 开头就看 `open` 属性；
   v8.16 的 `play_event` 却每次先 `RemoveEventProperty(open)` 再 `Play()`，于是每发一次
   就多生成一份（真机 `spawned_click_items` 2→4）。v8.17 去掉了这一行：现在
   重复发 `play_event{play:true}` 会得到 `play_skip="already_open"`，对象数不变。

**以后怎么办**：

- 收地图上的东西就是 `play_event{play:false, click:true}`（Play 交给游戏自己，或者
  只在 `play_skip` 为空时才需要 Play）；两次之间隔 1.5 秒。
- 先看 diag 里的 `click_items[].effects[].type` 决定预期：不是 4 就别等 loot，
  等 `hp/gold/exp` 的 delta。
- `state.backpack.items_loose` 只统计 `Item2.allItems` 里的活跃物品；「生成了但没激活」
  和「没生成」在 state 上长得一样 → 用新加的 `diag_items`（活跃/未激活分开列）分辨。
- `play_event` 的 ack 门只在事件真有 `type=4` 时才等 loot（`PlayEventExpectsLoot`），
  否则会像 v8.17 第一版那样白等 15 秒还回 `ok=false`。

## E16 · 宝箱开出来、地图上的地上物品：**离开房间就销毁**

**怎么发现的**：`open_chest` 掉了 4 件（LEATHER CAP / LIQUID ARMOR / BOWBLADE /
APPRENTICE STAFF，`items_loose` 0→4），接着走了三格到另一个房间，
`items_loose` 直接回到 0，`unplaced` 也空了 —— 一件都没捡到，也捡不回来了。

**为什么**：游戏自己的 `DestroyAllItemsOutsideGrid` 会在换房间时清掉「不在背包格子里」
的物品（`World.cs` 的注释里早记过：教程开局那 3 件也是摆在地上、走开就没了）。
所以「地上的东西」是**只在这个房间里有效**的临时状态。

**以后怎么办**：

- 开箱 / 拿到地上物品之后，**当场**用 `place_item`（`place_with_room`）整理完再走；
  能放的先放，放不下的用 `--make-room N` 丢低价值的换。
- `loop.py` 本身没问题：`place_item` 分支排在 `open_chest`/`enter_door`/换房间之前，
  它会先把地上的一件件放进来。**踩坑的是手工流程**：`open_chest` 之后直接用
  `tools/bph_nav.py go` 走开（这一班就是这么丢的 4 件）。
- 反过来也成立：想验证「地上物品能不能捡」，**必须**在同一个房间里量，
  别在下一格回头看 `items_loose`。

## E17 · 别拿被测试钩子污染过的状态当结论（扩容「恒为 0」是误判）

**怎么发现的**：连续两个班都写着「新开局 `spacesSaved` 恒为 0，升级界面只能选 0 格，
`grant_spaces` 是测试钩子」——一直到这一班用 `Xref.exe` 反查写入点，才看到真正加格子的
是 `Player.IncrementLevel()`：`spacesSaved += levelUps[level].rewards[0].rewardValue`，
而 Purse 的升级表每级都是 `rewardType=0`、`rewardValue` 4/4/3/3/…。
真机不开钩子直接升一级：`spaces_saved 0 → 4`、`number_to_unlock 4`、点 4 个
GridExtension 收尾后 `grid 9 → 13`。

**为什么坑**：早期为了让「选格子」界面动起来，用过
`grant_spaces`（`ChangeSpaces(N)+SetupSpaces`）和把 `numberToUnlock` 反复当参数传，
`ChangeSpaces` 是**累加**语义，配额被刷到过 256；`EndLevelUp` 又把 `numberToUnlock`
写回 `spacesSaved`。于是之后看到的 `spacesSaved` 全是假的，真正的正常路径一次都没跑到，
反而被记成「来源不明」。

**以后怎么办**：

- **写结论之前，先确认状态没被自己的钩子动过**：`diag_levelups` 现在能把
  `level / experience / spaces_saved / number_to_unlock / leveling_up / can_level_up`
  和整张升级表一次打出来，先看它是不是「干净」的（`spaces_saved` 不该是 256 这种数）。
- 有了 `Xref.exe` 就别靠猜「谁写的这个字段」：`Xref <asm> <Type> <Member>` 一次列全
  读/写/调用点（这一班的 `spacesSaved` 只有 3 个写点，1 个是真来源）。
- 测试钩子（`grant_spaces` / `clear_spaces` / 直接改 `numberToUnlock`）用完就
  `retry_run` 重开一局，别在污染的局里继续做验收。

## E18 · `start_event` 对「点物体」的事件是空操作，循环会原地卡死

**怎么发现的**：连续两次跑 `loop.py` 都停在同一类房间上：`legal` 里有 `start_event`，
但发出去永远是 `ok=False changed=False ms=400 delta={}`；连发 4 次就被判「卡死」。
`diag_event` 显示 `relevant_event: null`，而房间里的确有事件。

**为什么**：`DungeonPlayer.StartEvent()` 走的是 `GetRelevantEvent(playerPos)`，
它只认「触发型」事件（敌人/宝箱/门）；**靠点物体交互的两类永远是 null**：

| 事件类型 | 真正入口 |
|---|---|
| 11 `SimpleClickItem`（QuickBoost +HP/+XP/+Gold） | `DungeonEvent.Play()` + `SimpleClickItem.Click()` → 桥接的 `play_event` |
| 4 `Chance`（如 `Capybara Variant`，挂 `EventNPC`） | `EventNPC.OpenEvent()`（private，`OnPressMouseUpAsButton`/`OnPressStart("confirm")` 都只是调它）→ 之后弹出 `RandomEventMaster` 的随机事件 UI（**还没接**） |

**以后怎么办**：

- `loop.py` 现在按事件类型分流：本房间事件 `type == 11` 就发 `play_event`；
  其它类型 `start_event` 连续两次空操作就 **`blocked_events.add(uid)`**，
  房间选择器会跳过它（之前只拉黑不改分支，照样会重发到判卡死）。
- `Chance` 事件目前**只能拉黑**。要用它得接着做 `EventNPC.OpenEvent()` +
  `RandomEventMaster` 那套 UI（选项/奖励），这是一块独立的工作。
- 判断「这格能不能开」最快的方式：`diag_event` 的 `relevant_event` 是不是 null。






