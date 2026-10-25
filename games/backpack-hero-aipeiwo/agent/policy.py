"""v0.1 规则策略。可解释、可复现，先超过随机基线（MVP §3.7）。

    score(action) = 伤害 + 有效格挡 - 0.1*energy + (击杀 ? 100 : 0)

    「有效格挡」= min(这回合会受到的伤害, 这件物品能挡多少)
    —— 挡超出的部分是浪费，所以用 min；会死的时候再乘个紧急系数。

四条设计取舍：

1. **未知物品给探索分**。账本里没样本，就不知道它是伤害还是纯资源，
   给一个 0.5 的固定分让它试一次——MVP 说「HP > 50% 且能量够时试一次」，
   这里更宽：任何未知物品都试，但同一局同物品只试一次（避免死循环）。
2. **不预测「能不能用」**。合法列表由 Bridge 算好（state.legal），
   策略只在合法集合里挑，不自己判断能量够不够——否则两套判断会打架。
3. **没有正分就 end_turn**。不硬凑动作。
4. **意图只影响「防守值多少分」，不试图选目标**。
   因为 `use_item` 目前没有 target 参数，游戏自己决定打谁——
   想控制目标得先让 Bridge 支持指定目标，那是后面的事。

敌人意图 `attacks[].type`：0=attack, 1=block（见 Enemy+Attack+Type）。
只有 type==0 才算「这回合会受到的伤害」；敌人加盾不影响我们。
"""

from __future__ import annotations

from typing import Any

from ledger import Ledger

EXPLORE_SCORE = 0.5
LETHAL_BONUS = 100.0
ENERGY_WEIGHT = 0.1
URGENT_BLOCK_MULTIPLIER = 2.0   # 这一击会打死我们时，格挡更值钱

ATTACK = 0  # Enemy+Attack+Type.attack


def _enemies(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in state.get("enemies", []) if e.get("targetable", True)]


def _items_by_uid(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {it["uid"]: it for it in state.get("backpack", {}).get("items", [])}


def incoming_damage(state: dict[str, Any]) -> int:
    """这一回合敌人准备打我们多少（只看 attack 意图，不减我方护甲）。"""
    total = 0
    for e in _enemies(state):
        for atk in e.get("attacks") or []:
            if atk.get("hidden"):
                continue
            if atk.get("type") == ATTACK:
                p = atk.get("power")
                if isinstance(p, int) and p > 0:
                    total += p
    return total


def _block_value(est_block: float, incoming: int, hp: int) -> float:
    """格挡的实际价值：挡超出的部分是浪费。"""
    if incoming <= 0 or est_block <= 0:
        return 0.0
    effective = min(est_block, float(incoming))
    if incoming >= hp > 0:
        effective *= URGENT_BLOCK_MULTIPLIER
    return effective


def score_action(
    action: dict[str, Any],
    state: dict[str, Any],
    ledger: Ledger,
    explored: set[str],
) -> tuple[float, str]:
    """返回 (分数, 理由)。理由是给人看的，调试时不至于只有数字。"""
    if action.get("type") == "end_turn":
        return 0.0, "end_turn baseline"

    # 整理阶段（中污染/诅咒）：这不是可选战术，是流程阻塞点，必须点掉。
    if action.get("type") in ("finish_reorganize", "skip_reorganize"):
        return 1.0, f"{action.get('type')}: unblock the reorganize phase"

    uid = action.get("uid", "")
    item = _items_by_uid(state).get(uid)
    if item is None:
        return -1.0, f"unknown uid {uid}"

    name = item.get("name", "")
    energy = float(action.get("energy", item.get("energy_cost", 0)) or 0)
    incoming = incoming_damage(state)
    hp = int(state.get("player", {}).get("hp") or 0)

    est = ledger.estimate_damage(name)
    if est is None:
        key = name.strip()
        if key in explored:
            return 0.0, f"{name}: already explored"
        return EXPLORE_SCORE, f"{name}: explore (incoming={incoming})"

    block = ledger.estimate_block(name)
    total_enemy_hp = sum(e.get("hp", 0) for e in _enemies(state))
    lethal = est >= total_enemy_hp > 0

    bv = _block_value(block, incoming, hp)
    s = est + bv - ENERGY_WEIGHT * energy
    if lethal:
        s += LETHAL_BONUS
    return s, (f"{name}: est_dmg={est:.2f} est_block={block:.2f} "
               f"eff_block={bv:.2f} incoming={incoming} hp={hp} "
               f"energy={energy:.0f} lethal={lethal}")


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
