"""跑 A/B：每臂 N 局，每局都干净重启 + 独立账本。

    python games\\backpack-hero-aipeiwo\\tools\\ab_runs.py --runs 3
    python ... --arms full --runs 1          # 只跑一臂一局（冒烟）

为什么要重启：一局打完（阵亡）游戏停在死亡界面，`start_run.py` 看到 `mode=dead`
会直接返回 2「已经在对局中」。桥接没有「回主菜单」这个动作，所以只能重启。
重启同时会部署 DLL（已经是最新时会跳过，不需要管理员）。

为什么要独立账本：账本是跨局持久化的，先跑的臂会把物品学进账本，
后面的臂等于开卷考试，臂间就没法比了。每臂从同一份 `data/items.json` 播种。

产物：
- `data/runs/ab/ab-<arm>-<n>.jsonl`：loop 日志（含 start 事件里的 flags）
- `data/runs/ab/ab-<arm>-<n>.txt`：那局的 stdout，出错时看这个
- 汇总：`python tools\\run_report.py --dir data\\runs\\ab`
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
GAME = HERE.parent
RUNS = GAME / "data" / "runs"
AB = RUNS / "ab"
LEDGERS = GAME / "data" / "ledgers"
SEED = GAME / "data" / "items.json"

ARMS: dict[str, list[str]] = {
    "a-baseline": [],
    "b-aware-ratio": ["--aware-block", "--ratio-energy"],
    "c-full": ["--aware-block", "--ratio-energy", "--explore-threat", "--value-energy"],
    # c-full + 结构化回执里的状态/最大生命/幸运估值（SPIKE-09）
    "d-value-status": ["--aware-block", "--ratio-energy", "--explore-threat",
                       "--value-energy", "--value-status"],
}


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def read_state() -> dict:
    try:
        return json.loads((GAME / "runtime" / "state.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def wait_mode(target: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = read_state()
        if st.get("mode") == target:
            return True
        time.sleep(1.0)
    return False


def restart_game(dry: bool) -> bool:
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", str(HERE / "bph.ps1"), "restart"]
    if dry:
        say(f"[dry] {' '.join(cmd)}")
        return True
    say("restarting game (stop + deploy + start) ...")
    p = subprocess.run(cmd, cwd=str(GAME), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    tail = (p.stdout or "").strip().splitlines()[-3:]
    for line in tail:
        say(f"    {line}")
    if p.returncode != 0:
        say(f"!! restart failed rc={p.returncode}: {(p.stderr or '')[:300]}")
        return False
    return wait_mode("menu", 90)


def seed_ledger(arm: str) -> Path:
    LEDGERS.mkdir(parents=True, exist_ok=True)
    dst = LEDGERS / f"{arm}.json"
    shutil.copyfile(SEED, dst)
    return dst


def run_one(arm: str, idx: int, flags: list[str], max_steps: int,
            max_seconds: float, dry: bool, no_restart: bool = False,
            wait_menu: float = 0.0) -> dict:
    out = {"arm": arm, "run": idx, "ok": False}
    ledger = seed_ledger(arm)
    before = set(RUNS.glob("loop-*.jsonl"))

    if no_restart:
        # 桥接没有「回主菜单」的动作：死亡界面 popups=0 / legal=[] / MenuManager.main=null，
        # 三种 start_run 全部失败。所以只能等人点回主菜单，这里等它。
        if wait_menu > 0:
            say(f"waiting up to {wait_menu:.0f}s for the game to be at the main menu ...")
            if not wait_mode("menu", wait_menu):
                out["error"] = "timeout waiting for menu"
                say("!! still not at the main menu")
                return out
            say("main menu detected")
    elif not restart_game(dry):
        out["error"] = "restart"
        return out

    env_note = {"PYTHONIOENCODING": "utf-8"}
    import os
    env = dict(os.environ, **env_note)

    say("starting a run ...")
    p = subprocess.run([sys.executable, str(HERE / "start_run.py")],
                       cwd=str(GAME), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=180)
    if p.returncode == 2:
        # 还在上一局里（重启没把它清干净）。再重启一次，只重试一回。
        say("still in a previous run; restarting once more ...")
        if not restart_game(dry):
            out["error"] = "restart retry"
            return out
        p = subprocess.run([sys.executable, str(HERE / "start_run.py")],
                           cwd=str(GAME), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=180)
    if p.returncode != 0:
        say(f"!! start_run rc={p.returncode}: {(p.stdout or '')[-300:]}")
        out["error"] = f"start_run rc={p.returncode}"
        return out
    say("run started, driving the loop ...")

    cmd = [sys.executable, str(GAME / "agent" / "loop.py"),
           "--max-steps", str(max_steps),
           "--max-seconds", str(max_seconds),
           "--dump-decisions",
           "--ledger", str(ledger)] + flags
    p = subprocess.run(cmd, cwd=str(GAME), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env,
                       timeout=max_seconds + 300)

    new = sorted(set(RUNS.glob("loop-*.jsonl")) - before)
    AB.mkdir(parents=True, exist_ok=True)
    stem = f"ab-{arm}-{idx}"
    if new:
        dest = AB / f"{stem}.jsonl"
        shutil.move(str(new[-1]), dest)
        out["log"] = str(dest)
    (AB / f"{stem}.txt").write_text(
        (p.stdout or "") + "\n--- stderr ---\n" + (p.stderr or ""), encoding="utf-8")
    out["ok"] = bool(new)
    out["rc"] = p.returncode
    say(f"done {arm} #{idx} rc={p.returncode} log={out.get('log')}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A/B the combat policy across clean runs")
    ap.add_argument("--runs", type=int, default=3, help="每臂几局")
    ap.add_argument("--arms", nargs="*", default=list(ARMS), help="臂名（默认全部）")
    ap.add_argument("--max-steps", type=int, default=70)
    ap.add_argument("--max-seconds", type=float, default=900)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-restart", action="store_true",
                    help="不要重启游戏（agent 的 shell 停不掉它）；假设已经在主菜单")
    ap.add_argument("--wait-menu", type=float, default=0.0,
                    help="配合 --no-restart：先等这么久（秒）等出现主菜单")
    ap.add_argument("--start-index", type=int, default=1,
                    help="每臂从第几局开始编号（续跑时用，避免覆盖已有日志）")
    args = ap.parse_args(argv)

    plan = [(a, i) for a in args.arms
            for i in range(args.start_index, args.start_index + args.runs)]
    say(f"plan: {len(plan)} runs -> {[(a, i) for a, i in plan]}")

    results = []
    for arm, idx in plan:
        say(f"===== {arm} run {idx}/{args.runs} =====")
        try:
            results.append(run_one(arm, idx, ARMS[arm], args.max_steps,
                                   args.max_seconds, args.dry_run,
                                   no_restart=args.no_restart,
                                   wait_menu=args.wait_menu))
        except Exception as exc:                     # noqa: BLE001 - keep going
            say(f"!! {arm} #{idx} raised {type(exc).__name__}: {exc}")
            results.append({"arm": arm, "run": idx, "ok": False, "error": str(exc)})

    AB.mkdir(parents=True, exist_ok=True)
    (AB / "ab-summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in results if r.get("ok"))
    say(f"finished {ok}/{len(results)} runs")
    for r in results:
        if not r.get("ok"):
            say(f"   FAILED {r['arm']} #{r['run']}: {r.get('error', 'no log')}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
