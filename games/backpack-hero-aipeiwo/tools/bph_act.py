"""发一条任意动作给 bridge，打印 ack（调试 place_item / diag 用）。

    python tools\\bph_act.py '{"type":"diag_event"}'
    python tools\\bph_act.py '{"type":"place_item","uid":"i-4290","cell":"0,0"}' --timeout 15000
    python tools\\bph_act.py '{"type":"place_item","uid":"i-4290"}' --brief

`--brief` 只打 ok/changed/ms/delta/diag，不打印整份 ack。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "adapter"))

from client import Bridge, BridgeError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="send one raw action to the bridge")
    ap.add_argument("action", help="JSON action, e.g. '{\"type\":\"noop\"}'")
    ap.add_argument("--timeout", type=int, default=20000, help="bridge timeout_ms")
    ap.add_argument("--runtime", help="runtime dir")
    ap.add_argument("--brief", action="store_true", help="only print key fields")
    args = ap.parse_args(argv)

    try:
        action = json.loads(args.action)
    except json.JSONDecodeError as exc:
        print(f"bad action JSON: {exc}")
        return 2

    bridge = Bridge(args.runtime)
    try:
        ack = bridge.act(action, timeout_ms=args.timeout)
    except BridgeError as exc:
        print(f"act failed: {exc}")
        return 1

    if args.brief:
        out = {
            "ok": ack.get("ok"),
            "changed": ack.get("changed"),
            "ms": ack.get("ms"),
            "error": ack.get("error"),
            "delta": ack.get("delta"),
            "diag": ack.get("diag"),
        }
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(json.dumps(ack, ensure_ascii=False, indent=1))
    return 0 if ack.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
