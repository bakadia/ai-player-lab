"""房间图导航的离线自测。

    python games/backpack-hero-aipeiwo/agent/tests/test_mapnav.py

两部分：
- 合成的迷宫（直线 / 拐弯 / 墙 / 上锁 / 看不见 / 已知走不通的边）
- 真机录下来的整层地图（`fixtures/state-map-floor1.json`），
  用真实 doors/blocked 验一遍「连通 / 不连通」和「走到出口的最短路」
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = HERE.parent
sys.path.insert(0, str(AGENT))

from mapnav import DIRS, Room, RoomGraph  # noqa: E402


def room(uid, pos, doors, blocked=(), visible=True, locked=False, event=None):
    return Room(
        uid=uid,
        pos=(float(pos[0]), float(pos[1])),
        doors={d: (d in doors) for d in DIRS},
        blocked={d: (d in blocked) for d in DIRS},
        visible=visible,
        locked=locked,
        event=event,
    )


def test_line_and_wall():
    # 一条横线 a-b-c，c 右边有门但 d 的对应侧被墙堵住
    g = RoomGraph([
        room("a", (0, 0), "right"),
        room("b", (1, 0), "left right"),
        room("c", (2, 0), "left right"),
        room("d", (3, 0), "left", blocked="left"),
    ])
    assert g.neighbors("a") == ["b"], g.neighbors("a")
    assert g.neighbors("b") == ["a", "c"], g.neighbors("b")
    # c-d 之间：两边门都开着，但 d 那一侧 blocked -> 不连通
    assert "d" not in g.neighbors("c"), g.neighbors("c")
    assert g.path("a", "c") == ["a", "b", "c"], g.path("a", "c")
    assert g.next_hop("a", "c") == "b"
    assert g.path("a", "d") is None
    print("PASS line + wall")


def test_locked_and_hidden():
    g = RoomGraph([
        room("a", (0, 0), "right up"),
        room("b", (1, 0), "left right"),
        room("c", (2, 0), "left right", locked=True),    # 上锁：两边都不连通
        room("d", (3, 0), "left"),
        room("e", (0, 1), "down", visible=False),        # 看不见：默认不穿
    ])
    assert g.neighbors("b") == ["a"], g.neighbors("b")     # c 上锁 -> 不连
    assert g.path("a", "d") is None
    assert "e" not in g.reachable("a")
    assert g.path("a", "e") is None
    assert g.path("a", "e", allow_invisible=True) == ["a", "e"]
    print("PASS locked + hidden")


def test_bad_edges_reroute():
    # 菱形：a-b-d 与 a-c-d。b 那条实测走不通 -> 应该绕 c
    rooms = [
        room("a", (0, 0), "right up"),
        room("b", (1, 0), "left up"),
        room("c", (0, 1), "right down"),
        room("d", (1, 1), "left down"),
    ]
    g = RoomGraph(rooms)
    first = g.path("a", "d")
    assert first is not None and len(first) == 3, first
    g2 = RoomGraph(rooms, bad_edges=[("a", "b")])
    assert g2.path("a", "d") == ["a", "c", "d"], g2.path("a", "d")
    print("PASS bad edges reroute")


def test_visible_but_unreachable():
    g = RoomGraph([
        room("a", (0, 0), "right"),
        room("b", (1, 0), "left"),
        room("x", (5, 5), "left", visible=True),      # 看得见但没门连过来
    ])
    assert g.visible_but_unreachable("a") == ["x"], g.visible_but_unreachable("a")
    print("PASS visible but unreachable")


def test_real_floor():
    f = HERE / "fixtures" / "state-map-floor1.json"
    if not f.exists():
        print("SKIP real floor (fixture missing)")
        return
    state = json.loads(f.read_text(encoding="utf-8"))
    g = RoomGraph.from_state(state)
    cur = ((state.get("map") or {}).get("player") or {}).get("room_at")
    assert cur in g.rooms, cur

    # 真机数据里所有房间都看得见；至少要走得到一半以上，而且不能走到不相邻的房间
    reach = g.reachable(cur)
    assert len(reach) >= 3, sorted(reach)
    assert cur in reach

    # 每个邻居都必须是「真实相邻」的：位置差恰好一格
    for uid in g.rooms:
        for nxt in g.neighbors(uid):
            a, b = g.rooms[uid].pos, g.rooms[nxt].pos
            assert abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1.0, (uid, nxt, a, b)

    # 最短路必须是逐格相邻的
    exits = [r.uid for r in g.rooms.values()
             if r.event and _event_type(state, r.event) == "Exit"]
    assert exits, "这层应该至少有一个出口"
    target = next((e for e in exits if e in reach), None)
    if target:
        p = g.path(cur, target)
        assert p and p[0] == cur and p[-1] == target, p
        for a, b in zip(p, p[1:]):
            assert b in g.neighbors(a), (a, b)
        print(f"PASS real floor: {len(g.rooms)} 间, 可达 {len(reach)}, "
              f"到出口 {target} 走 {len(p) - 1} 步")
    else:
        print(f"PASS real floor: {len(g.rooms)} 间, 可达 {len(reach)}（出口不可达）")


def _event_type(state, event_uid):
    for ev in (state.get("map") or {}).get("events") or []:
        if ev.get("uid") == event_uid:
            return ev.get("type_name")
    return None


def main():
    test_line_and_wall()
    test_locked_and_hidden()
    test_bad_edges_reroute()
    test_visible_but_unreachable()
    test_real_floor()
    print("ALL PASS")


if __name__ == "__main__":
    main()
