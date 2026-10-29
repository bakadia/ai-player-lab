"""把「把一件东西放进背包」这件事做完整。

流程（每一轮都重新探一次，因为背包刚被改过）：

    probe_fit ─► plan_place ─┬─ impossible  -> 放弃（形状本身塞不进去）
                             ├─ needs_drop  -> 按 --make-room 丢一件最低价值的，重来
                             └─ steps       -> 先发挪/换，最后发 place_item

`loop.py` 和 `tools/bph_bag.py` 共用这一份，别在两边各写一遍。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from backpack import Bag, choose_drop, item_value, plan_place

Log = Callable[[str], None]


@dataclass
class PlaceResult:
    ack: dict[str, Any] | None = None    # 最后一次回执（None = 压根没发）
    plan: Any = None                     # 最后一次用的 Plan
    sent: dict[str, Any] | None = None   # 最后实际发出去的动作
    dropped: list[dict[str, Any]] = field(default_factory=list)
    drops_left: int = 0
    reason: str = ""

    def ok(self) -> bool:
        return bool(self.ack and self.ack.get("changed"))


def probe_bag(bridge: Any, log: Log | None = None) -> Bag | None:
    """拿一次几何表；桥接太老 / 出错就返回 None（调用方退回老路径）。"""
    try:
        ack = bridge.act({"type": "probe_fit"}, timeout_ms=25000)
    except Exception as exc:                     # noqa: BLE001 - 探针失败不该炸整局
        if log:
            log(f"probe_fit 失败：{exc}")
        return None
    diag = ack.get("diag")
    if not isinstance(diag, dict):
        if log:
            log("probe_fit 没返回 diag")
        return None
    return Bag.from_probe(diag)


def place_with_room(
    bridge: Any,
    uid: str,
    *,
    name: str = "",
    ledger: Any = None,
    db: Any = None,
    drops_left: int = 0,
    max_moves: int = 3,
    log: Log | None = None,
    record: Callable[[dict[str, Any]], None] | None = None,
) -> PlaceResult:
    """尽力把 `uid` 放进背包。`drops_left` 是还剩几次丢弃额度（`--make-room N`）。"""
    say = log or (lambda _s: None)
    res = PlaceResult(drops_left=max(0, int(drops_left)))
    label = name or uid

    for _attempt in range(4):
        bag = probe_bag(bridge, say)
        if bag is None:
            res.reason = "no probe"
            return res

        plan = plan_place(bag, uid, max_moves=max_moves)
        res.plan = plan

        if plan.impossible:
            res.reason = plan.why
            say(f"{label} 塞不进这个背包：{plan.why}")
            if record:
                record({"event": "place_impossible", "uid": uid, "why": plan.why})
            return res

        if plan.needs_drop:
            if res.drops_left <= 0:
                res.reason = plan.why
                say(f"放不下 {label}：{plan.why}（没有丢弃额度）")
                if record:
                    record({"event": "place_no_room", "uid": uid, "why": plan.why})
                return res
            pick = choose_drop(bag, ledger, db=db)
            if pick is None:
                res.reason = "nothing to drop"
                say("腾位：背包里没有可丢的东西")
                return res

            # 别为了捡一件东西把更值钱的丢掉。真机：为捡 ELECTRIC WAND 丢了
            # WOODEN SWORD，之后只能挨打（29 伤害打进 11 血）。
            incoming_value = item_value(name, ledger, db) if name else None
            pick_value = item_value(pick.name, ledger, db)
            if incoming_value is not None and pick_value > incoming_value + 0.5:
                res.reason = "not worth dropping"
                say(f"放弃 {label}：要丢的 {pick.name or pick.uid} 值 "
                    f"{pick_value:.2f}，比它自己 {incoming_value:.2f} 还高")
                if record:
                    record({"event": "place_not_worth", "uid": uid, "name": name,
                            "drop_uid": pick.uid, "drop_name": pick.name,
                            "drop_value": round(pick_value, 2),
                            "incoming_value": round(incoming_value, 2)})
                return res

            say(f"腾位：丢 {pick.name or pick.uid}（{len(pick.cells)} 格，"
                f"剩余 {res.drops_left - 1} 次）")
            ack = bridge.act({"type": "drop_item", "uid": pick.uid}, timeout_ms=20000)
            res.drops_left -= 1
            res.ack = ack
            res.sent = {"type": "drop_item", "uid": pick.uid}
            res.dropped.append({"uid": pick.uid, "name": pick.name,
                                "cells": sorted(pick.cells),
                                "ok": ack.get("changed")})
            if record:
                record({"event": "drop_for_room", "uid": pick.uid, "name": pick.name,
                        "ok": ack.get("ok"), "changed": ack.get("changed"),
                        "delta": ack.get("delta"), "error": ack.get("error")})
            if not ack.get("changed"):
                res.reason = "drop did not change anything"
                say("丢弃没生效，停止腾位")
                return res
            continue

        # 先发挪/换（每步都要真的 changed，否则背包和我算的不一样，重探一次）
        progressed = True
        for step in plan.steps[:-1]:
            ack = bridge.act(step, timeout_ms=20000)
            res.ack = ack
            res.sent = step
            if record:
                record({"event": "reorg", "action": step, "ok": ack.get("ok"),
                        "changed": ack.get("changed"), "delta": ack.get("delta"),
                        "error": ack.get("error")})
            say(f"腾位 {step.get('type')} {step.get('uid') or step.get('a', '')}"
                f" -> ok={ack.get('ok')} changed={ack.get('changed')}")
            if not ack.get("changed"):
                progressed = False
                break
        if not progressed:
            res.reason = "reorg step did not take effect"
            continue

        final = dict(plan.steps[-1])
        final["name"] = name
        ack = bridge.act(final, timeout_ms=20000)
        res.ack = ack
        res.sent = final
        res.reason = plan.why
        if record:
            record({"event": "act", "action": final, "ok": ack.get("ok"),
                    "changed": ack.get("changed"), "ms": ack.get("ms"),
                    "delta": ack.get("delta"), "error": ack.get("error"),
                    "plan": plan.why})
        say(f"放 {label} @{final.get('cell')} rot{final.get('rot_deg')} -> "
            f"ok={ack.get('ok')} changed={ack.get('changed')} ms={ack.get('ms')}"
            f"（{plan.why}）")
        return res

    return res
