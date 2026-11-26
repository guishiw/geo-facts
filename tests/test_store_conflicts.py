"""冲突检测：正例与反例。期望值均为手工推算。"""

from __future__ import annotations

import itertools
from datetime import date, timedelta
from typing import Any

from helpers_store import make_fact, memory_store
from hypothesis import given
from hypothesis import strategies as st

from geo_facts.store import (
    conflicting_fact_ids,
    detect_conflicts,
    effective_version,
    same_attribute,
    values_differ,
)
from geo_facts.store.conflicts import CONFLICT_NOTE, overlap_periods


def weight(fact_id: str, value: Any, **overrides: Any) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "entity_id": "product-lanxu-e3",
        "attribute": "整备质量",
        "value": value,
        "unit": "kg",
    }
    settings.update(overrides)
    return make_fact(fact_id, **settings)


def pairs(conflicts: list[dict[str, Any]]) -> list[tuple[str, int, str, int]]:
    return [
        (
            item["facts"][0]["fact_id"],
            item["facts"][0]["version"],
            item["facts"][1]["fact_id"],
            item["facts"][1]["version"],
        )
        for item in conflicts
    ]


# ------------------------------------------------------------------ 正例


def test_same_entity_attribute_conditions_overlap_and_different_value_is_a_conflict() -> None:
    official = weight("F-E3-WEIGHT-A", 1850)
    leaflet = weight(
        "F-E3-WEIGHT-B", 1790, source_tier="D", valid_from="2026-05-01", valid_until="2026-12-31"
    )
    conflicts = detect_conflicts(memory_store([leaflet, official]))
    assert conflicts == [
        {
            "entity_id": "product-lanxu-e3",
            "attribute": "整备质量",
            "conditions": {},
            "overlap": {"from": "2026-05-01", "until": "2026-12-31"},
            "periods": [{"from": "2026-05-01", "until": "2026-12-31"}],
            "facts": [
                {
                    "fact_id": "F-E3-WEIGHT-A",
                    "version": 1,
                    "value": 1850,
                    "unit": "kg",
                    "status": "active",
                    "source_tier": "C",
                    "evidence_id": "EV-S7-SPEC",
                    "source_name": "澜序汽车官网 S7 参数页（虚构）",
                    "valid_from": "2026-03-18",
                    "valid_until": None,
                },
                {
                    "fact_id": "F-E3-WEIGHT-B",
                    "version": 1,
                    "value": 1790,
                    "unit": "kg",
                    "status": "active",
                    "source_tier": "D",
                    "evidence_id": "EV-S7-SPEC",
                    "source_name": "澜序汽车官网 S7 参数页（虚构）",
                    "valid_from": "2026-05-01",
                    "valid_until": "2026-12-31",
                },
            ],
            "note": CONFLICT_NOTE,
        }
    ]
    # 冲突清单只陈列双方，不替人裁决：不出现“以谁为准”的字段。
    assert "winner" not in conflicts[0]
    assert "不自动裁决" in CONFLICT_NOTE
    assert conflicting_fact_ids(memory_store([leaflet, official]), date(2026, 6, 1)) == {
        "F-E3-WEIGHT-A": ["F-E3-WEIGHT-B"],
        "F-E3-WEIGHT-B": ["F-E3-WEIGHT-A"],
    }


def test_attribute_alias_and_normalized_conditions_still_conflict() -> None:
    named = weight("F-A", 1850, aliases=["车重"])
    aliased = weight("F-B", 1790, attribute="车重")
    assert same_attribute(named, aliased)
    assert pairs(detect_conflicts(memory_store([named, aliased]))) == [("F-A", 1, "F-B", 1)]
    upper = make_fact("F-C", value=620, conditions={"test_cycle": "CLTC"})
    lower = make_fact("F-D", value=600, conditions={"test_cycle": "cltc"})
    assert pairs(detect_conflicts(memory_store([upper, lower]))) == [("F-C", 1, "F-D", 1)]
    wltp = make_fact("F-E", value=540, conditions={"test_cycle": "WLTP"})
    wltc = make_fact("F-F", value=530, conditions={"test_cycle": "WLTC"})
    assert pairs(detect_conflicts(memory_store([wltp, wltc]))) == [("F-E", 1, "F-F", 1)]


