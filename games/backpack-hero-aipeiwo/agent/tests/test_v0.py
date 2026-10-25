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


def test_no_legal():
    led = Ledger(Path(tempfile.gettempdir()) / "_bph_nonexistent.json")
    st = {"mode": "battle", "legal": [], "backpack": {"items": []}, "enemies": [], "player": {}}
    act, why = choose(st, led, set())
    assert act is None, (act, why)
    print("PASS no legal actions")


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


def test_ledger_absorbed():
    with tempfile.TemporaryDirectory() as td:
        led = Ledger(Path(td) / "items.json")
        led.record_use("Sword", 7, 0)
        led.record_use("Sword", 0, 0, absorbed=7)
        assert led.get("Sword")["samples"] == 2
        assert led.get("Sword")["damage_sum"] == 7
        assert led.get("Sword")["absorbed_sum"] == 7
        print("PASS ledger absorbed")


if __name__ == "__main__":
    test_ledger()
    test_policy_explore_then_known()
    test_policy_all_zero_ends_turn()
    test_no_legal()
    test_damage_from_delta()
    test_absorbed()
    test_ledger_absorbed()
    print("ALL PASS")
