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

## 三条带开关的补丁（SPIKE-07 §4 + 一条补充）

`Flags` 里的开关默认**全关**，关着的时候行为与 v0.1 完全一致（可以逐位复现旧日志）。
开关由 `loop.py` 的命令行打开，方便 A/B。

1. `aware_block`：`incoming` 减去**已经买到的** `player.block`。
   不开的话，每面盾都按「全额来袭」估值——护甲叠到 18 了还在给新盾算 7 分。
   注意 `player.block` 是 `Status.armor`，`StateReader` 在拿不到时写 **-1 哨兵**，
   所以必须 `max(0, ...)`，否则 `incoming - (-1)` 会多算 1 点。

2. `ratio_energy`：按「每点能量的分数」选，而不是绝对分数。
   单独开它会**更差**（SPIKE-07 §3.1：盾通常更便宜，按性价比排序会把能量全喂给盾），
   必须和 `aware_block` 一起开。

3. `explore_threat`：**没见过的物品，按它能挡住的威胁估值**。
   这条是 SPIKE-07 §4 没写的，但它是真机日志里 ROUGH BUCKLER 整局 0 次使用的真正原因：
   未知物品只有 `explore_score`（0.5），永远打不过已知的剑（~6），于是永远不会被试，
   于是永远学不到数值——账本冷启动死锁。SPIKE-07 的合成 A/B 用的是「每件物品都已知」的
   替身账本，所以没暴露这件事（它自己的 §5.4 承认了这个假设）。

   估值方式不是拍一个常数，而是「它最多能替我挡掉多少」：
   `EXPLORE_THREAT_WEIGHT × min(incoming, EXPLORE_THREAT_CAP)`。
   来袭 9 时给 7.2 > 剑的 5.83，于是会去试；来袭 0 时给 0，不会白试。
   `0 < hp <= incoming`（下一击必死）时改用 `survival_prior`（40），
   高于普通输出、低于 `LETHAL_BONUS`（100），所以「能一击清场」仍然优先于探索。

4. `value_energy`：把「+N 能量」按「一点能量能换多少分」折价。
   回能量的消耗品是 0 伤害 0 格挡，不折价就是 0 分——正是卡牌游戏里
   「资源型动作在按场面打分时不可见」的经典盲区。一点能量的价值取背包里
   已知攻击物品的最高「均伤/费用」，没有已知攻击物品时退回 `ENERGY_VALUE_DEFAULT`。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ledger import Ledger

EXPLORE_SCORE = 0.5
LOOT_SCORE = 50.0
LETHAL_BONUS = 100.0
ENERGY_WEIGHT = 0.1
URGENT_BLOCK_MULTIPLIER = 2.0   # 这一击会打死我们时，格挡更值钱

# 格挡值多少分（= 一点格挡抵得上几点伤害）。1.0 是「挡 7 点 = 少挨 7 点」的朴素读法；
# SPIKE-07 的评测函数用的是 0.8（那会让 1 费 7 甲输给 1 费 6 伤的剑）。
BLOCK_WEIGHT = 1.0

# 未知物品按「它最多能替我挡掉多少」估值，再乘这个系数。
# 要能压过已知输出（剑 ~5.9/1 费）才能真的被试：0.8 × 来袭 9 = 7.2 > 5.9 ✓
EXPLORE_THREAT_WEIGHT = 0.8
EXPLORE_THREAT_CAP = 10.0

# 下一击必死时，未知物品值多少。三个常量的大小关系是有意的：
#   LETHAL_BONUS(100) > LOOT_SCORE(50) > SURVIVAL_PRIOR(40) > 任何普通输出(~6)
# 即：能一击清场 > 白捡东西（不耗能量、不捡就没） > 赌一把未知物品 > 继续输出。
# 别把它设成 50——会和 LOOT_SCORE 打平，胜负取决于 legal 里的顺序。
SURVIVAL_PRIOR = 40.0

# 一点能量值多少分。只在背包里没有任何已知攻击物品时当兜底。
ENERGY_VALUE_DEFAULT = 6.0

# 结构化回执里「一个状态点 / 一点最大生命 / 一点幸运」值多少分。
# 1.0 只是让这类物品不再恒为 0；具体值应该等更多样本再调，不要拍脑袋调大。
STATUS_VALUE = 1.0

ATTACK = 0  # Enemy+Attack+Type.attack


