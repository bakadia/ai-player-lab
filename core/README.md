# core · agent-core

与游戏无关的那一层。只认契约，不认游戏。

## 现在有什么

- `contract.py`：`Observation` / `Action` / `Expect` / `Transition` / `Adapter` 协议
- `verify.py`：`Check` / `Expect` 的通用 delta 校验
- `tests/`：stdlib `unittest`，不依赖 pytest

## 还没有（等 MVP 长出来）

- `runner.py`：episode 循环、watchdog、重试、急停
- `eval.py`：指标、基线、报告
- `ledger.py`：效果账本

## 边界

1. 不 import 任何游戏代码。
2. 不出现 `backpack` / `jx3` 这类名字。
3. 不假设动作类型；动作是 adapter 定义的字符串 + 参数。
4. 校验只认「路径 → 值」的 delta，不认具体游戏字段。

## 跑测试

```powershell
cd core
python -m unittest discover -s tests -t .
```

## 契约版本

`core.v0`：字段还会变；MVP 期间允许破坏性修改，但要同步改所有 adapter。
