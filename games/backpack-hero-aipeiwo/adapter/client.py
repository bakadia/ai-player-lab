#!/usr/bin/env python3
"""Backpack Hero 文件 IPC 客户端。

只做翻译和搬运，不做策略（策略在 games/<game>/agent/）。
协议（MVP §3.5）：

    写  runtime/state.json        游戏内 bridge 原子写，带单调 seq
    写  runtime/cmd/{id}.json     我们写命令
    读  runtime/ack/{id}.json     bridge 执行后回执，带 delta 和耗时
    碰  runtime/halt              存在即急停

用法：
    python adapter/client.py state
    python adapter/client.py watch
    python adapter/client.py end-turn
    python adapter/client.py use i12345
    python adapter/client.py use i12345 --via useitem --force
    python adapter/client.py halt on
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_RUNTIME = r"D:\projects\ai-player-lab\games\backpack-hero-aipeiwo\runtime"


class BridgeError(RuntimeError):
    pass


class Bridge:
    def __init__(self, runtime: str | Path | None = None) -> None:
        self.dir = Path(runtime or os.environ.get("BPH_RUNTIME", DEFAULT_RUNTIME))
        self.state_path = self.dir / "state.json"
        self.cmd_dir = self.dir / "cmd"
        self.ack_dir = self.dir / "ack"
        self.halt_path = self.dir / "halt"
        self._next_id = int(time.time() * 1000) % 1_000_000

    # ---------- 读状态 ----------

    def state(self, retries: int = 20) -> dict[str, Any]:
        """读一份 state。文件正被替换时会短暂读到半截，重试即可。

        PermissionError 也要重试：游戏进程刚写出来的文件，在 Windows 上
        可能有一瞬间 ACL 还没落定，读取会被拒绝（实测踩到，见 PITFALLS E7）。
        """
        last: Exception | None = None
        for _ in range(retries):
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                last = exc
                time.sleep(0.01)
        raise BridgeError(f"cannot read state.json: {last}")

    def wait_state(self, after_seq: int, timeout: float = 5.0) -> dict[str, Any]:
        """等到 seq 超过 after_seq。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            st = self.state()
            if st.get("seq", 0) > after_seq:
                return st
            time.sleep(0.02)
        raise BridgeError(f"state seq did not advance past {after_seq} within {timeout}s")

    # ---------- 发命令 ----------

    def next_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def act(
        self,
        action: dict[str, Any],
        *,
        expect: dict[str, Any] | None = None,
        timeout_ms: int = 5000,
        poll_timeout: float | None = None,
    ) -> dict[str, Any]:
        """发一条命令并等回执。返回 ack（含 ok / delta / ms / error）。"""
        if self.halt_path.exists():
            raise BridgeError("halt file exists; refusing to act (clear it first)")

        self.cmd_dir.mkdir(parents=True, exist_ok=True)
        self.ack_dir.mkdir(parents=True, exist_ok=True)

        cid = self.next_id()
        cmd = {"id": cid, "action": action, "timeout_ms": timeout_ms}
        if expect:
            cmd["expect"] = expect

        tmp = self.cmd_dir / f"{cid}.json.tmp"
        tmp.write_text(json.dumps(cmd, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.cmd_dir / f"{cid}.json")

        ack_path = self.ack_dir / f"{cid}.json"
        deadline = time.monotonic() + (poll_timeout or (timeout_ms / 1000 + 5.0))
        while time.monotonic() < deadline:
            try:
                ack = json.loads(ack_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                # OSError 覆盖 FileNotFoundError 和 PermissionError：
                # 文件可能还没出现，也可能刚出现但 ACL 未落定（PITFALLS E7）
                time.sleep(0.02)
                continue
            try:
                ack_path.unlink()
            except OSError:
                pass
            return ack

        raise BridgeError(f"no ack for cmd {cid} within deadline (bridge running? halt?)")

    # ---------- 急停 ----------

    def halt(self, on: bool = True) -> None:
        if on:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.halt_path.write_text("halt\n", encoding="utf-8")
        elif self.halt_path.exists():
            self.halt_path.unlink()


def _print(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backpack Hero bridge client")
    ap.add_argument("--runtime", help="runtime 目录（默认 BPH_RUNTIME 或仓库内路径）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("state", help="打印当前 state")
    pw = sub.add_parser("watch", help="盯着 state，seq 变了就打印")
    pw.add_argument("-n", "--count", type=int, default=10)

    sub.add_parser("end-turn", help="结束回合")
    pu = sub.add_parser("use", help="使用物品")
    pu.add_argument("uid", help="state.json 里 item 的 uid，形如 i12345")
    pu.add_argument("--via", choices=["consider", "useitem"], default="consider")
    pu.add_argument("--force", action="store_true")
    sub.add_parser("noop", help="空动作，用来验证链路")

    ph = sub.add_parser("halt", help="急停开关")
    ph.add_argument("value", choices=["on", "off"])

    args = ap.parse_args(argv)
    br = Bridge(args.runtime)

    if args.cmd == "state":
        _print(br.state())
        return 0

    if args.cmd == "watch":
        seq = br.state().get("seq", 0)
        seen = 0
        print(f"watching from seq={seq} (Ctrl+C to stop)")
        try:
            while seen < args.count:
                st = br.wait_state(seq, timeout=600)
                seq = st.get("seq", seq)
                seen += 1
                print(f"\n--- seq={seq} mode={st.get('mode')} floor={st.get('run', {}).get('floor')} "
                      f"hp={st.get('player', {}).get('hp')} ap={st.get('player', {}).get('energy')} "
                      f"enemies={len(st.get('enemies', []))}")
                for e in st.get("enemies", []):
                    print(f"      {e['uid']} {e['name'].strip()!r} hp={e['hp']}/{e['max_hp']}")
        except KeyboardInterrupt:
            return 0
        return 0

    if args.cmd == "end-turn":
        _print(br.act({"type": "end_turn"}))
        return 0

    if args.cmd == "use":
        action: dict[str, Any] = {"type": "use_item", "uid": args.uid, "via": args.via}
        if args.force:
            action["force"] = True
        _print(br.act(action))
        return 0

    if args.cmd == "noop":
        _print(br.act({"type": "noop"}))
        return 0

    if args.cmd == "halt":
        br.halt(args.value == "on")
        print(f"halt {args.value}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
