"""盯住 state.json，把「一闪而过」的界面记下来。

    python games/backpack-hero-aipeiwo/agent/watch.py
    python .../watch.py --seconds 300 --out data/state-log/custom.jsonl

为什么需要它：`state.json` 只保留**最后一帧**，而奖励三选一、升级、弹窗这些
界面出现几秒就没了。战斗结束后想回答「到底弹没弹奖励」，靠事后读一次 state
是看不到的（就算当时没弹，也没法证明）。这个脚本按 seq 变化逐条落盘。

每条只记「会瞬变」的字段，不记背包/房间明细（那部分量太大）。
日志写到 data/state-log/，不进 git。

容错：跨进程读文件必须容忍瞬时失败（PITFALLS E7）——`OSError` 和
`JSONDecodeError` 都当「还没好」，继续轮询。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "adapter"))

from client import Bridge, BridgeError  # noqa: E402

WATCH_UI = (
    "viewing_top",
    "inventory_phase",
    "leveling_up",
    "can_level_up",
    "victory_items",
    "reward_items",
    "select_item_dialogues",
    "select_item_finished",
    "selected_item",
    "current_event_type_name",
    "popups",
    "player_level",
    "player_experience",
    "tutorial_sequence_name",
)


def snapshot(st: dict[str, Any]) -> dict[str, Any]:
    """只挑会瞬变的字段。"""
    ui = st.get("ui") or {}
    backpack = st.get("backpack") or {}
    legal = st.get("legal") or []
    mp = st.get("map") or {}
    player = mp.get("player") or {}

    row: dict[str, Any] = {
        "seq": st.get("seq"),
        "mode": st.get("mode"),
        "battle_phase": st.get("battle_phase"),
        "floor": (st.get("run") or {}).get("floor"),
        "gold": (st.get("run") or {}).get("gold"),
        "hp": (st.get("player") or {}).get("hp"),
        "enemies": len(st.get("enemies") or []),
        "items_in_grid": backpack.get("items_in_grid"),
        "items_loose": backpack.get("items_loose"),
        "legal": [a.get("type") for a in legal],
        "room": player.get("room_at"),
        "event": player.get("event"),
    }
    for k in WATCH_UI:
        row[k] = ui.get(k)
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="watch state.json for transient UI states")
    ap.add_argument("--runtime", help="runtime 目录")
    ap.add_argument("--out", help="输出 jsonl（默认 data/state-log/<时间>.jsonl）")
    ap.add_argument("--seconds", type=float, default=0, help="跑多久；0 = 一直跑")
    ap.add_argument("--interval", type=float, default=0.2, help="轮询间隔秒")
    args = ap.parse_args(argv)

    bridge = Bridge(args.runtime)

    out = Path(args.out) if args.out else (
        HERE.parent / "data" / "state-log" / time.strftime("%Y%m%d-%H%M%S.jsonl")
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    log = out.open("w", encoding="utf-8")

    last_seq = -1
    started = time.monotonic()
    print(f"watching -> {out}")

    try:
        while True:
            if args.seconds and time.monotonic() - started > args.seconds:
                break
            try:
                st = bridge.state(retries=1)
            except BridgeError:
                time.sleep(args.interval)
                continue

            seq = st.get("seq", -1)
            if seq != last_seq:
                last_seq = seq
                row = snapshot(st)
                row["t"] = time.strftime("%H:%M:%S")
                log.write(json.dumps(row, ensure_ascii=False) + "\n")
                log.flush()
                print(f"  seq={row['seq']} mode={row['mode']} "
                      f"victory={row['victory_items']} reward={row['reward_items']} "
                      f"leveling={row['leveling_up']} legal={','.join(row['legal'])}")

            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        log.close()
        print(f"done -> {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
