"""效果账本：用实验回答「这个物品到底干什么」。

MVP §3.8 的意思：Backpack Hero 有 800+ 物品，效果写在代码里而不是数据里。
不靠读代码，靠打一次记一次。

键用 `displayName` 而不是 instance id——instance id 每局都变，名字跨局稳定。
注意名字带尾随空格（SPIKE-02 §3.3），存之前统一 `strip()`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "items.json"


class Ledger:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self.data: dict[str, dict[str, Any]] = self._load()

    # ---------- 持久化 ----------

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return raw.get("items", {}) if isinstance(raw, dict) else {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "schema": "bph.items.v1",
            "note": "均值是踩坑记录，不是官方数据：样本少时波动大，别当精确值用；"
                    "effects 来自结构化战斗回执（SPIKE-09）",
            "items": self.data,
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    # ---------- 查询 ----------

    @staticmethod
    def _key(name: str) -> str:
        return (name or "").strip()

    def get(self, name: str) -> dict[str, Any] | None:
        return self.data.get(self._key(name))

    def estimate_damage(self, name: str) -> float | None:
        """没样本返回 None——调用方据此决定「探索一次」。"""
        e = self.get(name)
        if not e or e.get("samples", 0) <= 0:
            return None
        return float(e["damage_sum"]) / e["samples"]

    def estimate_block(self, name: str) -> float:
        e = self.get(name)
        if not e or e.get("samples", 0) <= 0:
            return 0.0
        return float(e.get("block_sum", 0)) / e["samples"]

    def estimate_energy(self, name: str) -> float:
        """这件东西一次净回多少能量（负数=要花掉）。

        没有能量字段的老条目一律当 0：那时根本没记过这一项，不能当成「测出来是 0」。
        """
        e = self.get(name)
        if not e or e.get("samples", 0) <= 0:
            return 0.0
        return float(e.get("energy_sum", 0)) / e["samples"]

    def estimate_effects(self, name: str) -> dict[str, float]:
        """这件东西每次使用平均产生多少「不可见效果」。

        键形如 `status:BURN:enemy` / `max_hp` / `luck:rare` / `item_status:reverseHourglass`。
        没有回执的老账本返回空字典——不能把「没测过」当成「测出来是 0」。
        """
        e = self.get(name)
        if not e or e.get("samples", 0) <= 0:
            return {}
        n = max(1, int(e.get("samples", 0)))
        out: dict[str, float] = {}
        for key, agg in (e.get("effects") or {}).items():
            out[key] = float(agg.get("sum", 0)) / n
        return out

    def effect_score(self, name: str) -> float:
        """给 policy 用的粗估值：只算方向明确的正面效果。

        `items:spawned` / `phase:inventory` 仍然记进账本（供人复盘），
        但不参与打分——生成的不一定是好东西，进整理阶段通常是代价。
        每个状态点/最大生命/幸运/物品状态算 1 分。
        """
        positive = ("status:", "max_hp", "luck:", "item_status:", "heal", "gold")
        return sum(v for k, v in self.estimate_effects(name).items()
                   if k.startswith(positive))

    # ---------- 记录 ----------

    def record_use(self, name: str, damage: int, block: int, absorbed: int = 0,
                   energy: int = 0, receipt: dict[str, Any] | None = None) -> None:
        """记一次使用效果。

        damage   实际打掉的血
        block    给自己加的护甲
        absorbed 被对方格挡吃掉的量——单独记，否则「打中但被挡」会和
                 「这物品根本没伤害」混成一谈（SPIKE-04 实测踩到）
        energy   这次用下来**净**回的能量（`delta.player_energy`）。
                 回能量的消耗品全靠这一项才可见：它 0 伤害 0 格挡，
                 不记能量在打分里就是个纯 0 分的废物。
        receipt  桥接的结构化战斗回执；把 delta 看不见的状态/属性/生成物记进 `effects`。
        """
        k = self._key(name)
        if not k:
            return
        e = self.data.setdefault(k, {"samples": 0, "damage_sum": 0, "block_sum": 0})
        e["samples"] = int(e.get("samples", 0)) + 1
        e["damage_sum"] = int(e.get("damage_sum", 0)) + int(damage)
        e["block_sum"] = int(e.get("block_sum", 0)) + int(block)
        if absorbed:
            e["absorbed_sum"] = int(e.get("absorbed_sum", 0)) + int(absorbed)
        if energy:
            e["energy_sum"] = int(e.get("energy_sum", 0)) + int(energy)
        if receipt:
            self._record_effects(e, receipt)

    @staticmethod
    def _effect_key(eff: dict[str, Any]) -> str | None:
        kind = eff.get("kind")
        if kind == "status":
            status = eff.get("status") or eff.get("type")
            return "status:%s:%s" % (status, eff.get("to", "?"))
        if kind == "item_status":
            return "item_status:%s" % (eff.get("status") or eff.get("type"))
        if kind == "max_hp":
            return "max_hp"
        if kind == "luck":
            return "luck:%s" % eff.get("stat", "?")
        if kind == "items":
            return "items:%s" % eff.get("field", "?")
        if kind == "phase":
            return "phase:%s:%s" % (eff.get("field", "?"), eff.get("to", "?"))
        if kind == "gold":
            return "gold"
        if kind == "heal" and eff.get("to") == "player":
            return "heal"
        return None

    def _record_effects(self, entry: dict[str, Any], receipt: dict[str, Any]) -> None:
        """只收 delta 看不见的效果：状态 / 最大生命 / 幸运 / 物品自身状态。

        伤害和护甲仍由调用方按 delta 记，避免同一件事记两遍。
        """
        effects = entry.setdefault("effects", {})
        for eff in receipt.get("effects") or []:
            if not isinstance(eff, dict):
                continue
            key = self._effect_key(eff)
            if not key:
                continue
            delta = eff.get("delta")
            if delta is None:
                if eff.get("kind") == "items":
                    delta = eff.get("count", 0)
                elif eff.get("kind") == "phase":
                    delta = 1 if eff.get("from") != eff.get("to") else 0
                else:
                    delta = eff.get("amount", 0)
            try:
                amount = abs(float(delta))
            except (TypeError, ValueError):
                continue
            if amount <= 0:
                continue
            agg = effects.setdefault(key, {"count": 0, "sum": 0.0})
            agg["count"] = int(agg.get("count", 0)) + 1
            agg["sum"] = float(agg.get("sum", 0.0)) + amount

    # ---------- 报告 ----------

    def summary(self, limit: int = 20) -> str:
        if not self.data:
            return "(账本为空)"
        rows = []
        for name, e in self.data.items():
            n = max(1, int(e.get("samples", 0)))
            rows.append((float(e.get("damage_sum", 0)) / n, n, name,
                          float(e.get("block_sum", 0)) / n))
        rows.sort(reverse=True)
        out = [f"{'物品':28} {'样本':>4} {'均伤':>6} {'均挡':>6}"]
        for dmg, n, name, blk in rows[:limit]:
            out.append(f"{name[:28]:28} {n:>4} {dmg:>6.2f} {blk:>6.2f}")
        return "\n".join(out)
