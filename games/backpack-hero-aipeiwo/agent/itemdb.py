"""静态道具库：把 `data/item-catalog.json` 翻成「能打分的属性」。

背景（docs/PLAN-item-census.md）：`policy` 以前只看**账本**——没试过的物品等于不存在，
于是「回血 / 每回合护甲 / 稀有道具」这三类续航来源完全不可见。
桥接现在能把 903 件道具的原始数据 dump 出来（`dump_item_catalog`），
这个模块负责把原始数据压成 `ItemStats`：类别 + 派生数值 + 标签。

    from itemdb import ItemDB
    db = ItemDB.load()
    s = db.get("WOODEN SWORD")
    s.damage, s.cells, s.category

三条判断上的约定（都是踩坑换来的，见 docs/RESULT-20261029-item-census.md）：

1. **治疗看 `target`**：`HP +5 target=statusFromItem/player` 才是自愈；
   `HP -5 target=enemy` 是伤害；负数打在自己身上是代价，记进 `hp_cost` 不算治疗。
2. **`block_per_turn` 单独算**：`Block` 挂在 `onTurnStart` 上才是每回合护甲，
   一次性格挡只进 `block`。两者别相加，否则续航会被高估。
3. **`cost` 用折算值**：prefab 上的 `cost` 是 -999 哨兵（PITFALLS E20），
   这里只读 `energy_cost` / `mana_cost` / `gold_cost`。

类别是**互斥**的（`category`），标签是可叠加的（`tags`）。
`unknown` 的进 `data/items-unknown.md` 人工过（`python agent/itemdb.py --write`）。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "item-catalog.json"
ENUMS_PATH = ROOT / "data" / "enums.json"

# 目标集合：分「打敌人」和「给自己/友方」。来源 Item2+Effect+Target。
ENEMY_TARGETS = frozenset({"enemy", "allEnemies", "reactiveEnemy", "everyone", "everyoneButPochette"})
PLAYER_TARGETS = frozenset({
    "player", "truePlayer", "statusFromItem", "allFriendlies", "adjacentFriendlies",
    "friendliesInFront", "friendliesBehind", "frontmostFriendly", "backmostFriendly",
    "unspecified", "everyone",
})

# 上负面状态的（打敌人时算 status_inflicted，打自己算 status_self）
DEBUFF_STATUSES = frozenset({"Poison", "Burn", "Weak", "Slow", "Sleep", "Freeze", "Charm", "Curse", "Zombie"})
# 自 buff
BUFF_STATUSES = frozenset({"Regen", "Spikes", "Haste", "Rage", "ToughHide", "Dodge", "Vampire", "Luck"})
# 每回合都触发的时机
PER_TURN_TRIGGERS = frozenset({"onTurnStart", "constant", "constantEarly", "constantExtraEarly", "onStart"})
# 「用一下立刻结算」的时机（只有这些算 active，反应型不算）
ACTIVE_TRIGGERS = frozenset({
    "onUse", "onCombo", "onComboUse", "onComboSelect", "useEarly", "useLate",
    "onFirstUse", "onAlternateUse", "onUseUntilOverheat", "onUseUntilOverheatLate",
    "endOfActions",
})

# 纯资源/工具类（判 utility 用）
UTILITY_TYPES = frozenset({"Key", "Gold", "Mana", "ManaStone", "Ingredient", "Present", "Loot"})

# ItemType 里代表「稀有/唯一」
RELIC_TYPE = "Relic"


def _rev(table: dict[str, int] | None) -> dict[int, str]:
    return {int(v): k for k, v in (table or {}).items()}


@dataclass(frozen=True)
class ItemStats:
    name: str                  # prefab 名
    display: str               # 人话名（状态里的 key）
    display_text: str
    types: tuple[str, ...]
    rarity: str
    cells: int
    energy_cost: int
    mana_cost: int
    gold_cost: int
    damage: float = 0.0
    hits: int = 0
    block: float = 0.0
    block_per_turn: float = 0.0
    heal: float = 0.0
    hp_cost: float = 0.0
    max_hp_gain: float = 0.0
    energy_gain: float = 0.0
    status_inflicted: tuple[str, ...] = ()
    status_self: tuple[str, ...] = ()
    # 「用一下」就能拿到的部分（trigger 是 onUse/onCombo 之类）；
    # 反应型（onKill / whenAttacked / onTurnStart）只进上面的总量，不进来。
    damage_active: float = 0.0
    block_active: float = 0.0
    # 影响**别的**道具的修正（modifiers / addModifiers），不是自己的输出
    support_damage: float = 0.0
    support_block: float = 0.0
    modifier_count: int = 0
    category: str = "unknown"
    tags: frozenset[str] = frozenset()
    effect_count: int = 0

    @property
    def is_sustain(self) -> bool:
        return self.category == "sustain"


def _walk_effects(node: Any, trigger: str | None = None) -> Iterator[tuple[dict[str, Any], str | None]]:
    """递归找 `$type == "Effect"` 的原始对象，带上最近的 trigger（触发时机）。"""
    if isinstance(node, dict):
        t = node.get("trigger")
        if isinstance(t, dict) and isinstance(t.get("trigger"), int):
            trig_name = node.get("__trigger_name")  # 由调用方填
            trigger = trig_name if trig_name else trigger
        if node.get("$type") == "Effect":
            yield node, trigger
        for k, v in node.items():
            if k in ("trigger", "$type"):
                continue
            yield from _walk_effects(v, trigger)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_effects(v, trigger)


class ItemDB:
    def __init__(self, items: Iterable[ItemStats], unknown_types: Iterable[str] = ()) -> None:
        self.items: dict[str, ItemStats] = {}
        self.unknown_types = tuple(unknown_types)
        self._all: list[ItemStats] = []
        for it in items:
            self._all.append(it)
            self.items[it.display.strip().upper()] = it
            self.items.setdefault(it.name.strip().upper(), it)

    def __len__(self) -> int:
        return len(self._all)

    def all(self) -> list[ItemStats]:
        return list(self._all)

    def get(self, name: str) -> ItemStats | None:
        return self.items.get((name or "").strip().upper())

    def by_category(self, category: str) -> list[ItemStats]:
        return sorted((it for it in self._all if it.category == category),
                      key=lambda i: (-i.heal, -i.max_hp_gain, i.cells, i.display))

    # ---------- 加载 ----------

    @classmethod
    def load(cls, catalog: Path | str | None = None, enums: Path | str | None = None) -> "ItemDB":
        return _load_cached(str(catalog or CATALOG_PATH), str(enums or ENUMS_PATH))

    @classmethod
    def from_doc(cls, doc: dict[str, Any], enum_doc: dict[str, Any]) -> "ItemDB":
        return cls(_classify_all(doc, enum_doc), _unknown_types(doc, enum_doc))


@lru_cache(maxsize=4)
def _load_cached(catalog_path: str, enums_path: str) -> "ItemDB":
    doc = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    enum_doc = json.loads(Path(enums_path).read_text(encoding="utf-8"))
    return ItemDB.from_doc(doc, enum_doc)


def _unknown_types(doc: dict[str, Any], enum_doc: dict[str, Any]) -> list[str]:
    known = {int(v) for v in (enum_doc.get("types", {}).get("Item2+ItemType") or {}).values()}
    out: set[int] = set()
    for raw in doc.get("items") or []:
        for t in raw.get("item_type") or []:
            if isinstance(t, int) and t not in known:
                out.add(t)
    return [str(t) for t in sorted(out)]


def _classify_all(doc: dict[str, Any], enum_doc: dict[str, Any]) -> list[ItemStats]:
    types = enum_doc.get("types") or {}
    eff_names = _rev(types.get("Item2+Effect+Type"))
    target_names = _rev(types.get("Item2+Effect+Target"))
    trigger_names = _rev(types.get("Item2+Trigger+ActionTrigger"))
    item_types = _rev(types.get("Item2+ItemType"))
    rarity_names = _rev(types.get("Item2+Rarity"))

    out: list[ItemStats] = []
    for raw in doc.get("items") or []:
        out.append(_classify_one(raw, eff_names, target_names, trigger_names, item_types, rarity_names))
    return out


def _classify_one(
    raw: dict[str, Any],
    eff_names: dict[int, str],
    target_names: dict[int, str],
    trigger_names: dict[int, str],
    item_types: dict[int, str],
    rarity_names: dict[int, str],
) -> ItemStats:
    # trigger 名字要先填进去，_walk_effects 才能带回时机
    def decorate(node: Any) -> None:
        if isinstance(node, dict):
            t = node.get("trigger")
            if isinstance(t, dict) and isinstance(t.get("trigger"), int):
                node["__trigger_name"] = trigger_names.get(int(t["trigger"]), "?")
            for v in node.values():
                decorate(v)
        elif isinstance(node, list):
            for v in node:
                decorate(v)

    body = {k: v for k, v in raw.items() if k != "extra"}
    decorate(body)

    # 自己的效果只在 combat_effects 里；modifiers / addModifiers 是**改别的道具**
    # （例如 KNIGHT'S ARMOR 给相邻 Armor/Shield +2 甲）。混在一起会把「辅助」
    # 算成「自己的输出」，这是第一版分类器最大的偏差来源。
    own = list(_walk_effects(raw.get("combat_effects")))
    support = list(_walk_effects(raw.get("modifiers"))) + list(_walk_effects(raw.get("add_modifiers")))

    damage = block = block_per_turn = heal = hp_cost = max_hp = energy = 0.0
    damage_active = block_active = 0.0
    support_damage = support_block = 0.0
    inflicted: list[str] = []
    self_status: list[str] = []
    hits = 0
    tags: set[str] = set()

    for eff, trig in own:
        name = eff_names.get(int(eff.get("type", -1)), "?")
        try:
            value = float(eff.get("value") or 0)
        except (TypeError, ValueError):
            value = 0.0
        target = target_names.get(int(eff.get("target", -1)), "?")
        on_enemy = target in ENEMY_TARGETS
        on_self = target in PLAYER_TARGETS or target not in ENEMY_TARGETS
        active = trig in ACTIVE_TRIGGERS

        if name == "Damage":
            if on_enemy:
                damage += abs(value)
                if active:
                    damage_active += abs(value)
                hits += 1
                if target in ("allEnemies", "everyone", "everyoneButPochette"):
                    tags.add("aoe")
            elif on_self:
                hp_cost += abs(value)
                tags.add("self_harm")
        elif name == "Block":
            if value > 0:
                block += value
                if active:
                    block_active += value
                if trig in PER_TURN_TRIGGERS:
                    block_per_turn += value
                    tags.add("per_turn")
        elif name == "HP":
            if on_enemy:
                damage += -value if value < 0 else 0.0   # HP -N on enemy = N damage
                hp_cost += value if value > 0 else 0.0
            elif value > 0:
                heal += value
            elif value < 0:
                hp_cost += -value
                tags.add("self_harm")
        elif name in ("MaxHP", "MaxHPPassive"):
            if value > 0:
                max_hp += value
        elif name in ("AP", "Mana"):
            energy += value
        elif name in DEBUFF_STATUSES:
            (inflicted if on_enemy else self_status).append(name)
        elif name in BUFF_STATUSES:
            self_status.append(name)
        if name in ("Destroy", "Exhaust"):
            tags.add("consumable")
        if trig in ("onCombo", "onComboUse", "onComboSelect"):
            tags.add("combo")
        if not active and name in ("Damage", "Block") and trig not in PER_TURN_TRIGGERS:
            tags.add("reactive")

    meaningful_support = 0
    for eff, trig in support:
        name = eff_names.get(int(eff.get("type", -1)), "?")
        try:
            value = float(eff.get("value") or 0)
        except (TypeError, ValueError):
            value = 0.0
        if name == "Damage":
            support_damage += abs(value)
            meaningful_support += 1
        elif name == "Block":
            support_block += max(0.0, value)
            meaningful_support += 1
        elif name in DEBUFF_STATUSES or name in BUFF_STATUSES or name in ("MaxHP", "MaxHPPassive", "Luck"):
            meaningful_support += 1
        if name in ("Destroy", "Exhaust"):
            tags.add("consumable")

    modifier_count = meaningful_support
    if support_damage > 0 or support_block > 0 or meaningful_support > 0:
        tags.add("support")

    types = [item_types.get(int(t), str(t)) for t in (raw.get("item_type") or [])]
    if RELIC_TYPE in types or int(raw.get("one_of_a_kind") or 0) != 0:
        tags.add("relic")
    if "Curse" in types:
        tags.add("curse")
    if int(raw.get("rarity") or 0) >= 2:
        tags.add("rare")

    # 主类别互斥；顺序 = 「对续航/战斗最直接的先说」。
    if heal > 0 or max_hp > 0:
        category = "sustain"
    elif block > 0:
        category = "defense"
    elif damage > 0:
        category = "attack"
    elif support_damage > 0 or support_block > 0 or modifier_count > 0:
        category = "support"
    elif energy > 0:
        category = "energy"
    elif inflicted or self_status:
        category = "status"
    elif "relic" in tags:
        category = "relic"
    elif set(types) & UTILITY_TYPES:
        category = "utility"
    elif not own and not support:
        category = "unknown"
    else:
        category = "utility"

    return ItemStats(
        name=str(raw.get("name") or ""),
        display=str(raw.get("display_name") or raw.get("name") or "").strip(),
        display_text=str(raw.get("display_name_text") or ""),
        types=tuple(types),
        rarity=rarity_names.get(int(raw.get("rarity") or -1), "?"),
        cells=int(raw.get("cells") or 0),
        energy_cost=int(raw.get("energy_cost") if raw.get("energy_cost") is not None else -1),
        mana_cost=int(raw.get("mana_cost") if raw.get("mana_cost") is not None else -1),
        gold_cost=int(raw.get("gold_cost") if raw.get("gold_cost") is not None else -1),
        damage=damage,
        hits=hits,
        block=block,
        block_per_turn=block_per_turn,
        heal=heal,
        hp_cost=hp_cost,
        max_hp_gain=max_hp,
        energy_gain=energy,
        status_inflicted=tuple(dict.fromkeys(inflicted)),
        status_self=tuple(dict.fromkeys(self_status)),
        damage_active=damage_active,
        block_active=block_active,
        support_damage=support_damage,
        support_block=support_block,
        modifier_count=modifier_count,
        category=category,
        tags=frozenset(tags),
        effect_count=len(own) + len(support),
    )


# ---------------------------------------------------------------- 报告 / 交叉验证


def cross_check(db: ItemDB, ledger: Any, min_samples: int = 5) -> list[dict[str, Any]]:
    """把账本实测均值和目录静态值对一遍。

    `damage_sum` 是**净伤害**（打在有护甲的目标上会被吃掉，见 data/README.md），
    所以观测值通常**低于**静态值；偏差为负不算错，偏差为正才更可疑（多打了）。
    """
    rows: list[dict[str, Any]] = []
    for name, entry in (getattr(ledger, "data", None) or {}).items():
        stats = db.get(name)
        n = int(entry.get("samples") or 0)
        if stats is None or n < min_samples:
            continue
        obs_dmg = float(entry.get("damage_sum", 0)) / n
        obs_blk = float(entry.get("block_sum", 0)) / n
        rows.append({
            "name": name,
            "samples": n,
            "static_damage": stats.damage,
            "ledger_damage": round(obs_dmg, 2),
            "static_block": stats.block,
            "ledger_block": round(obs_blk, 2),
            "damage_dev": _dev(stats.damage, obs_dmg),
            "block_dev": _dev(stats.block, obs_blk),
        })
    rows.sort(key=lambda r: r["samples"], reverse=True)
    return rows


def _dev(static: float, observed: float) -> float | None:
    if static <= 0:
        return None if observed <= 0 else float("inf")
    return (observed - static) / static


def _dev_text(dev: float | None) -> str:
    if dev is None:
        return "-"
    if dev == float("inf"):
        return "账本有/目录无"
    return f"{dev * 100:+.0f}%"


def report(db: ItemDB, ledger: Any = None, min_samples: int = 5) -> str:
    from collections import Counter

    lines: list[str] = []
    cats = Counter(it.category for it in db.all())
    total = len(db)
    lines.append(f"道具总数 {total}；类别（互斥）:")
    for cat, n in cats.most_common():
        lines.append(f"  {cat:9} {n:4}  {n / total * 100:5.1f}%")
    unknown = cats.get("unknown", 0)
    lines.append(f"unknown 占比 {unknown / total * 100:.1f}%（目标 < 20%）")

    tags = Counter(t for it in db.all() for t in it.tags)
    lines.append("标签（可叠加）: " + ", ".join(f"{k}={v}" for k, v in tags.most_common()))

    sustain = db.by_category("sustain")
    lines.append(f"\n续航类 {len(sustain)} 件（按治疗量排序，前 12）:")
    for it in sustain[:12]:
        lines.append(f"  {it.display[:24]:24} heal={it.heal:5.1f} maxhp={it.max_hp_gain:4.1f} "
                     f"cells={it.cells} cost={it.energy_cost} tags={sorted(it.tags)}")

    per_turn = [it for it in db.all() if "per_turn" in it.tags]
    lines.append(f"\n每回合收益（per_turn）{len(per_turn)} 件（前 10）:")
    for it in sorted(per_turn, key=lambda i: -(i.block_per_turn + i.heal))[:10]:
        lines.append(f"  {it.display[:24]:24} block/turn={it.block_per_turn:4.1f} "
                     f"heal={it.heal:4.1f} cells={it.cells}")

    if ledger is not None:
        rows = cross_check(db, ledger, min_samples)
        lines.append(f"\n账本交叉验证（样本 >= {min_samples}，{len(rows)} 件）:")
        lines.append(f"  {'物品':24} {'样本':>4} {'静态伤':>6} {'实测伤':>6} {'偏差':>7} "
                     f"{'静态挡':>6} {'实测挡':>6} {'偏差':>7}")
        bad = 0
        for r in rows:
            flag = ""
            for key in ("damage_dev", "block_dev"):
                dev = r[key]
                if dev is not None and (dev == float("inf") or abs(dev) > 0.30):
                    flag = "  <-- >30%"
                    bad += 1
                    break
            lines.append(f"  {r['name'][:24]:24} {r['samples']:>4} {r['static_damage']:>6.1f} "
                         f"{r['ledger_damage']:>6.2f} {_dev_text(r['damage_dev']):>7} "
                         f"{r['static_block']:>6.1f} {r['ledger_block']:>6.2f} "
                         f"{_dev_text(r['block_dev']):>7}{flag}")
        lines.append(f"  偏差 >30% 的 {bad} 条（注意：账本记的是净伤害，负偏差是正常的）")

    if unknown:
        lines.append(f"\nunknown 按 ItemType 分组（前 10 组）:")
        groups: Counter[tuple[str, ...]] = Counter(tuple(it.types) or ("<无类型>",) for it in db.by_category("unknown"))
        for types, n in groups.most_common(10):
            lines.append(f"  {n:3} × {','.join(types)}")

    return "\n".join(lines)


def write_unknown(db: ItemDB, path: Path | str) -> Path:
    out = Path(path)
    rows = db.by_category("unknown")
    lines = ["# 未分类道具（unknown）", "",
             f"> 由 `python agent/itemdb.py --write` 生成。共 {len(rows)} 件，"
             "这些道具在 prefab 上没有任何 `Effect` 数据（多半靠 `ScriptedTrigger` / 组件脚本）。",
             "> 处理方式：要么补别名字段，要么给它们一个 `utility` 弱标签，别让它们永远不可见。",
             "", "| 道具 | ItemType | 格数 | 费用 | 稀有度 | 备注 |", "|---|---|---|---|---|---|"]
    for it in rows:
        lines.append(f"| {it.display or it.name} | {','.join(it.types) or '-'} | "
                     f"{it.cells} | {it.energy_cost} | {it.rarity} | "
                     f"{'有消耗标签' if 'consumable' in it.tags else ''} |")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="道具静态库：分类 + 与账本交叉验证")
    ap.add_argument("--catalog", default=str(CATALOG_PATH))
    ap.add_argument("--enums", default=str(ENUMS_PATH))
    ap.add_argument("--ledger", default=str(ROOT / "data" / "items.json"))
    ap.add_argument("--min-samples", type=int, default=5)
    ap.add_argument("--write", action="store_true", help="写 data/items-unknown.md")
    args = ap.parse_args(argv)

    db = ItemDB.load(args.catalog, args.enums)
    ledger = None
    ledger_path = Path(args.ledger)
    if ledger_path.exists():
        try:
            agent_dir = str(ROOT / "agent")
            if agent_dir not in sys.path:
                sys.path.insert(0, agent_dir)
            from ledger import Ledger  # noqa: WPS433

            ledger = Ledger(ledger_path)
        except Exception as exc:  # pragma: no cover - 账本坏了不该挡住分类器
            print(f"(账本读不了，跳过交叉验证: {exc})")

    print(report(db, ledger, args.min_samples))
    if args.write:
        path = write_unknown(db, ROOT / "data" / "items-unknown.md")
        print(f"\n写入 {path}（{len(db.by_category('unknown'))} 件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
