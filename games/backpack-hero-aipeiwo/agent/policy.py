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
from itemdb import ItemDB

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

# ---- 护甲流的定价（用户口径，2026-10-30）----
#
# 「每回合 7 护甲 ≈ 1 费」：ROUGH BUCKLER（1 费 / 7 挡）是实测基准。
# 而**每回合自动给甲**的防具（帽子/手套/鞋子/衣服）等于每回合白赚一份格挡、
# 还不吃能量——所以它的价值必须按「回合数 × 每回合的甲」折现，不能只看一次性的 block。
#
# 以前 `item_value` 只给 `block_per_turn * 0.5`（Knight's Armor 6/turn = 5.0 分），
# 比一把木剑（账本 5.4）还低——于是「腾位」时第一件被丢的正是护甲，和主流打法反着来。
#
# FIGHT_TURNS：M4 六局实测 30 个 end_turn / 8 场 ≈ 3.75 回合，取 3 保守一点
# （第一回合往往还没吃到整套甲，而且甲超过当回合 incoming 就是浪费）。
BLOCK_PER_ENERGY = 7.0
FIGHT_TURNS = 3.0
# 1 点能量折成多少「分数」：reward_score 里 1 费 7 伤的武器 = 7 × 2.0 = 14。
ENERGY_SCORE = 14.0
# 1 点能量折成多少「等价伤害」：账本实测木剑 5.4 净伤 / 1 费（M2 交叉验证）。
DAMAGE_PER_ENERGY = 5.4


def per_turn_energy(block_per_turn: float) -> float:
    """每回合 N 甲 → 等效能量数（7 甲 = 1 费，按 FIGHT_TURNS 回合折现）。

    `per_turn_energy(7) == 3.0`：一件 7 甲/回合的防具，一场三回合的战斗里相当于白送 3 费。
    """
    if block_per_turn is None or block_per_turn <= 0:
        return 0.0
    return block_per_turn / BLOCK_PER_ENERGY * FIGHT_TURNS


def per_turn_value(block_per_turn: float) -> float:
    """每回合 N 甲 → 等价伤害值（单位与账本一致，给「该留哪件 / 该丢哪件」用）。"""
    return per_turn_energy(block_per_turn) * DAMAGE_PER_ENERGY

# 一点能量值多少分。只在背包里没有任何已知攻击物品时当兜底。
ENERGY_VALUE_DEFAULT = 6.0

# 集火：杀掉一个敌人 = 它之后每回合的伤害都没了。
# 但 `use_item` 不能指定目标（`legal` 里没有 target 参数，游戏自己选），
# 所以这里只能按「这一击的伤害能不能打死某个敌人」估「能省下多少」：
#   kill_value = 能被打死的那些敌人的攻击力之和 × KILL_TURNS（大约还能活几回合）
# 2.0 是保守估计（一局一场战斗 3–6 回合），避免把「杀小怪」估得比「清场」还高。
KILL_TURNS = 2.0
# 「这一击能不能打死某个敌人」用账本均伤判断，均伤有波动，留一点余量更稳。
KILL_SLACK = 0.5

# 炸弹怪（Bomb 一族，真机见过 "Hermit Crab Bomb"）：几回合后自爆，一次掉 26 血。
# 它平时的攻击力可能是 0，所以 kill_value（按攻击力折价）完全看不见它——
# 单独给一笔「趁早炸掉」的分数。数值取 25，和实测的一次自爆伤害同量级。
BOMB_NAME_HINTS = ("BOMB",)
BOMB_KILL_VALUE = 25.0

# 结构化回执里「一个状态点 / 一点最大生命 / 一点幸运」值多少分。
# 1.0 只是让这类物品不再恒为 0；具体值应该等更多样本再调，不要拍脑袋调大。
STATUS_VALUE = 1.0

# 续航（`--sustain`）：一点**有效治疗**（不超过缺失血量）值多少分。
# 取 1.0 = 「回 1 血」和「挡 1 伤」等价——都不是直接赢，但都能多撑一回合。
# 满血时有效治疗是 0，所以不会在满血时乱吃食物（与 `_block_value` 的 min 逻辑一致）。
SUSTAIN_WEIGHT = 1.0