def test_missing_evidence_or_dispute_does_not_hide_a_conflict() -> None:
    bare = weight("F-A", 1850, evidence=None)
    disputed = weight("F-B", 1790, status="disputed")
    assert pairs(detect_conflicts(memory_store([bare, disputed]))) == [("F-A", 1, "F-B", 1)]


def test_point_versus_range_and_text_and_date_values() -> None:
    point = make_fact("F-A", value=620)
    span = make_fact("F-B", value={"min": 600, "max": 650})
    assert values_differ(point, span)
    rear = make_fact("F-C", attribute="驱动形式", value="后驱", unit=None)
    four = make_fact("F-D", attribute="驱动形式", value="四驱", unit=None)
    assert pairs(detect_conflicts(memory_store([rear, four]))) == [("F-C", 1, "F-D", 1)]
    march18 = make_fact("F-E", attribute="上市时间", value="2026-03-18", unit=None)
    march19 = make_fact("F-F", attribute="上市时间", value="2026年3月19日", unit=None)
    assert pairs(detect_conflicts(memory_store([march18, march19]))) == [("F-E", 1, "F-F", 1)]


def test_one_day_overlap_is_a_conflict() -> None:
    first = weight("F-A", 1850, valid_until="2026-09-01")
    second = weight("F-B", 1790, valid_from="2026-09-01")
    conflicts = detect_conflicts(memory_store([first, second]))
    assert [item["overlap"] for item in conflicts] == [
        {"from": "2026-09-01", "until": "2026-09-01"}
    ]


def test_every_conflicting_version_pair_is_listed() -> None:
    low = weight("F-A", 1800, valid_from="2026-01-01", valid_until="2026-06-30", version=1)
    high = weight("F-A", 1900, valid_from="2026-07-01", version=2)
    other = weight("F-B", 1850, valid_from="2026-01-01")
    conflicts = detect_conflicts(memory_store([high, other, low]))
    assert pairs(conflicts) == [("F-A", 1, "F-B", 1), ("F-A", 2, "F-B", 1)]
    assert [item["overlap"] for item in conflicts] == [
        {"from": "2026-01-01", "until": "2026-06-30"},
        {"from": "2026-07-01", "until": None},
    ]
    assert pairs(detect_conflicts(memory_store([high, other, low]), as_of=date(2026, 6, 30))) == [
        ("F-A", 1, "F-B", 1)
    ]
    assert pairs(detect_conflicts(memory_store([high, other, low]), as_of=date(2026, 7, 1))) == [
        ("F-A", 2, "F-B", 1)
    ]


# ------------------------------------------------------------------ 反例


def test_different_conditions_are_not_a_conflict() -> None:
    cltc = make_fact("F-S7-RANGE-CLTC", value=620, conditions={"test_cycle": "CLTC"})
    wltc = make_fact("F-S7-RANGE-WLTC", value=540, conditions={"test_cycle": "WLTC"})
    trim = make_fact(
        "F-S7-RANGE-CLTC-LONG", value=680, conditions={"test_cycle": "CLTC", "trim": "长续航版"}
    )
    bare = make_fact("F-S7-RANGE-ANY", value=600)
    assert detect_conflicts(memory_store([cltc, wltc, trim, bare])) == []


def test_non_overlapping_periods_are_not_a_conflict() -> None:
    old = weight("F-A", 1850, valid_until="2026-08-31")
    new = weight("F-B", 1790, valid_from="2026-09-01")
    assert detect_conflicts(memory_store([old, new])) == []
    assert conflicting_fact_ids(memory_store([old, new]), date(2026, 9, 1)) == {}


