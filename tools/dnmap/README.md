# dnmap · .NET 程序集类型地图

把 Unity/Mono 游戏的 `Assembly-CSharp.dll` 导出成可 grep 的 JSON + TSV，
用来定位 `GameManager` / `Player` / `Enemy` 这类类，然后写反射读取。

只读元数据，**不执行任何游戏代码**，不改游戏文件。

## 为什么不用 dnSpy / ILSpy

本机没有（也懒得为这个装 SDK）。这里只用两样现成的东西：

- Windows 自带的 .NET Framework `csc.exe`（C# 5 上限，够用）
- MelonLoader 安装包里自带的 `Mono.Cecil.dll`（`games/<game>/.tools/...`）

## 用法

```powershell
# 1. 编译 + 导出（默认目标是本机 Backpack Hero）
powershell -NoProfile -ExecutionPolicy Bypass -File tools\dnmap\build.ps1

# 换个程序集
powershell -NoProfile -ExecutionPolicy Bypass -File tools\dnmap\build.ps1 `
    -Assembly 'C:\path\to\SomeGame_Data\Managed\Assembly-CSharp.dll' `
    -OutDir   'D:\out'

# 2. 查询
python tools\dnmap\show.py GameManager
python tools\dnmap\show.py GameManager --grep hp
python tools\dnmap\show.py --search Enemy
python tools\dnmap\show.py GameManager --methods-only
```

> 本机执行策略是 `Restricted`，`.ps1` 必须走 `-ExecutionPolicy Bypass -File`。

## 产物

默认写到 `games/backpack-hero-aipeiwo/runtime/inspect/`（已 gitignore）：

| 文件 | 内容 |
|---|---|
| `types.json` | 全量：类型 + 基类 + 字段 + 方法(含签名) + 属性 |
| `index.tsv` | 一行一个类型，`全名 / 基类 / 字段数 / 方法数 / 属性数 / 标志`，给 grep 用 |

`types.json` 结构：

```json
{"assembly":"Assembly-CSharp","version":"0.0.0.0","typeCount":1219,
 "types":[{"full":"GameManager","name":"GameManager","namespace":"",
           "base":"UnityEngine.MonoBehaviour","enum":false,"abstract":false,
           "fields":[{"name":"floor","type":"System.Int32","static":false,"public":true}],
           "methods":[{"name":"StartCombat","ret":"System.Void","static":false,
                       "params":[]}],
           "properties":[]}]}
```

## 读方法体：`Ildump.exe`（Mono.Cecil）

`Dump.cs` 只有签名。要回答「这个方法到底干了什么」（比如
`ItemMovement.EndDrag()` 为什么把我们设的坐标冲掉），用同一个 Mono.Cecil 直接读 IL：

```powershell
# 编译（bin 目录已 gitignore）
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $csc /nologo /target:exe /langversion:5 /out:tools\dnmap\bin\Ildump.exe `
    /r:tools\dnmap\bin\Mono.Cecil.dll tools\dnmap\Ildump.cs

# 整个类型（字段 + 所有方法 IL）
tools\dnmap\bin\Ildump.exe "<Assembly-CSharp.dll>" ItemMovement > itemmovement.il.txt

# 只看几个方法（按方法名精确匹配）
tools\dnmap\bin\Ildump.exe "<Assembly-CSharp.dll>" ItemMovement StartDrag EndDrag AddToGrid

# 嵌套的编译器生成类型（协程）也能点名
tools\dnmap\bin\Ildump.exe "<Assembly-CSharp.dll>" "LevelUpManager/<LevelUp>d__39" MoveNext
```

输出里 `IL_xxxx: opcode operand`，分支的 operand 会带目标偏移。Windows PowerShell 的
`>` 是 UTF-16，给别的工具读之前转一下：

```powershell
$c = Get-Content x.il.txt -Encoding Unicode
[IO.File]::WriteAllLines((Resolve-Path x.il.txt), $c, (New-Object Text.UTF8Encoding($false)))
```

## 已知限制

- **只有签名，没有方法体**。要读实现逻辑还得上真反编译器（本项目目前不需要）：
  我们靠反射读状态、调方法，不靠抄游戏代码。
- 语言版本 C# 5：改 `Dump.cs` 时别用字符串插值 / `?.` / 表达式体成员。
- `types.json` 约 6–10 MB，别塞进仓库（已由 `.gitignore` 的 `games/*/runtime/` 兜住）。
