"""按日期查询：哪些事实可以作为判定依据，哪些不可以、为什么。

这是陈述判定和发布前闸门使用的查询接口：

```python
view = basis_view(load_store("STORE"), date(2026, 10, 8))
view.usable                 # 可作为确定依据的事实（当日有效版本）
view.unusable               # 不可用的事实及原因
view.status("F-S7-PRICE")   # 单个 fact_id 的状态
view.history("F-S7-PRICE")  # 全部历史版本
view.stale("F-S7-PRICE")    # 当日已过期或被取代的旧版本
```

“可作为确定依据”必须同时满足：当日有效、在事实区、状态为 active、绑定了证据、
且不处于未裁决的冲突中。观点区和营销表达区的条目永远不会作为依据。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from geo_facts.store.conflicts import conflicting_fact_ids
from geo_facts.store.model import FactStore
from geo_facts.store.timeline import (
    REASON_CODES,
    basis_blockers,
    describe_reasons,
    resolve,
    stale_versions,
)


@dataclass(frozen=True)
class FactStatus:
    """某个 fact_id 在查询日的状态。

    state：effective（有当日有效版本）、expired（已过期）、not_yet_effective（尚未生效）。
    fact：effective 时为当日有效版本；expired 时为最近过期的版本；
    not_yet_effective 时为最早将生效的版本。
    reasons：不能作为确定依据的原因代码，空元组表示可以。取值见 `REASON_CODES`：
    expired、not_yet_effective、not_fact_zone、retracted、disputed、no_evidence、conflict。
    conflicts_with：当日与之冲突的 fact_id。
    """

    fact_id: str
    state: str
    fact: dict[str, Any]
    reasons: tuple[str, ...]
    conflicts_with: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return not self.reasons

    @property
    def version(self) -> int:
        return int(self.fact["version"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "version": self.version,
            "state": self.state,
            "usable": self.usable,
            "reasons": list(self.reasons),
            "reason_text": describe_reasons(self.reasons),
            "conflicts_with": list(self.conflicts_with),
            "zone": self.fact["zone"],
            "is_core": self.fact["is_core"],
        }


@dataclass(frozen=True)
class BasisView:
    """某一天的事实库视图。statuses 按 fact_id 升序。"""

    as_of: date
    statuses: dict[str, FactStatus]
    versions: dict[str, list[dict[str, Any]]]

    @property
    def usable(self) -> list[dict[str, Any]]:
        """可作为确定依据的事实（每个 fact_id 一条：当日有效版本）。"""
        return [status.fact for status in self.statuses.values() if status.usable]

    @property
    def unusable(self) -> list[FactStatus]:
        """不可作为确定依据的事实及原因。"""
        return [status for status in self.statuses.values() if not status.usable]

    def status(self, fact_id: str) -> FactStatus | None:
        return self.statuses.get(fact_id)

    def history(self, fact_id: str) -> list[dict[str, Any]]:
        """某个 fact_id 的全部版本（版本号升序）；没有这个 fact_id 时为空列表。"""
        return list(self.versions.get(fact_id, []))

    def stale(self, fact_id: str) -> list[tuple[dict[str, Any], str]]:
        """查询日已不再生效的旧版本及原因（expired 过期 / superseded 被更高版本取代）。"""
        return stale_versions(self.versions.get(fact_id, []), self.as_of)

    def reason_counts(self) -> dict[str, int]:
        counts = dict.fromkeys(REASON_CODES, 0)
        for status in self.statuses.values():
            for reason in status.reasons:
                counts[reason] += 1
        return counts


def basis_view(store: FactStore, as_of: date) -> BasisView:
    """计算查询日的可用与不可用事实。"""
    grouped = store.by_fact_id()
    conflicts = conflicting_fact_ids(store, as_of)
    statuses: dict[str, FactStatus] = {}
    for fact_id in sorted(grouped):
        resolution = resolve(fact_id, grouped[fact_id], as_of)
        fact = resolution.fact
        assert fact is not None  # grouped 里的每个 fact_id 至少有一个版本
        found: set[str] = set(basis_blockers(fact))
        if resolution.state != "effective":
            found.add(resolution.state)
        # 冲突只在当日有效的版本之间计算，所以未生效、已过期的 fact_id 不会出现在 conflicts 里。
        others = tuple(conflicts.get(fact_id, ()))
        if others:
            found.add("conflict")
        statuses[fact_id] = FactStatus(
            fact_id=fact_id,
            state=resolution.state,
            fact=fact,
            reasons=tuple(code for code in REASON_CODES if code in found),
            conflicts_with=others,
        )
    return BasisView(as_of=as_of, statuses=statuses, versions=grouped)
