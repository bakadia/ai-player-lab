"""跑一场战斗直到结束。

    python games/backpack-hero-aipeiwo/agent/run.py
    python .../run.py --max-steps 50 --dry-run

只负责「战斗」这一段（MVP 的第一阶段）。地图/奖励/商店还没接，
遇到非战斗状态就停下来报告，不会瞎点。

三条硬规矩（对应 MVP 的 A1/A4）：
1. 每个动作都要看 ack 的 ok/changed，不能「发了就当成功」。
2. 连续 3 次动作没改变状态 -> 判定卡死，立刻停。
3. 每局写 JSONL，能回放才算跑过。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "adapter"))

from client import Bridge, BridgeError  # noqa: E402
from ledger import Ledger  # noqa: E402
from policy import choose  # noqa: E402

MAX_CONSECUTIVE_NOCHANGE = 3


def _damage_from(delta: dict[str, Any], pre_hp: dict[str, int] | None = None) -> int:
    """ack.delta 里敌人的负值加总 = 这一下打出的伤害。

    敌人被打死时 delta 里是字符串 "gone"，没有数值——这时要用**行动前的血量**
    当成伤害。否则致命一击会被记成 0，把账本均值压低（SPIKE-04 实测踩到）。
    """
    total = 0
    for uid, v in (delta.get("enemies") or {}).items():
        if isinstance(v, int):
            if v < 0:
                total += -v
        elif v == "gone" and pre_hp:
            total += pre_hp.get(uid, 0)
    return total


def _absorbed_from(delta: dict[str, Any]) -> int:
    """被格挡掉的伤害：敌人护甲减少量（负数取反）。"""
    total = 0
    for v in (delta.get("enemies_block") or {}).values():
        if isinstance(v, int) and v < 0:
            total += -v
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="run one battle")
    ap.add_argument("--runtime", help="runtime 目录")
    ap.add_argument("--max-steps", type=int, default=80)
    ap.add_argument("--max-seconds", type=float, default=600)
    ap.add_argument("--dry-run", action="store_true", help="只打印决策，不发命令")
    ap.add_argument("--ledger", help="账本路径")
    args = ap.parse_args(argv)

    bridge = Bridge(args.runtime)
    ledger = Ledger(args.ledger)

    runs_dir = HERE.parent / "data" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    log_path = runs_dir / time.strftime("%Y%m%d-%H%M%S.jsonl")
    log = log_path.open("w", encoding="utf-8")

    explored: set[str] = set()
    nochange_streak = 0
    steps = 0
    started = time.monotonic()

    def record(obj: dict[str, Any]) -> None:
        log.write(json.dumps(obj, ensure_ascii=False) + "\n")
        log.flush()

    print(f"run log: {log_path}")

    try:
        while steps < args.max_steps:
            if time.monotonic() - started > args.max_seconds:
                print("!! 超过总时限，停")
                break

            st = bridge.state()
            mode = st.get("mode")
            player_hp = st.get("player", {}).get("hp")
            enemies = st.get("enemies", [])

            print(f"\n[step {steps}] seq={st.get('seq')} mode={mode} "
                  f"hp={player_hp} ap={st.get('player', {}).get('energy')} enemies={len(enemies)}")

            if player_hp is not None and player_hp <= 0 and mode != "menu":
                print("玩家已阵亡，结束")
                break

            # bridge 现在会把 hp<=0 单独标成 dead（挂掉时游戏本身还是 battle）
            if mode == "dead":
                print("玩家已阵亡（mode=dead），结束")
                record({"event": "dead", "seq": st.get("seq")})
                break

            if mode not in ("battle", "reorganize"):
                print(f"离开战斗（mode={mode}），本 runner 只处理战斗/整理，停在这里")
                record({"event": "leave_battle", "mode": mode, "seq": st.get("seq")})
                break

            if mode == "reorganize":
                # 中污染/诅咒会强制进整理阶段，不点掉「结束整理」整局就卡住
                print("  进入整理阶段（污染/诅咒），准备点「结束整理」")

            if mode == "battle" and not enemies:
                print("战斗里没有存活敌人（可能在结算动画），等下一帧")
                time.sleep(0.3)
                steps += 1
                continue

            # legal 只在玩家回合填充；轮询到 enemyTurn 时是空的，要等而不是退出
            if not st.get("legal"):
                print("  还没轮到玩家（legal 为空），等")
                time.sleep(0.2)
                steps += 1
                continue

            action, why = choose(st, ledger, explored)
            if action is None:
                print(f"没有可做的动作：{why}")
                break

            print(f"  -> {action.get('type')} {action.get('uid', '')}  ({why})")
            record({"event": "decide", "seq": st.get("seq"), "action": action, "why": why})

            if args.dry_run:
                steps += 1
                break

            # 行动前的敌人血量：用来把 delta 里的 "gone" 换算成致命伤害
            pre_hp = {e["uid"]: e.get("hp", 0) for e in enemies}

            # 用物品前先记住它的名字，ack 之后按 delta 记账
            item_name = None
            if action.get("type") == "use_item":
                for it in st.get("backpack", {}).get("items", []):
                    if it["uid"] == action.get("uid"):
                        item_name = it.get("name", "")
                        break

            try:
                ack = bridge.act(action, timeout_ms=15000)
            except BridgeError as exc:
                print(f"!! 通信失败：{exc}")
                record({"event": "bridge_error", "error": str(exc)})
                break

            delta = ack.get("delta") or {}
            record({
                "event": "act",
                "action": action,
                "ok": ack.get("ok"),
                "changed": ack.get("changed"),
                "ms": ack.get("ms"),
                "delta": delta,
                "error": ack.get("error"),
            })

            print(f"     ok={ack.get('ok')} changed={ack.get('changed')} "
                  f"ms={ack.get('ms')} delta={json.dumps(delta, ensure_ascii=False)}")
            if ack.get("error"):
                print(f"     error={ack['error']}")

            if item_name:
                dmg = _damage_from(delta, pre_hp)
                absorbed = _absorbed_from(delta)
                blk = max(0, int(delta.get("player_block", 0) or 0))
                ledger.record_use(item_name, dmg, blk, absorbed)
                ledger.save()
                explored.add(item_name.strip())
                print(f"     账本记录 {item_name!r}: dmg={dmg} block={blk} absorbed={absorbed}")

            # 摆放失败的物品别再试：背包可能真的放不下，反复试会把整局拖死
            if action.get("type") == "place_item" and not ack.get("changed"):
                explored.add(f"place:{action.get('uid', '')}")
                print(f"     放弃摆放 {action.get('uid')}（{action.get('name', '')}）")

            if not ack.get("changed"):
                nochange_streak += 1
                if nochange_streak >= MAX_CONSECUTIVE_NOCHANGE:
                    print(f"!! 连续 {nochange_streak} 次动作没改变状态，判定卡死，停")
                    record({"event": "stuck", "streak": nochange_streak})
                    break
            else:
                nochange_streak = 0

            steps += 1

        print(f"\n结束：{steps} 步，日志 {log_path}")
        record({"event": "end", "steps": steps})
    finally:
        log.close()
        ledger.save()

    print("\n账本：")
    print(ledger.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