ATTACK = 0  # Enemy+Attack+Type.attack


@dataclass(frozen=True)
class Flags:
    """A/B 开关。全默认 = v0.1 的老行为。"""

    aware_block: bool = False
    ratio_energy: bool = False
    explore_threat: bool = False
    value_energy: bool = False
    value_status: bool = False
    focus_fire: bool = False
    sustain: bool = False
    explore_score: float = EXPLORE_SCORE
    survival_prior: float = SURVIVAL_PRIOR
    block_weight: float = BLOCK_WEIGHT
    explore_threat_weight: float = EXPLORE_THREAT_WEIGHT
    explore_threat_cap: float = EXPLORE_THREAT_CAP
    sustain_weight: float = SUSTAIN_WEIGHT


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
                         ("value_status", f.value_status),
                         ("focus_fire", f.focus_fire),
                         ("sustain", f.sustain)) if v]
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
    db: ItemDB | None = None,
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

    # 背包内挪动 / 互换 / 丢弃：只由「腾位」计划按需发出（agent/backpack.py）。
    # 战斗策略**不能**自己挑它们——丢东西不可逆，挪动本身也不产生任何价值，
    # 让它们参与打分只会把 end_turn 换成一次无意义的拖拽。
    if t in ("move_item", "rotate_item", "swap_items", "drop_item"):
        return -1.0, 0.0, 0.0, f"{t}: only the make-room planner may pick this"

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

    # 续航（`--sustain`）：先算「有效治疗」，账本有没有样本都要算——
    # 真机上大部分食物从来没被用过（账本里没有样本），如果只在 `est is not None`
    # 之后才给分，`--sustain` 对它们就是完全不可见的（8 分钟 A/B 里一条 sustain
    # 都没触发，就是这么来的）。
    sustain_worth = 0.0
    sustain_why = ""
    if flags.sustain and db is not None:
        st = db.get(name)
        if st is not None and st.heal > 0:
            max_hp = int(state.get("player", {}).get("max_hp") or 0)
            if max_hp > 0:
                missing = max(0, max_hp - hp)
                effective = min(st.heal, float(missing))
                sustain_worth = effective * flags.sustain_weight
                if effective > 0:
                    sustain_why = (f"sustain={effective:.0f}/{st.heal:.0f}hp"
                                   f"(hp={hp}/{max_hp}) worth={sustain_worth:+.2f}")
                else:
                    sustain_why = "sustain=0 (full hp)"

    est = ledger.estimate_damage(name)
    if est is None:
        key = name.strip()
        # 没测过的食物：有效治疗本身就是分数。检查过 `explored` 之后再判它，
        # 否则「试过一次的回血物品」会在真需要它的时候被永久跳过。
        if sustain_worth > 0:
            total = flags.explore_score + sustain_worth
            return total, 0.0, energy, f"{name}: {sustain_why} (unknown item)"
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
    enemies = _enemies(state)
    total_enemy_hp = sum(e.get("hp", 0) for e in enemies)
    lethal = est >= total_enemy_hp > 0

    bv = _block_value(block, incoming, hp) * flags.block_weight
    value = est + bv - ENERGY_WEIGHT * energy
    why_extra = ""

    # 集火（`focus_fire`）：原来的 `lethal` 只在「一击清场」时才给奖励，
    # 2–3 个敌人的时候几乎永远不成立 —— 于是策略完全没有「先打死一个」的动机，
    # 而每少一个敌人，每回合 incoming 就少一截（实测每场掉血 4–14，是死因）。
    # 这里改成「这一击能打死谁，就按那些敌人的攻击力折价」。
    kill_value = 0.0
    if flags.focus_fire and not lethal:
        hit = est + KILL_SLACK
        for e in enemies:
            ehp = e.get("hp")
            if not isinstance(ehp, int) or ehp <= 0:
                continue
            if hit < ehp:
                continue
            threat = 0
            for atk in e.get("attacks") or []:
                if atk.get("type") == ATTACK and not atk.get("hidden"):
                    p = atk.get("power")
                    if isinstance(p, int) and p > 0:
                        threat += p
            kill_value += threat * KILL_TURNS
            ename = str(e.get("name") or "").upper()
            if any(h in ename for h in BOMB_NAME_HINTS):
                # 炸弹怪：自爆伤害不看它的常规攻击力，单独加分（见 BOMB_KILL_VALUE）。
                kill_value += BOMB_KILL_VALUE
                why_extra += f" bomb({e.get('name')})"
        kill_value = min(kill_value, LETHAL_BONUS)
        if kill_value > 0:
            why_extra += f" kill_value={kill_value:.1f}(T={KILL_TURNS})"

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

    # 续航：用上面算好的有效治疗（满血时是 0，不会浪费；理由照样写进日志）。
    if sustain_why:
        value += sustain_worth
        why_extra += " " + sustain_why

    bonus = LETHAL_BONUS if lethal else kill_value
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
    db: ItemDB | None = None,
) -> tuple[float, str]:
    """返回 (分数, 理由)。理由是给人看的，调试时不至于只有数字。

    分数是**绝对分**（不含 `/energy` 归一化）——归一化只发生在 `choose` 里，
    这样复盘的日志仍然能横向比较。
    """
    f = flags or _active_flags
    value, bonus, _energy, why = _score_parts(action, state, ledger, explored, f, db)
    return value + bonus, why


