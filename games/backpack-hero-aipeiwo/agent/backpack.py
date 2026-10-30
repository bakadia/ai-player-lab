"""背包几何：落点 / 腾位 / swap 的搜索（纯数据，不需要游戏在跑）。

为什么单独一层：桥接的 `probe_fit` 会把「每件物品 × 4 个朝向 × 每个背包格子」的
footprint 一次性吐出来（见 `Actions.ProbeFitAction`）。有了这张表，剩下的就全是
集合运算——可以在 Python 里随便搜，还能离线单测（用 `data/runs/probe-*.json`
录下来的真实数据当 fixture）。

坐标约定：cell = (x, y)，和 `state.json` 里 `backpack.items[].cells` 的 `"0,-1"`
同一套坐标（`GridObject.WorldToCell`）。

计划（Plan）是一串**桥接能直接执行的动作**：

    {"type": "move_item",   "uid": "i-4198", "cell": "0,-1", "rot_deg": 90}
    {"type": "swap_items",  "a": "i1", "b": "i2", "a_cell": ..., "a_rot": ..., ...}
    {"type": "place_item",  "uid": "i-4282", "cell": "0,-2", "rot_deg": 0}

`loop.py` 只负责按顺序发出去、每步核对回执。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from policy import per_turn_value

Cell = tuple[int, int]

# 一个「背包格子」的四个邻格方向（只用来给落点排序，不影响合法性）
_NEIGHBORS = ((1, 0), (-1, 0), (0, 1), (0, -1))


class BackpackError(RuntimeError):
    """probe 数据不完整 / 坐标串写坏了。"""


# ---------- 小工具 ----------


def parse_cell(text: str) -> Cell:
    """"0,-1" -> (0, -1)。"""
    try:
        x, y = text.split(",")
        return (int(x), int(y))
    except (AttributeError, ValueError) as exc:
        raise BackpackError(f"bad cell: {text!r}") from exc


def fmt_cell(cell: Cell) -> str:
    return f"{cell[0]},{cell[1]}"


def parse_cells(items: Iterable[str] | None) -> frozenset[Cell]:
    return frozenset(parse_cell(s) for s in (items or ()))


def parse_footprint(text: str) -> frozenset[Cell]:
    """probe 里的 "cells" 是 `"0,-1;0,-2"` 这种分号串。"""
    if not text:
        return frozenset()
    return frozenset(parse_cell(part) for part in text.split(";") if part)


# ---------- probe 数据模型 ----------


@dataclass(frozen=True)
class Placement:
    """把物品的中心放到 `cell`、转 `rot` 度之后，它占哪些格。"""

    cell: Cell
    rot: int
    cells: frozenset[Cell]

    def as_args(self) -> dict[str, Any]:
        return {"cell": fmt_cell(self.cell), "rot_deg": self.rot}


@dataclass
class ItemFit:
    uid: str
    name: str
    in_bag: bool
    needs: int
    cells: frozenset[Cell]
    rotations: dict[int, dict[Cell, frozenset[Cell]]] = field(default_factory=dict)

    def placements(self, blocked: frozenset[Cell] = frozenset(),
                   bag: frozenset[Cell] | None = None) -> list[Placement]:
        """所有「footprint 不碰 blocked」的落点。"""
        out: list[Placement] = []
        for rot in sorted(self.rotations):
            for cell, foot in self.rotations[rot].items():
                if bag is not None and not foot <= bag:
                    continue
                if foot & blocked:
                    continue
                out.append(Placement(cell, rot, foot))
        return out

    def fits_anywhere(self, bag: frozenset[Cell]) -> bool:
        """形状在背包里存在哪怕一个「不管占没占」的位置——没有就永远放不进去。"""
        for _rot, table in self.rotations.items():
            for _cell, foot in table.items():
                if foot <= bag:
                    return True
        return False

    def shape_area(self) -> int:
        """footprint 的格子数（取任一朝向），probe 的 `needs` 不靠谱时兜底。"""
        for _rot, table in self.rotations.items():
            for _cell, foot in table.items():
                return len(foot)
        return len(self.cells) or max(self.needs, 0)


@dataclass
class Bag:
    cells: frozenset[Cell]
    items: dict[str, ItemFit]
    free_reported: int = -1
    grid_squares: int = -1

    # ---- 构造 ----

    @classmethod
    def from_probe(cls, probe: dict[str, Any]) -> "Bag":
        cells = parse_cells(probe.get("bag"))
        items: dict[str, ItemFit] = {}
        for raw in probe.get("items") or []:
            uid = raw.get("uid") or ""
            if not uid:
                continue
            rotations: dict[int, dict[Cell, frozenset[Cell]]] = {}
            for rot in raw.get("rotations") or []:
                deg = int(rot.get("rot", 0))
                table: dict[Cell, frozenset[Cell]] = {}
                for place in rot.get("placements") or []:
                    try:
                        table[parse_cell(place["cell"])] = parse_footprint(place.get("cells", ""))
                    except (KeyError, BackpackError):
                        continue
                if table:
                    rotations[deg] = table
            items[uid] = ItemFit(
                uid=uid,
                name=raw.get("name", ""),
                in_bag=bool(raw.get("in_bag")),
                needs=int(raw.get("needs", -1) or -1),
                cells=parse_cells(raw.get("cells")),
                rotations=rotations,
            )
        return cls(cells=cells, items=items,
                   free_reported=int(probe.get("free_squares", -1) or -1),
                   grid_squares=int(probe.get("grid_squares", -1) or -1))

    # ---- 查询 ----

    def item(self, uid: str) -> ItemFit | None:
        return self.items.get(uid)

    def in_bag(self) -> list[ItemFit]:
        return [it for it in self.items.values() if it.in_bag and it.cells]

    def loose(self) -> list[ItemFit]:
        return [it for it in self.items.values() if not it.in_bag]

    def occupied(self, exclude: Iterable[str] = ()) -> frozenset[Cell]:
        """按「还在包里、且有 cells 的物品」算占用（和桥接的 OccupiedCells 对齐）。"""
        skip = set(exclude)
        used: set[Cell] = set()
        for it in self.items.values():
            if it.uid in skip or not it.in_bag:
                continue
            used |= it.cells
        return frozenset(used)

    def free(self, exclude: Iterable[str] = ()) -> frozenset[Cell]:
        return self.cells - self.occupied(exclude)

    def free_count(self, exclude: Iterable[str] = ()) -> int:
        return len(self.free(exclude))


# ---------- 计划 ----------


@dataclass
class Plan:
    steps: list[dict[str, Any]] = field(default_factory=list)
    why: str = ""
    needs_drop: bool = False
    impossible: bool = False

    def __bool__(self) -> bool:
        return bool(self.steps)

    def describe(self) -> str:
        if self.steps:
            return " -> ".join(
                f"{s.get('type')}({s.get('uid') or s.get('a', '')}{'@' + s['cell'] if 'cell' in s else ''})"
                for s in self.steps)
        return self.why or "no plan"


def _place_step(uid: str, p: Placement) -> dict[str, Any]:
    step = {"type": "place_item", "uid": uid}
    step.update(p.as_args())
    return step


def _move_step(uid: str, p: Placement) -> dict[str, Any]:
    step = {"type": "move_item", "uid": uid}
    step.update(p.as_args())
    return step


def _sort_placements(bag: Bag, placements: list[Placement]) -> list[Placement]:
    """把落点排成「先试更像人摆的」顺序：贴边、紧凑、靠左下，且行为确定。

    纯为了可复现：同样的背包 + 同样的表，一定选同一个落点（方便 A/B 和单测）。
    """
    cells = bag.cells
    occupied = bag.occupied()

    def key(p: Placement) -> tuple[int, int, int, int]:
        # 1) 贴边（footprint 的邻格里有多少是背包外或已占 -> 越挤越优先）
        touch = 0
        for c in p.cells:
            for dx, dy in _NEIGHBORS:
                n = (c[0] + dx, c[1] + dy)
                if n not in cells or n in occupied:
                    touch += 1
        return (-touch, p.cell[1], p.cell[0], p.rot)

    return sorted(placements, key=key)


def place_options(bag: Bag, uid: str, exclude: Iterable[str] = ()) -> list[Placement]:
    """这件物品现在能放在哪（自身旧格算空，`exclude` 里的物品也当不在）。"""
    it = bag.item(uid)
    if it is None:
        return []
    blocked = bag.occupied(exclude=set(exclude) | {uid})
    return _sort_placements(bag, it.placements(blocked, bag.cells))


def plan_place(bag: Bag, uid: str, *, max_moves: int = 1, allow_swap: bool = True,
               allow_repack: bool = True) -> Plan:
    """把 `uid`（通常是一件地上的战利品）放进背包的计划。

    依次尝试：
      1. 直接放（4 个朝向 × 所有空格）——这就是「按朝向搜落点」
      2. 先挪**一件**背包物品，再放
      3. 先 swap 两件背包物品（等体积互换，最省事），再放
      4. 做一次「从零重排」（大件先放），再放——碎片化时才用
    都不行 -> `needs_drop=True`；连形状都塞不进背包 -> `impossible=True`（丢东西也没用）。
    """
    it = bag.item(uid)
    if it is None:
        return Plan(why=f"no probe data for {uid}", impossible=True)

    if not it.rotations:
        return Plan(why=f"no footprint table for {uid}", impossible=True)

    if not it.fits_anywhere(bag.cells):
        return Plan(why=f"{it.name or uid} can never fit ({it.needs} cells)", impossible=True)

    # 1) 直接放
    options = place_options(bag, uid)
    if options:
        return Plan([_place_step(uid, options[0])], why=f"place {it.name or uid} directly")

    # 2) 挪一件，再放
    if max_moves >= 1:
        for other in _movable(bag, uid):
            for p in place_options(bag, other.uid):
                if p.cells == other.cells:
                    continue
                new_occ = (bag.occupied() - other.cells) | p.cells
                target = bag.item(uid)
                hits = [pl for pl in target.placements(new_occ, bag.cells)]
                if hits:
                    hits = _sort_placements(bag, hits)
                    return Plan([_move_step(other.uid, p), _place_step(uid, hits[0])],
                                why=f"move {other.name or other.uid} out of the way")

    # 3) swap 两件（等体积），再放
    if allow_swap and max_moves >= 1:
        blockers = _blockers(bag, uid)
        for a in blockers:
            for b in _movable(bag, uid, exclude={a.uid}):
                if len(a.cells) != len(b.cells):
                    continue
                sp = plan_swap(bag, a.uid, b.uid)
                if not sp:
                    continue
                new_occ = _occupancy_after_swap(bag, a, b, sp)
                target = bag.item(uid)
                hits = [pl for pl in target.placements(new_occ, bag.cells)]
                if hits:
                    hits = _sort_placements(bag, hits)
                    return Plan(sp.steps + [_place_step(uid, hits[0])],
                                why=f"swap {a.name or a.uid} <-> {b.name or b.uid}")

    # 4) 从零重排（碎片化）
    if allow_repack and max_moves >= 3:
        repack = plan_repack(bag)
        if repack:
            new_occ = _occupancy_after_moves(bag, repack.steps)
            target = bag.item(uid)
            hits = [pl for pl in target.placements(new_occ, bag.cells)]
            if hits:
                hits = _sort_placements(bag, hits)
                return Plan(repack.steps + [_place_step(uid, hits[0])],
                            why="repack the bag")

    return Plan(why=f"no room for {it.name or uid} ({it.needs} cells, "
                    f"free={bag.free_count()})", needs_drop=True)


def _movable(bag: Bag, target_uid: str, exclude: Iterable[str] = ()) -> list[ItemFit]:
    skip = set(exclude) | {target_uid}
    return [it for it in bag.in_bag() if it.uid not in skip]


def _blockers(bag: Bag, uid: str) -> list[ItemFit]:
    """挡住「这件东西最可能想占的位置」的背包物品。"""
    it = bag.item(uid)
    if it is None:
        return []
    wanted: set[Cell] = set()
    for _rot, table in it.rotations.items():
        for foot in table.values():
            wanted |= foot
    out = []
    for other in bag.in_bag():
        if other.cells & wanted:
            out.append(other)
    return out


def _occupancy_after_moves(bag: Bag, steps: list[dict[str, Any]]) -> frozenset[Cell]:
    occ = set(bag.occupied())
    for step in steps:
        if step.get("type") != "move_item":
            continue
        it = bag.item(step["uid"])
        if it is None:
            continue
        foot = _footprint_for(it, step)
        if foot is None:
            continue
        occ -= it.cells
        occ |= foot
    return frozenset(occ)


def _footprint_for(it: ItemFit, step: dict[str, Any]) -> frozenset[Cell] | None:
    rot = int(step.get("rot_deg", 0))
    cell = parse_cell(step["cell"])
    table = it.rotations.get(rot)
    if table is None:
        return None
    return table.get(cell)


def plan_swap(bag: Bag, a_uid: str, b_uid: str) -> Plan | None:
    """两件互换：A 去 B 的地盘、B 去 A 的地盘（允许用上一点点空格）。

    注意中间态：桥接的 `swap_items` 是「先把 A 从背包里摘下来（旧格算空）→ 放 B →
    再放 A」，所以这里的两步只要求：
        footprint(B) ⊆ 背包 - 其他物品      （A 已经不在占用里）
        footprint(A) ⊆ 背包 - 其他物品 - B  （B 落位之后就按新位置算）
    """
    a = bag.item(a_uid)
    b = bag.item(b_uid)
    if a is None or b is None or not a.in_bag or not b.in_bag:
        return None

    others = bag.occupied() - a.cells - b.cells
    free = bag.cells - others

    b_area = b.cells
    a_area = a.cells

    for pb in b.placements(others, bag.cells):
        # B 必须真的占到 A 原来的地盘里（否则叫「挪」不叫「换」）
        if not (pb.cells & a_area):
            continue
        room_for_a = free - pb.cells
        for pa in a.placements(others | pb.cells, bag.cells):
            if not (pa.cells & b_area):
                continue
            if not (pa.cells <= room_for_a):
                continue
            steps = [{
                "type": "swap_items",
                "a": a_uid, "b": b_uid,
                "a_cell": fmt_cell(pa.cell), "a_rot": pa.rot,
                "b_cell": fmt_cell(pb.cell), "b_rot": pb.rot,
            }]
            return Plan(steps, why=f"swap {a.name or a_uid} <-> {b.name or b_uid}")
    return None


def _occupancy_after_swap(bag: Bag, a: ItemFit, b: ItemFit, plan: Plan) -> frozenset[Cell]:
    step = plan.steps[0]
    pa = _footprint_for(a, {"cell": step["a_cell"], "rot_deg": step["a_rot"]})
    pb = _footprint_for(b, {"cell": step["b_cell"], "rot_deg": step["b_rot"]})
    occ = set(bag.occupied())
    occ -= a.cells
    occ -= b.cells
    if pb:
        occ |= pb
    if pa:
        occ |= pa
    return frozenset(occ)


def plan_repack(bag: Bag) -> Plan | None:
    """从零重排：大件先放、尽量塞在左下角。只在碎片化、单步挪不动时用。

    **关键点：每一步都必须是「按当时的占用立马就能执行」的**。桥接的 move_item 会
    按当前 `gridPositions` 验 footprint，所以不能把「A 等 B 挪走之后才能挪」的顺序
    直接发出去。这里先算一个目标布局，再模拟执行：反复挑「现在就能落到目标位」的
    物品挪过去，直到没人能动；还剩没到位的就整案作废（宁可丢东西，也不发会失败的步骤）。
    """
    items = sorted(bag.in_bag(), key=lambda it: (-it.shape_area(), it.name))
    if len(items) < 2:
        return None

    # 1) 目标布局（忽略当前占用，从零摆）
    target: dict[str, Placement] = {}
    layout: frozenset[Cell] = frozenset()
    for it in items:
        options = _sort_placements(bag, it.placements(layout, bag.cells))
        if not options:
            return None
        target[it.uid] = options[0]
        layout |= options[0].cells

    # 2) 模拟执行
    occ = bag.occupied()
    todo = [it for it in items if target[it.uid].cells != it.cells]
    if not todo:
        return None

    steps: list[dict[str, Any]] = []
    for _guard in range(16):
        progressed = False
        for it in list(todo):
            want = target[it.uid]
            others = occ - it.cells
            if want.cells & others:
                continue                    # 目标位现在还被别人占着，等下一轮
            steps.append(_move_step(it.uid, want))
            occ = others | want.cells
            todo.remove(it)
            progressed = True
        if not todo:
            return Plan(steps, why=f"repack {len(steps)} item(s)")
        if not progressed:
            return None
    return None


# ---------- 丢哪一件 ----------


def item_value(name: str, ledger: Any, db: Any = None) -> float:
    """粗估值：账本里测到的「伤害 + 格挡 + 状态效果」，没样本给 1.0。

    没测过的物品不当作 0：0 会让它永远第一个被丢，而它可能是这局最好的东西。

    `db`（`agent/itemdb.py` 的 ItemDB）给了静态类别信息后，按类别补一点「不该丢」的分：
    续航/遗物最保值，每回合护甲次之，辅助看它给多少。这些是**加分**，不是覆盖账本，
    所以「账本测出来很强」的物品仍然排在前面。
    """
    scored = False
    value = 0.0
    if ledger is not None:
        dmg = ledger.estimate_damage(name)
        blk = ledger.estimate_block(name)
        eff = ledger.effect_score(name)
        if dmg is not None or blk or eff:
            value = float(dmg or 0.0) + float(blk or 0.0) + float(eff or 0.0)
            scored = True
    if db is not None:
        st = db.get(name)
        if st is not None:
            if st.category == "sustain":
                value += 4.0 + min(st.heal, 20.0) * 0.2
            elif st.category == "relic":
                value += 3.0
            elif st.block_per_turn > 0:
                # 每回合护甲 = 每回合白赚一份格挡，按「7 甲 = 1 费 × 3 回合」折现
                # （Knight's Armor 6/turn ≈ 14 等价伤害，木剑账本 5.4）。
                # 以前这里是 `2.0 + block_per_turn * 0.5`（6/turn = 5.0），
                # 比木剑还低，腾位时第一件被丢的就是护甲。
                value += 1.0 + per_turn_value(st.block_per_turn)
            elif st.block > 0:
                # 只有一次性格挡、账本又没样本时也别当成 0.5：
                # 一件没试过的 7 挡盾牌，静态值给 0.5 会被第一个丢掉。
                value += 0.5 + st.block * 0.5
            elif st.category == "support":
                value += 1.0 + (st.support_damage + st.support_block) * 0.3
            elif st.category == "attack":
                value += 0.0                 # 让账本/静态数值自己说话
            if "curse" in st.tags:
                value -= 2.0
    if not scored and db is None:
        return 1.0
    return value if value > 0 else 0.5


def drop_ranking(bag: Bag, ledger: Any, protected: Iterable[str] = (),
                 db: Any = None, keep: tuple[str, ...] = ("sustain", "relic")
                 ) -> list[tuple[float, ItemFit]]:
    """按「最该丢的先」排序：价值低优先，同价值时大的优先（腾得多）。

    `--sustain` 打开（且给了 `db`）时，`keep` 里的类别默认不进候选——
    食物/遗物是续航来源，为了塞一件普通武器把它们丢掉是本末倒置。
    但如果背包里**全是**这类东西，还是会退回去丢一件（否则永远腾不出位置）。
    """
    skip = set(protected)
    rows: list[tuple[float, ItemFit]] = []
    kept: list[tuple[float, ItemFit]] = []
    attacks: list[tuple[float, ItemFit]] = []
    for it in bag.in_bag():
        if it.uid in skip:
            continue
        value = item_value(it.name, ledger, db)
        if it.uid and it.name and it.name.strip().upper().endswith("AMULET"):
            value += 3.0           # 饰品多为被动，没见过也先留着
        row = (value, it)
        st = db.get(it.name) if db is not None else None
        if st is not None and st.category == "attack" and st.damage_active > 0:
            attacks.append(row)
        if st is not None and st.category in keep:
            kept.append(row)
        else:
            rows.append(row)

    # 唯一的主力输出不丢：真机上出现过「为捡 ELECTRIC WAND 把 WOODEN SWORD 丢了」，
    # 之后整局只能用盾挡、29 点伤害打进 11 血直接死。宁可少捡一件。
    if db is not None and len(attacks) == 1 and attacks[0] in rows:
        rows.remove(attacks[0])
        kept.append(attacks[0])

    rows = rows or kept               # 只剩续航/遗物/主武器时才动它们
    rows.sort(key=lambda row: (row[0], -row[1].shape_area(), row[1].name))
    return rows


def choose_drop(bag: Bag, ledger: Any, protected: Iterable[str] = (),
                db: Any = None) -> ItemFit | None:
    ranked = drop_ranking(bag, ledger, protected, db)
    return ranked[0][1] if ranked else None


# ---------- 调试 ----------


def load_probe(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def render(bag: Bag) -> str:
    """把背包画成字符画（调试 / 落盘证据用）。"""
    if not bag.cells:
        return "(empty bag)"
    xs = [c[0] for c in bag.cells]
    ys = [c[1] for c in bag.cells]
    owner: dict[Cell, str] = {}
    for idx, it in enumerate(bag.in_bag()):
        mark = chr(ord("A") + (idx % 26))
        for c in it.cells:
            owner[c] = mark
    lines = []
    for y in range(max(ys), min(ys) - 1, -1):
        row = []
        for x in range(min(xs), max(xs) + 1):
            c = (x, y)
            if c not in bag.cells:
                row.append(" .")
            else:
                row.append(" " + owner.get(c, "_"))
        lines.append(f"{y:>3} " + "".join(row))
    return "\n".join(lines)
