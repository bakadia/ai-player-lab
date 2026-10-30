"""给「AI 操作手」用的批处理接口：推进一小段，回一份紧凑摘要。

    python tools\\ai_play.py status                 # 只看现在什么情况（不推进）
    python tools\\ai_play.py play --steps 25        # 推进最多 25 步，再回摘要
    python tools\\ai_play.py new-run                # 死了/卡了就重开一局（retry_run）

**为什么是「批处理」而不是「一步一步」**：游戏状态 10Hz 刷新，一次动作回执 0.7–3s。
如果每个动作都问一次模型，一局 100–400 步就是 10 分钟以上的纯等待，而且不可复现。
所以分工是：

    模型：看 digest 决定「接下来这段干什么」（打哪层、拿什么、要不要改流派）
    代码：用 policy/backpack/place 把这段执行完，并把发生过什么压缩成 digest

`play` 内部调 `agent/loop.py --max-steps N`，跑完把 state.json + 本段 jsonl 合成一份
`ai.digest.v1`：

    {mode, floor, hp, level, gold, enemies, bag, free_squares, needs, events, hint}

`needs` 是「现在有没有必须人（或模型）拍板的事」：升级界面开着、战利品在等你挑、
特殊整理要付血、已经死了…… 这些不拍板，继续跑下去就是白跑。

只读 state/日志；唯一的副作用是 `play` 会真的推进游戏（`new-run` 会重开一局）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                       # games/backpack-hero-aipeiwo
RUNTIME = ROOT / "runtime"
STATE = RUNTIME / "state.json"
RUNS = ROOT / "data" / "runs"
LOOP = ROOT / "agent" / "loop.py"
BPH_ACT = HERE / "bph_act.py"

DEFAULT_FLAGS = ["--focus-fire", "--aware-block", "--sustain", "--make-room", "2"]


def _state() -> dict[str, Any]:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _per_turn_block(name: str) -> float:
    """这件道具每回合给多少护甲（静态库；拿不到就是 0）。"""
    try:
        sys.path.insert(0, str(ROOT / "agent"))
        from itemdb import ItemDB  # noqa: E402
        st = ItemDB.load().get(name)
        return float(getattr(st, "block_per_turn", 0.0) or 0.0) if st else 0.0
    except Exception:
        return 0.0


def _bag(st: dict[str, Any]) -> dict[str, Any]:
    b = st.get("backpack") or {}
    items = []
    armor_per_turn = 0.0
    for it in b.get("items") or []:
        bpt = _per_turn_block(it.get("name") or "")
        armor_per_turn += bpt
        items.append({
            "name": it.get("name"),
            "cells": len(it.get("cells") or []),
            "cost": it.get("energy_cost"),
            "blk_per_turn": bpt or None,
        })
    return {
        "items": items,
        "in_grid": b.get("items_in_grid"),
        "loose": b.get("items_loose"),
        "free": b.get("free_squares"),
        "grid": b.get("grid_squares"),
        "armor_per_turn": round(armor_per_turn, 1),
    }


def _needs(st: dict[str, Any]) -> list[str]:
    """哪些情况必须拍板（不拍板继续跑就是浪费）。"""
    ui = st.get("ui") or {}
    needs: list[str] = []
    mode = st.get("mode")
    if mode == "dead" or (st.get("player") or {}).get("hp") == 0:
        needs.append("dead: 这一局结束了，要么 new-run，要么复盘")
    if mode == "menu":
        needs.append("menu: 在主菜单，需要 new-run 开局")
    if ui.get("leveling_up"):
        needs.append("level_up: 升级界面开着，要选强化（护甲流优先每回合护甲/格子）")
    sr = ui.get("special_reorg") or {}
    if sr.get("active") and (sr.get("damage") or 0) > 0:
        needs.append(f"hazard: 特殊整理要付 {sr.get('damage')} 血（curses_skipped={sr.get('curses_skipped')}）")
    if ui.get("limited_item_reorganize"):
        needs.append(f"loot: 战利品还能拿 {ui.get('items_allowed_to_take')} 件，挑完要收尾")
    if (ui.get("dialogue_choices") or -1) > 0:
        needs.append(f"dialogue: N 选 1（候选 {ui.get('dialogue_items')}）")
    if (st.get("backpack") or {}).get("free_squares") == 0:
        needs.append("bag_full: 背包满了，再捡就要丢东西/换装备")
    return needs


def _events_since(paths: list[Path]) -> dict[str, Any]:
    """把这几段日志压缩成「发生过什么」。"""
    out: dict[str, Any] = {
        "battles": 0, "hp_lost": 0, "healed": 0, "hazard_paid": 0,
        "dropped": [], "dropped_for_room": [], "refused_swap": [],
        "sustain_used": [], "deaths": 0, "steps": 0,
    }
    for p in paths:
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            ev = o.get("event")
            if ev == "act":
                a = o.get("action") or {}
                d = o.get("delta") or {}
                php = d.get("player_hp")
                if isinstance(php, int) and php < 0:
                    out["hp_lost"] += -php
                elif isinstance(php, int) and php > 0:
                    out["healed"] += php
                if a.get("type") == "after_battle":
                    out["battles"] += 1
            elif ev == "hazard_event":
                out["hazard_paid"] += int(o.get("damage") or 0)
            elif ev == "drop_for_room":
                out["dropped_for_room"].append(o.get("name"))
            elif ev == "place_not_worth":
                out["refused_swap"].append(
                    f"{o.get('name')}(要丢 {o.get('drop_name')})")
            elif ev == "dead":
                out["deaths"] += 1
            elif ev == "end":
                out["steps"] = o.get("steps")
    return out


def _digest(state: dict[str, Any], events: dict[str, Any],
            loop_stdout: str = "", loop_rc: int | None = None) -> dict[str, Any]:
    p = state.get("player") or {}
    r = state.get("run") or {}
    enemies = [{"name": e.get("name"), "hp": e.get("hp"),
                "attacks": [a.get("power") for a in (e.get("attacks") or [])]}
               for e in (state.get("enemies") or [])]
    return {
        "schema": "ai.digest.v1",
        "mode": state.get("mode"),
        "floor": r.get("floor"),
        "turn": r.get("turn"),
        "hp": p.get("hp"),
        "max_hp": p.get("max_hp"),
        "level": r.get("level"),
        "gold": r.get("gold"),
        "enemies": enemies,
        "legal": [a.get("type") for a in (state.get("legal") or [])][:12],
        "bag": _bag(state),
        "needs": _needs(state),
        "events": events,
        "loop": {"rc": loop_rc, "tail": (loop_stdout or "").strip().splitlines()[-6:]},
    }


def _newest_run_log(after: float) -> list[Path]:
    if not RUNS.exists():
        return []
    return sorted(p for p in RUNS.glob("loop-*.jsonl") if p.stat().st_mtime >= after)


def cmd_status(_args: argparse.Namespace) -> int:
    st = _state()
    d = _digest(st, _events_since([]))
    print(json.dumps(d, ensure_ascii=False, indent=1))
    return 0


def cmd_new_run(_args: argparse.Namespace) -> int:
    rc = subprocess.call([sys.executable, str(BPH_ACT),
                          '{"type":"retry_run"}', "--brief"],
                         cwd=str(ROOT))
    time.sleep(3)
    st = _state()
    print(json.dumps({"ok": rc == 0, "mode": st.get("mode"),
                      "floor": (st.get("run") or {}).get("floor"),
                      "hp": (st.get("player") or {}).get("hp")},
                     ensure_ascii=False))
    return rc


def cmd_play(args: argparse.Namespace) -> int:
    t0 = time.time() - 1
    cmd = [sys.executable, str(LOOP), "--max-steps", str(args.steps),
           "--max-seconds", str(args.seconds)] + list(args.flags or DEFAULT_FLAGS)
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=args.seconds + 120)
    logs = _newest_run_log(t0)
    st = _state()
    d = _digest(st, _events_since(logs), proc.stdout, proc.returncode)
    d["schema"] = "ai.digest.v1"
    d["logs"] = [p.name for p in logs]
    print(json.dumps(d, ensure_ascii=False, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AI player batch interface (bph)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="只看当前状态").set_defaults(fn=cmd_status)
    sub.add_parser("new-run", help="retry_run 重开一局").set_defaults(fn=cmd_new_run)

    p = sub.add_parser("play", help="推进最多 N 步并回摘要")
    p.add_argument("--steps", type=int, default=25)
    p.add_argument("--seconds", type=int, default=180)
    p.add_argument("--flags", nargs="*", default=None,
                   help="覆盖传给 loop.py 的策略开关（默认 focus-fire/aware-block/sustain/make-room 2）")
    p.set_defaults(fn=cmd_play)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
