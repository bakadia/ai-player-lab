#!/usr/bin/env python3
"""查询 dnmap 导出的 types.json。

    python tools/dnmap/show.py GameManager
    python tools/dnmap/show.py GameManager --grep hp
    python tools/dnmap/show.py --search Enemy          # 按类型名模糊找
    python tools/dnmap/show.py GameManager --methods-only

默认读 games/backpack-hero-aipeiwo/runtime/inspect/types.json。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON = ROOT / "games" / "backpack-hero-aipeiwo" / "runtime" / "inspect" / "types.json"


def load(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"types.json not found: {path}\n先跑 tools/dnmap/build.ps1")
    return json.loads(path.read_text(encoding="utf-8"))


def short(type_name: str | None) -> str:
    """去掉常见前缀，便于对齐。"""
    if not type_name:
        return "-"
    return type_name.replace("System.", "").replace("UnityEngine.", "UE.")


def fmt_params(params: list[dict]) -> str:
    return ", ".join(f"{short(p['type'])} {p['name']}" for p in params)


def print_type(t: dict, grep: re.Pattern | None, show_methods: bool) -> None:
    flags = [k for k in ("enum", "interface", "abstract", "sealed") if t.get(k)]
    head = f"{t['full']}  :  {short(t['base'])}"
    if flags:
        head += "  [" + " ".join(flags) + "]"
    print(head)
    print("-" * len(head))

    def hit(name: str) -> bool:
        return grep is None or bool(grep.search(name))

    fields = [f for f in t.get("fields", []) if hit(f["name"])]
    props = [p for p in t.get("properties", []) if hit(p["name"])]
    methods = [m for m in t.get("methods", []) if hit(m["name"])] if show_methods else []

    if fields:
        print(f"fields ({len(fields)}):")
        for f in fields:
            mods = ("static " if f.get("static") else "") + ("" if f.get("public") else "nonpublic ")
            const = f" = {f['const']}" if f.get("const") is not None else ""
            print(f"  {mods}{short(f['type'])} {f['name']}{const}")
    if props:
        print(f"properties ({len(props)}):")
        for p in props:
            acc = ("get" if p.get("get") else "") + ("set" if p.get("set") else "")
            print(f"  {short(p['type'])} {p['name']}  [{acc}]")
    if methods:
        print(f"methods ({len(methods)}):")
        for m in methods:
            mods = ("static " if m.get("static") else "") + ("virtual " if m.get("virtual") else "")
            print(f"  {mods}{short(m['ret'])} {m['name']}({fmt_params(m['params'])})")
    if not (fields or props or methods):
        print("  (no matching members)")
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("type", nargs="?", help="类型名（全名或末尾匹配）")
    ap.add_argument("--json", type=Path, default=DEFAULT_JSON)
    ap.add_argument("--search", help="按类型名模糊搜索")
    ap.add_argument("--grep", help="只显示成员名匹配该正则的项")
    ap.add_argument("--no-methods", action="store_true", help="不显示方法")
    args = ap.parse_args()

    data = load(args.json)
    types = data["types"]
    grep = re.compile(args.grep, re.I) if args.grep else None

    if args.search:
        pat = re.compile(args.search, re.I)
        hits = [t for t in types if pat.search(t["full"])]
        print(f"{len(hits)} / {data['typeCount']} types match {args.search!r}\n")
        for t in hits:
            print_type(t, grep, not args.no_methods)
        return

    if not args.type:
        ap.print_help()
        return

    exact = [t for t in types if t["full"] == args.type]
    hits = exact or [t for t in types if t["full"].split("+")[-1] == args.type] or [
        t for t in types if t["full"].endswith(args.type)
    ]
    if not hits:
        sys.exit(f"type not found: {args.type}  (试试 --search)")

    for t in hits:
        print_type(t, grep, not args.no_methods)


if __name__ == "__main__":
    main()
