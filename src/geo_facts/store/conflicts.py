"""冲突检测：同实体、同属性、同适用条件、有效期重叠、归一后的值不同。

只列出冲突，不自动裁决。规则（写进 docs/methodology.md）：
- 只看事实区、未撤回的条目；同一 fact_id 的不同版本是“更新”，不算冲突；
- 属性相同 = 属性名折叠后相同，或一方的属性名是另一方的别名；
- 条件相同 = 归一后的条件完全一致（CLTC 与 WLTC 是不同条件，不构成冲突）；
- 值不同 = 归一后不完全相同（21.98 万元 与 219800 元 相同；620 km 与 600～650 km 不同）；
- 重叠 = 存在某一天，两条都是各自 fact_id 当日有效的版本。给了 --as-of 时只看那一天。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from geo_facts.normalize import compact, normalize_fact_value, same_conditions, same_value
from geo_facts.store.model import FactStore
from geo_facts.store.timeline import change_days, effective_version

Period = tuple[date, date | None]

CONFLICT_NOTE = (
    "同实体、同属性、同条件、有效期重叠但值不同；本工具不自动裁决，请人工核对后撤回或更正其中一条。"
)


def attribute_keys(fact: dict[str, Any]) -> tuple[str, frozenset[str]]:
    """（属性名的键, 别名的键集合）。"""
    aliases = frozenset(compact(alias) for alias in fact.get("aliases", []))
    return compact(fact["attribute"]), aliases


def same_attribute(first: dict[str, Any], second: dict[str, Any]) -> bool:
    name_a, aliases_a = attribute_keys(first)
    name_b, aliases_b = attribute_keys(second)
    return name_a == name_b or name_a in aliases_b or name_b in aliases_a


def values_differ(first: dict[str, Any], second: dict[str, Any]) -> bool:
    value_a = normalize_fact_value(first["value"], first["unit"])
    value_b = normalize_fact_value(second["value"], second["unit"])
    if value_a is None or value_b is None:
        return (first["value"], first["unit"]) != (second["value"], second["unit"])
    return not same_value(value_a, value_b)


def _window(first: dict[str, Any], second: dict[str, Any]) -> Period | None:
    """两条版本有效期的交集；不相交时为 None。"""
    start = max(date.fromisoformat(first["valid_from"]), date.fromisoformat(second["valid_from"]))
    ends = [
        date.fromisoformat(item["valid_until"])
        for item in (first, second)
        if item["valid_until"] is not None
    ]
    end = min(ends) if ends else None
    if end is not None and end < start:
        return None
    return start, end


def overlap_periods(
    first: dict[str, Any],
    second: dict[str, Any],
    versions_a: list[dict[str, Any]],
    versions_b: list[dict[str, Any]],
) -> list[Period]:
    """两条版本同时是各自 fact_id “当日有效版本”的连续时段（按时间升序）。"""
    window = _window(first, second)
    if window is None:
        return []
    start, end = window
    points = {start}
    for day in change_days([*versions_a, *versions_b]):
        if day > start and (end is None or day <= end):
            points.add(day)
    ordered = sorted(points)
    periods: list[Period] = []
    for index, day in enumerate(ordered):
        both = (
            effective_version(versions_a, day) is first
            and effective_version(versions_b, day) is second
        )
        if not both:
            continue
        last = ordered[index + 1] - timedelta(days=1) if index + 1 < len(ordered) else end
        previous_end = periods[-1][1] if periods else None
        if previous_end is not None and previous_end + timedelta(days=1) == day:
            periods[-1] = (periods[-1][0], last)
        else:
            periods.append((day, last))
    return periods


def _brief(fact: dict[str, Any]) -> dict[str, Any]:
    evidence = fact["evidence"]
    return {
        "fact_id": fact["fact_id"],
        "version": fact["version"],
        "value": fact["value"],
        "unit": fact["unit"],
        "status": fact["status"],
        "source_tier": fact["source_tier"],
        "evidence_id": None if evidence is None else evidence["evidence_id"],
        "source_name": None if evidence is None else evidence["source_name"],
        "valid_from": fact["valid_from"],
        "valid_until": fact["valid_until"],
    }


def _period_dict(period: Period) -> dict[str, str | None]:
    return {
        "from": period[0].isoformat(),
        "until": None if period[1] is None else period[1].isoformat(),
    }


def _eligible(fact: dict[str, Any]) -> bool:
    return bool(fact["zone"] == "fact" and fact["status"] != "retracted")


def _comparable(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """两条是否在说同一件事（同实体、同属性、同条件）且都参与冲突检测。"""
    return (
        _eligible(first)
        and _eligible(second)
        and first["entity_id"] == second["entity_id"]
        and same_attribute(first, second)
        and same_conditions(first["conditions"], second["conditions"])
    )


def _pair_conflicts(
    versions_a: list[dict[str, Any]], versions_b: list[dict[str, Any]], as_of: date | None
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for first in versions_a:
        for second in versions_b:
            if not _comparable(first, second) or not values_differ(first, second):
                continue
            periods = overlap_periods(first, second, versions_a, versions_b)
            if as_of is not None:
                periods = [
                    period
                    for period in periods
                    if period[0] <= as_of and (period[1] is None or as_of <= period[1])
                ]
            if not periods:
                continue
            found.append(
                {
                    "entity_id": first["entity_id"],
                    "attribute": first["attribute"],
                    "conditions": dict(sorted(first["conditions"].items())),
                    "overlap": _period_dict(periods[0]),
                    "periods": [_period_dict(period) for period in periods],
                    "facts": [_brief(first), _brief(second)],
                    "note": CONFLICT_NOTE,
                }
            )
    return found


def detect_conflicts(store: FactStore, *, as_of: date | None = None) -> list[dict[str, Any]]:
    """返回冲突清单（按实体、属性、fact_id、版本排序，结果确定）。

    不给 as_of：任何一天出现过重叠都列出；给 as_of：只列当天仍然存在的冲突。
    每一对冲突的版本各占一条；`overlap` 是两条版本同时生效的最早时段
    （给了 as_of 时是包含当天的那一段），`periods` 是全部时段。
    """
    grouped = store.by_fact_id()
    buckets: dict[str, set[str]] = {}
    for fact_id, versions in grouped.items():
        for fact in versions:
            buckets.setdefault(fact["entity_id"], set()).add(fact_id)
    seen: set[tuple[str, str]] = set()
    conflicts: list[dict[str, Any]] = []
    for entity_id in sorted(buckets):
        fact_ids = sorted(buckets[entity_id])
        for index, id_a in enumerate(fact_ids):
            for id_b in fact_ids[index + 1 :]:
                if (id_a, id_b) in seen:
                    continue
                seen.add((id_a, id_b))
                conflicts.extend(_pair_conflicts(grouped[id_a], grouped[id_b], as_of))
    conflicts.sort(
        key=lambda item: (
            item["entity_id"],
            item["attribute"],
            item["facts"][0]["fact_id"],
            item["facts"][1]["fact_id"],
            item["facts"][0]["version"],
            item["facts"][1]["version"],
        )
    )
    return conflicts


def conflicting_fact_ids(store: FactStore, as_of: date) -> dict[str, list[str]]:
    """某日处于冲突中的 fact_id → 与之冲突的 fact_id 列表（升序）。"""
    result: dict[str, set[str]] = {}
    for conflict in detect_conflicts(store, as_of=as_of):
        first, second = (item["fact_id"] for item in conflict["facts"])
        result.setdefault(first, set()).add(second)
        result.setdefault(second, set()).add(first)
    return {fact_id: sorted(others) for fact_id, others in sorted(result.items())}
