"""v1.5.0 经验/实体共现（调研 §2.12）。

- **无向去序**：`pair = "|".join(sorted({a, b}))` —— `a|b` 与 `b|a` 合并，不分裂计数；
- **只在成功时更新**：`observe(graph, ok=True)`；失败样本完全不进（不污染经验）；
- 采集：实体共现对 + 单实体频次 + IR 模式（节点 kind / adapter / mode 计数）；
- 存储：`{root}/experience.json`（原子写）；
- 出口：`export()` 结构化 JSON，供 MA 消费（AF 采集事实 → MA 生成假设，双向）。
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .af_flock import FileLock
from .af_atomic import atomic_write_text

__all__ = ["ExperienceStore", "EXPERIENCE_FILENAME"]

EXPERIENCE_FILENAME = "experience.json"
#: F11② 经验先验饱和尺度：total 经验证据达到该量级时 mean→1（仍严格 <1）
EXPERIENCE_PRIOR_SCALE = 8.0
#: F11② 经验先验权重上限：伪计数封顶，防止单点高频经验碾压真实时序证据
EXPERIENCE_PRIOR_WEIGHT_CAP = 20.0


class ExperienceStore:
    """实体共现/IR 模式经验库（单文件原子写）。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @property
    def path(self) -> Path:
        return self.root / EXPERIENCE_FILENAME

    # ── 读写 ──
    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("observed", 0)
        data.setdefault("pairs", {})
        data.setdefault("entities", {})
        data.setdefault("patterns", {"kinds": {}, "adapters": {}, "modes": {}})
        return data

    def _save(self, data: dict[str, Any]) -> None:
        data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        atomic_write_text(
            self.path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
        )

    # ── 采集 ──
    def observe(self, graph: Any, *, ok: bool = True) -> dict[str, Any]:
        """从一张 Graph 采集经验；**仅成功**（`ok=True`）时更新。"""
        if not ok:
            return {"updated": False, "reason": "failed-run-not-recorded"}

        entities: set[str] = set()
        kinds: dict[str, int] = {}
        adapters: dict[str, int] = {}
        modes: dict[str, int] = {}
        automations = 0
        for auto in graph:
            automations += 1
            entities |= auto.reads() | auto.writes()
            modes[auto.mode] = modes.get(auto.mode, 0) + 1
            for node in auto.nodes.values():
                kinds[node.kind] = kinds.get(node.kind, 0) + 1
                if node.adapter:
                    adapters[node.adapter] = adapters.get(node.adapter, 0) + 1

        # 读-改-写必须持锁：审计发现无锁时并发 observe 会互相覆盖计数
        with FileLock(str(self.path) + ".lock", timeout=10.0):
            data = self._load()
            data = self._accumulate(data, entities, kinds, adapters, modes)
            self._save(data)
        return {
            "updated": True,
            "automations": automations,
            "entities": len(entities),
            "pairs": len(entities) * (len(entities) - 1) // 2,
        }

    @staticmethod
    def _accumulate(
        data: dict[str, Any],
        entities: set[str],
        kinds: dict[str, int],
        adapters: dict[str, int],
        modes: dict[str, int],
    ) -> dict[str, Any]:
        """把一次观测累加进 data（**调用方需持锁**）。"""
        sorted_ents = sorted(entities)
        for i, a in enumerate(sorted_ents):
            for b in sorted_ents[i + 1 :]:
                pair = "|".join(sorted((a, b)))
                data["pairs"][pair] = int(data["pairs"].get(pair, 0)) + 1
        for e in sorted_ents:
            data["entities"][e] = int(data["entities"].get(e, 0)) + 1

        pat = data["patterns"]
        for key, counter in (("kinds", kinds), ("adapters", adapters), ("modes", modes)):
            bucket = pat.setdefault(key, {})
            for name, n in counter.items():
                bucket[name] = int(bucket.get(name, 0)) + n

        data["observed"] = int(data.get("observed", 0)) + 1
        return data

    # ── 查询 ──
    def top_pairs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        data = self._load()
        items = [{"pair": k, "count": int(v)} for k, v in data["pairs"].items()]
        items.sort(key=lambda it: (-it["count"], it["pair"]))
        return items[: max(0, int(limit))]

    def top_entities(self, *, limit: int = 20) -> list[dict[str, Any]]:
        data = self._load()
        items = [{"entity_id": k, "count": int(v)} for k, v in data["entities"].items()]
        items.sort(key=lambda it: (-it["count"], it["entity_id"]))
        return items[: max(0, int(limit))]

    def entity_counts(self) -> dict[str, int]:
        """全量「实体被成功使用次数」快照（供消费方做先验 / 破同分）。"""
        data = self._load()
        return {k: int(v) for k, v in (data.get("entities") or {}).items()}

    def entity_count(self, entity_id: str) -> int:
        """单实体被成功观测的次数（`0` = 无经验记录）。"""
        return int((self._load().get("entities") or {}).get(entity_id, 0))

    def co_occurrences(self, entity_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """与给定实体共现过的其他实体，按共现次数降序（无向去序存储，此处定向还原）。"""
        out: list[dict[str, Any]] = []
        for pair, n in (self._load().get("pairs") or {}).items():
            left, _, right = str(pair).partition("|")
            other = right if left == entity_id else (left if right == entity_id else None)
            if other:
                out.append({"entity_id": other, "count": int(n)})
        out.sort(key=lambda it: (-it["count"], it["entity_id"]))
        return out[: max(0, int(limit))]

    def empirical_prior(
        self,
        entities: Iterable[str],
        adapters: Iterable[str],
    ) -> tuple[float, float]:
        """经验先验（v2.4/F11②）：把一个 automation 的实体/适配器成功使用频次转成贝叶斯先验。

        返回 ``(mean, weight)``：
        - ``mean`` ∈ [0,1)：使用越频繁 → 越接近 1，表达「该 automation 是现实中活跃、反复出现的
          例行」；``0`` 表示零经验（调用方据此**不注入**先验，行为与接入前完全一致）。
        - ``weight`` ≥ 0：经验证据量（伪计数），喂给 ``af_predict`` 的概率合成做平滑；封顶于
          ``EXPERIENCE_PRIOR_WEIGHT_CAP``，避免单点高频经验碾压真实时序证据。

        **语义是「活跃度/确立度」先验，而非触发率估计**：经验库只记成功使用的频次，不记触发
        时刻，故不能直接预测「何时触发」，只能表达「这条 routine 是否真实存在、值得信任」。
        零经验 → ``(0.0, 0.0)``。
        """
        total = 0
        for e in entities:
            total += self.entity_count(str(e))
        patterns = self._load().get("patterns") or {}
        for a in adapters:
            total += int((patterns.get("adapters") or {}).get(str(a), 0))
        if total <= 0:
            return (0.0, 0.0)
        mean = 1.0 - math.exp(-total / EXPERIENCE_PRIOR_SCALE)
        weight = min(float(total), EXPERIENCE_PRIOR_WEIGHT_CAP)
        return (mean, weight)

    def patterns(self) -> dict[str, Any]:
        return self._load()["patterns"]

    def export(self, *, limit: int = 200) -> dict[str, Any]:
        """结构化导出（喂 MA）。"""
        data = self._load()
        return {
            "ok": True,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "observed": int(data.get("observed", 0)),
            "pairs": self.top_pairs(limit=limit),
            "entities": self.top_entities(limit=limit),
            "patterns": data["patterns"],
        }

    def summary(self, *, limit: int = 10) -> dict[str, Any]:
        """轻量摘要（HTTP/MCP 默认返回）。"""
        return {
            "ok": True,
            "observed": int(self._load().get("observed", 0)),
            "top_pairs": self.top_pairs(limit=limit),
            "top_entities": self.top_entities(limit=limit),
            "patterns": self.patterns(),
        }

    def clear(self) -> None:
        with FileLock(str(self.path) + ".lock", timeout=10.0):
            self._save(
                {"observed": 0, "pairs": {}, "entities": {}, "patterns": {"kinds": {}, "adapters": {}, "modes": {}}}
            )
