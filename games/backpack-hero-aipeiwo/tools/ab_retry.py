"""Run the A/B with `retry_run` instead of restarting the game.

Why this exists: `ab_runs.py` calls `bph.cmd restart`, which the agent shell
cannot do (the game runs in the user's session). `retry_run` was verified in
v7: it starts a fresh run from dead / map / battle, with hp=40 and floor=0.

Usage:
    python tools\\ab_retry.py --arms c-full d-value-status --runs 3
    python tools\\ab_retry.py --dry-run

Prerequisite: the game is already running (CUA launch_app or the user started
it). If it is at the main menu, this script starts the first run; if it is
already in a run, it calls retry_run once to get a clean one.

Each arm gets its own ledger seeded from data/items.json. Runs inside an arm
share that ledger (that is the point: the arm learns across its runs).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
GAME = HERE.parent
RUNS = GAME / "data" / "runs"
AB = RUNS / "ab"
LEDGERS = GAME / "data" / "ledgers"
SEED = GAME / "data" / "items.json"

sys.path.insert(0, str(GAME / "adapter"))
sys.path.insert(0, str(HERE))

from ab_runs import ARMS  # noqa: E402
from client import Bridge, BridgeError  # noqa: E402

IN_RUN = {"intro", "map", "battle", "reorganize", "dead"}


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def read_state() -> dict[str, Any]:
    try:
        return json.loads((GAME / "runtime" / "state.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def wait_mode(targets: set[str], timeout: float) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = read_state()
        if st.get("mode") in targets:
            return st
        time.sleep(0.5)
    return None


def run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(cmd, cwd=str(GAME), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env,
                          timeout=timeout)


def retry_run(bridge: Bridge) -> tuple[bool, dict[str, Any]]:
    ack = bridge.act({"type": "retry_run"}, timeout_ms=30000)
    return bool(ack.get("changed")), ack


def fresh_run(bridge: Bridge, first: bool) -> bool:
    """Return True when the game is in a clean run."""
    st = read_state()
    mode = st.get("mode")
    if first and mode in IN_RUN:
        # An existing run is fine, but retry once so every arm starts from the
        # same state (hp=40, floor=0). This also clears a stuck reorganize.
        say(f"already in a run (mode={mode}); retry_run for a clean start")
        ok, ack = retry_run(bridge)
        say(f"    retry ok={ack.get('changed')} err={ack.get('error')}")
        return ok

    if mode == "menu" or not st:
        say("game is at the main menu; starting a run ...")
        p = run([sys.executable, str(HERE / "start_run.py")], timeout=180)
        say(f"    start_run rc={p.returncode}")
        if p.returncode != 0:
            say((p.stdout or "")[-500:])
            return False
        return True

    if first:
        say(f"game is in a run (mode={mode}); using it")
        return True

    # Subsequent runs: a fresh run via retry_run, no restart.
    ok, ack = retry_run(bridge)
    say(f"retry_run changed={ack.get('changed')} ms={ack.get('ms')} err={ack.get('error')}")
    if ok:
        st = read_state()
        return st.get("mode") in IN_RUN

    # The game may have gone back to the menu between runs.
    if read_state().get("mode") == "menu":
        say("retry failed but the game is at the menu; start_run instead")
        p = run([sys.executable, str(HERE / "start_run.py")], timeout=180)
        return p.returncode == 0
    return False


def run_one(arm: str, idx: int, flags: list[str], args: argparse.Namespace) -> dict[str, Any]:
    out: dict[str, Any] = {"arm": arm, "run": idx, "ok": False}
    ledger = LEDGERS / f"{arm}.json"
    before = set(RUNS.glob("loop-*.jsonl"))

    cmd = [sys.executable, str(GAME / "agent" / "loop.py"),
           "--max-steps", str(args.max_steps),
           "--max-seconds", str(args.max_seconds),
           "--ledger", str(ledger)] + flags
    if args.dump_decisions:
        cmd.append("--dump-decisions")

    say(f"loop: {' '.join(cmd[-8:])}")
    if args.dry_run:
        say("    [dry] skipped")
        out["ok"] = True
        out["dry"] = True
        return out

    p = run(cmd, timeout=args.max_seconds + 300)
    AB.mkdir(parents=True, exist_ok=True)
    stem = f"ab-{arm}-{idx}"
    (AB / f"{stem}.txt").write_text(
        (p.stdout or "") + "\n--- stderr ---\n" + (p.stderr or ""), encoding="utf-8")

    new = sorted(set(RUNS.glob("loop-*.jsonl")) - before)
    if new:
        dest = AB / f"{stem}.jsonl"
        shutil.move(str(new[-1]), dest)
        out["log"] = str(dest)
    out["ok"] = bool(new)
    out["rc"] = p.returncode
    say(f"done {arm} #{idx} rc={p.returncode} log={out.get('log')}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A/B via retry_run (no game restart)")
    ap.add_argument("--arms", nargs="*", default=["c-full", "d-value-status"])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--start-index", type=int, default=1)
    ap.add_argument("--max-steps", type=int, default=80)
    ap.add_argument("--max-seconds", type=float, default=900)
    ap.add_argument("--dump-decisions", action="store_true", default=True)
    ap.add_argument("--no-dump-decisions", dest="dump_decisions", action="store_false")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reseed", action="store_true",
                    help="re-copy data/items.json into each arm ledger even when resuming")
    args = ap.parse_args(argv)

    unknown = [a for a in args.arms if a not in ARMS]
    if unknown:
        print(f"unknown arm(s): {unknown}; known: {sorted(ARMS)}")
        return 2

    bridge = Bridge(GAME / "runtime")
    plan = [(a, i) for a in args.arms
            for i in range(args.start_index, args.start_index + args.runs)]
    say(f"plan: {len(plan)} runs -> {[(a, i) for a, i in plan]}")
    say(f"game: {GAME}")
    if args.dry_run:
        say("[dry] no commands will be sent")

    # one ledger per arm, seeded from the current items.json
    if not args.dry_run:
        for arm in args.arms:
            if args.reseed or args.start_index == 1:
                LEDGERS.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(SEED, LEDGERS / f"{arm}.json")
                say(f"seeded ledger for {arm}: {LEDGERS / f'{arm}.json'}")

    results = []
    first = True
    for arm, idx in plan:
        say(f"===== {arm} run {idx} =====")
        try:
            if not args.dry_run and not fresh_run(bridge, first):
                results.append({"arm": arm, "run": idx, "ok": False,
                                "error": "cannot get a fresh run"})
                say("!! cannot get a fresh run; stop")
                break
            first = False
            results.append(run_one(arm, idx, ARMS[arm], args))
        except (BridgeError, subprocess.TimeoutExpired) as exc:
            say(f"!! {arm} #{idx}: {type(exc).__name__}: {exc}")
            results.append({"arm": arm, "run": idx, "ok": False, "error": str(exc)})

    AB.mkdir(parents=True, exist_ok=True)
    (AB / "ab-retry-summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in results if r.get("ok"))
    say(f"finished {ok}/{len(results)} runs")
    for r in results:
        if not r.get("ok"):
            say(f"   FAILED {r['arm']} #{r['run']}: {r.get('error', 'no log')}")

    if not args.dry_run:
        say("report: python tools/run_report.py --dir data/runs/ab --glob \"ab-*.jsonl\"")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
