"""事实库整体检查（`geo-facts validate STORE`）。

问题分两级：error（检查未通过，退出码 1）和 warning（提示，不影响退出码）。

- 结构检查看全部记录：契约、项目归属、实体引用、版本号、有效期先后、审计链；
- 绑定检查只看“仍在生效序列里”的版本（见 timeline.live_versions）：核心事实必须在事实区
  并绑定证据（026 §6.2）——被更高版本整段取代的旧版本不再追究，这样补录证据后检查才能通过；
- 时效检查看查询日：哪些核心事实当天不能作为确定依据；
- 冲突：同实体、同属性、同条件、有效期重叠但值不同，只列出、不裁决。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from geo_facts.jsonl import read_jsonl
from geo_facts.normalize import lookup_unit, normalize_fact_value
from geo_facts.store.audit import state_hash, verify_chain
from geo_facts.store.conflicts import detect_conflicts
from geo_facts.store.model import FactStore
from geo_facts.store.query import basis_view
from geo_facts.store.timeline import describe_reasons, live_versions


def _problem(level: str, code: str, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"level": level, "code": code, "message": message}
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def _entity_problems(store: FactStore) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    for entity in store.entities.values():
        entity_id = entity["entity_id"]
        if entity["project_id"] != store.project_id:
            problems.append(
                _problem(
                    "error",
                    "project_mismatch",
                    f"实体 {entity_id} 的 project_id 与事实库不一致",
                    entity_id=entity_id,
                )
            )
        parent = entity["parent_entity_id"]
        if parent is not None and parent not in store.entities:
            problems.append(
                _problem(
                    "error",
                    "unknown_parent_entity",
                    f"实体 {entity_id} 的上级实体 {parent} 不存在",
                    entity_id=entity_id,
                )
            )
    return problems


def _structure_problems(store: FactStore) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    for bad in store.invalid:
        first = bad.issues[0]
        problems.append(
            _problem(
                "error",
                "contract_invalid",
                f"{bad.file} 第 {bad.line} 行不符合契约（{first['path'] or '/'}：{first['message']}）",
                file=bad.file,
                line=bad.line,
            )
        )
    problems.extend(_entity_problems(store))
    seen: set[tuple[str, int]] = set()
    for fact in store.facts:
        fact_id, version = fact["fact_id"], int(fact["version"])
        where = {"fact_id": fact_id, "version": version}
        if (fact_id, version) in seen:
            problems.append(
                _problem(
                    "error", "duplicate_version", f"{fact_id} 的版本 {version} 出现多次", **where
                )
            )
        seen.add((fact_id, version))
        if fact["project_id"] != store.project_id:
            problems.append(
                _problem(
                    "error", "project_mismatch", f"{fact_id} 的 project_id 与事实库不一致", **where
                )
            )
        if fact["entity_id"] not in store.entities:
            problems.append(
                _problem(
                    "error",
                    "unknown_entity",
                    f"{fact_id} 引用的实体 {fact['entity_id']} 不在 entities.jsonl 中",
                    **where,
                )
            )
        if fact["valid_until"] is not None and fact["valid_until"] < fact["valid_from"]:
            problems.append(
                _problem("error", "invalid_period", f"{fact_id} 的失效日期早于生效日期", **where)
            )
        evidence = fact["evidence"]
        if evidence is not None and evidence["last_verified_at"] < evidence["collected_at"]:
            problems.append(
                _problem(
                    "error", "evidence_time_order", f"{fact_id} 的最近核验时间早于采集时间", **where
                )
            )
    for fact_id, versions in store.by_fact_id().items():
        numbers = sorted({int(fact["version"]) for fact in versions})
        if numbers != list(range(1, len(numbers) + 1)):
            problems.append(
                _problem(
                    "warning",
                    "version_gap",
                    f"{fact_id} 的版本号不连续：{numbers}",
                    fact_id=fact_id,
                )
            )
    return problems


def _binding_problems(store: FactStore) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """（问题列表, 没有证据的核心事实清单）。只看仍在生效序列里的版本。"""
    problems: list[dict[str, Any]] = []
    core_without_evidence: list[dict[str, Any]] = []
    grouped = store.by_fact_id()
    for fact_id in sorted(grouped):
        for fact in live_versions(grouped[fact_id]):
            version = int(fact["version"])
            where = {"fact_id": fact_id, "version": version}
            if fact["is_core"] and fact["zone"] != "fact":
                problems.append(
                    _problem(
                        "error",
                        "core_not_in_fact_zone",
                        f"{fact_id}（版本 {version}）标为核心事实，但不在事实区"
                        "（只有事实区能作为判定依据）",
                        **where,
                    )
                )
            if fact["zone"] == "fact" and fact["evidence"] is None:
                if fact["is_core"]:
                    core_without_evidence.append(where)
                    problems.append(
                        _problem(
                            "error",
                            "core_without_evidence",
                            f"核心事实 {fact_id}（版本 {version}）没有绑定证据，不得作为确定依据"
                            "（026 §6.2）",
                            **where,
                        )
                    )
                else:
                    problems.append(
                        _problem(
                            "warning",
                            "fact_without_evidence",
                            f"{fact_id}（版本 {version}）没有绑定证据，不会作为确定依据",
                            **where,
                        )
                    )
            if fact["unit"] is not None and lookup_unit(fact["unit"]) is None:
                problems.append(
                    _problem(
                        "warning",
                        "unknown_unit",
                        f"{fact_id} 的单位 {fact['unit']!r} 未收录，只能与同样写法的单位比较",
                        **where,
                    )
                )
            if normalize_fact_value(fact["value"], fact["unit"]) is None:
                problems.append(
                    _problem("warning", "value_not_normalized", f"{fact_id} 的值无法归一", **where)
                )
            ext = fact.get("ext")
            if isinstance(ext, dict) and ext.get("source_tier_defaulted") is True:
                problems.append(
                    _problem(
                        "warning",
                        "source_tier_defaulted",
                        f"{fact_id} 的来源等级是导入时按“企业自有域名默认 C 级”补的默认值，"
                        "请人工确认",
                        **where,
                    )
                )
    return problems, core_without_evidence


def _audit_problems(store: FactStore) -> list[dict[str, Any]]:
    events = read_jsonl(store.paths.audit, missing_ok=True)
    if not events:
        return [_problem("warning", "audit_missing", "audit.jsonl 没有任何记录，无法追溯变更")]
    problems = [
        _problem("error", "audit_chain", f"audit.jsonl 第 {issue['index']} 条：{issue['message']}")
        for issue in verify_chain(events)
    ]
    if not problems and events[-1].get("after_hash") != state_hash(store.paths):
        problems.append(
            _problem(
                "error",
                "audit_state_mismatch",
                "entities.jsonl 或 facts.jsonl 与最近一次审计记录的哈希不一致，"
                "可能被本工具之外的方式改动过",
            )
        )
    return problems


def check_store(store: FactStore, *, as_of: date, as_of_given: bool) -> dict[str, Any]:
    """整体检查。as_of_given 为假时冲突按“任意一天重叠”检查，否则只看 as_of 当天。"""
    problems = _structure_problems(store)
    binding, core_without_evidence = _binding_problems(store)
    problems.extend(binding)
    view = basis_view(store, as_of)
    for status in view.unusable:
        if status.fact["is_core"]:
            problems.append(
                _problem(
                    "warning",
                    "core_unusable",
                    f"核心事实 {status.fact_id}（版本 {status.version}）在 {as_of.isoformat()} "
                    f"不得作为确定依据：{describe_reasons(status.reasons)}",
                    fact_id=status.fact_id,
                    version=status.version,
                    reasons=list(status.reasons),
                )
            )
    conflicts = detect_conflicts(store, as_of=as_of if as_of_given else None)
    for conflict in conflicts:
        first, second = conflict["facts"]
        problems.append(
            _problem(
                "error",
                "conflict",
                f"冲突：{first['fact_id']}（版本 {first['version']}）与 {second['fact_id']}"
                f"（版本 {second['version']}）同实体、同属性、同条件、有效期重叠但值不同",
                fact_id=first["fact_id"],
                version=first["version"],
                other_fact_id=second["fact_id"],
                other_version=second["version"],
            )
        )
    problems.extend(_audit_problems(store))
    errors = [item for item in problems if item["level"] == "error"]
    warnings = [item for item in problems if item["level"] == "warning"]
    zones = {"fact": 0, "opinion": 0, "marketing": 0}
    for status in view.statuses.values():
        zones[status.fact["zone"]] += 1
    return {
        "store": str(store.paths.root),
        "store_id": store.store_id,
        "project_id": store.project_id,
        "as_of": as_of.isoformat(),
        "as_of_given": as_of_given,
        "ok": not errors,
        "entities": len(store.entities),
        "fact_ids": len(view.statuses),
        "fact_versions": len(store.facts),
        "zones": zones,
        "as_of_counts": {
            "usable": len(view.usable),
            "unusable": len(view.unusable),
            "reasons": view.reason_counts(),
        },
        "core_without_evidence": core_without_evidence,
        "unusable": [status.to_dict() for status in view.unusable],
        "conflicts": conflicts,
        "errors": errors,
        "warnings": warnings,
    }
