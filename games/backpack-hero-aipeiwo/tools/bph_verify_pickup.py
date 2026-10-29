"""真机验收：地图上的 SimpleClickItem 到底能不能捡进包。

为什么单独一个工具：`play_event` 的坑集中在**时序**上（见 PITFALLS E15 / v8.17）：

    Play()     实例化事件内容、绑 SimpleClickItem.dungeonEvent（幂等，靠 open 属性）
    Start()    **下一帧**才跑：才把事件底下真正的东西 SetActive(true) 摆到祭坛前
    Click()    动画 + FinishEvent + StartCoroutine(TakeAction)（item 效果本身不做事）

也就是说「东西变成 loose」发生在 Play 之后的那一帧，**不是**同一帧里量得到。
所以验收必须分两步、中间等几帧：

    play_event{click:false}  ->  等 --settle 秒  ->  diag_items
    play_event{play:false}   ->  等 --settle 秒  ->  diag_items / state

`diag_items` 会把「活跃」和「未激活」的 Item2 分开列——`items_loose` 只看
`Item2.allItems`，未激活的东西在 state 上和不存在一模一样，光看 state 会误判。

用法：

    python tools\\bph_verify_pickup.py                    # 当前房间的事件，两步走
    python tools\\bph_verify_pickup.py --uid d-5680 --place --make-room 1

退出码：0 = 东西真的变成 loose（或进了包）；1 = 没有；2 = bridge 出错。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "adapter"))
sys.path.insert(0, str(ROOT / "agent"))

from client import Bridge, BridgeError  # noqa: E402
from ledger import Ledger  # noqa: E402
from place import place_with_room  # noqa: E402


EFFECT_KINDS = {0: "hp", 1: "exp", 2: "gold", 3: "dungeon-property",
                4: "item", 5: "meta", 6: "spawn-items"}


def snap(bridge: Bridge) -> dict:
    st = bridge.state()
    bag = st.get("backpack") or {}
    mp = st.get("map") or {}
    run = st.get("run") or {}
    pl = st.get("player") or {}
    return {
        "seq": st.get("seq"),
        "mode": st.get("mode"),
        "room": ((mp.get("player") or {}).get("room_at") or ""),
        "hp": pl.get("hp"),
        "gold": run.get("gold"),
        "level": run.get("level"),
        "loose": bag.get("items_loose"),
        "in_grid": bag.get("items_in_grid"),
        "free": bag.get("free_squares"),
        "unplaced": [{"uid": i.get("uid"), "name": i.get("name")}
                     for i in (bag.get("unplaced") or [])],
    }


def effects_of(diag: dict | None) -> list[str]:
    """autoack.diag -> ['item x1', 'gold x5', ...]，用来判断这次点击「该」产出什么。"""
    out: list[str] = []
    if not isinstance(diag, dict):
        return out
    for sci in diag.get("click_items") or []:
        for ef in sci.get("effects") or []:
            t = ef.get("type")
            out.append(f"{EFFECT_KINDS.get(t, t)} x{ef.get('num')}")
    return out


def item_names(diag: dict | None) -> tuple[list[str], list[str]]:
    """diag_items -> (活跃物品名, 未激活物品名)。"""
    if not isinstance(diag, dict):
        return [], []
    act = [f"{i.get('display') or i.get('name')}[{i.get('uid')}]"
           for i in (diag.get("active") or [])]
    ina = [f"{i.get('display') or i.get('name')}[{i.get('uid')}]"
           for i in (diag.get("inactive") or [])]
    return act, ina


def show(tag: str, s: dict, diag: dict | None) -> None:
    act, ina = item_names(diag)
    print(f"  {tag}: mode={s['mode']} room={s['room']} hp={s['hp']} gold={s['gold']} "
          f"free={s['free']} in_grid={s['in_grid']} loose={s['loose']}")
    if act:
        print(f"    活跃 item: {', '.join(act)}")
    if ina:
        print(f"    未激活 item: {', '.join(ina)}")


def changed_fields(before: dict, after: dict) -> list[str]:
    keys = ("hp", "gold", "level", "loose", "in_grid")
    out = [f"{k}: {before.get(k)} -> {after.get(k)}"
           for k in keys if before.get(k) != after.get(k)]
    if len(after.get("unplaced") or []) != len(before.get("unplaced") or []):
        out.append("unplaced: "
                   f"{len(before.get('unplaced') or [])} -> {len(after.get('unplaced') or [])}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="verify map SimpleClickItem pickup")
    ap.add_argument("--runtime", help="runtime 目录")
    ap.add_argument("--uid", default="", help="事件 uid（d123）；省略=当前房间第一个事件")
    ap.add_argument("--settle", type=float, default=1.5, help="两步之间等多久（秒）")
    ap.add_argument("--place", action="store_true", help="捡到之后顺手放进背包")
    ap.add_argument("--make-room", type=int, default=0, help="放不下时允许丢几件")
    ap.add_argument("--evidence", default="", help="证据 JSON 路径")
    args = ap.parse_args(argv)

    bridge = Bridge(args.runtime)
    ev: dict = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "uid": args.uid,
                "settle": args.settle}

    try:
        before = snap(bridge)
        print("== 起点")
        diag_ev = bridge.act({"type": "diag_event"}, timeout_ms=8000)
        diag_items_0 = bridge.act({"type": "diag_items"}, timeout_ms=8000)
        show("before", before, diag_items_0.get("diag"))
        ev["before"] = before
        ev["diag_event"] = diag_ev
        ev["diag_items_before"] = diag_items_0.get("diag")

        if before["mode"] != "map":
            print(f"!! mode={before['mode']}，play_event 得在房间里做")
            return 1

        # ---- 第 1 步：只 Play，不点 ----
        a1 = bridge.act({"type": "play_event", "uid": args.uid, "click": False},
                        timeout_ms=15000)
        ev["play_only"] = a1
        print(f"== 第 1 步 play_event{{click:false}} ok={a1.get('ok')} "
              f"changed={a1.get('changed')} err={a1.get('error')}")
        print(f"    diag={json.dumps(a1.get('diag'), ensure_ascii=False)}")
        kinds = effects_of(a1.get("diag"))
        print(f"    这个事件点下去会产生: {', '.join(kinds) or '（读不到 effects）'}")
        ev["effect_kinds"] = kinds
        expect_item = any(k.startswith("item") for k in kinds)

        time.sleep(max(0.0, args.settle))
        s1 = snap(bridge)
        d1 = bridge.act({"type": "diag_items"}, timeout_ms=8000)
        show("after play", s1, d1.get("diag"))
        ev["after_play_state"] = s1
        ev["diag_items_after_play"] = d1.get("diag")

        # ---- 第 2 步：只 Click，不再 Play（Play 会叠对象）----
        a2 = bridge.act({"type": "play_event", "uid": args.uid, "play": False,
                         "click": True}, timeout_ms=15000)
        ev["click_only"] = a2
        print(f"== 第 2 步 play_event{{play:false}} ok={a2.get('ok')} "
              f"changed={a2.get('changed')} err={a2.get('error')}")
        print(f"    diag={json.dumps(a2.get('diag'), ensure_ascii=False)}")

        time.sleep(max(0.0, args.settle))
        s2 = snap(bridge)
        d2 = bridge.act({"type": "diag_items"}, timeout_ms=8000)
        show("after click", s2, d2.get("diag"))
        ev["after_click_state"] = s2
        ev["diag_items_after_click"] = d2.get("diag")

        got = (s2["loose"] or 0) > (before["loose"] or 0) or \
              len(s2["unplaced"]) > len(before["unplaced"])
        ev["loose_increased"] = bool(got)
        diff = changed_fields(before, s2)
        ev["changed_fields"] = diff

        # ---- 可选：放进背包 ----
        if args.place and got:
            new = [u for u in s2["unplaced"]
                   if u["uid"] not in {b["uid"] for b in before["unplaced"]}]
            ledger = Ledger(None)
            for it in new:
                print(f"== 放进背包 {it['name']} {it['uid']}")
                res = place_with_room(bridge, it["uid"], name=it["name"] or "",
                                      ledger=ledger, drops_left=max(0, args.make_room),
                                      log=lambda s: print("   " + s))
                ev.setdefault("place", []).append({
                    "uid": it["uid"], "ok": res.ok(), "reason": res.reason,
                    "sent": res.sent, "dropped": res.dropped,
                    "ack": {"ok": (res.ack or {}).get("ok"),
                            "changed": (res.ack or {}).get("changed"),
                            "error": (res.ack or {}).get("error")},
                })
            ev["after_place_state"] = snap(bridge)

        print(f"\n变化: {', '.join(diff) or '（什么都没有变）'}")
        print(f"RESULT effect={','.join(kinds) or '?'} item_expected={expect_item} "
              f"loose_increased={got} "
              f"before_loose={before['loose']} after_loose={s2['loose']}")
        # 判定：只要「这个事件该产出的东西」出现了就算成功。
        # 非 item 事件（QuickBoost HP/Gold 这类）本来就不会掉东西到地上。
        ev["ok"] = bool(diff) and (got or not expect_item)

    except BridgeError as exc:
        print(f"bridge error: {exc}")
        return 2

    out = Path(args.evidence) if args.evidence else (
        ROOT / "data" / "runs" / f"pickup-{time.strftime('%Y%m%d-%H%M%S')}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"证据: {out}")
    return 0 if ev.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
