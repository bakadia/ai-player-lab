"""与游戏无关的契约。

依赖方向：games/<game>/adapter -> core。core 不 import 任何游戏代码。

设计约束：
- Observation 是「某一刻的完整快照」，不是增量。
- Action 是「语义动作」，不是按键/坐标。
- Transition.delta 是扁平 dict：path -> 变化值或新值，由 adapter 定义。
- 每次 act 都必须带 Expect；没有 Expect 的动作视为 bug。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Mode = Literal["menu", "map", "battle", "reward", "shop", "event", "gameover"]


@dataclass(frozen=True)
class Entity:
    uid: str
    name: str
    hp: int
    max_hp: int
    block: int = 0
    intent: dict[str, Any] | None = None
    status: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Item:
    uid: str
    def_id: str
    name: str
    x: int
    y: int
    w: int
    h: int
    cost: int
    usable: bool
    target: str | None = None
    text: str = ""


@dataclass(frozen=True)
class Observation:
    schema: str
    seq: int
    ts: int
    mode: Mode
    run: dict[str, Any]
    player: Entity
    enemies: tuple[Entity, ...]
    items: tuple[Item, ...]
    backpack: dict[str, Any]
    legal: tuple[dict[str, Any], ...]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Action:
    type: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.params}


CheckOp = Literal["eq", "ne", "lt", "le", "gt", "ge"]


@dataclass(frozen=True)
class Check:
    """对 delta 的一条断言。

    path 例："enemies.e0.hp"（adapter 约定）、"player.energy"、"mode"。
    op 作用于 delta 里的值；对 hp 用 "le" + -1 表示「至少掉 1 点」。
    """

    path: str
    op: CheckOp
    value: Any


@dataclass(frozen=True)
class Expect:
    """动作后的最小断言集合；空 = 只要求 seq 前进。"""

    checks: tuple[Check, ...] = ()


@dataclass(frozen=True)
class Transition:
    ok: bool
    seq_before: int
    seq_after: int
    delta: dict[str, Any]
    ms: int
    error: str | None = None


class Adapter(Protocol):
    """一个游戏的适配层必须实现这四件事。"""

    def observe(self) -> Observation: ...

    def act(self, action: Action, expect: Expect) -> Transition: ...

    def reset(self, **kwargs: Any) -> None: ...

    def close(self) -> None: ...