@dataclass(frozen=True)
class Flags:
    """A/B 开关。全默认 = v0.1 的老行为。"""

    aware_block: bool = False
    ratio_energy: bool = False
    explore_threat: bool = False
    value_energy: bool = False
    value_status: bool = False
    explore_score: float = EXPLORE_SCORE
    survival_prior: float = SURVIVAL_PRIOR
    block_weight: float = BLOCK_WEIGHT
    explore_threat_weight: float = EXPLORE_THREAT_WEIGHT
    explore_threat_cap: float = EXPLORE_THREAT_CAP


_DEFAULT_FLAGS = Flags()
_active_flags = _DEFAULT_FLAGS


def get_flags() -> Flags:
    return _active_flags


def set_flags(flags: Flags | None = None, **kwargs: Any) -> Flags:
    """设定本进程的默认开关。`loop.py` 启动时按命令行设一次。"""
    global _active_flags
    base = _active_flags if flags is None else flags
    _active_flags = replace(base, **kwargs) if kwargs else base
    return _active_flags


def reset_flags() -> Flags:
    global _active_flags
    _active_flags = _DEFAULT_FLAGS
    return _active_flags


def flags_label(flags: Flags | None = None) -> str:
    """给日志用的短标签，A/B 分组靠它。"""
    f = flags or _active_flags
    on = [n for n, v in (("aware_block", f.aware_block),
                         ("ratio_energy", f.ratio_energy),
                         ("explore_threat", f.explore_threat),
                         ("value_energy", f.value_energy),
                         ("value_status", f.value_status)) if v]
    return "+".join(on) if on else "baseline"


def energy_value(state: dict[str, Any], ledger: Ledger) -> float:
    """一点能量大约值多少分：取背包里已知攻击物品的最高「均伤 / 费用」。

    这样「+2 能量」就能折成「够我再打两下最强的攻击」，而不是一个凭空的常数。
    """
    best = 0.0
    for it in (state.get("backpack") or {}).get("items", []):
        est = ledger.estimate_damage(it.get("name", ""))
        if not est or est <= 0:
            continue
        cost = max(1.0, float(it.get("energy_cost") or 0))
        best = max(best, float(est) / cost)
    return best if best > 0 else ENERGY_VALUE_DEFAULT


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


def player_block(state: dict[str, Any]) -> int:
    """已经买到的护甲。`StateReader` 拿不到 `Status` 时写 -1 哨兵，不能当数值用。"""
    v = (state.get("player") or {}).get("block")
    if not isinstance(v, int):
        return 0
    return max(0, v)


def effective_incoming(state: dict[str, Any], flags: Flags | None = None) -> int:
    """这回合真正会打进来的伤害；`aware_block` 打开时扣掉已买到的护甲。"""
    f = flags or _active_flags
    raw = incoming_damage(state)
    if not f.aware_block:
        return raw
    return max(0, raw - player_block(state))


def _block_value(est_block: float, incoming: int, hp: int) -> float:
    """格挡的实际价值：挡超出的部分是浪费。"""
    if incoming <= 0 or est_block <= 0:
        return 0.0
    effective = min(est_block, float(incoming))
    if incoming >= hp > 0:
        effective *= URGENT_BLOCK_MULTIPLIER
    return effective


