"""特殊整理「塞 vs 不塞」的经济性对比（E25 后续实验①）。

    python tools\\ab_hazard.py data\\runs\\ab-hazard          # 读目录下所有 jsonl
    python tools\\ab_hazard.py data\\runs\\ab-hazard --json out.json --window 3

**要回答的问题**：遇到网格外的 Hazard 道具（`GOO` / `STICKY HONEY` …）时，
把它塞回网格（省下 `damage` 点血，但要占一个格子、并且诅咒留在包里）
和不塞（直接付 `damage`，道具被销毁）哪个更划算？

**怎么比**：每次特殊整理记一条 `hazard_event`（`loop.py --hazard-policy` 控制选哪边）。
对每条事件，看 `--window` 场战斗之内玩家掉了多少血（`lost`），再按 choice 分组平均。

**必须看 `eligible`**：只有在 `free_squares > 0` 时「塞」才**可能**成立；
背包满的时候两边都会付钱（`place_item` 报 `no fitting cell`），这种样本对结论没有贡献，
单独列成 `ineligible`，不要混进均值。

只读日志，不碰游戏。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

BATTLE_ACTIONS = {"use_item", "end_turn"}


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def hp_lost_after(rows: list[dict[str, Any]], start: int, window: int) -> dict[str, Any]:
    """从 start 之后数 window 场战斗，累加玩家掉的血。"""
    lost = 0
    battles = 0
    used = 0
    dead = False
    for i in range(start, len(rows)):
        o = rows[i]
        ev = o.get("event")
        if ev == "dead":
            dead = True
            break
        if ev != "act":
            continue
        a = o.get("action") or {}
        t = a.get("type")
        d = o.get("delta") or {}
        php = d.get("player_hp")
        if isinstance(php, int) and php < 0:
            lost += -php
        if t == "after_battle":
            battles += 1
            used = i
            if battles >= window:
                break
    return {"lost": lost, "battles": battles, "dead": dead, "end_index": used}


def analyze_file(path: Path, window: int) -> list[dict[str, Any]]:
    rows = read_rows(path)
    out: list[dict[str, Any]] = []

    # 每条 hazard_event 之后紧跟的、带 "hazard": true 的那次 act 就是放置结果
    for i, o in enumerate(rows):
        if o.get("event") != "hazard_event":
            continue
        placed_ok: Any = None
        placed_err = ""
        for j in range(i + 1, min(i + 6, len(rows))):
            n = rows[j]
            if n.get("event") == "act" and (n.get("action") or {}).get("type") == "place_item":
                placed_ok = bool(n.get("ok"))
                placed_err = n.get("error") or ""
                break
        tail = hp_lost_after(rows, i + 1, window)
        free = o.get("free_squares")
        choice = o.get("choice")
        out.append({
            "file": path.name,
            "n": o.get("n"),
            "name": o.get("name"),
            "choice": choice,
            "damage": o.get("damage"),
            "hazards_outside": o.get("hazards_outside"),
            "curses_skipped": o.get("curses_skipped"),
            "free_squares": free,
            "hp": o.get("hp"),
            "eligible": bool(isinstance(free, int) and free > 0),
            "placed_ok": placed_ok,
            "placed_err": placed_err[:60],
            "lost": tail["lost"],
            "battles": tail["battles"],
            "died": tail["dead"],
        })
    return out


def _mean(xs: list[float]) -> str:
    xs = [x for x in xs if isinstance(x, (int, float))]
    return f"{statistics.mean(xs):.1f}" if xs else "-"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="E25: 'place hazard' vs 'pay damage' economics")
    ap.add_argument("target", help="目录或单个 jsonl")
    ap.add_argument("--window", type=int, default=3, help="看后面几场战斗（默认 3）")
    ap.add_argument("--json", dest="json_out", default="")
    args = ap.parse_args(argv)

    t = Path(args.target)
    files = [t] if t.is_file() else sorted(t.glob("*.jsonl"))
    if not files:
        print(f"no logs under {t}")
        return 1

    events: list[dict[str, Any]] = []
    for f in files:
        events.extend(analyze_file(f, args.window))
    if not events:
        print("没有 hazard_event：这批日志是不是没带 --hazard-policy？")
        return 1

    print(f"{'file':16} {'#':>2} {'item':16} {'choice':6} {'dmg':>4} {'free':>4} "
          f"{'ok':>5} {'lost':>5} {'battles':>7}")
    print("-" * 88)
    for e in events:
        ok = "-" if e["placed_ok"] is None else ("Y" if e["placed_ok"] else "N")
        print(f"{e['file'][:16]:16} {str(e['n']):>2} {str(e['name'])[:16]:16} "
              f"{str(e['choice']):6} {str(e['damage']):>4} {str(e['free_squares']):>4} "
              f"{ok:>5} {e['lost']:>5} {e['battles']:>7}")

    print("\n=== 分组（只看 eligible = free_squares > 0，塞才可能成立）===")
    col = f"后 {args.window} 场掉血(均)"
    print(f"{'choice':8} {'n':>2} {'damage(均)':>10} {col:>18} {'阵亡':>5}")
    print("-" * 60)
    for choice in ("place", "skip"):
        sub = [e for e in events if e["choice"] == choice and e["eligible"]]
        if not sub:
            print(f"{choice:8} {0:>2} {'-':>10} {'-':>18} {'-':>5}")
            continue
        deaths = sum(1 for e in sub if e["died"])
        print(f"{choice:8} {len(sub):>2} {_mean([e['damage'] for e in sub]):>10} "
              f"{_mean([e['lost'] for e in sub]):>18} {deaths}/{len(sub):<2}")

    inelig = [e for e in events if not e["eligible"]]
    if inelig:
        print(f"\n（ineligible = 背包满、塞不进去的样本：{len(inelig)} 条，"
              f"全部按付钱算，没进上面的均值）")
        for e in inelig:
            print(f"   # {e['name']} damage={e['damage']} err={e['placed_err']}")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\njson -> {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
