"""开一局：主菜单 → 快速开始 → 去冒险吧 → 教程。

**纯桥接调用，不模拟鼠标。** 这个环境里点到哪儿都不可靠（游戏窗口在主屏之外、
前台焦点被抢），所以主菜单按钮直接调 `MenuManager` 的 public 方法：

    快速开始   -> MenuManager.GoAdventuring()
    去冒险吧   -> NewCharacterSelector.StartGame()
    故事模式   -> MenuManager.StartGameButton()
    继续游戏   -> MenuManager.LoadGameAndContinueSave()

用法：

    python games\\backpack-hero-aipeiwo\\tools\\start_run.py
    python ... --via story          # 故事模式
    python ... --via continue       # 继续上次

前置：游戏已经启动（`bph.cmd start`），且停在主菜单。

退出码：0 = 进局了；1 = 超时；2 = 已经在对局中（没做任何事）。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "adapter"))

from client import Bridge, BridgeError  # noqa: E402

IN_RUN_MODES = {"intro", "map", "battle", "reorganize", "dead"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="start a Backpack Hero run through the bridge")
    ap.add_argument("--runtime", help="runtime 目录")
    ap.add_argument("--via", default="auto",
                    choices=["auto", "quickstart", "adventure", "story", "continue", "confirm"])
    ap.add_argument("--timeout", type=float, default=90, help="总共等多久")
    ap.add_argument("--settle", type=float, default=0.6, help="每步之间的等待")
    args = ap.parse_args(argv)

    bridge = Bridge(args.runtime)
    deadline = time.monotonic() + args.timeout

    try:
        st = bridge.state()
    except BridgeError as exc:
        print(f"读不到 state.json：{exc}")
        print("先确认游戏在跑：games\\backpack-hero-aipeiwo\\tools\\bph.cmd start")
        return 1

    mode = st.get("mode")
    if mode in IN_RUN_MODES:
        print(f"已经在对局中（mode={mode}），不动它")
        return 2

    print(f"起始：mode={mode} menu={st.get('menu')}")

    pressed_menu = False
    while time.monotonic() < deadline:
        try:
            st = bridge.state()
        except BridgeError:
            time.sleep(args.settle)
            continue

        mode = st.get("mode")
        if mode in IN_RUN_MODES:
            run = st.get("run") or {}
            print(f"进局了：mode={mode} floor={run.get('floor')} hero={run.get('hero')} "
                  f"gold={run.get('gold')}")
            mp = st.get("map") or {}
            bag = st.get("backpack") or {}
            print(f"       area={mp.get('area')} rooms={mp.get('rooms_total')} "
                  f"loose={bag.get('items_loose')} "
                  f"tutorial={((st.get('ui') or {}).get('tutorial_sequence_name'))}")
            return 0

        menu = st.get("menu") or {}

        # 实测出来的唯一正确序列：
        #   主菜单「快速开始」 -> MenuManager.StartGameButton()
        #   角色选择「去冒险吧」-> MenuManager.GoAdventuring()
        # 注意：在**主菜单**直接调 GoAdventuring 会绕过角色选择，
        # 开出一个带 TESTING SWORD / 500 金币的调试局——别那么干。
        if menu.get("character_selector"):
            step = "adventure"
        elif menu.get("menu_manager") and not pressed_menu:
            step = args.via if args.via in ("quickstart", "story", "continue") else "quickstart"
            pressed_menu = True
        else:
            time.sleep(args.settle)
            continue

        try:
            ack = bridge.act({"type": "start_run", "via": step}, timeout_ms=20000)
        except BridgeError as exc:
            print(f"动作失败：{exc}")
            time.sleep(args.settle)
            continue

        print(f"  start_run via={step} ok={ack.get('ok')} changed={ack.get('changed')} "
              f"ms={ack.get('ms')} err={ack.get('error')}")
        time.sleep(args.settle)

    print(f"超时：{args.timeout}s 内没有进局")
    return 1


if __name__ == "__main__":
    sys.exit(main())
