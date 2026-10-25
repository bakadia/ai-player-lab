"""v0 规则策略。可解释、可复现，先超过随机基线（MVP §3.7）。

    score(action) = damage + 0.5*block - 0.1*energy + (击杀 ? 100 : 0)

三条设计取舍：

1. **未知物品给探索分**。账本里没样本，就不知道它是伤害还是纯资源，
   给一个 0.5 的固定分让它试一次——MVP 说「HP > 50% 且能量够时试一次」，
   这里更宽：任何未知物品都试，但同一局同物品只试一次（避免死循环）。
2. **不预测「能不能用」**。合法列表由 Bridge 算好（state.legal），
   策略只在合法集合里挑，不自己判断能量够不够——否则两套判断会打架。
3. **没有正分就 end_turn**。不硬凑动作。

未知物品的分数 0.5 低于「已知有伤害」的物品，但高于 end_turn(0)，
所以探索优先于干等，不优先于已知收益。
"""

from __future__ import annotations

from typing import Any

from ledger import Ledger

EXPLORE_SCORE = 0.5
LETHAL_BONUS = 100.0
BLOCK_WEIGHT = 0.5
ENERGY_WEIGHT = 0.1


def _enemies(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in state.get("enemies", []) if e.get("targetable", True)]


def _items_by_uid(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {it["uid"]: it for it in state.get("backpack", {}).get("items", [])}


def score_action(
    action: dict[str, Any],
    state: dict[str, Any],
    ledger: Ledger,
    explored: set[str],
) -> tuple[float, str]:
    """返回 (分数, 理由)。理由是给人看的，调试时不至于只有数字。"""
    if action.get("type") == "end_turn":
        return 0.0, "end_turn baseline"

    uid = action.get("uid", "")
    item = _items_by_uid(state).get(uid)
    if item is None:
        return -1.0, f"unknown uid {uid}"

    name = item.get("name", "")
    energy = float(action.get("energy", item.get("energy_cost", 0)) or 0)

    est = ledger.estimate_damage(name)
    if est is None:
        key = name.strip()
        if key in explored:
            # 本局试过了还没拿到样本（可能不出伤害），别再耗能量
            return 0.0, f"{name}: already explored"
        return EXPLORE_SCORE, f"{name}: explore"

    block = ledger.estimate_block(name)
    total_enemy_hp = sum(e.get("hp", 0) for e in _enemies(state))
    lethal = est >= total_enemy_hp > 0

    s = est + BLOCK_WEIGHT * block - ENERGY_WEIGHT * energy
    if lethal:
        s += LETHAL_BONUS
    return s, f"{name}: est_dmg={est:.2f} block={block:.2f} energy={energy:.0f} lethal={lethal}"


def choose(
    state: dict[str, Any],
    ledger: Ledger,
    explored: set[str] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """在 state.legal 里挑一条。返回 (action, 理由)；None 表示没有可做的。"""
    explored = explored if explored is not None else set()

    legal = state.get("legal") or []
    if not legal:
        return None, "no legal actions in state"

    best: dict[str, Any] | None = None
    best_score = float("-inf")
    best_why = ""

    for action in legal:
        s, why = score_action(action, state, ledger, explored)
        if s > best_score:
            best_score, best, best_why = s, action, why

    if best is None:
        return None, "no candidate"
    if best_score <= 0 and best.get("type") != "end_turn":
        # 全是非正分 -> 结束回合
        for a in legal:
            if a.get("type") == "end_turn":
                return a, f"all non-positive (best {best_score:.2f}), end turn"
    return best, f"score={best_score:.2f} {best_why}"
