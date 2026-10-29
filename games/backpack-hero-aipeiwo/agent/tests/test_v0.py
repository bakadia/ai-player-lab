"""agent 侧离线自测。不需要游戏在跑。

    python games/backpack-hero-aipeiwo/agent/tests/test_v0.py

覆盖三件事：账本的累计/持久化、策略的挑选规则、delta 到伤害的换算。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = HERE.parent
sys.path.insert(0, str(AGENT))

from ledger import Ledger  # noqa: E402
from policy import choose  # noqa: E402
from run import _damage_from  # noqa: E402


def state_with(items, energy=3, enemy_hp=30):
    return {
        "seq": 1,
        "mode": "battle",
        "battle_phase": 1,
        "player": {"hp": 40, "energy": energy, "max_energy": 3},
        "enemies": [{"uid": "e1", "name": "Rat", "hp": enemy_hp, "max_hp": enemy_hp}],
        "backpack": {"items": items},
        "legal": [{"type": "use_item", "uid": it["uid"], "energy": it.get("energy_cost", 0)}
                  for it in items] + [{"type": "end_turn"}],
    }


def test_ledger():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "items.json"
        led = Ledger(path)

        assert led.estimate_damage("Sword") is None, "没样本应为 None"

        led.record_use("Sword", 7, 0)
        led.record_use(" Sword ", 3, 0)   # 名字带空格要归一
        assert led.estimate_damage("Sword") == 5.0, led.estimate_damage("Sword")
        led.save()

        again = Ledger(path)
        assert again.estimate_damage("Sword") == 5.0, "持久化后应一致"
        assert again.get("Sword")["samples"] == 2
        print("PASS ledger")


def test_policy_explore_then_known():
    with tempfile.TemporaryDirectory() as td:
        led = Ledger(Path(td) / "items.json")
        items = [
            {"uid": "i1", "name": "Sword", "energy_cost": 1},
            {"uid": "i2", "name": "Rock", "energy_cost": 0},
        ]
        st = state_with(items)

        # 都没样本 -> 应该选一个未知物品探索，而不是 end_turn
        act, why = choose(st, led, set())
        assert act["type"] == "use_item", (act, why)
        assert "explore" in why, why

        # 已知 Sword 打 7，Rock 没样本：Sword 分数应更高
        led.record_use("Sword", 7, 0)
        act, why = choose(st, led, {"Rock"})
        assert act["uid"] == "i1", (act, why)

        # 低于 1 血：给击杀奖励，仍选伤害
        st2 = state_with(items, enemy_hp=5)
        act, why = choose(st2, led, {"Rock"})
        assert act["uid"] == "i1" and "lethal=True" in why, why
        print("PASS policy explore/known")


def test_policy_all_zero_ends_turn():
    with tempfile.TemporaryDirectory() as td:
        led = Ledger(Path(td) / "items.json")
        # 已知物品均伤 0（比如纯资源），且都探索过了 -> 应 end_turn
        led.record_use("Rock", 0, 0)
        items = [{"uid": "i2", "name": "Rock", "energy_cost": 0}]
        st = state_with(items)
        act, why = choose(st, led, {"Rock"})
        assert act["type"] == "end_turn", (act, why)
        print("PASS policy fallback end_turn")


def test_incoming_damage():
    from policy import incoming_damage

    st = {"enemies": [
        {"hp": 10, "attacks": [{"type": 0, "power": 6}, {"type": 1, "power": 16}]},
        {"hp": 10, "attacks": [{"type": 0, "power": 9}]},
        {"hp": 10, "attacks": [{"type": 0, "power": 5, "hidden": True}]},
    ]}
    # 6 + 9 = 15；type=1(block) 不算，hidden 不算
    assert incoming_damage(st) == 15, incoming_damage(st)
    assert incoming_damage({"enemies": []}) == 0
    assert incoming_damage({"enemies": [{"hp": 5}]}) == 0
    print("PASS incoming damage")


def test_block_value():
    from policy import _block_value

    assert _block_value(5, 0, 40) == 0        # 没人打你，盾不值钱
    assert _block_value(5, 9, 40) == 5        # 正常挡
    assert _block_value(20, 9, 40) == 9       # 挡超出部分是浪费，截到 9
    assert _block_value(5, 20, 10) == 10      # 这一击会死 -> 紧急翻倍
    assert _block_value(0, 9, 40) == 0
    print("PASS block value")


def test_block_item_preferred_when_threatened():
    with tempfile.TemporaryDirectory() as td:
        led = Ledger(Path(td) / "items.json")
        led.record_use("Sword", 6, 0)
        led.record_use("Buckler", 0, 8)
        items = [
            {"uid": "i1", "name": "Sword", "energy_cost": 1},
            {"uid": "i2", "name": "Buckler", "energy_cost": 1},
        ]

        # 敌人只是在加盾：没有威胁 -> 用剑
        st = state_with(items, enemy_hp=40)
        st["enemies"][0]["attacks"] = [{"type": 1, "power": 5}]
        act, why = choose(st, led, set())
        assert act["uid"] == "i1", why

        # 敌人要打 9：盾的有效价值 8 > 剑的 6 -> 用盾
        st2 = state_with(items, enemy_hp=40)
        st2["enemies"][0]["attacks"] = [{"type": 0, "power": 9}]
        act, why = choose(st2, led, set())
        assert act["uid"] == "i2", why
        assert "eff_block" in why, why
        print("PASS block item preferred when threatened")


def test_reorganize_action():
    from policy import choose

    led = Ledger(Path(tempfile.gettempdir()) / "_bph_nonexistent.json")
    st = {
        "mode": "reorganize",
        "inventory_phase": 3,
        "player": {"hp": 55, "energy": 3},
        "enemies": [],
        "backpack": {"items": []},
        "legal": [{"type": "finish_reorganize"}, {"type": "skip_reorganize"}],
    }
    act, why = choose(st, led, set())
    assert act["type"] in ("finish_reorganize", "skip_reorganize"), (act, why)
    assert "reorganize" in why, why
    print("PASS reorganize action")


def test_no_legal():
    led = Ledger(Path(tempfile.gettempdir()) / "_bph_nonexistent.json")
    st = {"mode": "battle", "legal": [], "backpack": {"items": []}, "enemies": [], "player": {}}
    act, why = choose(st, led, set())
    assert act is None, (act, why)
    print("PASS no legal actions")


def test_aware_block_subtracts_purchased_armor():
    from policy import Flags, choose

    with tempfile.TemporaryDirectory() as td:
        led = Ledger(Path(td) / "items.json")
        led.record_use("Sword", 6, 0)
        led.record_use("Buckler", 0, 7)
        items = [
            {"uid": "i1", "name": "Sword", "energy_cost": 1},
            {"uid": "i2", "name": "Buckler", "energy_cost": 1},
        ]
        st = state_with(items, enemy_hp=40)
        st["enemies"][0]["attacks"] = [{"type": 0, "power": 9}]

        # 还没买盾：来袭 9，7 点格挡值 7 > 剑 6 -> 用盾（w=1.0）
        act, _ = choose(st, led, set())
        assert act["uid"] == "i2", "block weight 1.0 时 7 甲应压过 6 伤"

        # 已经买了 18 点甲：再买盾一点用都没有 -> 用剑
        st2 = state_with(items, enemy_hp=40)
        st2["player"]["block"] = 18
        st2["enemies"][0]["attacks"] = [{"type": 0, "power": 9}]
        act, _ = choose(st2, led, set(), Flags(aware_block=True))
        assert act["uid"] == "i1", "护甲已经够挡住全部来袭时不该再买盾"

        # -1 是哨兵，不能当成「负的护甲」让来袭变成 10
        st3 = state_with(items, enemy_hp=40)
        st3["player"]["block"] = -1
        st3["enemies"][0]["attacks"] = [{"type": 0, "power": 9}]
        act, why = choose(st3, led, set(), Flags(aware_block=True))
        assert "incoming=9 " in why or "incoming=9(" in why, why
        print("PASS aware_block")


def test_explore_threat_uses_unknown_defensive_item():
    from policy import Flags, choose

    with tempfile.TemporaryDirectory() as td:
        led = Ledger(Path(td) / "items.json")
        led.record_use("Sword", 6, 0)          # 已知的剑，1 费 6 伤
        items = [
            {"uid": "i1", "name": "Sword", "energy_cost": 1},
            {"uid": "i2", "name": "Buckler", "energy_cost": 1},   # 账本里没有
        ]
        f = Flags(explore_threat=True)

        # 来袭 9：未知物品按 0.8×9=7.2 估值，压过剑的 5.9 -> 去试它
        st = state_with(items, enemy_hp=40)
        st["enemies"][0]["attacks"] = [{"type": 0, "power": 9}]
        act, why = choose(st, led, set(), f)
        assert act["uid"] == "i2", why
        assert "explore against the threat" in why, why

        # 没有来袭：未知物品不值得试，老老实实打输出
        st2 = state_with(items, enemy_hp=40)
        st2["enemies"][0]["attacks"] = []
        act, _ = choose(st2, led, set(), f)
        assert act["uid"] == "i1", "没有威胁时不该白试未知物品"

        # 开关关着时保持老行为：0.5 的探索分永远输给剑
        act, _ = choose(st, led, set(), Flags())
        assert act["uid"] == "i1", "关掉开关必须回到 v0.1 行为"
        print("PASS explore_threat")


def test_explore_threat_survival_prior_when_lethal():
    from policy import Flags, choose

    with tempfile.TemporaryDirectory() as td:
        led = Ledger(Path(td) / "items.json")
        led.record_use("Sword", 6, 0)
        items = [
            {"uid": "i1", "name": "Sword", "energy_cost": 1},
            {"uid": "i2", "name": "Buckler", "energy_cost": 1},
        ]
        st = state_with(items, enemy_hp=40)
        st["player"]["hp"] = 2                    # 下一击必死
        st["enemies"][0]["attacks"] = [{"type": 0, "power": 7}]
        act, why = choose(st, led, set(), Flags(explore_threat=True))
        assert act["uid"] == "i2", why
        assert "next hit is lethal" in why, why

        # 但能一击清场时，击杀优先于探索
        led.record_use("Buckler", 0, 7)
        st2 = state_with(items, enemy_hp=5)
        st2["player"]["hp"] = 2
        st2["enemies"][0]["attacks"] = [{"type": 0, "power": 7}]
        act, why = choose(st2, led, set(), Flags(explore_threat=True))
        assert act["uid"] == "i1" and "lethal=True" in why, why
        print("PASS explore survival prior")


def test_value_energy_credits_refund():
    from policy import Flags, choose

    with tempfile.TemporaryDirectory() as td:
        led = Ledger(Path(td) / "items.json")
        led.record_use("Sword", 6, 0)
        # 0 费、回 2 能量的消耗品：0 伤害 0 格挡，不折价就恒为 0 分
        led.record_use("Elixir", 0, 0, energy=2)
        items = [
            {"uid": "i1", "name": "Sword", "energy_cost": 1},
            {"uid": "i2", "name": "Elixir", "energy_cost": 0},
        ]
        st = state_with(items, enemy_hp=40)
        st["enemies"][0]["attacks"] = [{"type": 0, "power": 9}]

        act, _ = choose(st, led, set(), Flags())
        assert act["uid"] == "i1", "不折价时消耗品是 0 分，永远没人用"

        act, why = choose(st, led, set(), Flags(value_energy=True))
        assert act["uid"] == "i2", why
        assert "energy_gain" in why, why
        print("PASS value_energy")


def test_damage_from_delta():
    assert _damage_from({"enemies": {"e1": -7}}) == 7
    assert _damage_from({"enemies": {"e1": -7, "e2": -3}}) == 10
    assert _damage_from({"enemies": {"e1": "gone"}}) == 0
    assert _damage_from({"enemies": {"e1": 2}}) == 0
    assert _damage_from({}) == 0
    # 致命一击：delta 里是 "gone"，要用行动前血量当伤害
    assert _damage_from({"enemies": {"e1": "gone"}}, {"e1": 36}) == 36
    assert _damage_from({"enemies": {"e1": -5, "e2": "gone"}}, {"e2": 4}) == 9
    # 敌人逃跑/消失但行动前血量未知 -> 不瞎记
    assert _damage_from({"enemies": {"e9": "gone"}}, {"e1": 36}) == 0
    print("PASS damage from delta")


def test_absorbed():
    from run import _absorbed_from

    assert _absorbed_from({"enemies_block": {"e1": -5}}) == 5
    assert _absorbed_from({"enemies_block": {"e1": -5, "e2": -2}}) == 7
    assert _absorbed_from({"enemies_block": {"e1": 3}}) == 0      # 对方加盾不算被吸收
    assert _absorbed_from({}) == 0
    print("PASS absorbed from delta")


def test_receipt_effects_make_status_item_visible():
    from policy import Flags, choose

    with tempfile.TemporaryDirectory() as td:
        led = Ledger(Path(td) / "items.json")
        receipt = {
            "action": "use_item",
            "item": "Heat Potion",
            "effects": [
                {"kind": "status", "from": "Heat Potion", "to": "player",
                 "status": "HEAT", "delta": 1, "value": 1},
                {"kind": "max_hp", "to": "player", "delta": 2},
                {"kind": "items", "field": "spawned", "count": 1},
                {"kind": "phase", "field": "inventory", "from": 0, "to": 3},
            ],
        }
        led.record_use("Heat Potion", 0, 0, receipt=receipt)
        assert led.estimate_damage("Heat Potion") == 0.0
        eff = led.estimate_effects("Heat Potion")
        assert eff["status:HEAT:player"] == 1.0, eff
        assert eff["max_hp"] == 2.0, eff
        assert eff["items:spawned"] == 1.0, eff
        assert eff["phase:inventory:3"] == 1.0, eff
        # items/phase 记账但不打分：生成的不一定是好东西，进整理通常是代价
        assert led.effect_score("Heat Potion") == 3.0, led.effect_score("Heat Potion")

        items = [{"uid": "i1", "name": "Heat Potion", "energy_cost": 0}]
        st = state_with(items, enemy_hp=30)

        # 没有回执/不打开开关：0 伤害 0 格挡 -> 只能 end_turn
        act, _ = choose(st, led, set(), Flags())
        assert act["type"] == "end_turn", act

        # 打开 value_status：状态效果把分数抬到正数 -> 会用
        act, why = choose(st, led, set(), Flags(value_status=True))
        assert act["uid"] == "i1", why
        assert "effects=" in why, why
        print("PASS receipt effects")


def test_ledger_absorbed():
    with tempfile.TemporaryDirectory() as td:
        led = Ledger(Path(td) / "items.json")
        led.record_use("Sword", 7, 0)
        led.record_use("Sword", 0, 0, absorbed=7)
        assert led.get("Sword")["samples"] == 2
        assert led.get("Sword")["damage_sum"] == 7
        assert led.get("Sword")["absorbed_sum"] == 7
        print("PASS ledger absorbed")


def test_planner_actions_never_chosen_in_battle():
    """腾位动作（挪/转/换/丢）不进战斗决策：丢东西不可逆，只能由 planner 发。"""
    from policy import choose

    led = Ledger(Path(tempfile.gettempdir()) / "_bph_nonexistent.json")
    st = {
        "mode": "battle",
        "battle_phase": 1,
        "player": {"hp": 40, "energy": 3},
        "enemies": [{"uid": "e1", "hp": 30, "max_hp": 30, "attacks": []}],
        "backpack": {"items": [{"uid": "i2", "name": "Buckler", "energy_cost": 0}]},
        "legal": [
            {"type": "drop_item", "uid": "i2", "name": "Buckler"},
            {"type": "move_item", "uid": "i2"},
            {"type": "rotate_item", "uid": "i2"},
            {"type": "swap_items", "a": "i2", "b": "i3"},
            {"type": "end_turn"},
        ],
    }
    act, why = choose(st, led, set())
    # 战斗里只剩腾位动作和 end_turn -> 必须结束回合，不能去丢东西
    assert act["type"] == "end_turn", (act, why)

    # 有战利品可捡时会去捡（LOOT_SCORE 高于一切普通动作）
    st2 = dict(st)
    st2["legal"] = list(st["legal"]) + [
        {"type": "place_item", "uid": "i9", "name": "Loot", "needs": 1, "free": 2},
    ]
    act, why = choose(st2, led, set())
    assert act["type"] == "place_item", (act, why)
    print("PASS planner actions not chosen in battle")


def test_current_room_event():
    """`_current_room_event`：按 pos 把事件和「玩家所在房间」配对（地图分支靠它判类型）。"""
    from loop import _current_room_event

    st = {
        "map": {
            "player": {"room_at": "r2"},
            "rooms": [
                {"uid": "r1", "pos": [0, 1.5]},
                {"uid": "r2", "pos": [1, 1.5]},
            ],
            "events": [
                {"uid": "d1", "type": 4, "pos": [0, 1.5], "finished": False},
                {"uid": "d2", "type": 11, "pos": [1.0, 1.5], "finished": False},
                {"uid": "d3", "type": 11, "pos": [1, 1.5], "finished": True},
            ],
        }
    }
    ev = _current_room_event(st)
    assert ev and ev["uid"] == "d2", ev          # 只认本房间且未结束的

    # 事件都结束了 -> None（循环会去走房间，而不是发 start_event）
    st["map"]["events"][1]["finished"] = True
    assert _current_room_event(st) is None

    # 房间/事件缺失也不能炸
    assert _current_room_event({"map": {}}) is None
    print("PASS current room event")


def test_focus_fire_rewards_killing_a_weak_enemy():
    """集火：能打死「某个」敌人时按它的攻击力加分；打不死就不加；清场仍是 100。"""
    from policy import Flags, score_action

    with tempfile.TemporaryDirectory() as td:
        led = Ledger(Path(td) / "items.json")
        led.record_use("Sword", 6, 0)              # 6 伤 / 1 费
        items = [{"uid": "i1", "name": "Sword", "energy_cost": 1}]
        act = {"type": "use_item", "uid": "i1", "energy": 1}

        # 两个敌人：5 血 9 攻（这一剑 6 伤能打死）+ 30 血 3 攻
        st = state_with(items, enemy_hp=30)
        st["enemies"] = [
            {"uid": "e1", "name": "Weak", "hp": 5, "max_hp": 30,
             "attacks": [{"type": 0, "power": 9}]},
            {"uid": "e2", "name": "Big", "hp": 30, "max_hp": 30,
             "attacks": [{"type": 0, "power": 3}]},
        ]
        off, _ = score_action(act, st, led, set(), Flags())
        on, why = score_action(act, st, led, set(), Flags(focus_fire=True))
        assert on == off + 18.0, (off, on, why)     # 9 攻 × KILL_TURNS(2)
        assert "kill_value=18.0" in why, why

        # 打不死任何敌人：一点分都不多给
        st2 = state_with(items, enemy_hp=40)
        st2["enemies"] = [
            {"uid": "e1", "name": "A", "hp": 30, "max_hp": 30,
             "attacks": [{"type": 0, "power": 9}]},
            {"uid": "e2", "name": "B", "hp": 30, "max_hp": 30,
             "attacks": [{"type": 0, "power": 3}]},
        ]
        off2, _ = score_action(act, st2, led, set(), Flags())
        on2, _ = score_action(act, st2, led, set(), Flags(focus_fire=True))
        assert on2 == off2, (off2, on2)

        # 一击清场仍然是 LETHAL_BONUS(100)，不会被集火的分盖过去
        st3 = state_with(items, enemy_hp=5)
        st3["enemies"] = [{"uid": "e1", "name": "Only", "hp": 5, "max_hp": 5,
                           "attacks": [{"type": 0, "power": 9}]}]
        on3, why3 = score_action(act, st3, led, set(), Flags(focus_fire=True))
        off3, _ = score_action(act, st3, led, set(), Flags())
        assert on3 == off3 and "lethal=True" in why3, why3
        print("PASS focus fire")


if __name__ == "__main__":
    test_ledger()
    test_policy_explore_then_known()
    test_policy_all_zero_ends_turn()
    test_no_legal()
    test_reorganize_action()
    test_incoming_damage()
    test_block_value()
    test_block_item_preferred_when_threatened()
    test_aware_block_subtracts_purchased_armor()
    test_explore_threat_uses_unknown_defensive_item()
    test_explore_threat_survival_prior_when_lethal()
    test_value_energy_credits_refund()
    test_receipt_effects_make_status_item_visible()
    test_damage_from_delta()
    test_absorbed()
    test_ledger_absorbed()
    test_planner_actions_never_chosen_in_battle()
    test_current_room_event()
    test_focus_fire_rewards_killing_a_weak_enemy()

    # 背包 planner 的自测也一起跑（纯函数，不需要游戏）
    from test_backpack import main as backpack_tests
    backpack_tests()

    print("ALL PASS")
