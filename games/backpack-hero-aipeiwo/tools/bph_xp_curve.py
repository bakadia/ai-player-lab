"""经验/战斗曲线采样：只读 `runtime/state.json`，不跟 loop 抢命令队列。

统计「一局能打几场、每场给多少 xp、掉多少血、打了几回合」——用来决定
「主动清敌人房换经验」还是「绕开保命」。

    python tools\\bph_xp_curve.py --seconds 900
    python tools\\bph_xp_curve.py --out data\\runs\\xp-curve-1.jsonl

要点：

- **纯读 state.json**（bridge 每 0.1s 原子写），一个命令都不发，所以可以跟
  `agent/loop.py` 同时跑。
- 经验是「本等级内的余量」（`Player.experience`），升级会减掉门槛；所以每场的
  增量按「(门槛 - 战前余量) + 战后余量」算（只处理升 1 级的情况，够用）。
- 战斗的起止用 `mode` 进出 `battle` 判断；同一层连续打两场也能分开。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_STATE = ROOT / "runtime" / "state.json"


def read_state(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def snap(st: dict) -> dict:
    run = st.get("run") or {}
    pl = st.get("player") or {}
    enemies = [e for e in (st.get("enemies") or []) if e.get("hp", 0) > 0]
    return {
        "mode": st.get("mode"),
        "floor": run.get("floor"),
        "level": pl.get("level", run.get("level")),
        "exp": run.get("experience"),
        "xp_next": run.get("xp_next"),
        "hp": pl.get("hp"),
        "gold": run.get("gold"),
        "enemies": len(enemies),
        "enemy_names": [e.get("name", "").strip() for e in enemies],
        "room": ((st.get("map") or {}).get("player") or {}).get("room_at") or "",
        "items_in_grid": (st.get("backpack") or {}).get("items_in_grid"),
    }


def xp_gain(a: dict, b: dict) -> int | str:
    """两次采样之间的经验增量（跨 1 级也算得出来）。"""
    ea, eb = a.get("exp"), b.get("exp")
    la, lb = a.get("level"), b.get("level")
    if not isinstance(ea, int) or not isinstance(eb, int):
        return "?"
    if la == lb:
        return eb - ea
    if isinstance(la, int) and isinstance(lb, int) and lb == la + 1:
        need = a.get("xp_next")
        if isinstance(need, int) and need > 0:
            return (need - ea) + eb
    return "?"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="sample xp / battle curve from state.json")
    ap.add_argument("--state", default=str(DEFAULT_STATE))
    ap.add_argument("--seconds", type=float, default=900)
    ap.add_argument("--interval", type=float, default=0.15)
    ap.add_argument("--out", default="")
    ap.add_argument("--stop-on-dead", action="store_true",
                    help="看到 mode=dead 就退出。**跑单局统计时务必加**："
                         "否则采样器会在下一局里继续写同一个文件（真机踩过）")
    args = ap.parse_args(argv)

    path = Path(args.state)
    out = Path(args.out) if args.out else (
        ROOT / "data" / "runs" / f"xp-curve-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    fh = out.open("w", encoding="utf-8")

    ev: list[dict] = []
    prev: dict | None = None
    battle: dict | None = None
    deadline = time.monotonic() + max(1.0, args.seconds)

    def emit(obj: dict) -> None:
        ev.append(obj)
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        fh.flush()

    while time.monotonic() < deadline:
        st = read_state(path)
        time.sleep(args.interval)
        if st is None:
            continue
        cur = snap(st)
        if prev is None:
            prev = cur
            continue
        if cur == prev:
            continue

        in_battle = cur["mode"] == "battle"
        was_battle = prev["mode"] == "battle"

        if in_battle and not was_battle:
            battle = {"start": cur, "turns": 0}
            print(f"[battle] floor={cur['floor']} room={cur['room']} "
                  f"enemies={cur['enemies']} ({','.join(cur['enemy_names'])}) "
                  f"hp={cur['hp']} lv={cur['level']} exp={cur['exp']}/{cur['xp_next']}")
        elif in_battle and battle is not None:
            battle["turns"] += 1        # 采样到变化就算「一帧有动作」，只作相对量
        elif was_battle and not in_battle and battle is not None:
            start = battle["start"]
            rec = {
                "event": "battle_end",
                "floor": start["floor"],
                "room": start["room"],
                "enemies": start["enemies"],
                "enemy_names": start["enemy_names"],
                "mode_after": cur["mode"],
                "hp_before": start["hp"],
                "hp_after": cur["hp"],
                "hp_lost": (start["hp"] - cur["hp"]) if isinstance(start["hp"], int)
                           and isinstance(cur["hp"], int) else None,
                "xp_gain": xp_gain(start, cur),
                "level_before": start["level"],
                "level_after": cur["level"],
                "gold_before": start["gold"],
                "gold_after": cur["gold"],
                "items_after": (cur.get("items_in_grid")),
            }
            emit(rec)
            print(f"          -> end {rec['mode_after']} hp {rec['hp_before']}->{rec['hp_after']} "
                  f"xp+{rec['xp_gain']} lv {rec['level_before']}->{rec['level_after']} "
                  f"gold {rec['gold_before']}->{rec['gold_after']}")
            battle = None
            if args.stop_on_dead and rec["mode_after"] == "dead":
                print("（--stop-on-dead：这一局结束了，采样退出）")
                break
        prev = cur

    fh.close()

    ends = [e for e in ev if e["event"] == "battle_end"]
    print("\n== 汇总")
    print(f"样本文件: {out}")
    print(f"战斗场次: {len(ends)}")
    if ends:
        xps = [e["xp_gain"] for e in ends if isinstance(e["xp_gain"], int)]
        hps = [e["hp_lost"] for e in ends if isinstance(e["hp_lost"], int)]
        print(f"每场 xp: {xps}（合计 {sum(xps)}）")
        print(f"每场掉血: {hps}（合计 {sum(hps)}）")
        death = [e for e in ends if e["mode_after"] == "dead"]
        print(f"打死的场次: {len(death)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
