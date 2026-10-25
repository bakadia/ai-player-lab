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
            "note": "均值是踩坑记录，不是官方数据：样本少时波动大，别当精确值用",
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

    # ---------- 记录 ----------

    def record_use(self, name: str, damage: int, block: int, absorbed: int = 0) -> None:
        """记一次使用效果。

        damage   实际打掉的血
        block    给自己加的护甲
        absorbed 被对方格挡吃掉的量——单独记，否则「打中但被挡」会和
                 「这物品根本没伤害」混成一谈（SPIKE-04 实测踩到）
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
