"""事实库整体检查（validate STORE 背后的 check_store）。期望值均为手工推算。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from helpers_store import NOW, build_store, make_entity, make_evidence, make_fact

from geo_facts import jsonl
from geo_facts.errors import InputError
from geo_facts.store import (
    check_store,
    commit_records,
    init_store,
    load_store,
    state_hash,
    verify_chain,
)

AS_OF = date(2026, 10, 8)


def run_check(store_root: Path, *, as_of: date = AS_OF, as_of_given: bool = True) -> dict[str, Any]:
    return check_store(load_store(store_root, strict=False), as_of=as_of, as_of_given=as_of_given)


def codes(items: list[dict[str, Any]]) -> list[str]:
    return [item["code"] for item in items]


def core_range(**overrides: Any) -> dict[str, Any]:
    settings: dict[str, Any] = {"value": 620, "is_core": True}
    settings.update(overrides)
    return make_fact("F-S7-RANGE", **settings)


# ------------------------------------------------------------------ 通过的情形


def test_clean_store_passes(tmp_path: Path) -> None:
    opinion = make_fact(
        "O-S7-COMFORT",
        attribute="乘坐舒适性",
        value="乘坐舒适",
        unit=None,
        zone="opinion",
        source_tier="D",
        content_use="review",
    )
    slogan = make_fact(
        "M-S7-SLOGAN",
        attribute="宣传语",
        value="安静得像一片湖",
        unit=None,
        zone="marketing",
        source_tier=None,
        content_use="promotion",
        evidence=None,
    )
    build_store(tmp_path / "store", [core_range(), opinion, slogan])
    result = run_check(tmp_path / "store")
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["warnings"] == []
    assert result["store_id"] == "store-auto-demo"
    assert result["project_id"] == "auto-demo"
    assert (result["entities"], result["fact_ids"], result["fact_versions"]) == (5, 3, 3)
    assert result["zones"] == {"fact": 1, "opinion": 1, "marketing": 1}
    assert result["as_of"] == "2026-10-08"
    assert result["as_of_counts"]["usable"] == 1
    assert result["as_of_counts"]["unusable"] == 2
    assert result["as_of_counts"]["reasons"]["not_fact_zone"] == 2
    assert result["core_without_evidence"] == []
    assert result["conflicts"] == []
    assert [item["fact_id"] for item in result["unusable"]] == ["M-S7-SLOGAN", "O-S7-COMFORT"]


# ------------------------------------------------------------------ 核心事实的证据绑定


def test_core_fact_without_evidence_is_listed_and_fails(tmp_path: Path) -> None:
    store = build_store(tmp_path / "store", [core_range(evidence=None)])
    result = run_check(tmp_path / "store")
    assert result["ok"] is False
    assert codes(result["errors"]) == ["core_without_evidence"]
    assert result["core_without_evidence"] == [{"fact_id": "F-S7-RANGE", "version": 1}]
    assert "026 §6.2" in result["errors"][0]["message"]
    assert codes(result["warnings"]) == ["core_unusable"]
    assert result["warnings"][0]["reasons"] == ["no_evidence"]
    # 追加带证据的第 2 版（同一有效期）后，第 1 版被整段取代，检查通过。
    commit_records(
        store,
        entities=[],
        facts=[core_range(version=2)],
        actor="测试夹具",
        reason="补录证据",
        now=NOW,
        details={},
    )
    fixed = run_check(tmp_path / "store")
    assert fixed["ok"] is True
    assert fixed["core_without_evidence"] == []
    assert fixed["warnings"] == []
    assert fixed["fact_versions"] == 2


def test_non_core_fact_without_evidence_is_only_a_warning(tmp_path: Path) -> None:
    build_store(tmp_path / "store", [core_range(is_core=False, evidence=None)])
    result = run_check(tmp_path / "store")
    assert result["ok"] is True
    assert codes(result["warnings"]) == ["fact_without_evidence"]
    assert result["core_without_evidence"] == []
    assert result["as_of_counts"]["usable"] == 0


def test_core_fact_outside_fact_zone_fails(tmp_path: Path) -> None:
    build_store(tmp_path / "store", [core_range(zone="opinion")])
    result = run_check(tmp_path / "store")
    assert codes(result["errors"]) == ["core_not_in_fact_zone"]
    assert codes(result["warnings"]) == ["core_unusable"]
    assert result["warnings"][0]["reasons"] == ["not_fact_zone"]


# ------------------------------------------------------------------ 时效


def test_core_fact_unusable_on_the_query_day_is_a_warning_not_an_error(tmp_path: Path) -> None:
    expired = make_fact("F-EXPIRED", is_core=True, valid_until="2026-08-31")
    future = make_fact(
        "F-FUTURE",
        attribute="电池容量",
        value=90,
        unit="kWh",
        is_core=True,
        valid_from="2027-01-01",
    )
    retracted = make_fact(
        "F-RETRACTED", attribute="整备质量", value=1850, unit="kg", is_core=True, status="retracted"
    )
    disputed = make_fact(
        "F-DISPUTED", attribute="百公里加速", value=5.9, unit="s", is_core=True, status="disputed"
    )
    build_store(tmp_path / "store", [expired, future, retracted, disputed])
    result = run_check(tmp_path / "store")
    assert result["ok"] is True
    assert {item["fact_id"]: item["reasons"] for item in result["warnings"]} == {
        "F-DISPUTED": ["disputed"],
        "F-EXPIRED": ["expired"],
        "F-FUTURE": ["not_yet_effective"],
        "F-RETRACTED": ["retracted"],
    }
    assert result["as_of_counts"] == {
        "usable": 0,
        "unusable": 4,
        "reasons": {
            "expired": 1,
            "not_yet_effective": 1,
            "not_fact_zone": 0,
            "retracted": 1,
            "disputed": 1,
            "no_evidence": 0,
            "conflict": 0,
        },
    }
    assert "已过期" in next(
        item["message"] for item in result["warnings"] if item["fact_id"] == "F-EXPIRED"
    )
    # 08-31 当天，过期那条仍然有效。
    earlier = run_check(tmp_path / "store", as_of=date(2026, 8, 31))
    assert earlier["as_of_counts"]["usable"] == 1


# ------------------------------------------------------------------ 冲突


def test_conflict_fails_the_check_and_as_of_narrows_it(tmp_path: Path) -> None:
    first = make_fact("F-A", attribute="整备质量", value=1850, unit="kg", valid_until="2026-06-30")
    second = make_fact("F-B", attribute="整备质量", value=1790, unit="kg")
    build_store(tmp_path / "store", [first, second])
    any_day = run_check(tmp_path / "store", as_of_given=False)
    assert codes(any_day["errors"]) == ["conflict"]
    assert any_day["errors"][0]["fact_id"] == "F-A"
    assert any_day["errors"][0]["other_fact_id"] == "F-B"
    assert len(any_day["conflicts"]) == 1
    # 10-08 当天 F-A 已过期：给了 --as-of 时不再算冲突。
    on_day = run_check(tmp_path / "store", as_of_given=True)
    assert on_day["ok"] is True
    assert on_day["conflicts"] == []
    during = run_check(tmp_path / "store", as_of=date(2026, 6, 30))
    assert codes(during["errors"]) == ["conflict"]
    assert during["as_of_counts"]["reasons"]["conflict"] == 2


# ------------------------------------------------------------------ 结构问题


def test_structural_problems_are_errors(tmp_path: Path) -> None:
    entities = [
        make_entity("brand-lanxu", "澜序", kind="brand"),
        make_entity("product-lanxu-s7", "澜序 S7", parent="brand-missing"),
        make_entity("product-other", "别的项目的车型", project_id="other-project"),
    ]
    facts = [
        make_fact("F-ORPHAN", entity_id="product-unknown"),
        make_fact(
            "F-FOREIGN", attribute="电池容量", value=82, unit="kWh", project_id="other-project"
        ),
        make_fact(
            "F-BACKWARDS",
            attribute="整备质量",
            value=1850,
            unit="kg",
            valid_from="2026-06-01",
            valid_until="2026-05-01",
        ),
        make_fact(
            "F-TIME",
            attribute="百公里加速",
            value=5.9,
            unit="s",
            evidence=make_evidence(
                collected_at="2026-10-01T02:00:00Z", last_verified_at="2026-09-01T02:00:00Z"
            ),
        ),
        make_fact("F-DUP", attribute="售价", value=21.98, unit="万元", version=1),
        make_fact("F-DUP", attribute="售价", value=21.98, unit="万元", version=1),
        make_fact("F-GAP", attribute="上市时间", value="2026-03-18", unit=None, version=3),
    ]
    build_store(tmp_path / "store", facts, entities)
    result = run_check(tmp_path / "store")
    assert result["ok"] is False
    by_code: dict[str, list[dict[str, Any]]] = {}
    for item in result["errors"]:
        by_code.setdefault(item["code"], []).append(item)
    assert sorted(by_code) == [
        "duplicate_version",
        "evidence_time_order",
        "invalid_period",
        "project_mismatch",
        "unknown_entity",
        "unknown_parent_entity",
    ]
    assert by_code["unknown_parent_entity"][0]["entity_id"] == "product-lanxu-s7"
    assert [item.get("entity_id") or item["fact_id"] for item in by_code["project_mismatch"]] == [
        "product-other",
        "F-FOREIGN",
    ]
    assert by_code["unknown_entity"][0]["fact_id"] == "F-ORPHAN"
    assert by_code["invalid_period"][0]["fact_id"] == "F-BACKWARDS"
    assert by_code["evidence_time_order"][0]["fact_id"] == "F-TIME"
    assert by_code["duplicate_version"] == [
        {
            "level": "error",
            "code": "duplicate_version",
            "message": "F-DUP 的版本 1 出现多次",
            "fact_id": "F-DUP",
            "version": 1,
        }
    ]
    assert [item["fact_id"] for item in result["warnings"] if item["code"] == "version_gap"] == [
        "F-GAP"
    ]


def test_lines_that_break_the_contract_are_reported_with_file_and_line(tmp_path: Path) -> None:
    build_store(tmp_path / "store", [core_range()])
    broken = core_range()
    del broken["zone"]
    impossible = make_fact(
        "F-DAY", attribute="电池容量", value=82, unit="kWh", valid_from="2026-02-30"
    )
    with (tmp_path / "store" / "facts.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(jsonl.dumps_line(broken) + "\n")
        handle.write(jsonl.dumps_line(impossible) + "\n")
    result = run_check(tmp_path / "store")
    assert codes(result["errors"]) == [
        "contract_invalid",
        "contract_invalid",
        "audit_state_mismatch",
    ]
    assert [(item["file"], item["line"]) for item in result["errors"][:2]] == [
        ("facts.jsonl", 2),
        ("facts.jsonl", 3),
    ]
    assert "不是真实存在的日期" in result["errors"][1]["message"]
    assert result["fact_versions"] == 1
    # 严格模式（导入、导出、判定用）直接拒绝读取这样的事实库。
    with pytest.raises(InputError, match=r"facts\.jsonl 第 2 行不符合契约 fact"):
        load_store(tmp_path / "store")


# ------------------------------------------------------------------ 提示


def test_unknown_unit_unnormalizable_value_and_defaulted_tier_are_warnings(tmp_path: Path) -> None:
    odd_unit = make_fact("F-UNIT", attribute="座舱评分", value=9, unit="星")
    reversed_range = make_fact(
        "F-RANGE", attribute="离地间隙", value={"min": 200, "max": 150}, unit="mm"
    )
    defaulted = make_fact("F-TIER", attribute="电池容量", value=82, unit="kWh")
    defaulted["ext"] = {"source_tier_defaulted": True}
    build_store(tmp_path / "store", [odd_unit, reversed_range, defaulted])
    result = run_check(tmp_path / "store")
    assert result["ok"] is True
    assert {item["fact_id"]: item["code"] for item in result["warnings"]} == {
        "F-RANGE": "value_not_normalized",
        "F-TIER": "source_tier_defaulted",
        "F-UNIT": "unknown_unit",
    }
    assert "默认" in next(
        item["message"] for item in result["warnings"] if item["code"] == "source_tier_defaulted"
    )


# ------------------------------------------------------------------ 审计链


def test_audit_chain_detects_edit_delete_and_reorder(tmp_path: Path) -> None:
    store = build_store(tmp_path / "store", [core_range()])
    audit_path = store.paths.audit
    events = jsonl.read_jsonl(audit_path)
    assert [event["event_id"] for event in events] == ["evt-000001", "evt-000002"]
    assert [event["action"] for event in events] == ["register", "import"]
    assert events[0]["before_hash"] is None
    assert events[1]["before_hash"] == events[0]["after_hash"]
    assert events[1]["after_hash"] == state_hash(store.paths)
    assert events[1]["previous_event_hash"] == events[0]["event_hash"]
    assert events[1]["target"] == {"type": "fact_store", "id": "store-auto-demo"}
    assert verify_chain(events) == []

    edited = json.loads(json.dumps(events))
    edited[0]["reason"] = "悄悄改掉的理由"
    assert [(item["index"], item["kind"]) for item in verify_chain(edited)] == [(1, "modified")]
    assert [(item["index"], item["kind"]) for item in verify_chain(events[1:])] == [
        (1, "chain_broken")
    ]
    assert [(item["index"], item["kind"]) for item in verify_chain(events[::-1])] == [
        (1, "chain_broken"),
        (2, "chain_broken"),
    ]
    malformed = [{"event_hash": 42}, events[0]]
    assert [(item["index"], item["kind"]) for item in verify_chain(malformed)] == [(1, "invalid")]

    audit_path.write_text(
        "".join(jsonl.dumps_line(event) + "\n" for event in edited), encoding="utf-8"
    )
    result = run_check(tmp_path / "store")
    assert codes(result["errors"]) == ["audit_chain"]
    assert "第 1 条" in result["errors"][0]["message"]


def test_missing_audit_log_is_a_warning_and_outside_edits_are_errors(tmp_path: Path) -> None:
    store = build_store(tmp_path / "store", [core_range()])
    extra = make_fact("F-SNEAKED", attribute="电池容量", value=82, unit="kWh")
    with store.paths.facts.open("a", encoding="utf-8") as handle:
        handle.write(jsonl.dumps_line(extra) + "\n")
    result = run_check(tmp_path / "store")
    assert codes(result["errors"]) == ["audit_state_mismatch"]
    store.paths.audit.unlink()
    result = run_check(tmp_path / "store")
    assert result["ok"] is True
    assert codes(result["warnings"]) == ["audit_missing"]


# ------------------------------------------------------------------ 内置样例


def test_example_store_summary_matches_hand_count(tmp_path: Path) -> None:
    init_store(tmp_path / "store", project_id=None, now=NOW, example="auto")
    result = run_check(tmp_path / "store")
    # 样例共 25 个 fact_id（S7 有 10 个、X5 有 3 个、E3 有 4 个、M9 有 3 个、岑野 S7 有 1 个、
    # 观点 2 个、营销表达 2 个），其中 F-S7-PRICE 有两个版本。
    assert (result["entities"], result["fact_ids"], result["fact_versions"]) == (10, 25, 26)
    assert result["zones"] == {"fact": 21, "opinion": 2, "marketing": 2}
    # 10-08 不可用的 7 条：2 条观点、2 条营销表达、1 条缺证据的核心事实、2 条互相冲突。
    assert result["as_of_counts"]["usable"] == 18
    assert result["as_of_counts"]["unusable"] == 7
    assert result["as_of_counts"]["reasons"] == {
        "expired": 0,
        "not_yet_effective": 0,
        "not_fact_zone": 4,
        "retracted": 0,
        "disputed": 0,
        "no_evidence": 3,  # F-M9-ACCEL 与两条没有证据的营销表达
        "conflict": 2,
    }
    assert codes(result["errors"]) == ["core_without_evidence", "conflict"]
    assert result["core_without_evidence"] == [{"fact_id": "F-M9-ACCEL", "version": 1}]
    # 03-18：X5 的 3 条事实（06-08 上市）尚未生效。
    launch_day = run_check(tmp_path / "store", as_of=date(2026, 3, 18))
    assert launch_day["as_of_counts"]["usable"] == 15
    assert launch_day["as_of_counts"]["reasons"]["not_yet_effective"] == 3
    # 03-17：全部尚未生效，当天也就不存在冲突。
    before = run_check(tmp_path / "store", as_of=date(2026, 3, 17))
    assert before["as_of_counts"]["usable"] == 0
    assert before["as_of_counts"]["reasons"]["not_yet_effective"] == 25
    assert codes(before["errors"]) == ["core_without_evidence"]
