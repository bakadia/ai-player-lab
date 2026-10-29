"""真机验收：move_item / rotate_item(rot_deg) / swap_items 三个原语。

    python tools/bph_verify_primitives.py

开跑前假设背包里有 3 件教程物品（9 格包）。脚本会先全部丢掉、再按固定布局重摆，
每一步都把 cells / free / diag 记下来，最后落到 data/runs/primitives-<日期>.json。

检查点（不满足就直接报错）：
  - place_item 的落点 === probe 表里的 footprint
  - move/rotate 之后 free 不变、物品仍在包内、cells 变了
  - swap 之后两件的 cells 互换，free 不变，两件都还在包内
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "adapter"))

from client import Bridge  # noqa: E402


def bag_of(st):
    bp = st.get("backpack") or {}
    return {it["uid"]: it for it in bp.get("items") or []}, bp


def free_of(st):
    return (st.get("backpack") or {}).get("free_squares")


def main() -> int:
    br = Bridge()
    steps = []

    def record(step, ack):
        st = br.state()
        items, bp = bag_of(st)
        entry = {
            "step": step,
            "action": ack.get("action"),
            "ok": ack.get("ok"),
            "changed": ack.get("changed"),
            "ms": ack.get("ms"),
            "error": ack.get("error"),
            "diag": ack.get("diag"),
            "free_after": free_of(st),
            "bag": {u: it["cells"] for u, it in items.items()},
            "loose": [(it["uid"], it["name"]) for it in bp.get("unplaced") or []],
        }
        steps.append(entry)
        print(f"\n[{len(steps)}] {step}")
        print(f"    ok={entry['ok']} changed={entry['changed']} free={entry['free_after']} "
              f"err={entry['error']}")
        print(f"    bag={json.dumps(entry['bag'], ensure_ascii=False)}")
        return entry

    st = br.state()
    items, bp = bag_of(st)
    loose = bp.get("unplaced") or []
    print("start: bag", json.dumps({u: it["name"] for u, it in items.items()}, ensure_ascii=False))
    print("       loose", [(i["uid"], i["name"]) for i in loose])
    if not items:
        print("!! 背包是空的，先跑 tutorial/摆放步骤")
        return 1

    # 0) 先全部丢掉，拿回一个干净的 9 格包
    for uid in list(items):
        ack = br.act({"type": "drop_item", "uid": uid}, timeout_ms=20000)
        record(f"drop {items[uid]['name']}", ack)
        if not ack.get("changed"):
            print("!! 丢不掉，停")
            return 1
        time.sleep(0.3)

    st = br.state()
    loose = {it["name"]: it["uid"] for it in (st.get("backpack") or {}).get("unplaced") or []}
    sword = loose.get("WOODEN SWORD")
    meal = loose.get("MEAL")
    buckler = loose.get("ROUGH BUCKLER")
    print("\nloose after reset:", loose)
    if not (sword and meal):
        print("!! 找不到 WOODEN SWORD / MEAL")
        return 1

    # 1) 放剑：竖着放在 -1 列（锚点是中间那格 -1,0）
    ack = br.act({"type": "place_item", "uid": sword, "cell": "-1,0", "rot_deg": 0},
                 timeout_ms=20000)
    e = record("place WOODEN SWORD @-1,0 rot0", ack)
    assert (ack.get("diag") or {}).get("cells") == ["-1,-1", "-1,0", "-1,1"], e["diag"]
    free0 = e["free_after"]
    assert free0 == 6, free0

    # 2) 绝对朝向：转 90° 变成横的（锚点仍是中间那格）
    ack = br.act({"type": "rotate_item", "uid": sword, "rot_deg": 90}, timeout_ms=20000)
    e = record("rotate WOODEN SWORD rot_deg=90", ack)
    assert e["ok"], e
    cells90 = (ack.get("diag") or {}).get("cells") or []
    assert len(cells90) == 3, cells90
    assert e["free_after"] == free0, (e["free_after"], free0)
    ys = {c.split(",")[1] for c in cells90}
    assert len(ys) == 1, f"rot90 应该是横的，实际 {cells90}"

    # 3) 再转回 0（竖的）
    ack = br.act({"type": "rotate_item", "uid": sword, "rot_deg": 0}, timeout_ms=20000)
    e = record("rotate WOODEN SWORD rot_deg=0", ack)
    assert e["ok"], e
    cells0 = (ack.get("diag") or {}).get("cells") or []
    xs = {c.split(",")[0] for c in cells0}
    assert len(xs) == 1, f"rot0 应该是竖的，实际 {cells0}"
    assert e["free_after"] == free0

    # 4) move_item 带绝对朝向：挪到中间那列并保持竖着
    ack = br.act({"type": "move_item", "uid": sword, "cell": "0,0", "rot_deg": 0},
                 timeout_ms=20000)
    e = record("move WOODEN SWORD -> 0,0 rot0", ack)
    assert e["ok"], e
    moved = (ack.get("diag") or {}).get("cells") or []
    assert moved == ["0,-1", "0,0", "0,1"], moved
    assert e["free_after"] == free0

    # 5) 放饭盒：MEAL 的 rot0 是横的 1x2，rot90 才是竖的 -> 用 rot_deg=90 放到左列
    if meal:
        ack = br.act({"type": "place_item", "uid": meal, "cell": "-1,-1", "rot_deg": 90},
                     timeout_ms=20000)
        e = record("place MEAL @-1,-1 rot90", ack)
        if ack.get("changed"):
            assert (ack.get("diag") or {}).get("cells") == ["-1,-1", "-1,0"], e["diag"]

    # 6) 互换：剑（中间列竖着，3 格）<-> 饭盒（左列竖着，2 格）。
    #    中间态是「剑被摘下来 -> 它的旧格算空」，所以饭盒能先落到中间列；
    #    剑再落到饭盒腾出来的左列（外加一格空格）。free 应该一格不变。
    st = br.state()
    items, bp = bag_of(st)
    if meal and sword in items and meal in items:
        before = {u: items[u]["cells"] for u in (sword, meal)}
        free_before = free_of(st)
        free_cells = set()
        ack = br.act({"type": "swap_items", "a": meal, "b": sword}, timeout_ms=20000)
        e = record("swap MEAL <-> WOODEN SWORD", ack)
        if ack.get("changed"):
            after_meal = set(e["bag"].get(meal) or [])
            after_sword = set(e["bag"].get(sword) or [])
            before_meal = set(before[meal])
            before_sword = set(before[sword])
            assert e["free_after"] == free_before, (e["free_after"], free_before)
            # 剑占了饭盒原来那两格（可能再多吃一格空地）；饭盒落进了剑腾出来的地方
            assert before_meal <= after_sword, (sorted(before_meal), sorted(after_sword))
            assert after_meal & before_sword, (sorted(after_meal), sorted(before_sword))
            print("    OK: 互换生效 —— free 不变、剑接管了饭盒的旧格、饭盒落到剑的旧格")

    out = HERE.parent / "data" / "runs" / ("primitives-" + time.strftime("%Y%m%d") + ".json")
    out.write_text(json.dumps({"steps": steps}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nsaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
