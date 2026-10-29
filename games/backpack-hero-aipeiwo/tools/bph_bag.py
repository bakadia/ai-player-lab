"""背包调试/验收工具：看几何表、算落点、真的去放。

    python tools\\bph_bag.py show
    python tools\\bph_bag.py plan i-4298
    python tools\\bph_bag.py place i-4298 --make-room 1
    python tools\\bph_bag.py place i-4298 --dry-run

`place` 走的就是 `loop.py` 用的同一条链（agent/place.py -> agent/backpack.py），
所以这里看到的「腾位」行为 = 真跑整局时的行为。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "adapter"))
sys.path.insert(0, str(HERE.parent / "agent"))

from client import Bridge, BridgeError  # noqa: E402
from backpack import Bag, plan_place, render  # noqa: E402
from ledger import Ledger  # noqa: E402
from place import probe_bag, place_with_room  # noqa: E402


def cmd_show(bridge: Bridge, args) -> int:
    bag = probe_bag(bridge, log=print)
    if bag is None:
        return 1
    print(f"bag {len(bag.cells)} cells / free {bag.free_count()} "
          f"(state 报 {bag.free_reported})")
    print(render(bag))
    print()
    for idx, it in enumerate(bag.in_bag()):
        print(f"  {chr(ord('A') + idx % 26)} {it.uid:10} {it.name:22} "
              f"{len(it.cells)} 格 {sorted(it.cells)}")
    for it in bag.loose():
        print(f"  ? {it.uid:10} {it.name:22} {it.needs} 格（地上）")
    return 0


def cmd_plan(bridge: Bridge, args) -> int:
    bag = probe_bag(bridge, log=print)
    if bag is None:
        return 1
    plan = plan_place(bag, args.uid, max_moves=args.max_moves)
    print(render(bag))
    print(f"plan: {plan.describe()}")
    print(f"  why={plan.why} needs_drop={plan.needs_drop} impossible={plan.impossible}")
    print(json.dumps(plan.steps, ensure_ascii=False, indent=1))
    return 0 if plan.steps or plan.needs_drop else 1


def cmd_place(bridge: Bridge, args) -> int:
    ledger = Ledger(args.ledger)
    res = place_with_room(
        bridge, args.uid, name=args.name or "",
        ledger=ledger, drops_left=max(0, args.make_room), log=print,
        record=lambda o: print("  record " + json.dumps(o, ensure_ascii=False)),
    )
    print(f"\nres: ok={res.ok()} reason={res.reason} drops_left={res.drops_left} "
          f"dropped={[d['name'] for d in res.dropped]}")
    print(f"     sent={json.dumps(res.sent, ensure_ascii=False)}")
    if res.ack:
        print(f"     ack: ok={res.ack.get('ok')} changed={res.ack.get('changed')} "
              f"ms={res.ack.get('ms')} err={res.ack.get('error')}")
        print(f"     diag={json.dumps(res.ack.get('diag'), ensure_ascii=False)}")
    return 0 if res.ok() else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="backpack geometry / placement helper")
    ap.add_argument("--runtime", help="runtime 目录")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("show", help="打印背包 + 几何表")

    p = sub.add_parser("plan", help="算一件物品的落点计划（只读）")
    p.add_argument("uid")
    p.add_argument("--max-moves", type=int, default=3)

    q = sub.add_parser("place", help="真的去放（含腾位）")
    q.add_argument("uid")
    q.add_argument("--name", default="")
    q.add_argument("--make-room", type=int, default=0, metavar="N")
    q.add_argument("--ledger", default=None)
    q.add_argument("--dry-run", action="store_true")

    args = ap.parse_args(argv)
    bridge = Bridge(args.runtime)

    try:
        if args.cmd == "show":
            return cmd_show(bridge, args)
        if args.cmd == "plan":
            return cmd_plan(bridge, args)
        if args.cmd == "place":
            if args.dry_run:
                return cmd_plan(bridge, args)
            return cmd_place(bridge, args)
    except BridgeError as exc:
        print(f"bridge error: {exc}")
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
