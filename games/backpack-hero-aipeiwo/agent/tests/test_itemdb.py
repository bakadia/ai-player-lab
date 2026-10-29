"""`agent/itemdb.py` 的回归测试（不需要游戏，读 data/ 里的两份快照）。

    python agent/tests/test_itemdb.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = HERE.parent
ROOT = AGENT.parent
sys.path.insert(0, str(AGENT))

from itemdb import CATALOG_PATH, ENUMS_PATH, ItemDB, cross_check  # noqa: E402


def main() -> int:
    db = ItemDB.load()
    assert CATALOG_PATH.exists() and ENUMS_PATH.exists()
    print(f"PASS catalog loaded: {len(db)} items")

    # ---- 抽样：数值 / 类别 / 占格 / 费用 ----
    sword = db.get("WOODEN SWORD")
    assert sword is not None
    assert sword.damage == 7 and sword.damage_active == 7, sword
    assert sword.category == "attack" and sword.cells == 3 and sword.energy_cost == 1, sword
    print("PASS WOODEN SWORD: damage=7 active=7 cells=3 cost=1 attack")

    buckler = db.get("ROUGH BUCKLER")
    assert buckler is not None
    assert buckler.block == 7 and buckler.category == "defense" and buckler.cells == 4, buckler
    print("PASS ROUGH BUCKLER: block=7 cells=4 defense")

    apple = db.get("APPLE")
    assert apple is not None
    assert apple.heal == 5 and apple.category == "sustain" and "consumable" in apple.tags, apple
    print("PASS APPLE: heal=5 sustain consumable")

    # 只算自己的效果：骑士甲每回合 6 甲 + 给相邻护甲/盾 +2（support）
    armor = db.get("KNIGHT'S ARMOR")
    assert armor is not None
    assert armor.block_per_turn == 6 and armor.block == 6, armor
    assert armor.support_block == 2 and armor.modifier_count == 1, armor
    print("PASS KNIGHT'S ARMOR: block/turn=6 own, +2 support to Armor/Shield")

    # ---- 整体分布 ----
    from collections import Counter

    cats = Counter(it.category for it in db.all())
    assert sum(cats.values()) == len(db)
    unknown_ratio = cats.get("unknown", 0) / len(db)
    assert unknown_ratio < 0.20, unknown_ratio
    assert cats.get("sustain", 0) >= 25, cats
    assert cats.get("attack", 0) >= 150, cats
    print(f"PASS 分布: unknown={unknown_ratio * 100:.1f}% (目标 <20%), sustain={cats['sustain']}")

    # ---- 枚举真值锚点（拿错程序集就会挂） ----
    enums = json.loads(ENUMS_PATH.read_text(encoding="utf-8"))["types"]
    assert enums["Item2+ItemType"]["Relic"] == 22
    assert enums["Item2+ItemType"]["Carving"] == 32
    assert enums["Item2+Effect+Type"]["HP"] == 2
    assert enums["Item2+Effect+Type"]["MaxHP"] == 4
    print("PASS enums: Relic=22 Carving=32 HP=2 MaxHP=4")

    # ---- 交叉验证能跑（账本样本 >= 5 的至少 1 件） ----
    from ledger import Ledger

    ledger = Ledger(ROOT / "data" / "items.json")
    rows = cross_check(db, ledger, min_samples=5)
    if rows:
        assert any(r["name"] == "WOODEN SWORD" for r in rows), rows
        print(f"PASS 交叉验证: {len(rows)} 件有 >=5 样本")
    else:
        print("SKIP 交叉验证: 账本样本不足")

    # ---- 名字查找要容错（状态里带尾随空格） ----
    assert db.get("  wooden sword  ") is sword
    print("PASS 名字查找忽略大小写/空格")

    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