def test_different_unit_with_equal_value_is_not_a_conflict() -> None:
    wan = make_fact("F-A", attribute="售价", value=21.98, unit="万元")
    yuan = make_fact("F-B", attribute="售价", value=219800, unit="元")
    assert not values_differ(wan, yuan)
    assert detect_conflicts(memory_store([wan, yuan])) == []
    km = make_fact("F-C", value=620, unit="km")
    metres = make_fact("F-D", value=620000, unit="米")
    assert detect_conflicts(memory_store([km, metres])) == []
    # 数值相同但量纲不同（620 km 与 620 kg）不是同一个值。
    assert values_differ(km, make_fact("F-E", value=620, unit="kg"))
    iso = make_fact("F-G", attribute="上市时间", value="2026-03-18", unit=None)
    chinese = make_fact("F-H", attribute="上市时间", value="2026年3月18日", unit=None)
    assert detect_conflicts(memory_store([iso, chinese])) == []
    spaced = make_fact("F-I", attribute="电机型号", value="LX-M220", unit=None)
    folded = make_fact("F-J", attribute="电机型号", value="lx m220", unit=None)
    assert detect_conflicts(memory_store([spaced, folded])) == []


def test_other_entity_attribute_zone_or_same_fact_id_is_not_a_conflict() -> None:
    ours = make_fact("F-OURS", value=620, conditions={"test_cycle": "CLTC"})
    theirs = make_fact(
        "F-THEIRS", entity_id="product-cenye-s7", value=580, conditions={"test_cycle": "CLTC"}
    )
    battery = make_fact("F-BATTERY", attribute="电池容量", value=82, unit="kWh")
    assert detect_conflicts(memory_store([ours, theirs, battery])) == []
    first = make_fact("F-S7-RANGE", value=620, version=1)
    second = make_fact("F-S7-RANGE", value=630, version=2)
    assert detect_conflicts(memory_store([first, second])) == []
    opinion_a = make_fact("O-A", attribute="口碑", value="好", unit=None, zone="opinion")
    opinion_b = make_fact("O-B", attribute="口碑", value="一般", unit=None, zone="opinion")
    fact_c = make_fact("F-C", attribute="口碑", value="差", unit=None)
    assert detect_conflicts(memory_store([opinion_a, opinion_b, fact_c])) == []


def test_retracting_or_correcting_one_side_resolves_the_conflict() -> None:
    official = weight("F-A", 1850)
    leaflet = weight("F-B", 1790)
    assert len(detect_conflicts(memory_store([official, leaflet]))) == 1
    retracted = weight("F-B", 1790, status="retracted", version=2)
    assert detect_conflicts(memory_store([official, leaflet, retracted])) == []
    corrected = weight("F-B", 1850, version=2)
    assert detect_conflicts(memory_store([official, leaflet, corrected])) == []


def test_as_of_limits_conflicts_to_that_day() -> None:
    ended = weight("F-A", 1850, valid_until="2026-06-30")
    open_ended = weight("F-B", 1790)
    store = memory_store([ended, open_ended])
    assert [item["overlap"] for item in detect_conflicts(store)] == [
        {"from": "2026-03-18", "until": "2026-06-30"}
    ]
    assert len(detect_conflicts(store, as_of=date(2026, 6, 30))) == 1
    assert detect_conflicts(store, as_of=date(2026, 7, 1)) == []
    assert detect_conflicts(store, as_of=date(2026, 3, 17)) == []


def test_overlap_follows_the_effective_versions_not_just_validity_windows() -> None:
    # A 的第 1 版写 1850，自 06-01 起的第 2 版改为 1790，与 B 一致：冲突只存在到 05-31。
    wrong = weight("F-A", 1850, valid_from="2026-01-01", version=1)
    fixed = weight("F-A", 1790, valid_from="2026-06-01", version=2)
    other = weight("F-B", 1790, valid_from="2026-01-01")
    store = memory_store([wrong, fixed, other])
    conflicts = detect_conflicts(store)
    assert pairs(conflicts) == [("F-A", 1, "F-B", 1)]
    assert conflicts[0]["overlap"] == {"from": "2026-01-01", "until": "2026-05-31"}
    assert detect_conflicts(store, as_of=date(2026, 6, 1)) == []


