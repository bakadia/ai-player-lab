"""整层事件一览：哪一格会掉物品、哪一格只是加属性。

    python tools\\bph_events.py
    python tools\\bph_events.py --json      # 原始 diag 也打出来

数据来自桥接的只读动作 `diag_events`：它读的是每个事件 `itemsToSpawn` 里
**prefab** 上的元数据（`SimpleClickItem.effects`），不 Play、不实例化、不改状态。
所以「走过去之前」就能知道：

- `effects` 里有 `type=4` → 点下去会往地上摆**物品**（Start() 干的）；
- `type=0/1/2` → QuickBoost，只加 血 / 经验 / 金币。

`item?` 那列是 YES 的，才值得为了「捡东西」专门跑一趟。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "adapter"))

from client import Bridge, BridgeError  # noqa: E402

KINDS = {0: "hp", 1: "exp", 2: "gold", 3: "dungeon-prop",
         4: "item", 5: "meta", 6: "spawn-items"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="list this floor's dungeon events")
    ap.add_argument("--runtime", help="runtime 目录")
    ap.add_argument("--json", action="store_true", help="打印原始 diag")
    args = ap.parse_args(argv)

    bridge = Bridge(args.runtime)
    try:
        ack = bridge.act({"type": "diag_events"}, timeout_ms=10000)
    except BridgeError as exc:
        print(f"bridge error: {exc}")
        return 2

    diag = ack.get("diag")
    if not isinstance(diag, dict):
        print("diag_events 没返回 diag（桥接太老？）")
        print(json.dumps(ack, ensure_ascii=False, indent=1))
        return 2

    if args.json:
        print(json.dumps(diag, ensure_ascii=False, indent=1))

    print(f"floor={diag.get('floor')} events={diag.get('count')}")
    print(f"{'uid':10} {'type':16} {'room':8} {'st':4} {'item?':5} 产出")
    any_item = False
    for ev in diag.get("events") or []:
        kinds: list[str] = []
        has_item = False
        for pf in ev.get("items_to_spawn_detail") or []:
            for ef in pf.get("effects") or []:
                t = ef.get("type")
                kinds.append(f"{KINDS.get(t, t)}x{ef.get('num')}")
                if t == 4:
                    has_item = True
            if (pf.get("item2_children") or 0) > 0:
                kinds.append(f"item2child x{pf.get('item2_children')}")
                has_item = True
        any_item = any_item or has_item
        state = "done" if ev.get("finished") else ("open" if ev.get("open_prop") else "-")
        names = ",".join((pf.get("name") or "?")
                         for pf in ev.get("items_to_spawn_detail") or [])
        print(f"{ev.get('uid'):10} {str(ev.get('type')):16} {ev.get('room'):8} "
              f"{state:4} {'YES' if has_item else '-':5} "
              f"{' + '.join(kinds) or '?':22} [{names}]")

    print("\n这一层有掉物品的事件" if any_item else "\n这一层没有 type=4(item) / item2 子物体的事件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
