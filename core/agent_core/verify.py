"""通用 delta 校验。只认路径和值，不认具体游戏字段。"""

from __future__ import annotations

from typing import Any

from .contract import Check, Expect

_MISSING = object()


def _resolve(delta: dict[str, Any], path: str) -> Any:
    if path in delta:
        return delta[path]
    return _MISSING


def _compare(actual: Any, op: str, expected: Any) -> bool:
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "lt":
        return actual < expected
    if op == "le":
        return actual <= expected
    if op == "gt":
        return actual > expected
    if op == "ge":
        return actual >= expected
    raise ValueError(f"unknown op: {op}")


def check(delta: dict[str, Any], expect: Expect) -> list[str]:
    """返回失败原因列表；空列表 = 通过。"""
    failures: list[str] = []
    for c in expect.checks:
        actual = _resolve(delta, c.path)
        if actual is _MISSING:
            failures.append(f"{c.path}: missing")
            continue
        try:
            ok = _compare(actual, c.op, c.value)
        except TypeError as exc:
            failures.append(f"{c.path}: {exc}")
            continue
        if not ok:
            failures.append(f"{c.path}: {actual!r} {c.op} {c.value!r} is false")
    return failures
