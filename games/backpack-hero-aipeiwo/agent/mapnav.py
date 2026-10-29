"""地图房间图：把 `state.map.rooms` 变成能走路的邻接表。

为什么需要：`legal` 里的 `move_to_room` **会列出走不到的房间**（不同支路、被墙挡住的），
对着它们发 `move_to_room` 只会 30 秒超时（真机踩过两次，`loop.py` 的 `bad_rooms`
就是事后拉黑用的）。而 `rooms[]` 里其实已经有足够的信息：

    pos     [x, y]            房间中心，相邻房间相差 1.0
    doors   {left,right,up,down}    这一侧有没有门
    blocked {left,right,up,down}    这一侧是不是被墙/障碍堵住
    locked  是否上锁
    visible 地图上看得见吗

所以「a 和 b 连通」= 两边各自朝着对方的门都开着、并且两边都不是 blocked/上锁。
这张表是纯数据，能离线单测（`agent/tests/test_mapnav.py`），也顺手给
`tools/bph_nav.py` 用。

坐标是半格（y 常见 .5），所以先 round 再当 key。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

DIRS = ("left", "right", "up", "down")
OPPOSITE = {"left": "right", "right": "left", "up": "down", "down": "up"}
STEP = 1.0


def _pos_key(pos: Iterable[float] | None) -> tuple[float, float]:
    if not pos or len(pos) < 2:
        return (0.0, 0.0)
    return (round(float(pos[0]), 2), round(float(pos[1]), 2))


def _direction(a: tuple[float, float], b: tuple[float, float]) -> str | None:
    dx = round(b[0] - a[0], 2)
    dy = round(b[1] - a[1], 2)
    if dx == STEP and dy == 0:
        return "right"
    if dx == -STEP and dy == 0:
        return "left"
    if dy == STEP and dx == 0:
        return "up"
    if dy == -STEP and dx == 0:
        return "down"
    return None


@dataclass(frozen=True)
class Room:
    uid: str
    pos: tuple[float, float]
    doors: dict[str, bool] = field(default_factory=dict)
    blocked: dict[str, bool] = field(default_factory=dict)
    visible: bool = True
    locked: bool = False
    event: str | None = None

    @classmethod
    def from_state(cls, raw: dict[str, Any]) -> "Room":
        return cls(
            uid=raw.get("uid") or "",
            pos=_pos_key(raw.get("pos")),
            doors={d: bool((raw.get("doors") or {}).get(d)) for d in DIRS},
            blocked={d: bool((raw.get("blocked") or {}).get(d)) for d in DIRS},
            visible=bool(raw.get("visible", True)),
            locked=bool(raw.get("locked", False)),
            event=raw.get("event"),
        )

    def open_side(self, direction: str) -> bool:
        return bool(self.doors.get(direction)) and not bool(self.blocked.get(direction))


class RoomGraph:
    """房间邻接图。`bad_edges` 里是「图说有路、实测走不到」的边（无向）。"""

    def __init__(self, rooms: Iterable[Room], bad_edges: Iterable[tuple[str, str]] = ()):
        self.rooms: dict[str, Room] = {r.uid: r for r in rooms if r.uid}
        self.bad: set[tuple[str, str]] = set()
        for a, b in bad_edges:
            self.bad.add((a, b))
            self.bad.add((b, a))
        self._adj: dict[str, list[str]] = self._build()

    # ---- 构造 ----

    @classmethod
    def from_state(cls, state: dict[str, Any],
                   bad_edges: Iterable[tuple[str, str]] = ()) -> "RoomGraph":
        rooms = (state.get("map") or {}).get("rooms") or []
        return cls([Room.from_state(r) for r in rooms], bad_edges)

    def _build(self) -> dict[str, list[str]]:
        by_pos: dict[tuple[float, float], str] = {}
        for uid, room in self.rooms.items():
            by_pos.setdefault(room.pos, uid)

        adj: dict[str, list[str]] = {uid: [] for uid in self.rooms}
        for uid, room in self.rooms.items():
            if room.locked:
                continue
            for direction in DIRS:
                if not room.open_side(direction):
                    continue
                dx, dy = {
                    "left": (-STEP, 0.0), "right": (STEP, 0.0),
                    "up": (0.0, STEP), "down": (0.0, -STEP),
                }[direction]
                other_uid = by_pos.get((round(room.pos[0] + dx, 2), round(room.pos[1] + dy, 2)))
                if not other_uid or other_uid == uid:
                    continue
                other = self.rooms[other_uid]
                if other.locked:
                    continue
                if not other.open_side(OPPOSITE[direction]):
                    continue
                if (uid, other_uid) in self.bad:
                    continue
                adj[uid].append(other_uid)

        for uid in adj:
            adj[uid] = sorted(set(adj[uid]))
        return adj

    # ---- 查询 ----

    def room(self, uid: str) -> Room | None:
        return self.rooms.get(uid)

    def neighbors(self, uid: str) -> list[str]:
        return list(self._adj.get(uid, ()))

    def path(self, start: str, goal: str, *,
             allow_invisible: bool = False,
             avoid: Iterable[str] = ()) -> list[str] | None:
        """最短路（含起点和终点）。默认只走「看得见」的房间。

        看不见的房间通常也不能 `move_to_room`（legal 只列可见的），所以默认不穿过去；
        真没路时调用方可以 `allow_invisible=True` 再试一次（走一步看一步）。
        `avoid` 里的房间不当路过点（起点/终点除外）——用来绕开有敌人/事件的房间。
        """
        if start == goal:
            return [start]
        if start not in self.rooms or goal not in self.rooms:
            return None
        blocked = set(avoid)
        blocked.discard(start)
        blocked.discard(goal)

        def passable(uid: str) -> bool:
            if uid in blocked:
                return False
            if uid == start:
                return True
            room = self.rooms.get(uid)
            if room is None:
                return False
            return bool(allow_invisible or room.visible)

        prev: dict[str, str] = {}
        seen = {start}
        queue = deque([start])
        while queue:
            cur = queue.popleft()
            for nxt in self._adj.get(cur, ()):
                if nxt in seen or not passable(nxt):
                    continue
                prev[nxt] = cur
                if nxt == goal:
                    out = [goal]
                    while out[-1] != start:
                        out.append(prev[out[-1]])
                    out.reverse()
                    return out
                seen.add(nxt)
                queue.append(nxt)
        return None

    def next_hop(self, start: str, goal: str, **kw: Any) -> str | None:
        p = self.path(start, goal, **kw)
        if not p or len(p) < 2:
            return None
        return p[1]

    def first_step(self, start: str, goal: str,
                   legal_rooms: Iterable[str] | None = None,
                   avoid: Iterable[str] = ()) -> str | None:
        """下一次 `move_to_room` 该发哪个房间。

        房间被「迷雾」盖着时（`visible=false`）图上仍然有完整 doors/blocked，所以
        长距离路径按全图算；但**游戏只让你点已经揭示的房间**。所以路径上的下一格
        如果还点不了，就退一步：从当前格「能点到的邻居」里挑离目标最近的那个，
        先往那个方向探一格（走过去之后新的房间多半就揭示出来了）。
        `avoid` 里的房间不当路过点（有敌人的房间别一头撞进去）。
        """
        blocked = set(avoid)
        blocked.discard(start)
        blocked.discard(goal)
        p = self.path(start, goal, allow_invisible=True, avoid=blocked)
        if not p or len(p) < 2:
            return None
        hop = p[1]
        if legal_rooms is None:
            return hop
        legal = set(legal_rooms)
        if hop in legal:
            return hop

        cands = [n for n in self.neighbors(start) if n in legal and n not in blocked]
        if not cands:
            return None
        goal_pos = self.rooms[goal].pos

        def dist(uid: str) -> tuple[float, float]:
            pos = self.rooms[uid].pos
            return (abs(pos[0] - goal_pos[0]) + abs(pos[1] - goal_pos[1]),
                    pos[0] + pos[1])

        return min(cands, key=dist)

    def reachable(self, start: str, *, allow_invisible: bool = False) -> set[str]:
        if start not in self.rooms:
            return set()
        seen = {start}
        queue = deque([start])
        while queue:
            cur = queue.popleft()
            for nxt in self._adj.get(cur, ()):
                if nxt in seen:
                    continue
                if not allow_invisible and not self.rooms[nxt].visible:
                    continue
                seen.add(nxt)
                queue.append(nxt)
        return seen

    def visible_but_unreachable(self, start: str) -> list[str]:
        reach = self.reachable(start)
        return sorted(uid for uid, r in self.rooms.items()
                      if r.visible and uid not in reach)

    # ---- 调试 ----

    def describe(self, start: str | None = None) -> str:
        lines = []
        if start:
            reach = self.reachable(start)
            lines.append(f"current={start} reachable={len(reach)}/{len(self.rooms)}")
        for uid in sorted(self.rooms, key=lambda u: (self.rooms[u].pos[1], self.rooms[u].pos[0])):
            room = self.rooms[uid]
            doors = "".join(d[0].upper() if room.open_side(d) else "." for d in DIRS)
            flags = []
            if not room.visible:
                flags.append("hidden")
            if room.locked:
                flags.append("locked")
            if room.event:
                flags.append(room.event)
            if start and uid not in self.reachable(start):
                flags.append("UNREACHABLE")
            lines.append(f"  {uid:>8} {str(room.pos):>14} [{doors}] {' '.join(flags)}"
                         f" -> {','.join(self.neighbors(uid)) or '-'}")
        if start:
            missing = self.visible_but_unreachable(start)
            if missing:
                lines.append("  visible but unreachable: " + ",".join(missing))
        return "\n".join(lines)
