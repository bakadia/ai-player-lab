"""道具普查：让桥接 dump 整张道具总池，然后验收这份目录。

    python tools\\bph_item_catalog.py                 # dump + 体检
    python tools\\bph_item_catalog.py --json          # 额外打印桥接的短 ack
    python tools\\bph_item_catalog.py --copy          # 顺手复制一份到 data\\item-catalog.json

数据来自只读动作 `dump_item_catalog`：遍历 `DebugItemManager.main.item2s`
（= `ItemSpawner.GetAllValidItems()` 用的那张出生池）。只读 prefab，不实例化。

体检项（对应 docs/PLAN-item-census.md §1.4）：
  - `count` 和文件里的 items 数一致
  - 关键字段非空、枚举整数能在 data/enums.json 里查到
  - 抽 5 件和游戏内名字对得上
  - 占格数（cells）分布 —— prefab 上 gridPositions 是不是真的有值
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "adapter"))

from client import Bridge, BridgeError  # noqa: E402

ROOT = HERE.parent
ENUMS_PATH = ROOT / "data" / "enums.json"
SAMPLES = ["WOODEN SWORD", "ROUGH BUCKLER", "MEAL", "DEBUFF POTION", "CRISIS RING"]

# 和 data/enums.json 对不上时的兜底（见 Item2+Effect+Type）
EFFECT_FALLBACK = {0: "Damage", 1: "Block", 2: "HP", 3: "AP", 4: "MaxHP", 42: "MaxHPPassive"}
SUSTAIN_TYPES = {"HP", "MaxHP", "MaxHPPassive"}


def load_enums() -> dict[str, dict[str, int]]:
    if not ENUMS_PATH.exists():
        return {}
    doc = json.loads(ENUMS_PATH.read_text(encoding="utf-8"))
    return doc.get("types") or {}


def reverse(table: dict[str, int]) -> dict[int, str]:
    return {v: k for k, v in table.items()}


def effect_type_names(enums: dict[str, dict[str, int]]) -> dict[int, str]:
    table = enums.get("Item2+Effect+Type")
    if not table:
        return dict(EFFECT_FALLBACK)
    out = reverse(table)
    for k, v in EFFECT_FALLBACK.items():
        out.setdefault(k, v)
    return out


def walk_effects(node, out: list[dict]) -> None:
    """收集所有 `$type == Effect` 的数据对象（桥接的通用 dumper 会带这个标记）。"""
    if isinstance(node, dict):
        if node.get("$type") == "Effect":
            out.append(node)
        for v in node.values():
            walk_effects(v, out)
    elif isinstance(node, list):
        for v in node:
            walk_effects(v, out)


def item_effects(item: dict) -> list[dict]:
    out: list[dict] = []
    for key in ("combat_effects", "energy_effects", "movement_effects", "create_effects",
                "modifiers", "add_modifiers", "active_item_status_effects", "uses_limits"):
        walk_effects(item.get(key), out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dump and sanity-check the item catalog")
    ap.add_argument("--out", default="item-catalog.json", help="相对 runtime/ 的输出文件")
    ap.add_argument("--timeout", type=int, default=30000, help="bridge timeout_ms")
    ap.add_argument("--json", action="store_true", help="打印桥接短 ack")
    ap.add_argument("--copy", action="store_true", help="复制到 data/item-catalog.json")
    ap.add_argument("--runtime", help="runtime 目录")
    args = ap.parse_args(argv)

    bridge = Bridge(args.runtime)
    try:
        ack = bridge.act({"type": "dump_item_catalog", "out": args.out}, timeout_ms=args.timeout)
    except BridgeError as exc:
        print(f"bridge error: {exc}")
        return 2

    diag = ack.get("diag")
    if not isinstance(diag, dict) or diag.get("action") != "dump_item_catalog":
        print("没拿到 diag（桥接太老？先 deploy 新 dll）")
        print(json.dumps(ack, ensure_ascii=False, indent=1)[:2000])
        return 2
    if args.json:
        print(json.dumps(diag, ensure_ascii=False, indent=1))
    if diag.get("error") or ack.get("error"):
        print(f"error: {diag.get('error') or ack.get('error')}")
        return 1

    path = Path(diag.get("path") or "")
    print(f"dump: {diag.get('count')} 件  core={diag.get('core_count')} mod={diag.get('mod_count')} "
          f"source={diag.get('source')}  {diag.get('bytes')} bytes  {diag.get('ms')} ms")
    print(f"      -> {path}")
    if diag.get("truncated"):
        print("警告: 输出被 MaxNodes 截断")
    if diag.get("errors"):
        print(f"警告: {diag.get('errors')} 个字段读取失败")
    if not path.exists():
        print("文件不存在")
        return 2

    doc = json.loads(path.read_text(encoding="utf-8"))
    items = doc.get("items") or []
    enums = load_enums()
    et_names = effect_type_names(enums)
    problems: list[str] = []

    # ---- 1. 数量 ----
    print(f"\n[1] 数量: file={len(items)}  ack={diag.get('count')}")
    if len(items) != diag.get("count"):
        problems.append("文件里的 items 数和 ack.count 不一致")

    # ---- 2. 字段完整度 ----
    missing = Counter()
    for it in items:
        for key in ("name", "display_name", "item_type", "rarity", "cells",
                    "descriptions", "combat_effects", "energy_effects"):
            if key not in it:
                missing[key] += 1
    print(f"[2] 字段缺失: {dict(missing) or '无'}")
    if missing:
        problems.append(f"字段缺失 {dict(missing)}")

    no_text = sum(1 for it in items if it.get("display_name_text") in (None, ""))
    fake_text = sum(1 for it in items
                    if str(it.get("display_name_text") or "").startswith("Translation not found"))
    print(f"    文本: {len(items) - no_text}/{len(items)} 件有 display_name_text"
          f"（其中 {fake_text} 条是 Translation not found 兜底）")
    no_cost = sum(1 for it in items if (it.get("energy_cost") in (None, -1)
                                        and it.get("mana_cost") in (None, -1)
                                        and it.get("gold_cost") in (None, -1)))
    print(f"    费用: {len(items) - no_cost}/{len(items)} 件有 costs[]（其余是纯被动）")

    # ---- 3. 枚举覆盖 ----
    print("[3] 枚举覆盖:")
    for field, ename in (("rarity", "Item2+Rarity"),
                         ("play_type", "Item2+PlayType"),
                         ("move_area", "Item2+Area"),
                         ("move_distance", "Item2+AreaDistance")):
        table = enums.get(ename) or {}
        unknown = Counter(it.get(field) for it in items if it.get(field) not in (None, -1)
                          and it.get(field) not in set(table.values()))
        print(f"    {field:14} {len(table):3} 个值, 未映射: {dict(unknown) or '无'}")
        if unknown:
            problems.append(f"{field} 有未映射整数 {dict(unknown)}")

    types_cnt = Counter()
    for it in items:
        for ef in item_effects(it):
            t = ef.get("type")
            if isinstance(t, int):
                types_cnt[t] += 1
    unknown_eff = {t: n for t, n in types_cnt.items() if t not in et_names}
    print(f"    effect.type    {len(types_cnt)} 种出现, 未映射: {unknown_eff or '无'}")
    if unknown_eff:
        problems.append(f"effect.type 有未映射整数 {unknown_eff}")

    # ---- 4. 抽 5 件 ----
    print("[4] 抽样（按名字，忽略大小写）:")
    by_name = {}
    for it in items:
        for key in (it.get("name"), it.get("display_name"), it.get("display_name_text")):
            if key:
                by_name[str(key).strip().upper()] = it
    for want in SAMPLES:
        it = by_name.get(want)
        if it is None:
            print(f"    {want:16} 没找到")
            problems.append(f"抽样缺 {want}")
            continue
        print(f"    {want:16} type={it.get('item_type')} rarity={it.get('rarity')} "
              f"cells={it.get('cells')} cost={it.get('energy_cost')} "
              f"text={(it.get('display_name_text') or '')[:28]!r}")

    # ---- 5. 占格 ----
    cells = Counter(it.get("cells") for it in items)
    zero = sum(n for c, n in cells.items() if not c or c < 0)
    print(f"[5] cells 分布: {dict(sorted(cells.items(), key=lambda kv: (kv[0] is None, kv[0])))}"
          f"  (空/未知 {zero} 件)")
    if zero == len(items):
        problems.append("所有 prefab 的 gridPositions 都是空的（占格数拿不到）")

    # ---- 6. 续航预览 ----
    sustain = []
    for it in items:
        kinds = {et_names.get(ef.get("type")) for ef in item_effects(it) if isinstance(ef.get("type"), int)}
        if kinds & SUSTAIN_TYPES:
            sustain.append((it, kinds))
    print(f"[6] 续航预览: {len(sustain)} 件带 HP/MaxHP 效果")
    for it, kinds in sustain[:12]:
        vals = [(et_names.get(ef.get("type")), ef.get("value"))
                for ef in item_effects(it) if et_names.get(ef.get("type")) in SUSTAIN_TYPES]
        print(f"    {(it.get('display_name') or it.get('name') or '?').strip():22} "
              f"cells={it.get('cells')} cost={it.get('energy_cost')} {vals}")
    if len(sustain) > 12:
        print(f"    ... 另外 {len(sustain) - 12} 件")

    if args.copy:
        dst = ROOT / "data" / "item-catalog.json"
        shutil.copyfile(path, dst)
        print(f"\n复制 -> {dst} ({dst.stat().st_size} bytes)")

    print()
    if problems:
        print(f"体检: {len(problems)} 个问题")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("体检: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