def choose(
    state: dict[str, Any],
    ledger: Ledger,
    explored: set[str] | None = None,
    flags: Flags | None = None,
    db: ItemDB | None = None,
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
        value, bonus, energy, why = _score_parts(action, state, ledger, explored, f, db)
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

# ---------------------------------------------------------------- 奖励 / 商店三选一


def reward_score(name: str, db: ItemDB | None, hp: int = 0, max_hp: int = 0) -> float:
    """候选只有名字时，估一下「拿它值不值」。

    没有 `--sustain` 也不该固定拿第 0 个：这个函数只回答排序问题，
    与战斗打分无关（真正用它的是 loop.py 的 `pick_dialogue`）。
    """
    if db is None:
        return 0.0
    st = db.get(name)
    if st is None:
        return 0.25                      # 不认识的候选给一点点分，别永远排第一
    score = 0.0
    if st.heal > 0 and max_hp > 0:
        missing = float(max(0, max_hp - hp))
        # 缺血时治疗更值钱：按「缺口占最大生命的比例」加权。
        # 不加权的话，7/40 血时 heal 10（10 分）会输给一把 7 伤武器（14 分），
        # 真机战利品里就这么错过过回血消耗品。
        urgency = 1.0 + 2.0 * (missing / float(max_hp))
        score += min(st.heal, missing) * SUSTAIN_WEIGHT * urgency
    score += st.damage_active / max(1, st.energy_cost) * 2.0
    # 每回合护甲按「7 甲 = 1 费 × FIGHT_TURNS 回合」折现，不再是一点 1.5 分的零头。
    # 6 甲/回合的 Knight's Armor ≈ 2.57 费 ≈ 36 分，压过一把 1 费 7 伤的剑（14 分）——
    # 这正是「护甲流」的定价：被动、不吃能量、每回合都生效。
    score += st.block_active * 0.5 + per_turn_energy(st.block_per_turn) * ENERGY_SCORE
    score += st.max_hp_gain * 2.0
    if st.category == "relic":
        score += 1.0
    if st.category == "support":
        score += (st.support_damage + st.support_block) * 0.3
    if "curse" in st.tags:
        score -= 3.0
    return score


def pick_reward(names: list[str], db: ItemDB | None, hp: int = 0, max_hp: int = 0) -> int:
    """在 N 选 1 里挑一个下标；名单为空或没有 db 时退回 0（老行为）。"""
    if not names or db is None:
        return 0
    best_idx, best = 0, float("-inf")
    for i, name in enumerate(names):
        s = reward_score(name, db, hp, max_hp)
        if s > best:
            best_idx, best = i, s
    return best_idx
