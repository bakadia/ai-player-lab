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
        "seq": state.get("seq"),
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


# ---------------------------------------------------------------- autoplay（M2）

class _RunTally:
    """一局从开局到死亡的累计。`play` 每次只回一段的事件，所以在这里累加。"""

    def __init__(self, idx: int):
        self.idx = idx
        self.chunks = 0
        self.battles = 0
        self.hp_lost = 0
        self.healed = 0
        self.hazard_paid = 0
        self.max_floor = 0
        self.death_floor: int | None = None
        self.level = None
        self.gold = None
        self.hp = None
        self.stalls = 0
        self.dropped: list[str] = []

    def absorb(self, d: dict[str, Any]) -> None:
        self.chunks += 1
        ev = d.get("events") or {}
        self.battles += int(ev.get("battles") or 0)
        self.hp_lost += int(ev.get("hp_lost") or 0)
        self.healed += int(ev.get("healed") or 0)
        self.hazard_paid += int(ev.get("hazard_paid") or 0)
        for nm in ev.get("dropped_for_room") or []:
            if nm:
                self.dropped.append(nm)
        f = d.get("floor")
        if isinstance(f, int):
            self.max_floor = max(self.max_floor, f)
        self.level = d.get("level")
        self.gold = d.get("gold")
        if d.get("hp") is not None:
            self.hp = d.get("hp")

    def close(self, floor: int | None) -> dict[str, Any]:
        self.death_floor = floor if isinstance(floor, int) else self.max_floor
        return {
            "run": self.idx,
            "floor": self.death_floor,
            "level": self.level,
            "gold": self.gold,
            "battles": self.battles,
            "hp_lost": self.hp_lost,
            "healed": self.healed,
            "hazard_paid": self.hazard_paid,
            "hp_per_battle": round(self.hp_lost / self.battles, 2) if self.battles else None,
            "chunks": self.chunks,
            "stalls": self.stalls,
            "dropped": self.dropped[:8],
        }


def _play_once(steps: int, seconds: int, flags: list[str]) -> dict[str, Any]:
    t0 = time.time() - 1
    cmd = [sys.executable, str(LOOP), "--max-steps", str(steps),
           "--max-seconds", str(seconds)] + flags
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=seconds + 120)
    logs = _newest_run_log(t0)
    return _digest(_state(), _events_since(logs), proc.stdout, proc.returncode)


def cmd_autoplay(args: argparse.Namespace) -> int:
    """无人值守：一直 play，死了就重开，直到打满 N 局或到楼层目标/时限。"""
    flags = list(args.flags or DEFAULT_FLAGS)
    deadline = time.monotonic() + args.minutes * 60.0
    journal: list[dict[str, Any]] = []

    d = _digest(_state(), _events_since([]))
    if d.get("mode") in ("menu", "dead"):
        cmd_new_run(argparse.Namespace())

    run = _RunTally(1)
    runs_done = 0
    prev_fp = None
    stall_count = 0

    def _progress_fp(d: dict[str, Any]) -> tuple:
        """「有没有真的推进」的指纹：只看会变好的量，不看 seq。

        `seq` 每帧都涨（游戏在跑），所以它证明不了进展——run 2 在地图上绕圈时
        seq 一直在变、floor/hp/level 一动不动，用 seq 判会永远判不出卡死。
        """
        return (d.get("mode"), d.get("floor"), d.get("hp"), d.get("level"),
                (d.get("events") or {}).get("battles"))

    while runs_done < args.runs and time.monotonic() < deadline:
        d = _play_once(args.steps, args.seconds, flags)
        run.absorb(d)

        fp = _progress_fp(d)
        if fp == prev_fp:
            stall_count += 1
            run.stalls = stall_count
        else:
            stall_count = 0
        prev_fp = fp

        print(f"[run {run.idx}] {d.get('mode')} floor={d.get('floor')} hp={d.get('hp')}/"
              f"{d.get('max_hp')} lvl={d.get('level')} bag_free={d.get('bag', {}).get('free')} "
              f"needs={','.join(n.split(':')[0] for n in d.get('needs') or []) or '-'}",
              flush=True)

        dead = d.get("mode") == "dead" or (d.get("hp") or 1) <= 0
        if dead:
            rec = run.close(d.get("floor"))
            journal.append(rec)
            runs_done += 1
            print(f"  -> run {rec['run']} 结束：floor={rec['floor']} level={rec['level']} "
                  f"battles={rec['battles']} hp/battle={rec['hp_per_battle']} "
                  f"hazard_paid={rec['hazard_paid']}", flush=True)
            if runs_done >= args.runs or time.monotonic() >= deadline:
                break
            cmd_new_run(argparse.Namespace())
            run = _RunTally(runs_done + 1)
            prev_fp = None
            stall_count = 0
            continue

        if d.get("mode") == "menu":
            print("  -> 回到主菜单，重开", flush=True)
            if run.battles or run.chunks > 1:
                journal.append(run.close(max(run.max_floor, 0)))
                runs_done += 1
                run = _RunTally(runs_done + 1)
            cmd_new_run(argparse.Namespace())
            prev_fp = None
            continue

        if stall_count >= args.stall_limit:
            print(f"  -> 连续 {stall_count} 段没有进展，判定卡死，重开", flush=True)
            journal.append(run.close(max(run.max_floor, 0)))
            runs_done += 1
            if runs_done >= args.runs:
                break
            cmd_new_run(argparse.Namespace())
            run = _RunTally(runs_done + 1)
            prev_fp = None
            stall_count = 0
            continue

        if args.floor_target and (run.max_floor or 0) >= args.floor_target:
            print(f"  -> 到达 floor {args.floor_target}，按 --floor-target 停", flush=True)
            break

    out = {"schema": "ai.autoplay.v1", "runs": journal,
           "summary": _autoplay_summary(journal)}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def _autoplay_summary(journal: list[dict[str, Any]]) -> dict[str, Any]:
    if not journal:
        return {"runs": 0}
    floors = [r["floor"] for r in journal if isinstance(r.get("floor"), int)]
    hp_per = [r["hp_per_battle"] for r in journal if r.get("hp_per_battle") is not None]
    return {
        "runs": len(journal),
        "floor_mean": round(sum(floors) / len(floors), 2) if floors else None,
        "floor_best": max(floors) if floors else None,
        "hp_per_battle_mean": round(sum(hp_per) / len(hp_per), 2) if hp_per else None,
        "battles_total": sum(r["battles"] for r in journal),
        "hazard_paid_total": sum(r["hazard_paid"] for r in journal),
    }


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

    a = sub.add_parser("autoplay", help="无人值守：死了重开，直到打满 N 局/到楼层目标/超时")
    a.add_argument("--runs", type=int, default=2, help="打几局（按死亡计）")
    a.add_argument("--steps", type=int, default=120, help="每段最多推进几步")
    a.add_argument("--seconds", type=int, default=240, help="每段最多几秒")
    a.add_argument("--minutes", type=float, default=40.0, help="总时限（墙钟）")
    a.add_argument("--floor-target", type=int, default=0, help="到这一层就停（0=不停）")
    a.add_argument("--stall-limit", type=int, default=3, help="连续几段状态不变算卡死")
    a.add_argument("--flags", nargs="*", default=None)
    a.set_defaults(fn=cmd_autoplay)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
