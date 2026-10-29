"""在游戏的资源文件里找字符串（找 prefab 名字这类「代码里看不到」的东西）。

Unity 的 prefab 名是序列化在 `*_Data/*.assets` / `levelN` 里的普通字符串，
所以 `Assembly-CSharp.dll` 里搜不到的命名族（例如 `QuickBoost HP Variant`），
直接扫资源就能一次看到全部变体——不用一格格走过去撞。

    python tools\\bph_asset_scan.py QuickBoost "Variant"
    python tools\\bph_asset_scan.py --dir "E:\\steam\\...\\Backpack Hero_Data" QuickBoost

输出：每个文件里的命中上下文（非可打印字符换成 `|`），去重后打印。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_DIR = r"E:\steam\steamapps\common\Backpack Hero\Backpack Hero_Data"


def ctx(raw: bytes) -> str:
    return re.sub(rb"[^\x20-\x7e]", b"|", raw).decode("ascii")


def scan_file(path: Path, needles: list[bytes], before: int, after: int,
              limit: int) -> list[tuple[bytes, str]]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        print(f"skip {path.name}: {exc}")
        return []
    out: list[tuple[bytes, str]] = []
    for needle in needles:
        idx = 0
        n = 0
        while n < limit:
            i = data.find(needle, idx)
            if i < 0:
                break
            out.append((needle, ctx(data[max(0, i - before):i + after])))
            idx = i + 1
            n += 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="grep-ish scan of Unity asset files")
    ap.add_argument("needles", nargs="+", help="要找的字符串")
    ap.add_argument("--dir", default=DEFAULT_DIR, help="Backpack Hero_Data 目录")
    ap.add_argument("--before", type=int, default=40)
    ap.add_argument("--after", type=int, default=90)
    ap.add_argument("--limit", type=int, default=40, help="每个文件每个串最多几条")
    ap.add_argument("--all", action="store_true", help="连 *.resources 也扫")
    args = ap.parse_args(argv)

    base = Path(args.dir)
    if not base.is_dir():
        print(f"not a directory: {base}")
        return 2

    files = sorted(base.glob("*.assets")) + sorted(base.glob("level*"))
    if args.all:
        files += sorted(base.glob("*.resource")) + sorted(base.glob("*.resS"))

    needles = [n.encode() for n in args.needles]
    total = 0
    for p in files:
        hits = scan_file(p, needles, args.before, args.after, args.limit)
        if not hits:
            continue
        print(f"== {p.name} ({len(hits)} hits)")
        seen = set()
        for needle, text in hits:
            key = (needle, text)
            if key in seen:
                continue
            seen.add(key)
            print(f"   [{needle.decode()}] {text}")
            total += 1
    print(f"\n{total} unique hit(s)")
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())
