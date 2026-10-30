"""背包 planner 的离线自测（不需要游戏）。

    python games/backpack-hero-aipeiwo/agent/tests/test_backpack.py

数据是**合成的 probe**（形状 + 朝向偏移），格式和桥接 `probe_fit` 的输出完全一致，
所以这层测的就是真实回执会被怎么解读。真机录下来的 probe 存在
`data/runs/probe-*.json`，`test_real_probe_if_present` 会顺带验一遍。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = HERE.parent
sys.path.insert(0, str(AGENT))

from backpack import (  # noqa: E402
    Bag,
    Plan,
    choose_drop,
    fmt_cell,
    parse_cell,
    plan_place,
    plan_repack,
    plan_swap,
    place_options,
)

# 形状：朝向 -> 相对锚点的偏移
S1 = {0: ((0, 0),)}
V2 = {0: ((0, 0), (0, 1)), 90: ((0, 0), (1, 0))}
H2 = {0: ((0, 0), (1, 0)), 90: ((0, 0), (0, 1))}
V3 = {0: ((0, 0), (0, 1), (0, 2)), 90: ((0, 0), (1, 0), (2, 0))}
Q2 = {0: ((0, 0), (1, 0), (0, 1), (1, 1))}


def rect(x0: int, y0: int, w: int, h: int) -> set[tuple[int, int]]:
    return {(x, y) for x in range(x0, x0 + w) for y in range(y0, y0 + h)}


def make_probe(bag_cells, specs) -> dict:
    """specs: [(uid, name, in_bag, cells, shapes)] -> 桥接 probe_fit 的那种 JSON。"""
    out_items = []
    for uid, name, in_bag, cells, shapes in specs:
        rotations = []
        for rot in sorted(shapes):
            placements = []
            for ax in sorted(bag_cells):
                foot = {(ax[0] + dx, ax[1] + dy) for dx, dy in shapes[rot]}
                if foot <= bag_cells:
                    placements.append({
                        "cell": fmt_cell(ax),
                        "cells": ";".join(fmt_cell(c) for c in sorted(foot)),
                    })
            if placements:
                rotations.append({"rot": rot, "placements": placements})
        out_items.append({
            "uid": uid,
            "name": name,
            "in_bag": in_bag,
            "cells": [fmt_cell(c) for c in sorted(cells)],
            "needs": len(cells) if cells else len(next(iter(shapes.values()))),
            "rotations": rotations,
        })
    free = len(bag_cells) - sum(len(cells) for _u, _n, ib, cells, _s in specs if ib)
    return {
        "bag": [fmt_cell(c) for c in sorted(bag_cells)],
        "grid_squares": len(bag_cells),
        "free_squares": free,
        "items": out_items,
    }


def bag_of(bag_cells, specs) -> Bag:
    return Bag.from_probe(make_probe(bag_cells, specs))


def replay(bag: Bag, steps: list[dict]) -> set[tuple[int, int]]:
    """按顺序「执行」计划，返回最终占用；不合法就抛错（和桥接的校验同一套规则）。"""
    occ = set(bag.occupied())
    held: dict[str, frozenset] = {}

    def footprint(uid, cell, rot):
        it = bag.item(uid)
        assert it is not None, uid
        table = it.rotations.get(int(rot))
        assert table is not None, (uid, rot, sorted(it.rotations))
        foot = table.get(parse_cell(cell))
        assert foot is not None, (uid, cell, rot)
        return foot

    for step in steps:
        kind = step["type"]
        if kind in ("place_item", "move_item"):
            uid = step["uid"]
            foot = footprint(uid, step["cell"], step.get("rot_deg", 0))
            assert foot <= bag.cells, f"{kind} {uid} leaves the bag: {sorted(foot)}"
            clash = foot & (occ - (bag.item(uid).cells if kind == "move_item" else set()))
            assert not clash, f"{kind} {uid} hits occupied {sorted(clash)}"
            if kind == "place_item":
                held.pop(uid, None)
                occ |= foot
            else:
                occ -= bag.item(uid).cells
                occ |= foot
        elif kind == "swap_items":
            a, b = step["a"], step["b"]
            fa_new = footprint(a, step["a_cell"], step["a_rot"])
            fb_new = footprint(b, step["b_cell"], step["b_rot"])
            others = occ - bag.item(a).cells - bag.item(b).cells
            assert not (fb_new & others), "swap: B hits someone else"
            assert not (fa_new & (others | fb_new)), "swap: A hits B's new cells"
            occ = others | fb_new | fa_new
        else:
            raise AssertionError(f"unknown step {kind}")
    return occ


def test_parse_roundtrip():
    assert parse_cell("0,-1") == (0, -1)
    probe = make_probe(rect(0, 0, 2, 2), [("i1", "A", True, [(0, 0)], S1)])
    bag = Bag.from_probe(probe)
    assert bag.cells == frozenset(rect(0, 0, 2, 2)), bag.cells
    it = bag.item("i1")
    assert it is not None and it.in_bag
    assert it.rotations[0][(1, 1)] == frozenset({(1, 1)})
    print("PASS parse/roundtrip")


def test_plan_direct():
    # 3x3 里空一格：1 格的战利品应该一步放进去
    specs = [
        ("i1", "Sword", True, [(0, 0), (0, 1), (0, 2)], V3),
        ("i9", "Loot", False, [], S1),
    ]
    bag = bag_of(rect(0, 0, 3, 3), specs)
    plan = plan_place(bag, "i9")
    assert plan, plan.describe()
    assert len(plan.steps) == 1 and plan.steps[0]["type"] == "place_item", plan.steps
    replay(bag, plan.steps)
    print("PASS plan direct")


def test_plan_by_rotation():
    # 4x2 的包，只剩「横着两格」，2 格竖着的东西必须转 90° 才放得下
    bag_cells = rect(0, 0, 4, 2)
    specs = [
        ("i1", "Buckler", True, [(0, 0), (1, 0), (0, 1), (1, 1)], Q2),
        ("i2", "B", True, [(2, 1)], S1),
        ("i3", "C", True, [(3, 1)], S1),
        ("i9", "Meal", False, [], V2),
    ]
    bag = bag_of(bag_cells, specs)
    assert bag.free_count() == 2, bag.free_count()
    plan = plan_place(bag, "i9")
    assert plan, plan.describe()
    assert len(plan.steps) == 1, plan.steps
    step = plan.steps[0]
    assert step["type"] == "place_item"
    assert step["rot_deg"] == 90, step
    assert step["cell"] in ("2,0",), step
    replay(bag, plan.steps)
    assert sorted(place_options(bag, "i9")[0].cells) == [(2, 0), (3, 0)]
    print("PASS plan by rotation")


def test_plan_move_one():
    # 12 格：两个空格不相邻，挪走一件 1 格物品后拼出 1x2
    bag_cells = rect(0, 0, 4, 3)
    specs = [
        ("i1", "Sword", True, [(0, 0), (0, 1), (0, 2)], V3),
        ("i2", "Buckler", True, [(1, 1), (2, 1), (1, 2), (2, 2)], Q2),
        ("i3", "C", True, [(1, 0)], S1),
        ("i4", "D", True, [(3, 0)], S1),
        ("i5", "E", True, [(3, 2)], S1),
        ("i9", "Meal", False, [], V2),
    ]
    bag = bag_of(bag_cells, specs)
    assert bag.free_count() == 2, bag.free_count()
    assert not place_options(bag, "i9"), "两个空格不相邻，不该能直接放"
    plan = plan_place(bag, "i9")
    assert plan, plan.describe()
    assert [s["type"] for s in plan.steps] == ["move_item", "place_item"], plan.steps
    replay(bag, plan.steps)
    assert "move" in plan.why, plan.why
    print("PASS plan move one")


def test_plan_needs_drop():
    # 包全满、每件都 2 格以上，挪不动 → 只能丢
    bag_cells = rect(0, 0, 2, 2)
    specs = [
        ("i1", "A", True, [(0, 0), (0, 1)], V2),
        ("i2", "B", True, [(1, 0), (1, 1)], V2),
        ("i9", "Loot", False, [], S1),
    ]
    bag = bag_of(bag_cells, specs)
    plan = plan_place(bag, "i9")
    assert not plan, plan.steps
    assert plan.needs_drop and not plan.impossible, plan
    print("PASS needs drop")


def test_plan_impossible():
    # 5 格的东西 + 4 格的包：丢掉别的也没用
    bag_cells = rect(0, 0, 2, 2)
    specs = [("i9", "Big", False, [], {0: ((0, 0), (1, 0), (0, 1), (1, 1), (2, 0))})]
    bag = bag_of(bag_cells, specs)
    plan = plan_place(bag, "i9")
    assert plan.impossible and not plan.steps, plan
    print("PASS impossible")


def test_plan_swap():
    # 2x2 全满、每件 2 格：谁也挪不动，但互换完全可行
    bag_cells = rect(0, 0, 2, 2)
    specs = [
        ("i1", "A", True, [(0, 0), (0, 1)], V2),
        ("i2", "B", True, [(1, 0), (1, 1)], V2),
    ]
    bag = bag_of(bag_cells, specs)
    plan = plan_swap(bag, "i1", "i2")
    assert plan is not None, "两个同形物品应该能互换"
    step = plan.steps[0]
    assert step["type"] == "swap_items"
    # A 去 B 的地盘，B 去 A 的地盘
    assert step["a_cell"] == "1,0" and step["b_cell"] == "0,0", step
    occ = replay(bag, plan.steps)
    a = bag.item("i1")
    b = bag.item("i2")
    a_cells = a.rotations[int(step["a_rot"])][parse_cell(step["a_cell"])]
    b_cells = b.rotations[int(step["b_rot"])][parse_cell(step["b_cell"])]
    assert a_cells <= occ and b_cells <= occ
    # 换完还是满的：A 占了 B 的旧格、B 占了 A 的旧格
    assert occ == bag.cells, sorted(occ)
    assert a_cells == b.cells, (sorted(a_cells), sorted(b.cells))
    assert b_cells == a.cells, (sorted(b_cells), sorted(a.cells))
    print("PASS plan swap")


def test_repack_steps_are_executable():
    # 3x3，三件 2 格物品 + 三格空：重排必须给出「按顺序真能执行」的步骤
    bag_cells = rect(0, 0, 3, 3)
    specs = [
        ("i1", "A", True, [(0, 1), (0, 2)], V2),
        ("i2", "B", True, [(1, 1), (1, 2)], V2),
        ("i3", "C", True, [(2, 0), (2, 1)], V2),
    ]
    bag = bag_of(bag_cells, specs)
    plan = plan_repack(bag)
    assert plan is not None, "应该能重排"
    final = replay(bag, plan.steps)
    # 每一步都合法（replay 会断言），且重排后三件都在包里、不重叠
    assert len(final) == 6, sorted(final)
    print("PASS repack steps executable")


def test_armor_priced_above_sword():
    """护甲流定价：每回合 7 甲 ≈ 1 费 × FIGHT_TURNS 回合，护甲不该比木剑先被丢。

    用户口径（2026-10-30）：帽子/手套/鞋子/衣服每回合自动给甲，
    「7 护甲 = 1 费，收益还挺高」。以前 `item_value` 只给 `0.5×每回合甲`，
    Knight's Armor（6/turn）拿 5.0 分，比木剑（账本 5.4）还低 —— 腾位时先丢护甲。
    """
    from itemdb import ItemDB
    from policy import FIGHT_TURNS, per_turn_energy, per_turn_value

    assert per_turn_energy(7.0) == FIGHT_TURNS, per_turn_energy(7.0)
    assert per_turn_energy(0.0) == 0.0
    assert per_turn_value(7.0) > per_turn_value(6.0) > 0.0

    class Ledger:
        def __init__(self, data):
            self.data = data

        def estimate_damage(self, name):
            return self.data.get(name, {}).get("damage")

        def estimate_block(self, name):
            return self.data.get(name, {}).get("block", 0.0)

        def effect_score(self, name):
            return 0.0

    db = ItemDB.load()
    led = Ledger({"WOODEN SWORD": {"damage": 5.4}})          # 账本实测净伤
    from backpack import item_value

    sword = item_value("WOODEN SWORD", led, db)
    armor = item_value("KNIGHT'S ARMOR", led, db)            # 6 甲/回合、4 格
    assert armor > sword * 1.5, (armor, sword)

    # 背包里同时有甲 / 剑 / 杂物时，丢的顺序必须是「杂物 → 剑 → 甲」。
    # 注意「唯一的主力输出不丢」那条保护仍然生效（剑只有一把时会进 kept），
    # 所以这里直接看 `drop_ranking` 的顺序，而不是只看 choose_drop 的第一个。
    specs = [
        ("i1", "KNIGHT'S ARMOR", True, [(0, 0), (1, 0), (0, 1), (1, 1)], Q2),
        ("i3", "ROCK", True, [(1, 2)], S1),
    ]
    bag = bag_of(rect(0, 0, 2, 3), specs)
    led2 = Ledger({"WOODEN SWORD": {"damage": 5.4}, "ROCK": {"damage": 0.0}})
    from backpack import drop_ranking

    order = [it.name for _v, it in drop_ranking(bag, led2, db=db)]
    assert order and order[0] == "ROCK", order
    assert order[-1] == "KNIGHT'S ARMOR", order
    print("PASS armor priced above sword")


def test_synergy_bias_prefers_armor_neighbors():
    """E27：相邻加成的道具要贴着目标摆（描述里的 adjacent 是真实规则）。

    `ANGEL BUCKLER` 的描述是「相邻的护甲 +3 格挡」（1 格），放在护甲旁边才有收益。
    """
    from itemdb import ItemDB
    from place import synergy_bias

    db = ItemDB.load()
    st = db.get("ANGEL BUCKLER")
    assert st is not None and st.helps_armor, st
    assert st.synergy_block >= 1.0, st.synergy_block

    specs = [("i1", "ROUGH BUCKLER", True, [(0, 0), (0, 1), (1, 0), (1, 1)], Q2)]
    bag = bag_of(rect(0, 0, 5, 2), specs)
    bias = synergy_bias(bag, "ANGEL BUCKLER", db)
    assert bias is not None, "护甲在包里却没算出 bias"

    class P:
        def __init__(self, cells):
            self.cells = cells

    assert bias(P([(2, 0)])) == 1.0     # 紧贴护甲右边缘
    assert bias(P([(4, 1)])) == 0.0     # 隔了两格，拿不到加成
    # 背包里没有护甲时不该硬凑协同
    empty = bag_of(rect(0, 0, 5, 2), [])
    assert synergy_bias(empty, "ANGEL BUCKLER", db) is None
    print("PASS synergy bias (adjacent armor)")


def test_choose_drop_by_value():
    class Ledger:
        def __init__(self, data):
            self.data = data

        def estimate_damage(self, name):
            v = self.data.get(name, {}).get("damage")
            return v

        def estimate_block(self, name):
            return self.data.get(name, {}).get("block", 0.0)

        def effect_score(self, name):
            return 0.0

    bag_cells = rect(0, 0, 3, 2)
    specs = [
        ("i1", "WOODEN SWORD", True, [(0, 0), (0, 1)], V2),
        ("i2", "ROCK", True, [(1, 0)], S1),
        ("i3", "MYSTERY", True, [(2, 0)], S1),
    ]
    bag = bag_of(bag_cells, specs)
    led = Ledger({"WOODEN SWORD": {"damage": 6.0}, "ROCK": {"damage": 0.0}})

    # ROCK 测出来是 0 —— 先丢它，而不是没测过的 MYSTERY
    pick = choose_drop(bag, led)
    assert pick is not None and pick.name == "ROCK", pick and pick.name

    # 保护 ROCK：下一个该是「未知」的 MYSTERY，不是 6 伤的剑
    pick = choose_drop(bag, led, protected={"i2"})
    assert pick is not None and pick.name == "MYSTERY", pick and pick.name
    print("PASS choose drop by value")


def test_real_probe_if_present():
    """真机录的 probe（`agent/tests/fixtures/probe-*.json`）必须能解析。

    fixture 是从真机 `probe_fit` 回执原样存下来的；`data/runs/` 里也可能有更新的
    一份（那个目录不入库），有就一起验。
    """
    files = sorted((HERE / "fixtures").glob("probe-*.json"))
    runs = HERE.parents[1] / "data" / "runs"
    files += sorted(runs.glob("probe-*.json")) if runs.exists() else []
    if not files:
        print("SKIP real probe (none captured yet)")
        return
    for f in files:
        probe = json.loads(f.read_text(encoding="utf-8"))
        bag = Bag.from_probe(probe)
        assert bag.cells, f"{f.name}: bag cells 为空"
        assert bag.items, f"{f.name}: 没有物品"
        for it in bag.in_bag() or bag.items.values():
            assert it.rotations, f"{f.name}: {it.name} 没有 footprint 表"
            for rot, table in it.rotations.items():
                for cell, foot in table.items():
                    assert foot <= bag.cells, f"{f.name}: {it.name}@{cell} rot{rot} 出界"
        print(f"PASS real probe {f.name}: {len(bag.cells)} 格 / {len(bag.items)} 件 / "
              f"free={bag.free_count()}")


class FakeBridge:
    """只认四种动作的假桥接：probe_fit / drop_item / move_item / place_item。

    用来测 agent/place.py 的编排（探针 -> 腾位 -> 重探 -> 放），不需要游戏。
    """

    def __init__(self, probe, ledger_values=None):
        self.probe = probe
        self.calls = []
        self.dropped = []

    def act(self, action, timeout_ms=0):        # noqa: ARG002
        self.calls.append(dict(action))
        kind = action["type"]
        if kind == "probe_fit":
            return {"ok": False, "changed": False, "diag": self.probe}
        if kind == "drop_item":
            uid = action["uid"]
            self.dropped.append(uid)
            # 从"背包里"摘掉，搬到地上（这就是 drop 后的 probe 会长的样子）
            item = next(i for i in self.probe["items"] if i["uid"] == uid)
            item["in_bag"] = False
            item["cells"] = []
            self.probe["free_squares"] += 1
            return {"ok": True, "changed": True, "diag": {"action": "drop_item", "uid": uid}}
        return {"ok": True, "changed": True, "diag": {"action": dict(action)}}


def test_place_with_room_drops_lowest_value():
    """满包 + 放不下 -> 丢掉最低价值的一件 -> 再放。整条链只跑一次 probe 一次 drop。"""
    from place import place_with_room

    bag_cells = rect(0, 0, 2, 2)
    probe = make_probe(bag_cells, [
        ("i1", "WOODEN SWORD", True, [(0, 0), (0, 1)], V2),
        ("i2", "ROCK", True, [(1, 0), (1, 1)], V2),
        ("i9", "Loot", False, [], S1),
    ])
    bridge = FakeBridge(probe)

    class Ledger:
        def estimate_damage(self, name):
            return {"WOODEN SWORD": 6.0, "ROCK": 0.0}.get(name)

        def estimate_block(self, name):
            return 0.0

        def effect_score(self, name):
            return 0.0

    res = place_with_room(bridge, "i9", name="Loot", ledger=Ledger(), drops_left=1)
    kinds = [c["type"] for c in bridge.calls]
    assert kinds == ["probe_fit", "drop_item", "probe_fit", "place_item"], kinds
    assert bridge.dropped == ["i2"], bridge.dropped        # ROCK 值 0，先丢它
    assert res.ok() and res.drops_left == 0, (res.ok(), res.drops_left)
    assert res.sent["type"] == "place_item" and res.sent["cell"], res.sent
    print("PASS place_with_room drops then places")


def test_place_with_room_respects_zero_budget():
    """--make-room 0：放不下就不放，绝不偷偷丢东西。"""
    from place import place_with_room

    bag_cells = rect(0, 0, 2, 2)
    probe = make_probe(bag_cells, [
        ("i1", "A", True, [(0, 0), (0, 1)], V2),
        ("i2", "B", True, [(1, 0), (1, 1)], V2),
        ("i9", "Loot", False, [], S1),
    ])
    bridge = FakeBridge(probe)
    res = place_with_room(bridge, "i9", drops_left=0)
    kinds = [c["type"] for c in bridge.calls]
    assert kinds == ["probe_fit"], kinds
    assert bridge.dropped == [], bridge.dropped
    assert not res.ok() and res.reason, res.reason
    print("PASS place_with_room refuses to drop without budget")


def main():
    test_parse_roundtrip()
    test_plan_direct()
    test_plan_by_rotation()
    test_plan_move_one()
    test_plan_needs_drop()
    test_plan_impossible()
    test_plan_swap()
    test_repack_steps_are_executable()
    test_choose_drop_by_value()
    test_synergy_bias_prefers_armor_neighbors()
    test_armor_priced_above_sword()
    test_real_probe_if_present()
    test_place_with_room_drops_lowest_value()
    test_place_with_room_respects_zero_budget()
    print("ALL PASS")


if __name__ == "__main__":
    main()
