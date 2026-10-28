"""离线 A/B：同一个估值函数，只换「决策过程」。

问题：`policy.choose` 是**逐步贪心**——每次只挑当前分数最高的一个动作，
选完再看下一轮。它有两个盲点：

1. **预算耦合**：先花 1 点能量买小件，可能就买不起剩下那个 2 费的大件；
2. **格挡饱和**：它拿「敌人总伤害」算每件盾的价值，但第一面盾已经把伤害挡完了，
   第二面盾的价值应该是 0，贪心却按满值算。

这个脚本把「同估值」这件事钉死：**估值函数、账本、合法性判断全都复用 `policy.py`**，
只把决策过程换掉，对比四种：

    greedy             = 现在的 policy.choose（逐步贪心）
    greedy_ratio       = 同一个贪心，但按「每点能量的分数」选
    greedy_aware       = 同一个贪心，但每步把「已被挡掉的伤害」从 incoming 里扣掉
    greedy_ratio_aware = 上面两条一起（合起来约 6 行改动）

另外一个 `enumerate_turn` 算**单回合精确解**当分母：树太小、局面确定，用 DP 而不是 MCTS。

对比指标是**回合终局价值**，不是「它自己觉得好不好」：

    V = 有效伤害 + w_block * 有效格挡 + w_kill * 击杀折价 - 0.1 * 消耗能量

    有效伤害 = min(这一下能打出的伤害, 目标剩余血)，溢出算浪费
    有效格挡 = min(累计格挡, 还活着的敌人的总攻击)
    击杀折价 = 被杀掉的敌人「这回合原本能打我们多少」（不写这项，会得出
               「留 1 血不杀、把能量拿去套盾」更优的错误结论）

用法：

    # 合成局面扫一遍（没有决策级日志时用这个）
    python games/backpack-hero-aipeiwo/agent/ab_combat.py --mode synthetic --cases 300

    # 拿真实快照跑（要求 state.json 处于战斗中；统计值用 --stats）
    python games/backpack-hero-aipeiwo/agent/ab_combat.py --mode snapshot ^
        --state games/backpack-hero-aipeiwo/runtime/state.json

注意：合成局面只是「机制探针」，它的数值不构成对 BPH 平衡性的任何主张。
真实日志 `data/state-log/*.jsonl` 是**探针快照**（enemies/items 是计数，legal 只有类型名），
不含费用/血量，所以没法直接做决策级 A/B —— 这是本次要一起报的结论之一。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import policy  # noqa: E402

DEFAULT_OUT = HERE.parent / "data" / "ab-combat"


# ---------------------------------------------------------------- 物品与局面


@dataclass(frozen=True)
class Item:
    """合成局面里的一件物品。真实快照里这些值来自账本/文本解析。"""

    uid: str
    name: str
    cost: int
    damage: float = 0.0
    block: float = 0.0
    empower: float = 0.0      # 本回合后续攻击 +N 伤害
    max_uses: int = 9


@dataclass
class State:
    hp: int
    energy: int
    incoming: int                          # 存活敌人的攻击力之和（击杀后会下调）
    enemies: list[int]                    # 每只敌人的剩余血量（按 uid 顺序）
    enemy_atk: list[int] = field(default_factory=list)   # 每只敌人的攻击力（原值，用于击杀折价）
    block: float = 0.0
    empower: float = 0.0
    dmg_eff: float = 0.0
    overkill: float = 0.0
    energy_used: int = 0
    kills: int = 0
    killed_atk: float = 0.0               # 这回合杀掉的敌人「本来还能打我们多少」
    uses: dict[str, int] = field(default_factory=dict)
    plan: list[str] = field(default_factory=list)

    def copy(self) -> "State":
        return State(
            hp=self.hp, energy=self.energy, incoming=self.incoming,
            enemies=list(self.enemies), enemy_atk=list(self.enemy_atk),
            block=self.block, empower=self.empower,
            dmg_eff=self.dmg_eff, overkill=self.overkill,
            energy_used=self.energy_used, kills=self.kills, killed_atk=self.killed_atk,
            uses=dict(self.uses), plan=list(self.plan),
        )


def _living(st: State) -> list[int]:
    return [i for i, hp in enumerate(st.enemies) if hp > 0]


def apply_item(st: State, it: Item, items: dict[str, Item]) -> State:
    """在副本上执行一次使用。**不判断合法性**，调用方保证 cost <= energy。"""
    nxt = st.copy()
    nxt.energy -= it.cost
    nxt.energy_used += it.cost
    nxt.uses[it.uid] = nxt.uses.get(it.uid, 0) + 1
    nxt.plan.append(it.uid)

    if it.block:
        nxt.block += it.block
    if it.empower:
        nxt.empower += it.empower
    if it.damage:
        hit = it.damage + nxt.empower
        alive = _living(nxt)
        if not alive:
            nxt.overkill += hit                  # 已经没有目标了，纯浪费
        else:
            # 游戏自己决定打谁（`legal` 里没有 target 字段）→ 这里统一「打血最少的」
            tgt = min(alive, key=lambda i: (nxt.enemies[i], i))
            dealt = min(hit, float(nxt.enemies[tgt]))
            nxt.enemies[tgt] = int(nxt.enemies[tgt] - dealt)
            nxt.dmg_eff += dealt
            nxt.overkill += hit - dealt
            if nxt.enemies[tgt] <= 0:
                # 杀掉它 = 它这回合原本要打我们的那一份没了。这是贪心估值里完全缺失的项。
                nxt.kills += 1
                atk = float(nxt.enemy_atk[tgt]) if tgt < len(nxt.enemy_atk) else 0.0
                nxt.killed_atk += atk
                nxt.incoming = max(0, int(nxt.incoming - atk))
    return nxt


def effective_incoming(st: State) -> float:
    """敌人全死了就没有来袭伤害了——战斗在最后一个敌人倒下时就结束。

    这条不写对，会高估「边打边挡」：明明这回合能清场，还去算格挡的价值。
    """
    return float(st.incoming) if any(hp > 0 for hp in st.enemies) else 0.0


def terminal_value(st: State, w_block: float, w_kill: float = 1.0) -> float:
    """回合终局价值。

    w_kill 是「杀掉一只敌人」折多少分。凑合着用「它这回合原本的攻击力」当代理：
    杀掉它至少省下一整轮，而敌人伤害是随回合涨的，所以这还是个保守估计。
    """
    return (st.dmg_eff + w_block * min(st.block, effective_incoming(st))
            + w_kill * st.killed_atk - policy.ENERGY_WEIGHT * st.energy_used)


def affordable(st: State, items: list[Item]) -> list[Item]:
    return [it for it in items
            if it.cost <= st.energy and st.uses.get(it.uid, 0) < it.max_uses]


# ---------------------------------------------------------------- 三种决策过程


def _as_snapshot(st: State, items: list[Item], incoming: int | None = None) -> dict[str, Any]:
    """把模拟状态投影成 `policy.py` 认得的快照形状（只填它读的字段）。

    每只敌人只挂自己的攻击力；`incoming` 传了值就按比例分摊（用于「已经挡掉一部分」的场景），
    保证 `incoming_damage()` 加起来正好等于想要的数。
    """
    alive = [i for i, hp in enumerate(st.enemies) if hp > 0]
    atk = {i: (st.enemy_atk[i] if i < len(st.enemy_atk) else 0) for i in alive}
    if incoming is not None:
        total = sum(atk.values()) or 1
        scaled: dict[int, int] = {}
        left = incoming
        for i in alive[:-1]:
            part = int(round(incoming * atk[i] / total))
            part = max(0, min(part, left))
            scaled[i] = part
            left -= part
        if alive:
            scaled[alive[-1]] = max(0, left)
        atk = scaled

    enemies = []
    for i, hp in enumerate(st.enemies):
        power = atk.get(i, 0)
        attacks = [{"type": 0, "power": power}] if power > 0 and hp > 0 else []
        enemies.append({"uid": f"e{i}", "hp": hp, "attacks": attacks})
    return {
        "player": {"hp": st.hp, "energy": st.energy},
        "enemies": enemies,
        "backpack": {"items": [{"uid": it.uid, "name": it.name, "energy_cost": it.cost}
                               for it in items]},
        "legal": [{"type": "use_item", "uid": it.uid, "energy": it.cost}
                  for it in affordable(st, items)] + [{"type": "end_turn"}],
    }


class _TableLedger:
    """用固定表冒充账本：让三种策略用**完全一样**的估值。"""

    def __init__(self, items: list[Item]) -> None:
        self.table = {it.name: it for it in items}

    def estimate_damage(self, name: str) -> float | None:
        it = self.table.get((name or "").strip())
        return None if it is None else it.damage

    def estimate_block(self, name: str) -> float:
        it = self.table.get((name or "").strip())
        return 0.0 if it is None else it.block


def greedy(st: State, items: list[Item], aware: bool) -> State:
    """复刻 `policy.choose` 的主循环；aware=True 时把已挡掉的伤害从 incoming 扣掉。"""
    ledger = _TableLedger(items)
    cur = st.copy()
    seen: set[str] = set()
    for _ in range(32):
        if not any(hp > 0 for hp in cur.enemies):
            break
        remaining = max(0, int(effective_incoming(cur)) - int(cur.block)) if aware else None
        snap = _as_snapshot(cur, items, incoming=remaining)
        action, _why = policy.choose(snap, ledger, seen)  # type: ignore[arg-type]
        if action is None or action.get("type") != "use_item":
            break
        it = next((x for x in items if x.uid == action.get("uid")), None)
        if it is None:
            break
        if it.cost > cur.energy or cur.uses.get(it.uid, 0) >= it.max_uses:
            seen.add(it.name)
            continue
        cur = apply_item(cur, it, {x.uid: x for x in items})
    return cur


def greedy_ratio(st: State, items: list[Item], aware: bool) -> State:
    """同估值，但按「每点能量的分数」选，而不是绝对分数。

    绝对分数会偏爱大件（2 费 14 伤 > 1 费 7 伤），可能量只有 3 点，
    买一件大件常常把余量卡死。除以费用就是把这个换算补上。
    """
    ledger = _TableLedger(items)
    cur = st.copy()
    seen: set[str] = set()
    by_uid = {it.uid: it for it in items}
    for _ in range(32):
        if not any(hp > 0 for hp in cur.enemies):
            break
        remaining = max(0, int(effective_incoming(cur)) - int(cur.block)) if aware else None
        snap = _as_snapshot(cur, items, incoming=remaining)
        best: dict[str, Any] | None = None
        best_s = float("-inf")
        for a in snap["legal"]:
            if a.get("type") != "use_item":
                continue
            s, _ = policy.score_action(a, snap, ledger, seen)  # type: ignore[arg-type]
            s /= max(1, int(a.get("energy", 0)))
            if s > best_s:
                best_s, best = s, a
        if best is None or best_s <= 0:
            break
        it = by_uid.get(best.get("uid", ""))
        if it is None or it.cost > cur.energy or cur.uses.get(it.uid, 0) >= it.max_uses:
            break
        cur = apply_item(cur, it, by_uid)
    return cur


def enumerate_turn(st: State, items: list[Item], w_block: float, w_kill: float = 1.0) -> State:
    """单回合精确解（DP）。状态够小，直接记忆化，不需要 MCTS。"""
    by_uid = {it.uid: it for it in items}
    memo: dict[tuple, tuple[float, State]] = {}

    def key(s: State) -> tuple:
        return (s.energy, tuple(s.enemies), round(s.block, 4), round(s.empower, 4),
                tuple(sorted(s.uses.items())))

    def best(s: State) -> tuple[float, State]:
        k = key(s)
        if k in memo:
            return memo[k]
        value, pick = terminal_value(s, w_block, w_kill), s
        for it in affordable(s, items):
            val, end = best(apply_item(s, it, by_uid))
            if val > value + 1e-9:
                value, pick = val, end
        memo[k] = (value, pick)
        return memo[k]

    return best(st.copy())[1]


# ---------------------------------------------------------------- 合成局面


# 费用曲线是一个**假设**，不是事实——所以做成参数，扫给你们看敏感性。
# 现实里 BPH 的 1 费武器大约 7~9 伤害（Wooden Sword 7、Brutal Spear 7、Heavy Blade 8、
# Tai Chi Ruler 9），高费武器是否线性成长没有实测数据。
#
#   linear = 伤害与费用成正比（1 费 7、2 费 14、3 费 21）→ 每点能量产出持平
#   steep  = 边际递减（1 费 6~9、2 费 11~15、3 费 15~21）→ 便宜货更划算
#   flat   = 不看费用（都是 7）→ 0 费/低费直接碾压
CURVES = {
    "linear": {"attack": (0, 3, 7, 14, 21), "block": (0, 3, 5, 10, 15)},
    "steep": {"attack": (0, 3, 7, 12, 17), "block": (0, 3, 6, 10, 14)},
    "flat": {"attack": (0, 7, 7, 7, 7), "block": (0, 5, 5, 5, 5)},
}

# 三种组合：plain=纯攻防；blocky=盾多、来袭低（专门压格挡饱和）；
# mixed=掺 5% 的「本回合增伤」，专门压顺序依赖。
MIXES = {
    "plain": {"attack": 0.65, "block": 0.35, "empower": 0.0},
    "blocky": {"attack": 0.35, "block": 0.65, "empower": 0.0},
    "mixed": {"attack": 0.60, "block": 0.35, "empower": 0.05},
}


def _roll_cost(rng: random.Random, weights=(0.12, 0.55, 0.30, 0.03)) -> int:
    total = sum(weights)
    r = rng.random() * total
    for cost, w in enumerate(weights):
        r -= w
        if r <= 0:
            return cost
    return 1


def synth_case(rng: random.Random, n_items: int, n_enemies: int, mix: str,
               curve: str = "linear") -> tuple[State, list[Item]]:
    weights = MIXES[mix]
    atk = CURVES[curve]["attack"]
    blk = CURVES[curve]["block"]
    items: list[Item] = []
    for i in range(n_items):
        r = rng.random()
        kind = "attack" if r < weights["attack"] else (
            "block" if r < weights["attack"] + weights["block"] else "empower")
        cost = _roll_cost(rng) if kind != "empower" else 1
        max_uses = 1 if cost == 0 else 3      # 0 费物品当成「一次性消耗品」
        if kind == "attack":
            dmg = max(1, atk[cost] + rng.randint(-1, 2))
            items.append(Item(f"i{i}", f"ATK{cost}_{i}", cost, damage=float(dmg),
                              max_uses=max_uses))
        elif kind == "block":
            b = max(1, blk[cost] + rng.randint(-1, 2))
            items.append(Item(f"i{i}", f"BLK{cost}_{i}", cost, block=float(b),
                              max_uses=max_uses))
        else:
            items.append(Item(f"i{i}", f"EMP_{i}", cost, empower=float(rng.randint(1, 3)),
                              max_uses=max_uses))

    enemy_hp = [rng.randint(8, 12) + 4 * (n_enemies - 1) for _ in range(n_enemies)]
    enemy_atk = [rng.randint(3, 7) for _ in range(n_enemies)]
    incoming = sum(enemy_atk)
    hp = rng.randint(max(6, incoming), 40)
    return State(hp=hp, energy=3, incoming=incoming, enemies=enemy_hp,
                 enemy_atk=enemy_atk), items


# ---------------------------------------------------------------- 真实快照


def case_from_snapshot(doc: dict[str, Any], stats: dict[str, dict[str, float]] | None) -> tuple[State, list[Item]]:
    """从 `runtime/state.json` 造一个局面。

    真实快照里物品的伤害/格挡**没有字段**（只有 text），所以必须外部提供 `--stats`：
        {"WOODEN SWORD": {"damage": 7, "block": 0}, ...}
    """
    stats = stats or {}
    items: list[Item] = []
    for it in (doc.get("backpack") or {}).get("items", []):
        name = (it.get("name") or "").strip()
        s = stats.get(name)
        if not s:
            continue
        items.append(Item(it.get("uid", name), name, int(it.get("energy_cost", 0)),
                          damage=float(s.get("damage", 0)), block=float(s.get("block", 0)),
                          empower=float(s.get("empower", 0))))
    enemies = [int(e.get("hp", 0)) for e in doc.get("enemies", []) if int(e.get("hp", 0)) > 0]
    enemy_atk = []
    for e in doc.get("enemies", []):
        if int(e.get("hp", 0)) <= 0:
            continue
        dmg = 0
        for a in e.get("attacks") or []:
            if a.get("type") == 0 and not a.get("hidden"):
                dmg += int(a.get("power") or 0)
        enemy_atk.append(dmg)
    player = doc.get("player") or {}
    return (State(hp=int(player.get("hp", 0)), energy=int(player.get("energy", 0)),
                  incoming=sum(enemy_atk), enemies=enemies, enemy_atk=enemy_atk), items)


# ---------------------------------------------------------------- 跑一组


# 四个候选：前两个是「现状」，后两个是「两处 3 行改动」。名字前缀用来对齐表格列。
POLICIES: tuple[tuple[str, Callable[[State, list[Item]], State]], ...] = (
    ("greedy", lambda st, items: greedy(st, items, aware=False)),
    ("greedy_ratio", lambda st, items: greedy_ratio(st, items, aware=False)),
    ("greedy_aware", lambda st, items: greedy(st, items, aware=True)),
    ("greedy_ratio_aware", lambda st, items: greedy_ratio(st, items, aware=True)),
)


def run_case(case: tuple[State, list[Item]], w_block: float, w_kill: float = 1.0) -> dict[str, Any]:
    st, items = case
    out: dict[str, Any] = {"incoming": st.incoming, "energy": st.energy, "n_items": len(items),
                           "enemy_hp": list(st.enemies), "enemy_atk": list(st.enemy_atk),
                           "items": [{"n": it.name, "c": it.cost, "d": it.damage,
                                      "b": it.block, "e": it.empower} for it in items]}
    if not items:
        out["skipped"] = "no items"
        return out

    opt = enumerate_turn(st, items, w_block, w_kill)
    v_opt = terminal_value(opt, w_block, w_kill)
    for name, fn in POLICIES:
        end = fn(st, items)
        v = terminal_value(end, w_block, w_kill)
        out[name] = {
            "value": round(v, 3),
            "dmg_eff": round(end.dmg_eff, 3),
            "overkill": round(end.overkill, 3),
            "block_eff": round(min(end.block, effective_incoming(end)), 3),
            "block_waste": round(max(0.0, end.block - effective_incoming(end)), 3),
            "energy_left": end.energy,
            "kills": end.kills,
            "optimal": abs(v - v_opt) < 1e-6,
            "plan": end.plan,
        }
    out["enumerate"] = {
        "value": round(v_opt, 3),
        "dmg_eff": round(opt.dmg_eff, 3),
        "overkill": round(opt.overkill, 3),
        "block_eff": round(min(opt.block, effective_incoming(opt)), 3),
        "block_waste": round(max(0.0, opt.block - effective_incoming(opt)), 3),
        "energy_left": opt.energy,
        "kills": opt.kills,
        "optimal": True,
        "plan": opt.plan,
    }
    return out


def summarize(rows: list[dict[str, Any]], label: str, w_block: float) -> dict[str, Any]:
    ok = [r for r in rows if "greedy" in r]
    if not ok:
        return {"label": label, "cases": 0}
    opt_mean = sum(r["enumerate"]["value"] for r in ok) / len(ok)
    res: dict[str, Any] = {"label": label, "cases": len(ok), "enumerate_value": round(opt_mean, 3)}
    for name, _fn in POLICIES:
        vals = [r[name]["value"] for r in ok]
        gap = [r["enumerate"]["value"] - v for r, v in zip(ok, vals)]
        res[name] = {
            "value": round(sum(vals) / len(vals), 3),
            "gap_pct": round(100.0 * sum(gap) / sum(r["enumerate"]["value"] for r in ok), 2),
            "optimal_share": round(100.0 * sum(1 for r in ok if r[name]["optimal"]) / len(ok), 1),
            "worst_gap": round(max(gap), 2),
            "energy_left": round(sum(r[name]["energy_left"] for r in ok) / len(ok), 2),
            "block_waste": round(sum(r[name]["block_waste"] for r in ok) / len(ok), 2),
            # 一回合下来一点伤害都没打出去（敌人还活着）——这是最刺眼的失败形态
            "zero_dmg_share": round(100.0 * sum(
                1 for r in ok if r[name]["dmg_eff"] == 0 and sum(r["enemy_hp"]) > 0) / len(ok), 1),
        }
    return res


def print_table(summaries: list[dict[str, Any]]) -> None:
    cols = [("g", "greedy"), ("r", "greedy_ratio"), ("a", "greedy_aware"), ("ra", "greedy_ratio_aware")]
    head = f"{'config':24} {'n':>4} {'opt':>6} | " + " | ".join(f"{c[0]:>4}gap {c[0]:>4}0dm" for c in cols)
    print(head)
    print("-" * len(head))
    for s in summaries:
        if not s.get("cases"):
            print(f"{s['label']:24} {s.get('cases', 0):>4}  (skipped)")
            continue
        cells = " | ".join(f"{s[k]['gap_pct']:>7.1f} {s[k]['zero_dmg_share']:>6.1f}" for _c, k in cols)
        print(f"{s['label']:24} {s['cases']:>4} {s['enumerate_value']:>6.2f} | {cells}")


def _sample_cases(rows: list[dict[str, Any]], per_config: int = 8) -> list[dict[str, Any]]:
    """落盘只留样：每个 config 取 gap 最大的几条 + 前两条，免得 JSON 失控。"""
    by_cfg: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if "greedy" in r:
            by_cfg.setdefault(r.get("config", "?"), []).append(r)
    keep: list[dict[str, Any]] = []
    for cfg, rs in by_cfg.items():
        rs = sorted(rs, key=lambda r: r["enumerate"]["value"] - r["greedy_aware"]["value"],
                    reverse=True)
        keep.extend(rs[:per_config])
        keep.extend(rs[-1:])
    return keep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=("synthetic", "snapshot"), default="synthetic")
    ap.add_argument("--cases", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--items", type=str, default="3,5,8,10", help="合成局面：物品件数列表")
    ap.add_argument("--enemies", type=str, default="1,2", help="合成局面：敌人数量列表")
    ap.add_argument("--mixes", type=str, default="plain,blocky,mixed")
    ap.add_argument("--curves", type=str, default="linear,steep", help="费用曲线假设：linear/steep/flat")
    ap.add_argument("--w-block", type=float, default=0.8, help="有效格挡的权重")
    ap.add_argument("--w-kill", type=float, default=1.0,
                    help="击杀折价：杀一只敌人算它「原本这一下」的多少倍（0=关闭）")
    ap.add_argument("--state", type=str, default=str(HERE.parent / "runtime" / "state.json"))
    ap.add_argument("--stats", type=str, default=None, help="真实快照的物品数值表（JSON）")
    ap.add_argument("--only-attack", action="store_true", help="敏感性：把盾全去掉")
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args(argv)

    rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    if args.mode == "synthetic":
        for curve in [c for c in args.curves.split(",") if c]:
            for mix in [m for m in args.mixes.split(",") if m]:
                for n_items in [int(x) for x in args.items.split(",")]:
                    for n_enemies in [int(x) for x in args.enemies.split(",")]:
                        rng = random.Random(f"{args.seed}-{curve}-{mix}-{n_items}-{n_enemies}")
                        cases = []
                        for _ in range(args.cases):
                            case = synth_case(rng, n_items, n_enemies, mix, curve)
                            if args.only_attack:
                                case = (case[0], [it for it in case[1] if it.damage > 0])
                            cases.append(case)
                        out_rows = [run_case(c, args.w_block, args.w_kill) for c in cases]
                        label = f"{curve}/{mix}/i{n_items}/e{n_enemies}"
                        for r in out_rows:
                            r["config"] = label
                        rows.extend(out_rows)
                        summaries.append(summarize(out_rows, label, args.w_block))
    else:
        doc = json.loads(Path(args.state).read_text(encoding="utf-8-sig"))
        stats = json.loads(Path(args.stats).read_text(encoding="utf-8-sig")) if args.stats else None
        case = case_from_snapshot(doc, stats)
        if not case[1]:
            print(f"快照里没有可用物品（state={args.state}）；真实模式需要 --stats 提供数值")
            return 2
        row = run_case(case, args.w_block, args.w_kill)
        rows.append(row)
        summaries.append(summarize([row], f"snapshot/{Path(args.state).name}", args.w_block))

    print_table(summaries)
    print()

    # 全场合计：把每个 case 当同权样本，看整体差多少
    scored = [s for s in summaries if s.get("cases")]
    if scored:
        total = sum(s["cases"] for s in scored)
        for name, _fn in POLICIES:
            gap = sum(s[name]["gap_pct"] * s["cases"] for s in scored) / total
            zero = sum(s[name]["zero_dmg_share"] * s["cases"] for s in scored) / total
            best = sum(s[name]["optimal_share"] * s["cases"] for s in scored) / total
            print(f"合计 {name:19} gap={gap:5.1f}%  0dmg={zero:5.1f}%  直接最优={best:5.1f}%  (n={total})")

    print()
    print(f"V = 有效伤害 + {args.w_block} × 有效格挡 + {args.w_kill} × 击杀折价 - 0.1 × 消耗能量；"
          f"gap% = 相对精确解的缺口；0dmg% = 一回合零伤害的局面占比；best% = 直接达到最优的局面占比")

    out_dir = Path(args.out) if args.out else DEFAULT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"ab-{stamp}.json"
    path.write_text(json.dumps({"args": vars(args), "summaries": summaries,
                                "cases": _sample_cases(rows)}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"\n写出：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