def test_split_overlap_periods_are_all_reported() -> None:
    # A 的第 2 版只在 3、4 月生效且与 B 一致；第 1 版在 1～2 月和 5～12 月与 B 冲突。
    base = weight("F-A", 1850, valid_from="2026-01-01", valid_until="2026-12-31", version=1)
    patch = weight("F-A", 1790, valid_from="2026-03-01", valid_until="2026-04-30", version=2)
    other = weight("F-B", 1790, valid_from="2026-01-01", valid_until="2026-12-31")
    store = memory_store([base, patch, other])
    conflicts = detect_conflicts(store)
    assert len(conflicts) == 1
    assert conflicts[0]["periods"] == [
        {"from": "2026-01-01", "until": "2026-02-28"},
        {"from": "2026-05-01", "until": "2026-12-31"},
    ]
    assert conflicts[0]["overlap"] == {"from": "2026-01-01", "until": "2026-02-28"}
    assert detect_conflicts(store, as_of=date(2026, 3, 15)) == []
    in_may = detect_conflicts(store, as_of=date(2026, 5, 20))
    assert [item["overlap"] for item in in_may] == [{"from": "2026-05-01", "until": "2026-12-31"}]


def test_values_that_cannot_be_normalized_are_compared_literally() -> None:
    reversed_range = make_fact("F-A", value={"min": 700, "max": 600})
    same_reversed = make_fact("F-B", value={"min": 700, "max": 600})
    point = make_fact("F-C", value=620)
    assert not values_differ(reversed_range, same_reversed)
    assert values_differ(reversed_range, point)


# ------------------------------------------------------------------ 随机输入

BASE_DAY = date(2026, 1, 1)
SCAN_DAYS = 130  # 生效日最晚第 60 天、有效期最长 60 天，扫到第 130 天足够


@st.composite
def fact_versions(draw: st.DrawFn, fact_id: str) -> list[dict[str, Any]]:
    versions = []
    for number in range(1, draw(st.integers(min_value=1, max_value=3)) + 1):
        start = BASE_DAY + timedelta(days=draw(st.integers(min_value=0, max_value=60)))
        length = draw(st.one_of(st.none(), st.integers(min_value=0, max_value=60)))
        versions.append(
            weight(
                fact_id,
                1800 + number,
                valid_from=start.isoformat(),
                valid_until=None
                if length is None
                else (start + timedelta(days=length)).isoformat(),
                version=number,
            )
        )
    return versions


@given(versions_a=fact_versions("F-A"), versions_b=fact_versions("F-B"), data=st.data())
def test_overlap_periods_match_day_by_day_scan(
    versions_a: list[dict[str, Any]], versions_b: list[dict[str, Any]], data: st.DataObject
) -> None:
    first = data.draw(st.sampled_from(versions_a))
    second = data.draw(st.sampled_from(versions_b))
    expected = [
        BASE_DAY + timedelta(days=offset)
        for offset in range(-1, SCAN_DAYS + 1)
        if effective_version(versions_a, BASE_DAY + timedelta(days=offset)) is first
        and effective_version(versions_b, BASE_DAY + timedelta(days=offset)) is second
    ]
    scan_end = BASE_DAY + timedelta(days=SCAN_DAYS)
    covered: list[date] = []
    periods = overlap_periods(first, second, versions_a, versions_b)
    for start, end in periods:
        day = start
        while day <= (scan_end if end is None else end):
            covered.append(day)
            day += timedelta(days=1)
    assert covered == expected
    # 时段按时间升序、互不相邻（相邻的已合并）。
    for (_, earlier_end), (later_start, _) in itertools.pairwise(periods):
        assert earlier_end is not None
        assert later_start > earlier_end + timedelta(days=1)
