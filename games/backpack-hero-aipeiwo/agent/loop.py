"""整局循环：地图 → 房间 → 战斗 → 升级 → 下一间，一直跑。

    python games/backpack-hero-aipeiwo/agent/loop.py --max-steps 200

和 `run.py` 的区别：`run.py` 只打一场，看到 `mode=map` 就退出；
这个会在 map / battle / reorganize 之间来回切，直到死、通关或步数用完。

**战斗策略完全复用 `policy.py`，这里不碰**。本文件只负责三件地图侧的事：

1. 地图决策：挑房间（战斗 > 宝箱 > 机会 > 出口）
2. 升级收尾：`level_up` 打开界面、`finish_level_up` 关掉
3. 捡掉落物：地上的东西在战斗结束瞬间会被销毁（SPIKE-06 §18/§20）

四条硬规矩和 run.py 一致：看 ack、防卡死、写 JSONL、别把失败当成功。
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
from itemdb import ItemDB  # noqa: E402
from policy import (Flags, choose, flags_label, pick_reward, reward_score,  # noqa: E402
                    score_action, set_flags)
from place import place_with_room  # noqa: E402
from mapnav import RoomGraph  # noqa: E402

MAX_CONSECUTIVE_NOCHANGE = 4
MAX_LEVEL_ATTEMPTS = 12  # 同一次升级最多试几次（扩容要 grant+逐格点+收尾，步数变多了）

# 房间里事件的优先级：先打怪（有经验），再拿东西，最后走出口
EVENT_PRIORITY = {
    "Enemy": 0,
    "Elite": 0,
    "Chest": 1,
    "Chance": 2,
    "Store": 3,
    "SimpleClickItem": 2,
    "Exit": 9,
    "ExitAlternate": 9,
}


def _damage_from(delta: dict[str, Any], pre_hp: dict[str, int] | None = None) -> int:
    total = 0
    for uid, v in (delta.get("enemies") or {}).items():
        if isinstance(v, int):
            if v < 0:
                total += -v
        elif v == "gone" and pre_hp:
            total += pre_hp.get(uid, 0)
    return total


def _absorbed_from(delta: dict[str, Any]) -> int:
    total = 0
    for v in (delta.get("enemies_block") or {}).values():
        if isinstance(v, int) and v < 0:
            total += -v
    return total


def _scorecard(st: dict[str, Any]) -> dict[str, Any]:
    """一局的收尾指标。A/B 要比的就是这些，别只留一个 seq。"""
    run = st.get("run") or {}
    pl = st.get("player") or {}
    return {
        "floor": run.get("floor"),
        "hp": pl.get("hp"),
        "max_hp": pl.get("max_hp"),
        "level": run.get("level"),
        "gold": run.get("gold"),
        "turn": run.get("turn"),
        "enemies": len(st.get("enemies") or []),
    }


def _current_room_event(state: dict[str, Any]) -> dict[str, Any] | None:
    """玩家所在房间里的那个未结束事件（没有就 None）。

    `state.map.events[]` 只带 `pos`，所以用房间的 `pos` 跟它配对。
    """
    mp = state.get("map") or {}
    cur = (mp.get("player") or {}).get("room_at")
    if not cur:
        return None
    pos = None
    for r in mp.get("rooms") or []:
        if r.get("uid") == cur:
            pos = r.get("pos")
            break
    if pos is None:
        return None
    for ev in mp.get("events") or []:
        if ev.get("finished"):
            continue
        ep = ev.get("pos") or [0, 0]
        if abs(ep[0] - pos[0]) < 0.01 and abs(ep[1] - pos[1]) < 0.01:
            return ev
    return None


def _event_rooms(state: dict[str, Any], blocked: set[str] | None = None) -> list[tuple[int, str, str]]:
    """(优先级, 房间 uid, 事件 uid)。只收「未结束、看得见、有房间」的事件。

    `blocked` 是「确认做不了的事件」——比如地图开着时 StartEvent 是空操作
    （SPIKE-08 §8）。不排除它们的话，这些事件永远是「未完成」，
    每一轮都会被重新选中，于是玩家在两个房间之间来回走。
    """
    blocked = blocked or set()
    mp = state.get("map") or {}
    rooms = mp.get("rooms") or []
    events = mp.get("events") or []
    out: list[tuple[int, str, str]] = []

    for ev in events:
        if ev.get("finished"):
            continue
        if ev.get("uid") in blocked:
            continue
        pos = ev.get("pos") or [0, 0]
        prio = EVENT_PRIORITY.get(ev.get("type_name", ""), 5)

        for r in rooms:
            rp = r.get("pos") or [0, 0]
            if abs(rp[0] - pos[0]) < 0.01 and abs(rp[1] - pos[1]) < 0.01:
                if r.get("locked") or not r.get("visible", True):
                    break
                out.append((prio, r["uid"], ev["uid"]))
                break

    out.sort(key=lambda x: x[0])
    return out


def hazard_place_action(state: dict[str, Any], tried: set[str]) -> dict[str, Any] | None:
    """E25：特殊整理里「网格外的 Hazard 道具」→ 先塞回网格，再结束整理。

    游戏侧的规则（`GameManager.GetDamageForHazards` 的 IL，v8.23 核对）：

        damage = Σ_{k=1..n} (k + floor + cursesSkipped)   # n = 网格外且 itemType 含 Hazard(52) 的件数
        cursesSkipped += n                                 # 结束整理时累加，所以第二次会比第一次贵

    也就是说：**只要把这些道具放进网格（`itemMovement.inGrid = true`），它们就不再计入伤害**，
    而直接点「结束整理」是「扣血 + 道具被销毁」两亏。以前 `loop` 只会点结束，等于每局白送
    14-15 点血（M4 实测：A 组 11.3 血/局全部来自这里）。

    返回一条 `place_item` 动作；没什么可做就返回 None。同一个 uid 只试一次
    （塞不进去就别死循环，老老实实付钱），`tried` 在离开特殊整理时清空。
    """
    sr = (state.get("ui") or {}).get("special_reorg") or {}
    if not sr.get("active"):
        tried.clear()
        return None
    if (sr.get("hazards_outside") or 0) <= 0:
        return None
    for it in sr.get("items") or []:
        uid = it.get("uid")
        if not it.get("hazard") or not uid or uid in tried:
            continue
        tried.add(uid)
        return {"type": "place_item", "uid": uid,
                "name": it.get("name") or "",
                "_hazard": True, "_damage": sr.get("damage")}
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="run a whole dungeon floor loop")
    ap.add_argument("--runtime", help="runtime 目录")
    ap.add_argument("--max-steps", type=int, default=200)
    ap.add_argument("--max-seconds", type=float, default=1800)
    ap.add_argument("--ledger", help="账本路径")
    ap.add_argument("--dry-run", action="store_true", help="只打印决策，不发命令")
    ap.add_argument("--dump-decisions", action="store_true",
                    help="每次战斗决策都写一份完整 state + 候选分数（复盘/A-B 用，日志会变大）")

    # ---- 战斗策略开关（默认全关 = v0.1 老行为）。A/B 靠这三个。 ----
    ap.add_argument("--aware-block", action="store_true",
                    help="SPIKE-07 §4-1：incoming 减去已经买到的 player.block")
    ap.add_argument("--ratio-energy", action="store_true",
                    help="SPIKE-07 §4-2：按每点能量的分数选（务必和 --aware-block 一起开）")
    ap.add_argument("--explore-threat", action="store_true",
                    help="没见过的物品按「能挡掉多少威胁」估值，否则账本永远学不到防御物品")
    ap.add_argument("--value-energy", action="store_true",
                    help="把「+N 能量」折成分数，否则回能量的消耗品恒为 0 分、永远不会被用")
    ap.add_argument("--value-status", action="store_true",
                    help="用结构化回执给「状态/最大生命/幸运」类效果估值（需要新桥接）")
    ap.add_argument("--focus-fire", action="store_true",
                    help="集火：能一击打死某个敌人时，按它的攻击力 × KILL_TURNS 加分"
                         "（多敌人房里减少每回合 incoming；实测每场掉血 4-14 是死因）")
    ap.add_argument("--sustain", action="store_true",
                    help="续航：按「有效治疗」（min(heal, max_hp-hp)）给食物/药水打分，"
                         "腾位时不丢 sustain/relic，三选一按类别挑。默认关 = 老行为")
    ap.add_argument("--lethal-explore", dest="explore_threat", action="store_true",
                    help=argparse.SUPPRESS)   # 旧名字，等同 --explore-threat
    ap.add_argument("--record-unverified", action="store_true",
                    help="老行为：动作没生效也记一条账本样本（会把物品永久记成 0/0，慎用）")
    ap.add_argument("--level-up-via", default="select", choices=["select", "spaces", "end"],
                    help="收升级界面的方式。select/spaces 都走 Select()（spaces 曾用 ChangeSpaces "
                         "加配额，会把 numberToUnlock 翻倍，已修）；end 只 EndLevelUp")
    ap.add_argument("--unlock-spaces", type=int, default=-1, metavar="N",
                    help="升级界面里点几个 GridExtension 扩背包；**默认 -1 = 自动**"
                         "（问游戏本级给了几格，真机 2026-10-29 Purse 第 0 级给 4 格，9->13）。"
                         "0 = 本次升级不扩格子")
    ap.add_argument("--grant-spaces", type=int, default=0, metavar="N",
                    help="**测试钩子**（正常情况下不需要）：先 ChangeSpaces(N)+SetupSpaces 硬造配额。"
                         "扩容其实是正常的：Player.IncrementLevel 会把本级 rewards[0].rewardValue"
                         " 加进 spacesSaved，所以别再用这个钩子污染状态（会把 numberToUnlock 刷爆）")
    ap.add_argument("--auto-retry", action="store_true",
                    help="阵亡后调 GameManager.Retry() 再开一局（需要新桥接的 retry_run）")
    ap.add_argument("--max-retries", type=int, default=8,
                    help="--auto-retry 最多重开几次")
    ap.add_argument("--make-room", type=int, default=0, metavar="N",
                    help="背包放不下战利品时，最多丢 N 件最低价值的背包物品来腾位。"
                         "0 = 不丢（老行为：放不下就放弃）。判断价值用账本均值，"
                         "没测过的物品不当作 0 分（不会第一个被丢）")
    ap.add_argument("--stop-at-special", type=int, default=0, metavar="N",
                    help="**调试钩子**：遇到第 N 次「特殊整理」（in_special_reorg）就停下，"
                         "把游戏留在那个阶段，方便手工/脚本验证 hazard 放置（E25）。0 = 不停。")
    ap.add_argument("--probe-hazard", action="store_true",
                    help="**调试钩子**：遇到特殊整理时，绕开 planner 直接对 hazard 道具发一次 "
                         "place_item，把 ack 记进日志——用来确认这个阶段到底让不让放（E25）。")
    ap.add_argument("--hazard-policy", default="skip",
                    choices=["auto", "place", "skip", "alternate"],
                    help="特殊整理里网格外的 hazard 道具要不要塞回网格（E25）。"
                         "**默认 skip = 付钱**：2026-10-30 实测「塞成功」之后游戏会卡在"
                         "inventory_phase=3 / in_special_reorg=false，finish 与 skip 都 no-op、"
                         "seq 冻结（2/2 局），整局报废——比付 2-4 点血更差。"
                         "place/auto = 仍然去试（只在不丢东西的前提下），alternate = 奇数次塞。")
    args = ap.parse_args(argv)

    flags = set_flags(Flags(
        aware_block=args.aware_block,
        ratio_energy=args.ratio_energy,
        explore_threat=args.explore_threat,
        value_energy=args.value_energy,
        value_status=args.value_status,
        focus_fire=args.focus_fire,
        sustain=args.sustain,
    ))
    print(f"policy flags: {flags_label(flags)}")

    bridge = Bridge(args.runtime)
    ledger = Ledger(args.ledger)
    # 静态道具库（903 件，data/item-catalog.json）：只有 --sustain 才用，
    # 这样 A/B 的两组除了「续航」这一项之外完全一致。
    itemdb = ItemDB.load() if args.sustain else None
    if itemdb is not None:
        print(f"itemdb: {len(itemdb)} 件（sustain {len(itemdb.by_category('sustain'))} 件）")

    runs_dir = HERE.parent / "data" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    log_path = runs_dir / ("loop-" + time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
    log = log_path.open("w", encoding="utf-8")

    explored: set[str] = set()
    tried_rooms: set[str] = set()
    blocked_events: set[str] = set()   # 确认做不了的事件（地图开着时的 start_event）
    bad_rooms: set[str] = set()        # legal 里有、但 move_to_room 实际走不到的房间
    bad_edges: set[tuple[str, str]] = set()   # 房间图上「看着通、实测走不通」的边
    move_from = ""                     # 上一次 move_to_room 是从哪个房间出发的
    place_skips: dict[str, int] = {}   # place_item 失败后换候选格的重试计数
    hazard_tried: set[str] = set()     # 本次「特殊整理」里已经试过塞回网格的 hazard uid（E25）
    special_seen = 0                   # 遇到过几次特殊整理（--stop-at-special 用）
    special_prev_active = False        # 上一帧是否在特殊整理里（用来数「第几次」）
    hazard_warned = False              # --hazard-policy 允许塞时的警告只打一次
    start_fails: dict[str, int] = {}   # start_event 空操作次数（2 次就拉黑事件）
    close_map_tries = 0                # 同一次「地图开着」里试过几次 close_map
    arrivals: dict[str, int] = {}      # 每个房间到过几次（用来终结「两房间来回走」）
    pick_moves: dict[str, int] = {}    # 同一个目标房间已经走了几步（防「磨到天荒地老」）
    recent_rooms: list[str] = []       # 最近走过的房间（检测来回走）
    bad_uids: dict[str, Any] = {}      # uid -> 在哪个回合里失败过（只在本回合内剔除）
    level_attempt = 0
    unlock_left = args.unlock_spaces            # -1 = 自动：这次升级能点几格就点几格
    grant_left = max(0, args.grant_spaces)     # 测试钩子：还要塞多少配额
    nochange_streak = 0
    reorg_fails = 0                 # 连续多少次 finish_reorganize 没生效（E9）
    steps = 0
    retries = 0
    started = time.monotonic()
    last_st: dict[str, Any] | None = None
    max_floor: int | None = None

    def record(obj: dict[str, Any]) -> None:
        log.write(json.dumps(obj, ensure_ascii=False) + "\n")
        log.flush()

    # ---------------- 放战利品（含腾位） ----------------
    # 桥接的 place_item 只会按「当前朝向」扫空格，满包/碎片化时直接报 no fitting cell。
    # 现在走 agent/place.py：probe_fit 拿几何表 -> agent/backpack.py 搜落点
    # （4 朝向 × 所有格）-> 需要就先挪/换一件背包物品 -> 还不行就按 --make-room
    # 丢最低价值的 -> 再放。整条链的每一跳都在 ack 里留痕。
    drops_left = max(0, args.make_room)
    loot_mode = False       # 正在「战后战利品限时挑选」里
    loot_taken = 0          # 本段战利品已经拿了几件（配额由游戏给）

    def try_place(base: dict[str, Any], allow_drops: bool = True) -> dict[str, Any] | None:
        """尽力放一件战利品。返回最后一次 ack；None = 这件不放了。

        `allow_drops=False`：不放「为它丢别的东西」这条路（E25 的 hazard 道具用）——
        塞得进去就白赚，塞不进去就按游戏规则付那 2-5 点血，不值得拿主武器去换。
        """
        nonlocal drops_left
        res = place_with_room(
            bridge, base.get("uid", ""),
            name=base.get("name", "") or "",
            ledger=ledger,
            db=itemdb,
            # 战利品环节允许丢，但**整段最多丢一件**：真机为了塞 2-3 格的法杖
            # 来回丢 DUELING SWORD / DARK WAND，拿了又丢净亏。丢谁仍由价值表决定。
            drops_left=drops_left if allow_drops else 0,
            log=lambda s: print(f"     {s}"),
            record=record,
        )
        if allow_drops:
            drops_left = res.drops_left
        if res.reason == "no probe":
            # 探针不可用（老桥接 / 形状算不出来）：退回原来的「直接发一次」。
            return bridge.act(base, timeout_ms=20000)
        return res.ack

    print(f"loop log: {log_path}")
    record({"event": "start", "flags": flags_label(flags),
            "flags_detail": {"aware_block": flags.aware_block,
                             "ratio_energy": flags.ratio_energy,
                             "explore_threat": flags.explore_threat,
                             "value_energy": flags.value_energy,
                             "value_status": flags.value_status,
                             "focus_fire": flags.focus_fire,
                             "sustain": flags.sustain},
            "ledger": str(ledger.path), "ledger_items": sorted(ledger.data.keys()),
            "max_steps": args.max_steps})

    try:
        while steps < args.max_steps:
            if time.monotonic() - started > args.max_seconds:
                print("!! 超过总时限，停")
                break

            try:
                st = bridge.state()
            except BridgeError as exc:
                print(f"!! 读状态失败：{exc}")
                time.sleep(0.3)
                continue

            mode = st.get("mode")
            hp = (st.get("player") or {}).get("hp")
            enemies = st.get("enemies") or []
            ui = st.get("ui") or {}
            last_st = st
            was_loot = loot_mode
            loot_mode = bool(ui.get("limited_item_reorganize"))
            if loot_mode and not was_loot:
                loot_taken = 0
                # 战利品值得丢 1–2 件换（配额 3 件，拿到的通常是升级）。真机：
                # 背包 free=0 时 WARM DRINK 就在眼前却因为丢弃额度用完而拿不到。
                # 丢谁仍由价值表 + place.py 的「比要捡的贵就不丢」兜着。
                drops_left = 1
                print(f"     （战后战利品：可拿 {ui.get('items_allowed_to_take')}"
                      f"/{ui.get('total_items_allowed')} 件，地上 "
                      f"{(st.get('backpack') or {}).get('items_loose')} 件）")
            f = (st.get("run") or {}).get("floor")
            if isinstance(f, int) and f >= 0:
                max_floor = f if max_floor is None else max(max_floor, f)

            print(f"\n[step {steps}] seq={st.get('seq')} mode={mode} hp={hp} "
                  f"enemies={len(enemies)} in_grid={(st.get('backpack') or {}).get('items_in_grid')} "
                  f"loose={(st.get('backpack') or {}).get('items_loose')}")

            if mode == "menu":
                # 主菜单上 hp 是 -1（哨兵），不能当阵亡
                print("回到主菜单，结束")
                break

            if mode == "dead" or (hp is not None and hp <= 0):
                print("玩家已阵亡，结束")
                record({"event": "dead", "seq": st.get("seq"), **_scorecard(st)})
                if args.auto_retry and retries < args.max_retries:
                    # 桥接没有「回主菜单」的动作，但 GameManager.Retry() 是 public。
                    # 成功的话整局循环就能无人值守地跑完 A/B。
                    retries += 1
                    print(f"     尝试 retry_run 重开第 {retries} 次…")
                    ack = bridge.act({"type": "retry_run"}, timeout_ms=30000)
                    record({"event": "retry", "n": retries, "ok": ack.get("ok"),
                            "changed": ack.get("changed"), "error": ack.get("error")})
                    if ack.get("changed"):
                        explored.clear()
                        tried_rooms.clear()
                        blocked_events.clear()
                        bad_rooms.clear()
                        bad_edges.clear()
                        arrivals.clear()
                        pick_moves.clear()
                        recent_rooms.clear()
                        place_skips.clear()
                        level_attempt = 0
                        nochange_streak = 0
                        reorg_fails = 0
                        continue
                    print(f"     retry 没生效（{ack.get('error')}），停")
                break

            # ---------------- 战斗 / 整理 ----------------
            # 「点了、但状态这一帧看不出变化」的动作（典型：升级界面点 GridExtension，
            # 格子数要等 finish_level_up 才 +N）。这类动作不能算进「卡死」计数，
            # 否则点 4 次扩容就把自己判定成卡死（真机 39 步时踩到）。
            deferred_ok = False

            if mode in ("battle", "reorganize"):
                if not st.get("legal"):
                    print("  等玩家回合（legal 为空）")
                    time.sleep(0.3)
                    steps += 1
                    continue

                # 桥接的 legal 会列进「当前其实用不了」的物品（实测 SUNFLOWER SHIV
                # 0 费、能量也够，但每次 use_item 都 ok=false）。同一回合里失败过的
                # 物品先剔掉，否则会在同一个动作上连撞 4 次被判卡死。
                turn_now = (st.get("run") or {}).get("turn")
                legal_now = st.get("legal") or []
                kept = [a for a in legal_now
                        if not (a.get("type") == "use_item"
                                and bad_uids.get(a.get("uid")) == turn_now)]
                if len(kept) != len(legal_now):
                    st = dict(st)
                    st["legal"] = kept
                    print(f"     （本回合已剔除用不了的物品 {len(legal_now) - len(kept)} 个）")

                action, why = choose(st, ledger, explored, flags, itemdb)
                if action is None:
                    print(f"没有可做的动作：{why}")
                    break

                # ---- E9 的死状态：phase=3 但 in_special_reorg=false ----
                # `FinishReorganizeButton()` 第一条就是 `if (!inSpecialReorg) return;`，
                # 所以这个状态下点多少次都不会有变化（真机上每次还要等 20s 超时）。
                # 以前这条路只在「连续 no-op 之后」才兜底，而且只在 in_special_reorg=true
                # 时发 skip；现在把 hazard 塞回网格后，游戏自己会把 inSpecialReorg 置 false
                # 而 phase 仍是 3 —— 直接发 `skip_reorganize` 才能出这个阶段。
                _ui0 = st.get("ui") or {}
                if (action.get("type") in ("finish_reorganize", "skip_reorganize")
                        and _ui0.get("inventory_phase") == 3
                        and not _ui0.get("in_special_reorg")
                        and not _ui0.get("limited_item_reorganize")
                        and action.get("type") == "finish_reorganize"):
                    print("     （phase=3 但 in_special_reorg=false：finish 一定 no-op，"
                          "改发 skip_reorganize（E9））")
                    record({"event": "reorg_skip_e9", "seq": st.get("seq"),
                            "inventory_phase": _ui0.get("inventory_phase"),
                            "in_special_reorg": _ui0.get("in_special_reorg")})
                    action = {"type": "skip_reorganize"}
                    why = "E9: phase=3 且 in_special_reorg=false，finish 无效"

                # ---- E25：特殊整理的 hazard 道具（扣血的真正来源）----
                # `FinishReorganizeButton()` 会调 `GetDamageForHazards`：
                #   网格外、`itemType` 含 Hazard(52) 的道具，第 k 个扣 (k + floor + cursesSkipped)，
                #   结束后 `cursesSkipped += 数量` —— 同一局第二次是 3、第三次 4、……越拖越贵，
                #   而且道具会被销毁。以前的做法是直接点「结束整理」，等于每局白送 14-15 血。
                # 这些道具不在 `backpack.items` 里（那只列网格内的），只能看 `ui.special_reorg`。
                _hz = hazard_place_action(st, hazard_tried)
                _sr_now = (st.get("ui") or {}).get("special_reorg") or {}
                _sr_active = bool(_sr_now.get("active"))
                if _sr_active and not special_prev_active:
                    special_seen += 1
                    if args.probe_hazard:
                        # 只探一次：直接发 place_item，看游戏在「特殊整理进行中」让不让放。
                        _probe = [it for it in (_sr_now.get("items") or []) if it.get("hazard")]
                        if _probe:
                            _pu = _probe[0]["uid"]
                            _pa = bridge.act({"type": "place_item", "uid": _pu}, timeout_ms=20000)
                            _after = bridge.state()
                            _sr2 = (_after.get("ui") or {}).get("special_reorg") or {}
                            print(f"     !! probe-hazard: place {_pu} ok={_pa.get('ok')} "
                                  f"changed={_pa.get('changed')} err={_pa.get('error')}")
                            print(f"     !! probe-hazard: after -> free="
                                  f"{(_after.get('backpack') or {}).get('free_squares')} "
                                  f"hazards_outside={_sr2.get('hazards_outside')} "
                                  f"damage={_sr2.get('damage')}")
                            record({"event": "hazard_probe", "seq": _after.get("seq"),
                                    "uid": _pu, "ok": _pa.get("ok"),
                                    "changed": _pa.get("changed"), "error": _pa.get("error"),
                                    "delta": _pa.get("delta"),
                                    "free_after": (_after.get("backpack") or {}).get("free_squares"),
                                    "hazards_outside_after": _sr2.get("hazards_outside"),
                                    "damage_after": _sr2.get("damage")})
                    if args.stop_at_special and special_seen >= args.stop_at_special:
                        print(f"     !! --stop-at-special：停在第 {special_seen} 次特殊整理"
                              f"（damage={_sr_now.get('damage')} "
                              f"free={(st.get('backpack') or {}).get('free_squares')}）")
                        record({"event": "special_stop", "seq": st.get("seq"),
                                "n": special_seen, "special_reorg": _sr_now,
                                "free_squares": (st.get("backpack") or {}).get("free_squares"),
                                "items": (st.get("backpack") or {}).get("items")})
                        break
                special_prev_active = _sr_active
                if _hz is not None and action.get("type") == "finish_reorganize":
                    # ① 经济性实验：「塞」vs「不塞」由 --hazard-policy 决定。
                    # 每次特殊整理记一条 hazard_event（含 damage / free / 选了哪边），
                    # 事后用 tools/ab_hazard.py 按「后面 3 场掉血」来比。
                    _want_place = (args.hazard_policy in ("place", "auto")
                                   or (args.hazard_policy == "alternate"
                                       and (special_seen % 2) == 1))
                    if _want_place and not hazard_warned:
                        hazard_warned = True
                        print("     !! --hazard-policy 允许塞 hazard：实测塞成功后游戏会卡在"
                              "phase=3 / in_special_reorg=false（E25），整局可能报废")
                    if not _want_place:
                        print(f"     （特殊整理：按 --hazard-policy={args.hazard_policy} 不塞，"
                              f"付 {_hz.get('_damage')} 血）")
                        record({"event": "hazard_event", "seq": st.get("seq"),
                                "n": special_seen, "choice": "skip",
                                "uid": _hz.get("uid"), "name": _hz.get("name"),
                                "damage": _hz.get("_damage"),
                                "hazards_outside": _sr_now.get("hazards_outside"),
                                "curses_skipped": _sr_now.get("curses_skipped"),
                                "free_squares": (st.get("backpack") or {}).get("free_squares"),
                                "hp": (st.get("player") or {}).get("hp")})
                    else:
                        print(f"     （特殊整理：先把 {_hz.get('name')} 塞回网格，"
                              f"否则结束整理扣 {_hz.get('_damage')} 血）")
                        record({"event": "hazard_event", "seq": st.get("seq"),
                                "n": special_seen, "choice": "place",
                                "uid": _hz.get("uid"), "name": _hz.get("name"),
                                "damage": _hz.get("_damage"),
                                "hazards_outside": _sr_now.get("hazards_outside"),
                                "curses_skipped": _sr_now.get("curses_skipped"),
                                "free_squares": (st.get("backpack") or {}).get("free_squares"),
                                "hp": (st.get("player") or {}).get("hp")})
                        why = f"special_reorg hazard（躲开 {_hz.get('_damage')} 点血）"
                        action = _hz

                print(f"  -> {action.get('type')} {action.get('uid', '')}  ({why})")
                record({"event": "decide", "seq": st.get("seq"), "action": action, "why": why})

                # 决策级日志：把「当轮完整 state + 每个候选的分数」一起落盘。
                # 没有这份原料，事后就没法复盘「当时为什么选这个」——
                # 也正是 SPIKE-07 里做 A/B 最缺的东西。
                if args.dump_decisions:
                    cands = []
                    for a in st.get("legal") or []:
                        sc, wy = score_action(a, st, ledger, explored, flags, itemdb)
                        cands.append({"type": a.get("type"), "uid": a.get("uid"),
                                      "score": round(sc, 3), "why": wy})
                    record({"event": "decision", "seq": st.get("seq"),
                            "chosen": action, "candidates": cands, "state": st,
                            "flags": flags_label(flags)})

                if args.dry_run:
                    break

                pre_hp = {e["uid"]: e.get("hp", 0) for e in enemies}
                item_name = None
                if action.get("type") == "use_item":
                    for it in (st.get("backpack") or {}).get("items", []):
                        if it["uid"] == action.get("uid"):
                            item_name = it.get("name", "")
                            break

                if action.get("type") == "place_item":
                    if action.get("_hazard"):
                        # E25：特殊整理的道具是放到**游戏给的特殊区域**里的，不走主背包。
                        # 所以别用 planner 的「主网格有没有空位」去拦它——实测 free=0
                        # 时游戏照样接受（place_item ok=True），放进去后 hazards_outside 立刻归零。
                        _act = {"type": "place_item", "uid": action.get("uid")}
                        ack = bridge.act(_act, timeout_ms=20000)
                        record({"event": "act", "action": _act, "ok": ack.get("ok"),
                                "changed": ack.get("changed"), "ms": ack.get("ms"),
                                "delta": ack.get("delta"), "error": ack.get("error"),
                                "hazard": True})
                        print(f"     ok={ack.get('ok')} changed={ack.get('changed')} "
                              f"err={ack.get('error')}")
                    else:
                        # 走 planner：4 朝向搜落点 + 需要时挪/换/丢（--make-room）
                        _sk = place_skips.get(action.get("uid", ""), 0)
                        if _sk:
                            action = dict(action)
                            action["skip"] = _sk
                        ack = try_place(action, allow_drops=not action.get("_hazard"))
                        if ack is None:
                            explored.add(f"place:{action.get('uid', '')}")
                            steps += 1
                            continue
                        if loot_mode and ack.get("changed"):
                            loot_taken += 1
                else:
                    ack = bridge.act(action, timeout_ms=20000)
                delta = ack.get("delta") or {}
                # place_item 的 ack 已经在 try_place 里记过（带 plan/cell/rot），别记两遍
                if action.get("type") != "place_item":
                    record({"event": "act", "action": action, "ok": ack.get("ok"),
                            "changed": ack.get("changed"), "ms": ack.get("ms"),
                            "delta": delta, "receipt": ack.get("receipt"),
                            "error": ack.get("error")})
                    print(f"     ok={ack.get('ok')} changed={ack.get('changed')} "
                          f"ms={ack.get('ms')} delta={json.dumps(delta, ensure_ascii=False)}")
                if ack.get("error"):
                    print(f"     error={ack['error']}")

                # 用不掉的物品：记下「本回合失败」，下一次 choose 会把它剔掉。
                if action.get("type") == "use_item" and not ack.get("changed"):
                    bad_uids[action.get("uid")] = turn_now
                    print("     （这个物品用不了，本回合不再选它）")

                if item_name:
                    # 只有「真的发生了可见变化」才进账本。
                    # 以前不管成没成都记一条——一次失败的使用会被永久记成 0/0 均值，
                    # 等于把「没测到」当成「测出来是 0」，物品就再也翻不了身了。
                    verified = ack.get("ok") and ack.get("changed")
                    if verified or args.record_unverified:
                        # 净回能量：回能量的消耗品全靠这一项才在打分里存在（见 policy §4）。
                        # 只记**正**的净收益：普通攻击物品的 -1 是「花掉的费用」，
                        # 那一项由 policy 的 ENERGY_WEIGHT 管，记进来会重复扣一次。
                        gain = delta.get("player_energy")
                        gain = gain if isinstance(gain, int) and gain > 0 else 0
                        ledger.record_use(item_name, _damage_from(delta, pre_hp),
                                          max(0, int(delta.get("player_block", 0) or 0)),
                                          _absorbed_from(delta), energy=gain,
                                          receipt=ack.get("receipt"))
                        ledger.save()
                    explored.add(item_name.strip())

                if action.get("type") == "unlock_spaces":
                    _d = ack.get("diag") or {}
                    _clicked = _d.get("clicked")
                    _clicked = _clicked if isinstance(_clicked, int) else 0
                    unlock_left = max(0, unlock_left - _clicked)
                    # 点格子这一帧看不出变化（grid 要等 finish_level_up 才 +N），
                    # 别让它被算进「卡死」计数。
                    deferred_ok = deferred_ok or _clicked > 0
                    print(f"     （扩容：点了 {_clicked} 个格子 -> grid "
                          f"{_d.get('grid_squares_before')}→{_d.get('grid_squares_after')}，"
                          f"剩余配额 {unlock_left}）")
                    if _clicked <= 0:
                        # 没配额/没候选：本次升级不加格子了，免得死循环
                        unlock_left = 0
                        print("     （扩容：没有可点的 GridExtension，改成正常收尾）")
                if action.get("type") == "grant_spaces":
                    _d = ack.get("diag") or {}
                    print(f"     （配额 {_d.get('number_to_unlock_before')}→"
                          f"{_d.get('number_to_unlock_after')}）")

                if action.get("type") == "place_item" and ack.get("changed"):
                    place_skips.pop(action.get("uid", ""), None)
                if action.get("type") == "place_item" and not ack.get("changed"):
                    uid = action.get("uid", "")
                    # 摆不进去是**可恢复**的失败：换候选格 / 记进 explored 就完事，
                    # 不该计入「卡死」。否则两件放不进去的战利品就能把整局判死
                    # （真机 167 步时踩到：cell 1,0 footprint hits occupied 1,-1）。
                    deferred_ok = True
                    if place_skips.get(uid, 0) < 1:
                        # 桥接会按形状挑候选格；第一个候选失败时换下一个再试一次。
                        place_skips[uid] = place_skips.get(uid, 0) + 1
                        print("     摆放失败（形状/遮挡），换候选格重试一次")
                    else:
                        explored.add(f"place:{uid}")
                        place_skips.pop(uid, None)
                if action.get("type") == "finish_level_up" and not ack.get("changed"):
                    print("     升级界面收不掉，下一轮再试")

                # PITFALLS E9：inventory_phase=3 但 inSpecialReorg=false 时，
                # FinishReorganizeButton() 第一行就 return，点多少次都没用。
                # 偶发一次的交回下一轮重试；连续两次仍 no-op 且有 --auto-retry
                # 就用 retry_run 重开，**不要**硬置 inventoryPhase（会跳过清理）。
                if action.get("type") == "finish_reorganize":
                    if ack.get("changed"):
                        reorg_fails = 0
                    else:
                        reorg_fails += 1
                        ui_now = st.get("ui") or {}
                        print(f"     整理收尾没生效（第 {reorg_fails} 次，"
                              f"in_special_reorg={ui_now.get('in_special_reorg')}）")
                        if not ack.get("changed"):
                            # 特殊整理（污染/诅咒 / hazard 塞完）时会落到这里：
                            # `FinishReorganizeButton` 在 `!inSpecialReorg` 时第一行 return，
                            # 所以 **in_special_reorg 为真或为假都可能 no-op**，一律试 skip。
                            # `SkipReorganizeButton` 实测能直接退出这个阶段，代价是留在原地的
                            # 道具被销毁——比整局卡死好（E9 / E25）。
                            ack3 = bridge.act({"type": "skip_reorganize"}, timeout_ms=15000)
                            record({"event": "reorg_skip", "fails": reorg_fails,
                                    "ok": ack3.get("ok"), "changed": ack3.get("changed"),
                                    "in_special_reorg": ui_now.get("in_special_reorg"),
                                    "error": ack3.get("error")})
                            print(f"     整理收尾 no-op：改发 skip_reorganize -> "
                                  f"ok={ack3.get('ok')} changed={ack3.get('changed')}")
                            if ack3.get("changed"):
                                reorg_fails = 0
                        if (reorg_fails >= 2 and args.auto_retry
                                and retries < args.max_retries):
                            retries += 1
                            print(f"     连续 {reorg_fails} 次 no-op，retry_run 重开（E9 兜底）")
                            ack2 = bridge.act({"type": "retry_run"}, timeout_ms=30000)
                            record({"event": "reorg_retry", "n": retries,
                                    "fails": reorg_fails,
                                    "in_special_reorg": ui_now.get("in_special_reorg"),
                                    "ok": ack2.get("ok"), "changed": ack2.get("changed"),
                                    "error": ack2.get("error")})
                            if ack2.get("changed"):
                                explored.clear()
                                tried_rooms.clear()
                                blocked_events.clear()
                                bad_rooms.clear()
                                bad_edges.clear()
                                arrivals.clear()
                                pick_moves.clear()
                                recent_rooms.clear()
                                place_skips.clear()
                                level_attempt = 0
                                nochange_streak = 0
                                reorg_fails = 0
                                steps += 1
                                continue
                            print(f"     retry_run 没生效（{ack2.get('error')}），继续重试")

                nochange_streak = 0 if (ack.get("changed") or deferred_ok) \
                    else nochange_streak + 1
                if nochange_streak >= MAX_CONSECUTIVE_NOCHANGE:
                    print("!! 连续多次没变化，判定卡死，停")
                    record({"event": "stuck", "streak": nochange_streak})
                    break

                steps += 1
                continue

            # ---------------- 地图 ----------------
            legal = st.get("legal") or []
            types = {a.get("type") for a in legal}

            # 升级界面真的没了才重置重试计数；只要还有 pending 的升级
            # （level_up 或 finish_level_up 还合法），就继续算同一次。
            if "finish_level_up" not in types and "level_up" not in types:
                level_attempt = 0

            if ui.get("viewing_top") != 1:
                close_map_tries = 0

            # 地图开着时 StartEvent 是空操作（SPIKE-08 §8）。先试 close_map；
            # 只有 close_map 连续失败两次，才把这个事件拉黑、换个目标。
            # （以前是一看到「地图开着 + start_event 合法」就拉黑，close_map 根本没机会跑。）
            if "start_event" in types and ui.get("viewing_top") == 1 and close_map_tries >= 2:
                ev_uid = ((st.get("map") or {}).get("player") or {}).get("event")
                if ev_uid and ev_uid not in blocked_events:
                    blocked_events.add(ev_uid)
                    print(f"     （地图开着，{ev_uid} 开不了；拉黑它，换个目标）")
                    record({"event": "event_blocked", "seq": st.get("seq"), "event_uid": ev_uid})

            action: dict[str, Any] | None = None

            # 当前房间里那个事件（以及它是不是已被判定「做不了」）。
            # SimpleClickItem / Chance 这类「点物体」的事件，start_event 一定是空操作，
            # 拉黑之后必须真的**不再发** start_event，否则会原地重试到判卡死。
            _cur_ev = _current_room_event(st)
            _cur_ev_uid = (_cur_ev or {}).get("uid") or ""
            _cur_ev_blocked = bool(_cur_ev_uid and _cur_ev_uid in blocked_events)

            if "finish_level_up" in types:
                # 实测（HANDOFF §0）：**只有** `Select()`×numberToUnlock 再 `EndLevelUp()`
                # 真的能收掉升级界面；`ChangeSpaces` 和「只调 EndLevelUp」都会
                # 「关掉又弹回来」。所以默认固定走 select，别按次数升级到别的备选。
                #
                # 之前这里把 level_attempt 当成「全局第几次升级」，
                # 第一次用 select、第二次 spaces、之后一直是 end —— 于是从第二个升级起
                # 必然陷入「level_up → finish_level_up → 又 level_up」的死循环。
                # 现在 level_attempt 只在**同一次升级**里累加，界面一关就归零。
                level_attempt += 1
                if level_attempt > MAX_LEVEL_ATTEMPTS:
                    print(f"升级界面第 {level_attempt} 次仍收不掉，停下（需要人工看 UI）")
                    record({"event": "level_up_stuck", "attempts": level_attempt})
                    break
                lvia = args.level_up_via
                if unlock_left < 0:
                    # 自动扩容：升级界面每次给几格由游戏自己算
                    # （`Player.IncrementLevel` 把 levelUps[level].rewards[0].rewardValue
                    # 加进 `spacesSaved`，`LevelUp` 再把它写进 `numberToUnlock`）。
                    # 真机 2026-10-29：Purse 第 0 级给 4 格，9 → 13。
                    _d = (bridge.act({"type": "diag_levelups"}, timeout_ms=8000).get("diag") or {})
                    _ntu = _d.get("number_to_unlock")
                    unlock_left = _ntu if isinstance(_ntu, int) and _ntu > 0 else 0
                    print(f"     （升级界面：本级可扩 {unlock_left} 格"
                          f"（spaces_saved={_d.get('spaces_saved')}））")
                if grant_left > 0:
                    action = {"type": "grant_spaces", "n": grant_left}
                    grant_left = 0
                    print("     （测试钩子：先塞扩容配额）")
                elif unlock_left > 0:
                    # 扩容：升级界面里点 GridExtension 加格子（点一个 = 消耗 1 个配额）。
                    # **一次只点一个**：Click 后 SetupSpaces 会重建候选对象，
                    # 同一帧里连点会点到已销毁的 extension（配额扣了、格子没加）。
                    # 动作之间本来就有 settle（~0.5s），正好让新对象注册完。
                    action = {"type": "unlock_spaces", "n": 1}
                    print(f"     （扩容：点 1 个 GridExtension，剩余 {unlock_left}）")
                elif level_attempt > 2 and lvia == "select":
                    # 兜底：连续两次 select 没关掉，再给它一次「少选一格」的机会
                    # （numberToUnlock 可能算多了，多点一下会把已选中的取消）。
                    action = {"type": "finish_level_up", "via": "select",
                              "times": max(1, level_attempt - 2)}
                else:
                    action = {"type": "finish_level_up", "via": lvia}
                if action.get("type") == "unlock_spaces":
                    # 扩完必须用 end 收尾（Select 会再扣配额）
                    lvia = "end"
                    print(f"     （升级收尾第 {level_attempt} 次，via=end）")
                else:
                    print(f"     （升级收尾第 {level_attempt} 次，via={action.get('via')}"
                          f"{'，times=' + str(action.get('times')) if action.get('times') else ''}）")
            elif "close_popup" in types:
                action = {"type": "close_popup"}
            elif ui.get("select_item_dialogues"):
                # 战后奖励 / 事件里的「N 选 1」：桥接的 pick_dialogue。
                # 候选名现在在 ui.dialogue_items 里（v8.22）；开了 --sustain 就按类别挑：
                # 缺血先拿续航，否则按「输出/费用」拿最划算的。
                n = ui.get("dialogue_choices")
                names = ui.get("dialogue_items") or []
                idx = 0
                if args.sustain and isinstance(names, list) and names:
                    hp = int((st.get("player") or {}).get("hp") or 0)
                    max_hp = int((st.get("player") or {}).get("max_hp") or 0)
                    idx = pick_reward([str(x) for x in names], itemdb, hp, max_hp)
                action = {"type": "pick_dialogue", "index": idx}
                picked = names[idx] if isinstance(names, list) and 0 <= idx < len(names) else "?"
                print(f"     （N 选 1 对话框，候选 {n} 个：{names}，拿第 {idx} 个 {picked}）")
            elif (ui.get("after_battle_button") or ui.get("inventory_phase") == 8) \
                    and not ui.get("battle_button_pressed") and not ui.get("leveling_up"):
                # 胜利结算：真正的入口是 AfterBattleButtonPressed，不是 LevelUpButton。
                # IL 里它才会 SpawnGoldAndMana + StartLimitedItemGetPeriod（摆 7 件战利品、
                # 允许拿 3 件）。以前这里直接发 level_up，战利品环节整段被跳过。
                #
                # 注意：如果升级界面开着，AfterBattleButtonPressed 第一行就 return
                # （`if (levelingUp) return`），所以要先让它把升级收掉、再点一次。
                # 判定用 after_battle_button 是否还在，而不是 inventory_phase==8：
                # 升级收完后 phase 已经回到 0，但按钮还在，这时才会开战利品。
                action = {"type": "after_battle"}
                print("     （胜利结算：after_battle（会开战利品环节））")
            elif "level_up" in types:
                action = {"type": "level_up"}
            elif ui.get("limited_item_reorganize") and (
                    (isinstance(ui.get("items_allowed_to_take"), int)
                     and ui.get("items_allowed_to_take") <= 0)
                    or loot_taken >= max(1, int(ui.get("total_items_allowed") or 3))):
                # 配额拿满了（游戏只让拿 3 件）或已经拿够：收掉限时挑选。
                # 继续放会被 StartDrag 拒绝（IsComplete=True），白等。
                action = {"type": "finish_item_selection"}
                print(f"     （战利品：已拿 {loot_taken} 件，配额 "
                      f"{ui.get('total_items_allowed')}，收尾）")
            elif any(a.get("type") == "place_item"
                     and f"place:{a.get('uid')}" not in explored for a in legal):
                _cands = [a for a in legal if a.get("type") == "place_item"
                          and f"place:{a.get('uid')}" not in explored]
                if loot_mode and itemdb is not None and len(_cands) > 1:
                    # 战利品只有 3 个名额，按「拿了值不值」挑：缺血先拿续航，
                    # 否则按输出/费用。游戏给的顺序不可靠（真机把回血消耗品排在后面）。
                    _hp = int((st.get("player") or {}).get("hp") or 0)
                    _max = int((st.get("player") or {}).get("max_hp") or 0)
                    _free = int((st.get("backpack") or {}).get("free_squares") or 0)

                    def _loot_key(a: dict[str, Any]) -> tuple[int, float]:
                        _st = itemdb.get(str(a.get("name") or ""))
                        _fits = 1 if (_st is not None and 0 < _st.cells <= _free) else 0
                        return (_fits, reward_score(str(a.get("name") or ""),
                                                    itemdb, _hp, _max))

                    _cands.sort(key=_loot_key, reverse=True)
                pa = _cands[0]
                _sk = place_skips.get(pa.get("uid", ""), 0)
                if _sk:
                    pa = dict(pa)
                    pa["skip"] = _sk
                action = pa
            elif ui.get("limited_item_reorganize"):
                # 战利品拿完了（或再也放不下）：收掉限时挑选，别把 7 件留在地上。
                action = {"type": "finish_item_selection"}
                print(f"     （战利品：还能拿 {ui.get('items_allowed_to_take')} 件，收尾）")
            elif "open_chest" in types:
                # 真 Chest 的 StartEvent 是 no-op；桥接新增的 open_chest 走 Chest.OpenChest()。
                action = next(a for a in legal if a.get("type") == "open_chest")
            elif "enter_door" in types:
                # 站在出口房间里：先去下一层，不要傻站着
                action = next(a for a in legal if a.get("type") == "enter_door")
            elif not _cur_ev_blocked and _cur_ev is not None and _cur_ev.get("type") == 11 \
                    and ("start_event" in types or "play_event" in types):
                # SimpleClickItem（地图上的「检」字块）：`start_event` 对它**永远是空操作**
                # （E15），真正入口是 `play_event`（Play 幂等 + Click）。
                # 真机：QuickBoost XP/Gold/HP 都是这个形状，点一下就进账。
                action = {"type": "play_event", "uid": _cur_ev_uid}
                print(f"     （SimpleClickItem {_cur_ev_uid}：start_event 是空操作，"
                      f"改走 play_event）")
            elif "start_event" in types and ui.get("viewing_top") != 1 and not _cur_ev_blocked:
                action = next(a for a in legal if a.get("type") == "start_event")
            elif "start_event" in types and close_map_tries < 2 and not _cur_ev_blocked:
                # 地图开着时 StartEvent 是空操作（SPIKE-08 §8）。桥接新增了 close_map
                # （走 nonpublic 的 mapInputHandler，最后手段是直接把 viewingTop 拨回 inventory）。
                # 但只试两次：万一这个版本不生效，就退回「拉黑这个事件、换房间」，
                # 不能一直重试同一条死路。
                close_map_tries += 1
                action = {"type": "close_map"}
                print(f"     （地图开着，start_event 无效；先 close_map，第 {close_map_tries} 次）")
            # 实测（SPIKE-08 §8）：**地图开着时 DungeonPlayer.StartEvent() 是空操作**——
            # 每次返回 ok=false / ms=400 / delta={} / seq 不动，连点 MAX_CONSECUTIVE_NOCHANGE
            # 次就被判卡死。而地图关不掉：GameManager 只有 ShowMap(force)，开着时怎么调都不关，
            # 而 `open_map` 的阶段门是 viewingTop==1，所以「关地图」这个动作根本不存在。
            # 所以上面那条**故意加了 `viewing_top != 1`**：地图开着时它不匹配，
            # 分支会继续往下落到「换房间」，避免站在宝箱格上死锁。
            # 代价是宝箱暂时不捡，等桥接补一个 close_map 再说。
            elif ui.get("inventory_phase") == 8 and "level_up" not in types:
                # 胜利画面：屏幕上明明有「升级!」按钮，但桥接的 `can_level_up` 是 false，
                # 于是 `level_up` 不进 legal，循环只能去点 `open_map` —— 而那个界面下
                # `viewingTop` 永远上不去，每次都 30 秒超时，连超 4 次就被判卡死。
                # 动作层没有合法性门（Actions.cs：level_up 直接调 LevelUpButton()），
                # 所以这里直接发。phase 8 = notInteractable，就是胜利/升级的过渡态。
                action = {"type": "level_up"}
                print("     （胜利画面卡在 phase=8，直接发 level_up 兜底）")
            elif "open_map" in types:
                action = {"type": "open_map"}
            else:
                # 挑房间：按事件优先级，其次按「没去过的」。
                # **可达性用房间图算**（`agent/mapnav.py`）：`legal` 会把走不到的房间
                # 也列出来，对它发 move_to_room 只会 30 秒超时（真机踩过两次）。
                # 房间图来自 `map.rooms` 的 pos/doors/blocked，比 legal 可靠。
                graph = RoomGraph.from_state(st, bad_edges)
                cur = ((st.get("map") or {}).get("player") or {}).get("room_at") or ""
                # 可达性用全图算（新楼层一开始大部分房间是「迷雾」，但 doors 数据是全的）；
                # 真正能不能点由 legal 决定，见下面的 first_step。
                reach = graph.reachable(cur, allow_invisible=True)
                legal_rooms = {a.get("room") for a in legal
                               if a.get("type") == "move_to_room"}
                moves = [a for a in legal if a.get("type") == "move_to_room"
                         and a.get("room") in reach and a.get("room") not in bad_rooms]
                # 到过两次的房间，它的事件就别再追了——否则「A 的事件开不了 → 去 B →
                # B 也开不了 → 回 A」会无限来回走（真机实测）。
                for _p, _room, _ev in _event_rooms(st, blocked_events):
                    if arrivals.get(_room, 0) >= 2 and _ev not in blocked_events:
                        blocked_events.add(_ev)
                        print(f"     （{_room} 到过两次事件还没完成，拉黑 {_ev}）")
                cands = [c for c in _event_rooms(st, blocked_events)
                         if c[1] in reach and c[1] not in bad_rooms]
                pick = None
                for prio, room_uid, ev_uid in cands:
                    if room_uid == cur:
                        continue
                    pick = room_uid
                    break
                if pick is None:
                    # 没有可做的事件了：优先去出口，别在两个房间之间来回走。
                    exit_rooms = {r for _p, r, _e in _event_rooms(st) if _p == EVENT_PRIORITY["Exit"]}
                    exits = [a for a in moves if a.get("room") in exit_rooms and a.get("room") != cur]
                    unvisited = [a for a in moves if a.get("room") not in tried_rooms]
                    if exits:
                        pick = exits[0].get("room")
                    elif unvisited:
                        pick = unvisited[0].get("room")
                    elif moves:
                        pick = moves[0].get("room")

                if pick is None:
                    # 图上可达但 legal 里没有（或全去过）：至少别站着不动
                    if reach - {cur}:
                        print(f"     （图上有 {len(reach) - 1} 间可达，但 legal 里没有可走的；等等看）")
                    print("没有可去的房间，结束")
                    break

                # 防「同一个目标磨到天荒地老」：真机出现过在 r-56880 / r-56866 之间
                # 来回走 85 步——目标在房间图上可达，但 legal 上永远点不到（迷雾/墙）。
                # 连走 12 步还没到，或最近 8 步里当前房间出现 3 次（明显绕圈），
                # 就把目标拉黑、换一个候选，别让整局停在这里。
                recent_rooms.append(cur)
                del recent_rooms[:-8]
                if cur in arrivals:
                    pick_moves.pop(cur, None)      # 已经到过的目标，计数清零
                pick_moves[pick] = pick_moves.get(pick, 0) + 1
                circling = recent_rooms.count(cur) >= 3
                if pick_moves[pick] > 12 or (circling and pick_moves[pick] >= 4):
                    bad_rooms.add(pick)
                    print(f"     （走向 {pick} 已走 {pick_moves[pick]} 步，还在 {cur} 附近绕；"
                          f"拉黑换目标）")
                    pick_moves.pop(pick, None)
                    recent_rooms.clear()
                    continue

                # 一次只走一格：整层 19 间的时候，直接点远处房间会被判定「走不到」。
                # 迷雾里的下一格点不了时，first_step 会挑一个「能点到的邻居」先探过去。
                hop = graph.first_step(cur, pick, legal_rooms)
                if hop is None:
                    # 理论上不会发生（pick 在 reach 里）；防御一下，别把整局拖死
                    bad_rooms.add(pick)
                    print(f"     （当前没有能点到、又朝着 {pick} 的房间，跳过）")
                    continue
                tried_rooms.add(pick)
                move_from = cur
                action = {"type": "move_to_room", "room": hop, "via": "room"}
                if hop != pick:
                    path_len = len(graph.path(cur, pick, allow_invisible=True) or [])
                    print(f"     （去 {pick}：先走到 {hop}，共 {max(0, path_len - 1)} 步）")

            print(f"  -> {action.get('type')} {action.get('room', action.get('uid', ''))}")
            record({"event": "map", "seq": st.get("seq"), "action": action})

            if args.dry_run:
                break

            if action.get("type") == "place_item":
                _sk = place_skips.get(action.get("uid", ""), 0)
                if _sk:
                    action = dict(action)
                    action["skip"] = _sk
                ack = try_place(action)
                if ack is None:
                    explored.add(f"place:{action.get('uid', '')}")
                    steps += 1
                    continue
            else:
                ack = bridge.act(action, timeout_ms=30000)
                print(f"     ok={ack.get('ok')} changed={ack.get('changed')} ms={ack.get('ms')} "
                      f"delta={json.dumps(ack.get('delta') or {}, ensure_ascii=False)}")
            if ack.get("error"):
                print(f"     error={ack['error']}")

            # SimpleClickItem 点不动（已经点过 / 被 travelling 挡住）：拉黑这个事件，
            # 否则「点不开 → 换房间 → 回来再点」会来回走（和 blocked_events 同一套）。
            if action.get("type") == "play_event" and not ack.get("changed"):
                _ev = action.get("uid") or ((ack.get("diag") or {}).get("event") or "")
                if _ev:
                    blocked_events.add(_ev)
                    print(f"     事件 {_ev} 点不动，拉黑它，换别的房间")

            # `start_event` 对「靠点物体交互」的事件（SimpleClickItem / Chance 的
            # EventNPC）永远是空操作（真机：ok=False changed=False ms=400 delta={}，
            # relevant_event 也是 null）。连续两次就拉黑，否则整局会卡在这一格
            # （实测 26 步时死在一次 Chance 上）。
            if action.get("type") == "start_event" and not ack.get("changed"):
                _ev = (_current_room_event(st) or {}).get("uid")
                if _ev:
                    start_fails[_ev] = start_fails.get(_ev, 0) + 1
                    if start_fails[_ev] >= 2:
                        blocked_events.add(_ev)
                        print(f"     事件 {_ev} 的 start_event 连续空操作，拉黑（做不了这类事件）")

            # 游戏会把「实际走不到」的房间也列进 legal，对它发 move_to_room 只会
            # 30 秒超时。现在正常路径由房间图保证（见上），这里只是兜底：
            # 记住这条「图说有路、实际走不通」的边，下次 BFS 绕开它。
            if action.get("type") == "move_to_room" and not ack.get("changed"):
                bad_rooms.add(action.get("room"))
                if move_from:
                    bad_edges.add((move_from, action.get("room")))
                    print(f"     （{move_from} -> {action.get('room')} 图上有边但走不通，"
                          f"记一条坏边）")
            if action.get("type") == "move_to_room" and ack.get("changed"):
                _arr = action.get("room")
                arrivals[_arr] = arrivals.get(_arr, 0) + 1
            # 扩容（升级界面）：grant 是测试钩子，unlock 点真实 GridExtension。
            # 放这里也处理一份：升级动作走的是地图分支。
            if action.get("type") == "unlock_spaces":
                _d = ack.get("diag") or {}
                _clicked = _d.get("clicked")
                _clicked = _clicked if isinstance(_clicked, int) else 0
                unlock_left = max(0, unlock_left - _clicked)
                deferred_ok = deferred_ok or _clicked > 0
                print(f"     （扩容：点了 {_clicked} 个格子 -> grid "
                      f"{_d.get('grid_squares_before')}→{_d.get('grid_squares_after')}，"
                      f"剩余配额 {unlock_left}）")
                if _clicked <= 0:
                    unlock_left = 0
                    print("     （扩容：没有可点的 GridExtension，改成正常收尾）")
            if action.get("type") == "grant_spaces":
                _d = ack.get("diag") or {}
                print(f"     （配额 {_d.get('number_to_unlock_before')}→"
                      f"{_d.get('number_to_unlock_after')}）")

            # 地图阶段的战利品摆放：和战斗里同一套（planner + --make-room）。
            if action.get("type") == "place_item" and ack.get("changed"):
                place_skips.pop(action.get("uid", ""), None)
            if action.get("type") == "place_item" and not ack.get("changed"):
                uid = action.get("uid", "")
                deferred_ok = True          # 可恢复失败，不算卡死（见战斗分支的注释）
                if place_skips.get(uid, 0) < 1:
                    place_skips[uid] = place_skips.get(uid, 0) + 1
                    print("     摆放失败（形状/遮挡），下一轮换候选格再试一次")
                else:
                    explored.add(f"place:{uid}")
                    place_skips.pop(uid, None)
            if action.get("type") != "place_item":
                record({"event": "act", "action": action, "ok": ack.get("ok"),
                        "changed": ack.get("changed"), "ms": ack.get("ms"),
                        "delta": ack.get("delta"), "receipt": ack.get("receipt"),
                        "error": ack.get("error")})

            nochange_streak = 0 if (ack.get("changed") or deferred_ok) \
                else nochange_streak + 1
            if nochange_streak >= MAX_CONSECUTIVE_NOCHANGE:
                print("!! 连续多次没变化，判定卡死，停")
                record({"event": "stuck", "streak": nochange_streak})
                break

            steps += 1

        print(f"\n结束：{steps} 步，日志 {log_path}")
        tail = _scorecard(last_st) if last_st else {}
        tail["max_floor"] = max_floor
        record({"event": "end", "steps": steps, **tail})
    finally:
        log.close()
        ledger.save()

    print("\n账本：")
    print(ledger.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
