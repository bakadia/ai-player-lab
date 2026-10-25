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
