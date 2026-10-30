"""M4 的 A/B 汇总：每局掉血 / 最深楼层 / 等级，按臂聚合。

    python tools\\ab_m4.py data\\runs\\ab                    # 读 arm-*/run*.jsonl
    python tools\\ab_m4.py data\\runs\\ab --json out.json     # 同时给机器读
    python tools\\ab_m4.py one.jsonl --verbose               # 单局明细（每场伤害）

**指标定义**（对齐 `docs/PLAN-item-census.md` §4 的 M4）：

- **掉血/场** = 本局掉的总血量 / 本局赢下的战斗数。
  掉血只算 `act` 里玩家 HP 的负 delta；`use_item` / `end_turn` 才算战斗内，
  其余动作（`finish_reorganize`、事件）算「非战斗掉血」单独列一列 ——
  整理阶段的毒/诅咒每轮 -3~-8，混进战斗会污染结论。
- **回血** = 战斗内玩家 HP 的正 delta 之和（`--sustain` 真正生效的证据）。
- **战斗数** = `after_battle` 动作的次数（每次胜利一次，E24）。
- **最深楼层 / 等级** = 局终 `end`（或 `dead`）事件里的 `run.floor` / `run.level`。
- **零伤回合** = 一个回合里没对敌人造成伤害的回合数 / 总回合数（按 `end_turn` 切段）。

只读日志，不碰游戏。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

BATTLE_ACTIONS = {"use_item", "end_turn"}


def _enemy_damage(delta: dict[str, Any], pre_hp: dict[str, int] | None) -> int:
    total = 0
    for uid, v in (delta.get("enemies") or {}).items():
        if isinstance(v, int):
            if v < 0:
                total += -v
        elif v == "gone" and pre_hp:
            total += pre_hp.get(uid, 0)
    return total


def read_run(path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    out: dict[str, Any] = {
        "file": path.name,
        "arm": "unknown",
        "steps": None,
        "floor": None,
        "level": None,
        "gold": None,
        "hp": None,
        "max_hp": None,
        "died": False,
        "battles": 0,
        "hp_lost_battle": 0,
        "hp_lost_other": 0,
        "heal_battle": 0,
        "turns": 0,
        "clean_turns": 0,
        "actions": Counter(),
        "per_battle": [],      # 每场战斗掉了多少血（按 after_battle 收口）
        "battle_loss": 0,      # 当前这场已经在战斗中掉了多少
    }

    turn_damage = 0
    in_battle = False

    for o in rows:
        ev = o.get("event")
        if ev == "start":
            out["arm"] = o.get("flags") or "unknown"
        elif ev == "act":
            a = o.get("action") or {}
            t = a.get("type") or "?"
            out["actions"][t] += 1
            delta = o.get("delta") or {}
            php = delta.get("player_hp")
            if isinstance(php, int) and php != 0:
                if t in BATTLE_ACTIONS:
                    in_battle = True
                    if php < 0:
                        out["hp_lost_battle"] += -php
                        out["battle_loss"] += -php
                    else:
                        out["heal_battle"] += php
                else:
                    if php < 0:
                        out["hp_lost_other"] += -php
                    else:
                        out["heal_battle"] += php
            if t in BATTLE_ACTIONS:
                turn_damage += _enemy_damage(delta, None)
            if t == "end_turn":
                out["turns"] += 1
                if turn_damage == 0:
                    out["clean_turns"] += 1
                turn_damage = 0
            elif t == "after_battle":
                # 胜利结算可能第一次 ok=False、第二次 ok=True（E24），这里不算 ok，
                # 因为一个房间只结算一次；重复计数会把「场数」和「掉血/场」都拉偏。
                out["battles"] += 1
                out["per_battle"].append(out["battle_loss"])
                out["battle_loss"] = 0
                in_battle = False
        elif ev in ("dead", "end"):
            if ev == "dead":
                out["died"] = True
                # 最后一场没打赢（也是掉血最多的那场）：照样算一场
                if out["battle_loss"]:
                    out["battles"] += 1
                    out["per_battle"].append(out["battle_loss"])
                    out["battle_loss"] = 0
                    in_battle = False
            for k in ("steps", "floor", "level", "gold", "hp", "max_hp"):
                if o.get(k) is not None:
                    out[k] = o[k]
            if o.get("max_floor") is not None:
                out["floor"] = max(out["floor"] or 0, o["max_floor"])

    if turn_damage:
        out["turns"] += 1
    # 最后一场打赢了但没来得及收口（或死在最后一场）-> 单独补一场
    if out["battle_loss"] and not in_battle:
        out["battles"] += 1
        out["per_battle"].append(out["battle_loss"])

    lost = out["hp_lost_battle"] + out["hp_lost_other"]
    out["hp_lost_total"] = lost
    out["per_battle_avg"] = (lost / out["battles"]) if out["battles"] else None
    # 致命那一场往往是 20-35 血的异常值，会把「每场掉血」整体拉高；
    # 和 PLAN 里的「每场掉血」并排给一个去掉致命场的版本，方便判断是不是真的变差。
    nonfatal = out["per_battle"][:-1] if (out["died"] and out["per_battle"]) else out["per_battle"]
    out["per_battle_nonfatal_avg"] = (
        (sum(nonfatal) + out["hp_lost_other"]) / len(nonfatal)) if nonfatal else None
    out["zero_dmg_ratio"] = (out["clean_turns"] / out["turns"]) if out["turns"] else None
    out["actions"] = dict(out["actions"])
    out["per_battle"] = [x for x in out["per_battle"] if x is not None]
    return out


def _mean(xs: list[float]) -> str:
    xs = [x for x in xs if x is not None]
    return f"{statistics.mean(xs):.1f}" if xs else "-"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="M4 A/B summary (hp lost per battle / depth / level)")
    ap.add_argument("target", help="目录（读 arm-*/run*.jsonl）或单个 jsonl")
    ap.add_argument("--json", dest="json_out", default="", help="把结果也写一份 JSON")
    ap.add_argument("--verbose", action="store_true", help="单局明细：每场战斗掉血")
    args = ap.parse_args(argv)

    t = Path(args.target)
    files = [t] if t.is_file() else sorted(p for p in t.glob("arm-*/run*.jsonl"))
    if not files:
        print(f"no logs under {t}")
        return 1

    runs = [read_run(p) for p in files]

    print(f"{'file':10} {'arm':24} {'层':>3} {'等级':>4} {'血':>3} {'场':>3} "
          f"{'掉血/场':>7} {'非致命/场':>9} {'战斗掉血':>8} {'非战斗':>6} {'回血':>4} {'步':>4} {'零伤':>5}")
    print("-" * 114)
    for r in runs:
        pb = "-" if r["per_battle_avg"] is None else f"{r['per_battle_avg']:.1f}"
        nf = "-" if r.get("per_battle_nonfatal_avg") is None else f"{r['per_battle_nonfatal_avg']:.1f}"
        z = "-" if r["zero_dmg_ratio"] is None else f"{r['zero_dmg_ratio'] * 100:.0f}%"
        print(f"{r['file'][:10]:10} {r['arm'][:24]:24} {str(r['floor']):>3} {str(r['level']):>4} "
              f"{str(r['hp']):>3} {r['battles']:>3} {pb:>7} {nf:>9} {r['hp_lost_battle']:>8} "
              f"{r['hp_lost_other']:>6} {r['heal_battle']:>4} {str(r['steps']):>4} {z:>5}")

    by_arm: dict[str, list[dict[str, Any]]] = {}
    for r in runs:
        by_arm.setdefault(r["arm"], []).append(r)

    print("\n=== 按臂聚合 ===")
    print(f"{'arm':24} {'n':>2} {'层数(均)':>8} {'最好层':>6} {'等级(均)':>8} "
          f"{'掉血/场':>7} {'非致命/场':>9} {'战斗掉血/局':>10} {'非战斗/局':>9} {'回血/局':>7} {'零伤':>5} {'阵亡':>5}")
    print("-" * 124)
    summary: dict[str, Any] = {}
    for arm, rs in by_arm.items():
        floors = [r["floor"] for r in rs if r["floor"] is not None]
        levels = [r["level"] for r in rs if r["level"] is not None]
        pbs = [r["per_battle_avg"] for r in rs if r["per_battle_avg"] is not None]
        nfs = [r["per_battle_nonfatal_avg"] for r in rs if r.get("per_battle_nonfatal_avg") is not None]
        zs = [r["zero_dmg_ratio"] for r in rs if r["zero_dmg_ratio"] is not None]
        deaths = sum(1 for r in rs if r["died"])
        summary[arm] = {
            "n": len(rs),
            "floor_mean": statistics.mean(floors) if floors else None,
            "floor_best": max(floors) if floors else None,
            "level_mean": statistics.mean(levels) if levels else None,
            "per_battle_mean": statistics.mean(pbs) if pbs else None,
            "per_battle_nonfatal_mean": statistics.mean(nfs) if nfs else None,
            "hp_lost_battle_mean": statistics.mean([r["hp_lost_battle"] for r in rs]),
            "hp_lost_other_mean": statistics.mean([r["hp_lost_other"] for r in rs]),
            "heal_battle_mean": statistics.mean([r["heal_battle"] for r in rs]),
            "zero_dmg_mean": statistics.mean(zs) if zs else None,
            "deaths": deaths,
            "battles_mean": statistics.mean([r["battles"] for r in rs]),
        }
        s = summary[arm]
        print(f"{arm[:24]:24} {s['n']:>2} {_mean(floors):>8} {str(s['floor_best']):>6} "
              f"{_mean(levels):>8} {_mean(pbs):>7} {_mean(nfs):>9} {s['hp_lost_battle_mean']:>10.1f} "
              f"{s['hp_lost_other_mean']:>9.1f} {s['heal_battle_mean']:>7.1f} "
              f"{'-' if s['zero_dmg_mean'] is None else format(s['zero_dmg_mean'] * 100, '.0f') + '%':>5} "
              f"{s['deaths']}/{s['n']:>1}")

    if args.verbose:
        for r in runs:
            print(f"\n--- {r['file']} ({r['arm']}) 每场掉血: {r['per_battle']}")
            print(f"    动作: {dict(sorted(r['actions'].items(), key=lambda kv: -kv[1]))}")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"runs": runs, "by_arm": summary}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"\njson -> {args.json_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
