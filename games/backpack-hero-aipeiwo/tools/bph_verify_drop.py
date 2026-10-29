"""真机验收：满包 + 第 4 件战利品 -> `--make-room` 丢一件再放。

    python tools\\bph_verify_drop.py --make-room 1

它是一条可以反复跑的链（打不过/半路进战斗就退出，下次接着来）：

1. 把地上现有的东西都放进背包（教程那 3 件），尽量塞满
2. 找一件新的战利品：**优先开宝箱**（`open_chest`），没有宝箱就找地图上的
   SimpleClickItem（`play_event`），都没有就走去出口换层
3. 战利品落地 -> `place_with_room(loot, drops_left=N)`：放不下就丢最低价值的一件再放
4. 断言：确实丢了一件、loot 进包、free 不变；证据写到
   `data/runs/drop-make-room-<日期>.json`

`--report` 只打印当前局势（背包 / 事件距离），不动手。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "adapter"))
sys.path.insert(0, str(HERE.parent / "agent"))

from client import Bridge, BridgeError  # noqa: E402
from ledger import Ledger  # noqa: E402
from mapnav import RoomGraph  # noqa: E402
from place import place_with_room  # noqa: E402

STOP_MODES = {"battle", "reorganize", "dead", "menu"}
DANGER = ("Enemy", "Elite", "Shambler")
LOOT_EVENTS = ("Chest", "SimpleClickItem", "Chance")


class Ctx:
    def __init__(self, bridge: Bridge, make_room: int) -> None:
        self.bridge = bridge
        self.make_room = make_room
        self.steps: list[dict] = []
        self.bad_edges: set[tuple[str, str]] = set()
        self.tried_events: set[str] = set()

    def log(self, msg: str) -> None:
        print(msg)
        self.steps.append({"log": msg})
        # GBK 控制台打不出生僻符号，别把自己搞崩
        sys.stdout.flush()

    def state(self) -> dict:
        return self.bridge.state()

    def save(self, ok: bool) -> Path:
        out = (HERE.parent / "data" / "runs"
               / ("drop-make-room-" + time.strftime("%Y%m%d") + ".json"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"ok": ok, "steps": self.steps},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        return out


# ---------- 读状态 ----------


def cur_room(st: dict) -> str:
    return ((st.get("map") or {}).get("player") or {}).get("room_at") or ""


def event_types(st: dict) -> dict[str, str]:
    return {e["uid"]: e.get("type_name", "") for e in (st.get("map") or {}).get("events") or []}


def bag(st: dict) -> dict:
    bp = st.get("backpack") or {}
    return {
        "free": bp.get("free_squares"),
        "in_grid": bp.get("items_in_grid"),
        "loose": bp.get("items_loose"),
        "items": [[i["name"], len(i["cells"])] for i in bp.get("items") or []],
        "unplaced": [[i["uid"], i["name"]] for i in bp.get("unplaced") or []],
    }


def legal_types(st: dict) -> set[str]:
    return {a.get("type") for a in st.get("legal") or []}


# ---------- 走路 ----------


def walk(ctx: Ctx, target: str, max_steps: int = 22) -> str:
    """走到 target。返回 'arrived' / 'interrupted' / 'failed'。"""
    prev = ""
    for _ in range(max_steps):
        st = ctx.state()
        if st.get("mode") in STOP_MODES - {"menu"}:
            ctx.log(f"  .. mode={st.get('mode')}，走路中断（先处理眼前的事）")
            return "interrupted"
        cur = cur_room(st)
        if cur == target:
            return "arrived"

        legal = st.get("legal") or []
        # 走路要开着地图：关着时远处房间不 reveal，legal 里只剩脚下几格
        if (st.get("ui") or {}).get("viewing_top") != 1 and "open_map" in legal_types(st):
            ack = ctx.bridge.act({"type": "open_map"}, timeout_ms=20000)
            ctx.log(f"  open_map ok={ack.get('changed')}")
            time.sleep(0.4)
            continue

        graph = RoomGraph.from_state(st, ctx.bad_edges)
        types = event_types(st)
        danger = {r.uid for r in graph.rooms.values()
                  if r.event and types.get(r.event) in DANGER}
        legal_rooms = {a.get("room") for a in legal if a.get("type") == "move_to_room"}
        if not legal_rooms:
            ctx.log("  !! 没有可走的房间")
            return "failed"

        hop = graph.first_step(cur, target, legal_rooms,
                               avoid=danger | ({prev} if prev else set()))
        hop = hop or graph.first_step(cur, target, legal_rooms, avoid=danger)
        hop = hop or graph.first_step(cur, target, legal_rooms)
        if not hop:
            ctx.log(f"  !! {cur} 没有能点又朝 {target} 的房间")
            return "failed"

        ack = ctx.bridge.act({"type": "move_to_room", "room": hop, "via": "room"},
                             timeout_ms=25000)
        ok = bool(ack.get("changed"))
        ctx.log(f"  {cur} -> {hop} ok={ok} ms={ack.get('ms')}")
        if not ok:
            ctx.bad_edges.add((cur, hop))
        prev = cur
        time.sleep(0.35)
    ctx.log("  !! 步数用完还没到")
    return "failed"


def pick_target(st: dict, ctx: Ctx) -> tuple[str, str, str] | None:
    """(kind, room_uid, event_uid) —— 最近的宝箱 / 可点击物品 / 机会事件。"""
    graph = RoomGraph.from_state(st, ctx.bad_edges)
    cur = cur_room(st)
    types = event_types(st)
    best: tuple[int, str, str, str] | None = None
    for uid, room in graph.rooms.items():
        if not room.event or room.event in ctx.tried_events:
            continue
        kind = types.get(room.event, "")
        if kind not in LOOT_EVENTS:
            continue
        p = graph.path(cur, uid, allow_invisible=True)
        if not p:
            continue
        rank = 0 if kind == "Chest" else 1
        cand = (rank, len(p), uid, room.event)
        if best is None or cand[:2] < best[:2]:
            best = cand
    if best is None:
        return None
    kind = types.get(best[3], "")
    ctx.log(f"目标：{kind} {best[2]}（{best[1] - 1} 步）")
    return kind, best[2], best[3]


# ---------- 主流程 ----------


def fill_bag(ctx: Ctx) -> None:
    for _ in range(8):
        st = ctx.state()
        bp = st.get("backpack") or {}
        loose = bp.get("unplaced") or []
        if not loose or bp.get("free_squares") == 0:
            return
        item = loose[0]
        res = place_with_room(ctx.bridge, item["uid"], name=item.get("name", ""),
                              ledger=Ledger(), drops_left=0, log=ctx.log)
        if not res.ok():
            return


def get_loot(ctx: Ctx) -> str:
    """让地上出现一件东西。返回 'ok' / 'interrupted' / 'failed'。"""
    for _ in range(4):
        st = ctx.state()
        if (st.get("backpack") or {}).get("items_loose", 0) > 0:
            return "ok"

        # 已经站在有宝箱/可点物品的房间里 -> 直接交互
        kinds = legal_types(st)
        if "open_chest" in kinds:
            ack = ctx.bridge.act(next(a for a in st["legal"]
                                      if a.get("type") == "open_chest"), timeout_ms=30000)
            ctx.log(f"  open_chest ok={ack.get('changed')} delta={ack.get('delta')}")
            time.sleep(1.2)
            continue

        target = pick_target(st, ctx)
        if target is None:
            ctx.log("这一层没有可达的战利品事件，去出口换层")
            if not leave_floor(ctx):
                return "failed"
            continue

        kind, room, ev = target
        got = walk(ctx, room)
        if got != "arrived":
            return got
        time.sleep(0.5)
        st = ctx.state()
        if kind == "Chest" or "open_chest" in legal_types(st):
            ack = ctx.bridge.act(next(a for a in st["legal"]
                                      if a.get("type") == "open_chest"), timeout_ms=30000)
            ctx.log(f"  open_chest ok={ack.get('changed')} delta={ack.get('delta')}")
        else:
            ack = ctx.bridge.act({"type": "play_event", "uid": ev, "click": True},
                                 timeout_ms=25000)
            ctx.log(f"  play_event({kind}) ok={ack.get('changed')} "
                    f"diag={json.dumps(ack.get('diag'), ensure_ascii=False)}")
            if not ack.get("changed"):
                ctx.tried_events.add(ev)
        time.sleep(1.2)
    st = ctx.state()
    return "ok" if (st.get("backpack") or {}).get("items_loose", 0) > 0 else "failed"


def leave_floor(ctx: Ctx) -> bool:
    st = ctx.state()
    graph = RoomGraph.from_state(st, ctx.bad_edges)
    types = event_types(st)
    cur = cur_room(st)
    exits = [r.uid for r in graph.rooms.values()
             if r.event and types.get(r.event) in ("Exit", "ExitAlternate")]
    target = next((e for e in exits if graph.path(cur, e, allow_invisible=True)), None)
    if target is None:
        ctx.log("  !! 这层没有可达的出口")
        return False
    if walk(ctx, target) != "arrived":
        return False
    st = ctx.state()
    door = next((a.get("door") for a in st.get("legal") or []
                 if a.get("type") == "enter_door"), "")
    if not door:
        ctx.log("  !! 站在出口但没有 enter_door")
        return False
    ack = ctx.bridge.act({"type": "enter_door", "door": door}, timeout_ms=30000)
    ctx.log(f"  enter_door ok={ack.get('changed')} delta={ack.get('delta')}")
    time.sleep(1.5)
    ctx.bad_edges.clear()
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="verify the --make-room drop path for real")
    ap.add_argument("--runtime")
    ap.add_argument("--make-room", type=int, default=1, metavar="N")
    ap.add_argument("--report", action="store_true", help="只看局势，不动手")
    args = ap.parse_args(argv)

    ctx = Ctx(Bridge(args.runtime), args.make_room)
    st = ctx.state()
    ctx.log(f"start: mode={st.get('mode')} floor={(st.get('run') or {}).get('floor')} "
            f"hp={(st.get('player') or {}).get('hp')} "
            f"{json.dumps(bag(st), ensure_ascii=False)}")

    if args.report:
        print(RoomGraph.from_state(st).describe(cur_room(st)))
        print(json.dumps(bag(st), ensure_ascii=False, indent=1))
        return 0

    if st.get("mode") in STOP_MODES - {"menu"}:
        # 战斗中地图没有房间数据，导航无从下手；先让 loop.py 把眼前的事处理完
        ctx.log(f"!! mode={st.get('mode')}，先打完这一场（run agent/loop.py），再回来跑本工具")
        return 0

    fill_bag(ctx)
    st = ctx.state()
    ctx.log(f"after fill: {json.dumps(bag(st), ensure_ascii=False)}")

    got = get_loot(ctx)
    if got != "ok":
        ctx.log(f"!! 没拿到战利品（{got}）")
        ctx.save(False)
        return 0 if got == "interrupted" else 1

    st = ctx.state()
    loot = ((st.get("backpack") or {}).get("unplaced") or [])
    if not loot:
        ctx.log("!! 地上没有战利品")
        ctx.save(False)
        return 1
    uid, name = loot[0]["uid"], loot[0].get("name", "")
    free_before = (st.get("backpack") or {}).get("free_squares")
    ctx.log(f"捡 {name}（{uid}）：free={free_before}，drops_left={args.make_room}")

    res = place_with_room(ctx.bridge, uid, name=name, ledger=Ledger(),
                          drops_left=args.make_room, log=ctx.log)
    ctx.steps.append({"place": {"uid": uid, "name": name, "reason": res.reason,
                                "drops_left": res.drops_left, "dropped": res.dropped,
                                "sent": res.sent, "ack": res.ack}})

    st = ctx.state()
    after = bag(st)
    ctx.log(f"after place: {json.dumps(after, ensure_ascii=False)}")
    dropped = [d for d in res.dropped if d.get("ok")]
    in_bag = any(name and name in (i.get("name") or "") for i in
                 (st.get("backpack") or {}).get("items") or [])
    ok = bool(res.ok() and dropped and after["free"] == free_before and in_bag)
    ctx.log(f"RESULT ok={ok} dropped={[d.get('name') for d in dropped]} in_bag={in_bag}")
    out = ctx.save(ok)
    ctx.log(f"saved {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BridgeError as exc:
        print(f"bridge error: {exc}")
        sys.exit(1)