def _score_parts(
    action: dict[str, Any],
    state: dict[str, Any],
    ledger: Ledger,
    explored: set[str],
    flags: Flags,
) -> tuple[float, float, float, str]:
    """返回 (价值, 一次性奖励, 能量, 理由)。

    把 `lethal_bonus` 单独拎出来，是因为 `ratio_energy` 只该除「价值」：
    连奖励一起除的话，3 费的必杀一击会从 100 掉到 33，反而输给 1 费的普通输出。
    """
    t = action.get("type")
    if t == "end_turn":
        return 0.0, 0.0, 0.0, "end_turn baseline"

    # 整理阶段（中污染/诅咒）：这不是可选战术，是流程阻塞点，必须点掉。
    if t in ("finish_reorganize", "skip_reorganize"):
        return 1.0, 0.0, 0.0, f"{t}: unblock the reorganize phase"

    # 捡掉落物：不耗能量，而且战斗一结束就会被销毁（SPIKE-06 §18）。
    # 给高分让它在出手前先做掉；真的放不进去（背包满了）就用 explored 记住，
    # 避免同一件东西反复尝试把整局拖死。
    if t == "place_item":
        uid = action.get("uid", "")
        key = f"place:{uid}"
        if key in explored:
            return -1.0, 0.0, 0.0, f"{uid}: already failed to place"
        return LOOT_SCORE, 0.0, 0.0, f"pick up {action.get('name', uid)!r} before it is destroyed"

    uid = action.get("uid", "")
    item = _items_by_uid(state).get(uid)
    if item is None:
        return -1.0, 0.0, 0.0, f"unknown uid {uid}"

    name = item.get("name", "")
    energy = float(action.get("energy", item.get("energy_cost", 0)) or 0)
    raw_incoming = incoming_damage(state)
    incoming = effective_incoming(state, flags)
    hp = int(state.get("player", {}).get("hp") or 0)
    if flags.aware_block and incoming != raw_incoming:
        inc_txt = f"{incoming}(={raw_incoming}-{player_block(state)})"
    else:
        inc_txt = f"{incoming}"

    est = ledger.estimate_damage(name)
    if est is None:
        key = name.strip()
        if key in explored:
            return 0.0, 0.0, energy, f"{name}: already explored"
        # 下一击必死，又没见过这件东西：先试它。理由见文件头 §3。
        # 走「奖励」而不是「价值」：这是当下的情境判断，不该按能量摊薄——
        # 3 费的未知盾在必死回合并不比 1 费的差，反正都是最后一搏。
        if flags.explore_threat and 0 < hp <= incoming:
            return 0.0, flags.survival_prior, energy, (
                f"{name}: try an unknown item — next hit is lethal "
                f"(incoming={inc_txt} >= hp={hp}), knowledge is worth more than damage")
        # 没到必死，但场上有威胁：按「它最多能替我挡掉多少」估。
        # 不来袭就是 0 分，不会白试。
        if flags.explore_threat and incoming > 0:
            prior = flags.explore_threat_weight * min(float(incoming), flags.explore_threat_cap)
            return prior, 0.0, energy, (
                f"{name}: explore against the threat (incoming={inc_txt}, "
                f"prior={prior:.2f} = {flags.explore_threat_weight}×min(incoming,{flags.explore_threat_cap:.0f}))")
        return flags.explore_score, 0.0, energy, f"{name}: explore (incoming={inc_txt})"

    block = ledger.estimate_block(name)
    total_enemy_hp = sum(e.get("hp", 0) for e in _enemies(state))
    lethal = est >= total_enemy_hp > 0

    bv = _block_value(block, incoming, hp) * flags.block_weight
    value = est + bv - ENERGY_WEIGHT * energy
    why_extra = ""

    # 回能量的消耗品：0 伤害 0 格挡，只有把能量折成分数它才存在。
    # 只认正的净收益；负的（花掉的费用）已经由 ENERGY_WEIGHT 计过，别再扣一次。
    if flags.value_energy:
        gain = ledger.estimate_energy(name)
        if gain > 0:
            worth = gain * energy_value(state, ledger)
            value += worth
            why_extra = f" energy_gain={gain:+.2f} worth={worth:+.2f}"

    # 状态 / 最大生命 / 幸运：这些效果在 hp/block 里看不见，只能靠结构化回执。
    # 没有回执的老账本 effect_score 为 0，行为与 v0.1 完全一致。
    if flags.value_status:
        eff = ledger.effect_score(name)
        if eff > 0:
            worth = eff * STATUS_VALUE
            value += worth
            why_extra += f" effects={eff:.2f} worth={worth:+.2f}"

    bonus = LETHAL_BONUS if lethal else 0.0
    why = (f"{name}: est_dmg={est:.2f} est_block={block:.2f} "
           f"eff_block={bv:.2f} incoming={inc_txt} hp={hp} "
           f"energy={energy:.0f} lethal={lethal}{why_extra}")
    return value, bonus, energy, why


def score_action(
    action: dict[str, Any],
    state: dict[str, Any],
    ledger: Ledger,
    explored: set[str],
    flags: Flags | None = None,
) -> tuple[float, str]:
    """返回 (分数, 理由)。理由是给人看的，调试时不至于只有数字。

    分数是**绝对分**（不含 `/energy` 归一化）——归一化只发生在 `choose` 里，
    这样复盘的日志仍然能横向比较。
    """
    f = flags or _active_flags
    value, bonus, _energy, why = _score_parts(action, state, ledger, explored, f)
    return value + bonus, why


def choose(
    state: dict[str, Any],
    ledger: Ledger,
    explored: set[str] | None = None,
    flags: Flags | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """在 state.legal 里挑一条。返回 (action, 理由)；None 表示没有可做的。"""
    explored = explored if explored is not None else set()
    f = flags or _active_flags

    legal = state.get("legal") or []
    if not legal:
        return None, "no legal actions in state"

    best: dict[str, Any] | None = None
    best_score = float("-inf")
    best_why = ""

    for action in legal:
        value, bonus, energy, why = _score_parts(action, state, ledger, explored, f)
        s = value / max(1.0, energy) if f.ratio_energy else value
        s += bonus
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
