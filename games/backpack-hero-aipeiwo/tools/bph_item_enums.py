"""导出游戏里的**枚举表**（整数 -> 名字），落成 `data/enums.json`。

桥接为了让 ack 小、跨版本稳，枚举一律存整数；这个脚本把真值表补上：

    python tools\\bph_item_enums.py                 # 编译 EnumDump + 导出
    python tools\\bph_item_enums.py --no-build      # 用现成的 bin\\EnumDump.exe
    python tools\\bph_item_enums.py --asm <dll> --out data\\enums.json

值来自程序集元数据里的**常量**（不是字段顺序猜的），所以显式赋值也对。
输出格式：

    {"schema": "bph.enums.v1", "assembly": "Assembly-CSharp", "count": 165,
     "types": {"Item2+ItemType": {"Any": 0, "Grid": 1, ..., "Relic": 22}}}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DNMAP = ROOT.parent.parent / "tools" / "dnmap"
DEFAULT_ASM = Path(r"E:\steam\steamapps\common\Backpack Hero\Backpack Hero_Data\Managed\Assembly-CSharp.dll")

# 验收锚点：和 PITFALLS / 交接里对过的值。对不上说明拿错程序集了。
ANCHORS = {
    "Item2+ItemType": {"Relic": 22, "Carving": 32, "Grid": 1},
    "Item2+Effect+Type": {"Damage": 0, "Block": 1, "HP": 2, "MaxHP": 4},
    "Item2+Rarity": {"Common": 0, "Rare": 2, "Legendary": 3},
}


def csc() -> Path:
    import os

    return Path(os.environ["WINDIR"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"


def build_enumdump() -> Path:
    src = DNMAP / "EnumDump.cs"
    exe = DNMAP / "bin" / "EnumDump.exe"
    cecil = DNMAP / "bin" / "Mono.Cecil.dll"
    if not src.exists():
        raise SystemExit(f"找不到 {src}")
    if not cecil.exists():
        raise SystemExit(f"找不到 {cecil}")
    exe.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(csc()), "/nologo", "/target:exe", "/langversion:5",
           f"/out:{exe}", f"/r:{cecil}", str(src)]
    print("csc:", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        raise SystemExit(f"EnumDump 编译失败 ({r.returncode})")
    return exe


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dump the game's enums to data/enums.json")
    ap.add_argument("--asm", default=str(DEFAULT_ASM), help="Assembly-CSharp.dll")
    ap.add_argument("--out", default=str(ROOT / "data" / "enums.json"))
    ap.add_argument("--no-build", action="store_true", help="不重新编译 EnumDump.exe")
    args = ap.parse_args(argv)

    asm = Path(args.asm)
    if not asm.exists():
        print(f"找不到程序集: {asm}")
        return 2

    exe = DNMAP / "bin" / "EnumDump.exe"
    if not args.no_build:
        exe = build_enumdump()
    elif not exe.exists():
        print(f"没有 {exe}；别加 --no-build")
        return 2

    r = subprocess.run([str(exe), str(asm)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        print(f"EnumDump 失败 ({r.returncode})")
        return 2

    raw = json.loads(r.stdout)
    types = raw.get("types") or {}

    doc = {
        "schema": "bph.enums.v1",
        "assembly": raw.get("assembly"),
        "assembly_sha256": hashlib.sha256(asm.read_bytes()).hexdigest()[:16],
        "count": len(types),
        "types": types,
    }

    bad = 0
    for tname, anchors in ANCHORS.items():
        table = types.get(tname)
        if not table:
            print(f"  缺少枚举 {tname}")
            bad += 1
            continue
        for k, v in anchors.items():
            if table.get(k) != v:
                print(f"  值对不上 {tname}.{k}: {table.get(k)} != {v}")
                bad += 1
    if bad:
        print(f"警告：{bad} 个验收锚点不匹配（程序集版本不对？）")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(types)} 个枚举 -> {out} ({out.stat().st_size} bytes)")
    for tname in ("Item2+ItemType", "Item2+Effect+Type", "Item2+Rarity", "Item2+PlayType"):
        table = types.get(tname) or {}
        print(f"  {tname}: {len(table)} 个值")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
