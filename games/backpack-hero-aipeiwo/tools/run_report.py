"""把 `loop.py` 的日志汇总成一局的成绩单，并跨局/跨臂聚合。

    python games\\backpack-hero-aipeiwo\\tools\\run_report.py                 # data/runs 全部
    python ... --dir data\\runs\\ab\\arm-b                                        # 只看一臂
    python ... data\\runs\\loop-20261027-211255.jsonl                             # 只看一局
    python ... --json out.json                                               # 也给机器读

三个指标是用户要的那三个：

- **存活层数** = 这局到过的最深 `run.floor`（越深越好）
- **剩余血量** = 结束时的 `player.hp`（死的时候是 0 或负；也在 0..max 之间看掉了多少）
- **零伤害回合占比** = 「一个回合里没对任何敌人造成伤害」的回合数 / 总回合数

回合怎么切：日志里没有回合号，但每个回合都以一条 `end_turn` 动作收尾，
所以按 `end_turn` 切段最稳，也不依赖 `turnNumber` 跨战斗重置的行为。

只读日志，不碰游戏。旧日志（没有 `start` 事件）也能读，臂名记成 `unknown`。
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEFAULT_DIR = HERE.parent / "data" / "runs"


def _damage_from(delta: dict[str, Any], pre_hp: dict[str, int] | None = None) -> int:
    total = 0
    for uid, v in (delta.get("enemies") or {}).items():
        if isinstance(v, int):
            if v < 0:
                total += -v
        elif v == "gone" and pre_hp:
            total += pre_hp.get(uid, 0)
    return total


def _item_name(state: dict[str, Any] | None, action: dict[str, Any]) -> str:
    if not state:
        return "?"
    for it in (state.get("backpack") or {}).get("items", []):
        if it.get("uid") == action.get("uid"):
            return (it.get("name") or "?").strip()
    return "?"


def read_run(path: Path) -> dict[str, Any]:
    rows = []
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
        "ledger": None,
        "floor": None,
        "hp": None,
        "max_hp": None,
        "died": False,
        "steps": None,
        "turns": 0,
        "clean_turns": 0,
        "items": Counter(),
        "battle_decisions": 0,
    }

    last_state = None
    pre_hp: dict[str, int] = {}
    turn_damage = 0

    for o in rows:
        ev = o.get("event")
        if ev == "start":
            out["arm"] = o.get("flags") or "unknown"
            out["ledger"] = o.get("ledger")
            out["ledger_items"] = o.get("ledger_items")
        elif ev == "decision":
            last_state = o.get("state")
            out["battle_decisions"] += 1
            st = last_state or {}
            fl = (st.get("run") or {}).get("floor")
            if isinstance(fl, int) and fl >= 0:
                out["floor"] = fl if out["floor"] is None else max(out["floor"], fl)
            pre_hp = {e["uid"]: e.get("hp", 0) for e in st.get("enemies") or []}
        elif ev == "act":
            a = o.get("action") or {}
            if a.get("type") == "use_item":
                dmg = _damage_from(o.get("delta") or {}, pre_hp)
                turn_damage += dmg
                out["items"][_item_name(last_state, a)] += 1
            elif a.get("type") == "end_turn":
                out["turns"] += 1
                if turn_damage == 0:
                    out["clean_turns"] += 1
                turn_damage = 0
        elif ev == "dead":
            out["died"] = True
            out["hp"] = o.get("hp", 0)
            out["floor"] = o.get("floor", out["floor"])
        elif ev == "end":
            out["steps"] = o.get("steps")
            if out["hp"] is None:
                out["hp"] = o.get("hp")
            if out["floor"] is None:
                out["floor"] = o.get("floor")
            if o.get("max_floor") is not None:
                out["floor"] = max(out["floor"] or 0, o["max_floor"])

    # a battle can end without a closing end_turn (last enemy killed) -> flush it
    if turn_damage or (out["turns"] == 0 and out["items"]):
        out["turns"] += 1
        if turn_damage == 0:
            out["clean_turns"] += 1

    out["zero_dmg_ratio"] = (out["clean_turns"] / out["turns"]) if out["turns"] else None
    out["items"] = dict(out["items"])
    return out


def fmt_run(r: dict[str, Any]) -> str:
    hp = "?" if r["hp"] is None else str(r["hp"])
    floor = "?" if r["floor"] is None else str(r["floor"])
    zdr = "-" if r["zero_dmg_ratio"] is None else f"{100 * r['zero_dmg_ratio']:.0f}%"
    flag = "DEAD" if r["died"] else "alive/stop"
    top = sorted(r["items"].items(), key=lambda kv: -kv[1])[:4]
    used = ", ".join(f"{n}×{c}" for n, c in top) or "-"
    return (f"{r['file']:28} {r['arm']:32} floor={floor:>2} hp={hp:>3} "
            f"turns={r['turns']:>2} zero={zdr:>4} {flag:9} {used}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="summarise loop.py run logs")
    ap.add_argument("paths", nargs="*", help="日志文件；省略则用 --dir")
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help="日志目录（默认 data/runs）")
    ap.add_argument("--glob", default="loop-*.jsonl", help="目录内匹配（默认 loop-*.jsonl）")
    ap.add_argument("--json", help="把结果也写到这个文件")
    ap.add_argument("--full-items", action="store_true", help="列出每局用过的所有物品")
    args = ap.parse_args(argv)

    if args.paths:
        files = [Path(p) for p in args.paths]
    else:
        files = sorted(Path(args.dir).glob(args.glob))
    if not files:
        print(f"没有日志：{args.dir}\\{args.glob}")
        return 1

    runs = [read_run(f) for f in files]

    print(f"{'file':28} {'arm':32} {'floor':>7} {'hp':>5} {'turns':>7} {'zero':>6} "
          f"{'outcome':9} items used")
    print("-" * 132)
    for r in runs:
        print(fmt_run(r))
        if args.full_items:
            for n, c in sorted(r["items"].items(), key=lambda kv: -kv[1]):
                print(f"      {n:32} ×{c}")

    print("\n=== 按臂聚合 ===")
    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in runs:
        by_arm[r["arm"]].append(r)

    print(f"{'arm':32} {'n':>3} {'层数(均)':>9} {'最好层':>7} {'剩余血(均)':>11} "
          f"{'零伤回合':>9} {'阵亡':>5} {'buckler':>8}")
    for arm, rs in sorted(by_arm.items()):
        n = len(rs)
        floors = [r["floor"] for r in rs if r["floor"] is not None]
        hps = [r["hp"] for r in rs if r["hp"] is not None]
        zdrs = [r["zero_dmg_ratio"] for r in rs if r["zero_dmg_ratio"] is not None]
        deaths = sum(1 for r in rs if r["died"])
        bu = sum(c for r in rs for nm, c in r["items"].items() if "BUCKLER" in nm.upper())
        print(f"{arm:32} {n:>3} "
              f"{(sum(floors) / len(floors) if floors else float('nan')):>9.1f} "
              f"{(max(floors) if floors else '-'):>7} "
              f"{(sum(hps) / len(hps) if hps else float('nan')):>11.1f} "
              f"{(f'{100 * sum(zdrs) / len(zdrs):.0f}%' if zdrs else '-'):>9} "
              f"{f'{deaths}/{n}':>5} {bu:>8}")

    if args.json:
        Path(args.json).write_text(json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n写出：{args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
