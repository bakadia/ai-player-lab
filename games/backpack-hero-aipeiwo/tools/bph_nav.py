"""地图导航工具：看房间图、一格一格走过去。

    python tools\\bph_nav.py show
    python tools\\bph_nav.py go r-10066          # 走到某个房间
    python tools\\bph_nav.py go event:Exit       # 走到带某类事件的房间（最近的）
    python tools\\bph_nav.py go event:Chest --enter   # 到了就 enter_door / open_chest
    python tools\\bph_nav.py exit                # 走到出口并进门

可达性用 `agent/mapnav.py`（房间图），不是 `legal`——后者会把走不到的房间也列出来。
一次只发一格：真机上直接点远处房间会被判「走不到」并等满 30 秒。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "adapter"))
sys.path.insert(0, str(HERE.parent / "agent"))

from client import Bridge, BridgeError  # noqa: E402
from mapnav import RoomGraph  # noqa: E402

STOP_MODES = {"battle", "reorganize", "dead", "menu"}


def _state(bridge: Bridge) -> dict:
    return bridge.state()


def _cur(st: dict) -> str:
    return ((st.get("map") or {}).get("player") or {}).get("room_at") or ""


def _event_types(st: dict) -> dict[str, str]:
    return {e["uid"]: e.get("type_name", "") for e in (st.get("map") or {}).get("events") or []}


def _candidates(st: dict, graph: RoomGraph, cur: str, kind: str) -> list[tuple[int, str, str]]:
    """(步数, 房间, 事件) —— 按步数从近到远。迷雾里的房间也算（doors 数据是全的）。"""
    types = _event_types(st)
    out = []
    for uid, room in graph.rooms.items():
        if not room.event or types.get(room.event) != kind:
            continue
        p = graph.path(cur, uid, allow_invisible=True)
        if p:
            out.append((len(p) - 1, uid, room.event))
    out.sort()
    return out


def cmd_show(bridge: Bridge, args) -> int:
    st = _state(bridge)
    graph = RoomGraph.from_state(st)
    cur = _cur(st)
    print(f"mode={st.get('mode')} floor={(st.get('run') or {}).get('floor')} "
          f"viewing={(st.get('ui') or {}).get('viewing_top')} current={cur}")
    print(graph.describe(cur))
    types = _event_types(st)
    rooms = [r for r in graph.rooms.values() if r.event and not _finished(st, r.event)]
    if rooms:
        print("\n事件（按步数）:")
        for room in sorted(rooms,
                           key=lambda r: len(graph.path(cur, r.uid, allow_invisible=True)
                                             or [""] * 99)):
            p = graph.path(cur, room.uid, allow_invisible=True)
            steps = "unreachable" if not p else f"{len(p) - 1} 步: " + " -> ".join(p[1:])
            print(f"  {types.get(room.event, '?'):<16} {room.uid:>8} {room.event:>8} {steps}")
    return 0


def _finished(st: dict, event_uid: str) -> bool:
    for e in (st.get("map") or {}).get("events") or []:
        if e.get("uid") == event_uid:
            return bool(e.get("finished"))
    return True


def cmd_go(bridge: Bridge, args) -> int:
    bad_edges: set[tuple[str, str]] = set()
    target = args.target
    for step in range(args.max_steps):
        st = _state(bridge)
        mode = st.get("mode")
        if mode in STOP_MODES - {"menu"}:
            print(f"!! mode={mode}，停下来（导航只负责走路）")
            return 0
        cur = _cur(st)
        graph = RoomGraph.from_state(st, bad_edges)

        if target.startswith("event:"):
            kind = target.split(":", 1)[1]
            cands = _candidates(st, graph, cur, kind)
            if not cands:
                print(f"这一层没有可达的 {kind} 事件")
                print(graph.describe(cur))
                return 1
            steps, target_room, ev_uid = cands[0]
            print(f"最近的 {kind}：{target_room}（{steps} 步，事件 {ev_uid}）")
            target = target_room

        if cur == target:
            print(f"到了 {cur}")
            if args.enter:
                return _enter_here(bridge, st)
            return 0

        legal = st.get("legal") or []
        legal_rooms = {a.get("room") for a in legal if a.get("type") == "move_to_room"}
        if not legal_rooms:
            # 地图没开时 legal 里只有 open_map（真机踩到）
            if any(a.get("type") == "open_map" for a in legal):
                ack = bridge.act({"type": "open_map"}, timeout_ms=20000)
                print(f"  open_map ok={ack.get('changed')}")
                time.sleep(args.settle)
                continue
            print("!! 现在既没有可走的房间、也开不了地图")
            return 1
        hop = graph.first_step(cur, target, legal_rooms)
        if not hop:
            print(f"!! {cur} 这一步没有能点、又朝 {target} 的房间（迷雾太厚？）")
            print(graph.describe(cur))
            return 1
        ack = bridge.act({"type": "move_to_room", "room": hop, "via": "room"}, timeout_ms=25000)
        ok = bool(ack.get("changed"))
        print(f"  [{step}] {cur} -> {hop} ok={ok} ms={ack.get('ms')} "
              f"{ack.get('error') or ''}")
        if not ok:
            bad_edges.add((cur, hop))
            print("     （这条边走不通，记下来换条路）")
            if len(bad_edges) > 4:
                return 1
        time.sleep(args.settle)
    print("!! 步数用完还没到")
    return 1


def _enter_here(bridge: Bridge, st: dict) -> int:
    legal = st.get("legal") or []
    kinds = {a.get("type") for a in legal}
    if "open_chest" in kinds:
        act = next(a for a in legal if a.get("type") == "open_chest")
    elif "enter_door" in kinds:
        act = next(a for a in legal if a.get("type") == "enter_door")
    elif "start_event" in kinds:
        act = next(a for a in legal if a.get("type") == "start_event")
    else:
        print("这一格没有可用的交互动作")
        return 1
    ack = bridge.act(act, timeout_ms=30000)
    print(f"  {act.get('type')} ok={ack.get('ok')} changed={ack.get('changed')} "
          f"ms={ack.get('ms')} err={ack.get('error')}")
    print(f"  delta={ack.get('delta')}")
    return 0 if ack.get("changed") else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="walk the dungeon using the room graph")
    ap.add_argument("--runtime", help="runtime 目录")
    ap.add_argument("--settle", type=float, default=0.6)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="打印房间图 + 事件距离")
    g = sub.add_parser("go", help="一格一格走到目标")
    g.add_argument("target", help="房间 uid（r123）或 event:<类型>（如 event:Chest）")
    g.add_argument("--enter", action="store_true", help="到了之后 open_chest / enter_door / start_event")
    g.add_argument("--max-steps", type=int, default=24)
    ex = sub.add_parser("exit", help="走到出口并进门")
    ex.add_argument("--max-steps", type=int, default=24)

    args = ap.parse_args(argv)
    bridge = Bridge(args.runtime)
    try:
        if args.cmd == "show":
            return cmd_show(bridge, args)
        if args.cmd == "go":
            return cmd_go(bridge, args)
        if args.cmd == "exit":
            args.target = "event:Exit"
            args.enter = True
            return cmd_go(bridge, args)
    except BridgeError as exc:
        print(f"bridge error: {exc}")
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
