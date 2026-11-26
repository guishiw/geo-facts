"""事实库：三区分治、按日期取版本、可用与不可用原因。期望值均为手工推算。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from helpers_store import make_evidence, make_fact, memory_store
from hypothesis import given
from hypothesis import strategies as st

from geo_facts.errors import InputError
from geo_facts.store import (
    REASON_CODES,
    REASON_TEXT,
    basis_blockers,
    basis_view,
    describe_reasons,
    effective_version,
    live_versions,
    parse_day,
    resolve,
    stale_versions,
    today_in,
    valid_on,
)


def price_history() -> list[dict[str, Any]]:
    """S7 售价：上市价 23.98 万元（03-18 至 08-31），09-01 起调整为 21.98 万元。"""
    return [
        make_fact(
            "F-S7-PRICE",
            attribute="售价",
            value=23.98,
            unit="万元",
            content_use="pricing",
            is_core=True,
            valid_from="2026-03-18",
            valid_until="2026-08-31",
            version=1,
        ),
        make_fact(
            "F-S7-PRICE",
            attribute="售价",
            value=21.98,
            unit="万元",
            content_use="pricing",
            is_core=True,
            valid_from="2026-09-01",
            version=2,
        ),
    ]


# ------------------------------------------------------------------ as-of 取版本


@pytest.mark.parametrize(
    ("day", "state", "version", "usable"),
    [
        ("2026-03-17", "not_yet_effective", 1, False),  # 生效日前一天
        ("2026-03-18", "effective", 1, True),  # 生效日当天（含）
        ("2026-08-31", "effective", 1, True),  # 失效日当天（含）
        ("2026-09-01", "effective", 2, True),  # 新版本生效日
        ("2026-10-08", "effective", 2, True),
    ],
)
def test_as_of_picks_version_including_boundary_days(
    day: str, state: str, version: int, usable: bool
) -> None:
    view = basis_view(memory_store(price_history()), date.fromisoformat(day))
    status = view.status("F-S7-PRICE")
    assert status is not None
    assert (status.state, status.version, status.usable) == (state, version, usable)
    if usable:
        assert [fact["version"] for fact in view.usable] == [version]
        assert view.unusable == []
    else:
        assert view.usable == []
        assert status.reasons == ("not_yet_effective",)


def test_expired_fact_is_not_a_basis_but_stays_queryable() -> None:
    fact = make_fact(
        "F-PROMO", attribute="购车补贴", value=5000, unit="元", valid_until="2026-06-30"
    )
    store = memory_store([fact])
    last_day = basis_view(store, date(2026, 6, 30))
    assert last_day.usable == [fact]
    after = basis_view(store, date(2026, 7, 1))
    assert after.usable == []
    status = after.status("F-PROMO")
    assert status is not None
    assert status.state == "expired"
    assert status.reasons == ("expired",)
    assert status.fact is fact
    # 过期版本仍可查到，用于判断“回答与已过期的版本一致”。
    assert after.history("F-PROMO") == [fact]
    assert after.stale("F-PROMO") == [(fact, "expired")]


def test_history_and_stale_versions_for_price_change() -> None:
    old, new = price_history()
    view = basis_view(memory_store([new, old]), date(2026, 10, 8))  # 文件顺序故意颠倒
    assert view.history("F-S7-PRICE") == [old, new]
    assert view.stale("F-S7-PRICE") == [(old, "expired")]
    assert view.history("F-UNKNOWN") == []
    assert view.stale("F-UNKNOWN") == []
    assert view.status("F-UNKNOWN") is None
    # 旧价生效期间，新价尚未生效：它不是“旧版本”。
    assert basis_view(memory_store([old, new]), date(2026, 8, 1)).stale("F-S7-PRICE") == []


def test_overlapping_versions_take_highest_version_and_mark_superseded() -> None:
    first = make_fact("F-S7-RANGE", value=620, valid_from="2026-03-18", version=1)
    second = make_fact("F-S7-RANGE", value=630, valid_from="2026-06-01", version=2)
    versions = [first, second]
    assert effective_version(versions, date(2026, 5, 31)) is first
    assert effective_version(versions, date(2026, 6, 1)) is second
    assert stale_versions(versions, date(2026, 5, 31)) == []
    assert stale_versions(versions, date(2026, 6, 1)) == [(first, "superseded")]
    assert effective_version(versions, date(2026, 3, 17)) is None


def test_gap_between_versions_counts_as_expired() -> None:
    first = make_fact("F-GAP", valid_from="2026-01-01", valid_until="2026-01-31", version=1)
    second = make_fact("F-GAP", valid_from="2026-03-01", version=2)
    resolution = resolve("F-GAP", [first, second], date(2026, 2, 15))
    assert resolution.state == "expired"
    assert resolution.fact is first
    assert resolve("F-GAP", [first, second], date(2025, 12, 31)).state == "not_yet_effective"
    assert resolve("F-GAP", [first, second], date(2025, 12, 31)).fact is first
    assert resolve("F-NONE", [], date(2026, 2, 15)).state == "unknown"
    assert resolve("F-NONE", [], date(2026, 2, 15)).fact is None


def test_valid_on_is_inclusive_on_both_ends() -> None:
    fact = make_fact("F-X", valid_from="2026-03-18", valid_until="2026-08-31")
    assert not valid_on(fact, date(2026, 3, 17))
    assert valid_on(fact, date(2026, 3, 18))
    assert valid_on(fact, date(2026, 8, 31))
    assert not valid_on(fact, date(2026, 9, 1))
    assert valid_on(make_fact("F-Y", valid_from="2026-03-18"), date(2099, 1, 1))


# ------------------------------------------------------------------ 撤回、争议、无证据


def test_retracted_version_blocks_and_does_not_fall_back_to_older_version() -> None:
    active = make_fact("F-S7-RANGE", value=620, version=1)
    retracted = make_fact("F-S7-RANGE", value=620, status="retracted", version=2)
    view = basis_view(memory_store([active, retracted]), date(2026, 10, 8))
    status = view.status("F-S7-RANGE")
    assert status is not None
    assert status.fact is retracted
    assert status.state == "effective"
    assert status.reasons == ("retracted",)
    assert view.usable == []
    # 撤回之前的日子同样按“当日有效的最高版本”取：两个版本的有效期相同，所以仍是撤回版本。
    assert basis_view(memory_store([active, retracted]), date(2026, 3, 18)).usable == []


def test_disputed_and_missing_evidence_are_not_a_basis() -> None:
    disputed = make_fact(
        "F-DISPUTED", attribute="电池容量", value=82, unit="kWh", status="disputed"
    )
    bare = make_fact("F-BARE", attribute="百公里加速", value=5.9, unit="s", evidence=None)
    good = make_fact("F-GOOD", attribute="整备质量", value=1850, unit="kg")
    view = basis_view(memory_store([disputed, bare, good]), date(2026, 10, 8))
    assert view.usable == [good]
    assert {status.fact_id: status.reasons for status in view.unusable} == {
        "F-BARE": ("no_evidence",),
        "F-DISPUTED": ("disputed",),
    }
    assert view.reason_counts() == {
        "expired": 0,
        "not_yet_effective": 0,
        "not_fact_zone": 0,
        "retracted": 0,
        "disputed": 1,
        "no_evidence": 1,
        "conflict": 0,
    }


def test_reasons_accumulate_in_fixed_order() -> None:
    fact = make_fact(
        "F-OLD",
        status="retracted",
        evidence=None,
        valid_from="2026-01-01",
        valid_until="2026-02-01",
    )
    status = basis_view(memory_store([fact]), date(2026, 10, 8)).status("F-OLD")
    assert status is not None
    assert status.reasons == ("expired", "retracted", "no_evidence")
    assert status.to_dict() == {
        "fact_id": "F-OLD",
        "version": 1,
        "state": "expired",
        "usable": False,
        "reasons": ["expired", "retracted", "no_evidence"],
        "reason_text": "已过期、已撤回、没有绑定证据",
        "conflicts_with": [],
        "zone": "fact",
        "is_core": False,
    }
    assert set(REASON_TEXT) == set(REASON_CODES)
    assert describe_reasons(["conflict"]) == "事实库对此存在未裁决的冲突"


# ------------------------------------------------------------------ 三区分治


def test_only_fact_zone_can_be_a_basis() -> None:
    fact = make_fact("F-S7-RANGE", value=620)
    opinion = make_fact(
        "O-S7-COMFORT",
        attribute="乘坐舒适性",
        value="后排空间宽敞，乘坐舒适",
        unit=None,
        zone="opinion",
        source_tier="D",
        content_use="review",
    )
    marketing = make_fact(
        "M-S7-SLOGAN",
        attribute="宣传语",
        value="安静得像一片湖",
        unit=None,
        zone="marketing",
        source_tier=None,
        content_use="promotion",
        evidence=None,
    )
    view = basis_view(memory_store([fact, opinion, marketing]), date(2026, 10, 8))
    assert view.usable == [fact]
    reasons = {status.fact_id: status.reasons for status in view.unusable}
    # 观点即使带证据、状态正常也不能作为依据；营销表达同理。
    assert reasons == {
        "M-S7-SLOGAN": ("not_fact_zone", "no_evidence"),
        "O-S7-COMFORT": ("not_fact_zone",),
    }
    assert basis_blockers(opinion) == ["not_fact_zone"]
    assert basis_blockers(fact) == []


def test_conflicting_facts_are_not_a_basis_until_resolved() -> None:
    official = make_fact(
        "F-E3-WEIGHT-A", entity_id="product-lanxu-e3", attribute="整备质量", value=1850, unit="kg"
    )
    leaflet = make_fact(
        "F-E3-WEIGHT-B",
        entity_id="product-lanxu-e3",
        attribute="整备质量",
        value=1790,
        unit="kg",
        source_tier="D",
    )
    other = make_fact("F-E3-RANGE", entity_id="product-lanxu-e3", value=560)
    view = basis_view(memory_store([official, leaflet, other]), date(2026, 10, 8))
    assert view.usable == [other]
    first = view.status("F-E3-WEIGHT-A")
    second = view.status("F-E3-WEIGHT-B")
    assert first is not None
    assert second is not None
    assert (first.reasons, first.conflicts_with) == (("conflict",), ("F-E3-WEIGHT-B",))
    assert (second.reasons, second.conflicts_with) == (("conflict",), ("F-E3-WEIGHT-A",))
    # 撤回其中一条（追加新版本）后，另一条恢复可用。
    withdrawn = make_fact(
        "F-E3-WEIGHT-B",
        entity_id="product-lanxu-e3",
        attribute="整备质量",
        value=1790,
        unit="kg",
        source_tier="D",
        status="retracted",
        version=2,
    )
    resolved = basis_view(memory_store([official, leaflet, other, withdrawn]), date(2026, 10, 8))
    assert resolved.usable == [other, official]  # 按 fact_id 升序：F-E3-RANGE 在前
    assert resolved.status("F-E3-WEIGHT-B").reasons == ("retracted",)  # type: ignore[union-attr]


# ------------------------------------------------------------------ 仍在生效序列里的版本


def test_live_versions_drop_fully_superseded_versions() -> None:
    typo = make_fact("F-S7-RANGE", value=602, version=1)
    fixed = make_fact("F-S7-RANGE", value=620, version=2)
    assert live_versions([typo, fixed]) == [fixed]
    old, new = price_history()
    assert live_versions([new, old]) == [old, new]
    # 第 2 版只在 6 月生效：第 1 版在 6 月之前和之后仍是当日有效版本。
    base = make_fact("F-A", valid_from="2026-01-01", version=1)
    june = make_fact("F-A", valid_from="2026-06-01", valid_until="2026-06-30", version=2)
    assert live_versions([base, june]) == [base, june]
    # 失效日期早于生效日期的版本永远不会生效。
    broken = make_fact("F-B", valid_from="2026-06-01", valid_until="2026-05-01")
    assert live_versions([broken]) == []
    assert live_versions([make_fact("F-C", valid_until="9999-12-31")])[0]["fact_id"] == "F-C"


# ------------------------------------------------------------------ 日期解析


def test_parse_day_accepts_only_iso_dates() -> None:
    assert parse_day("2026-10-08") == date(2026, 10, 8)
    for bad in ("2026-10-8", "20261008", "2026-13-01", "2026/10/08", ""):
        with pytest.raises(InputError, match="YYYY-MM-DD"):
            parse_day(bad)
    with pytest.raises(InputError, match="查询日期"):
        parse_day("昨天", what="查询日期")


def test_today_in_uses_the_given_timezone() -> None:
    utc_today = datetime.now(UTC).date()
    assert abs((today_in("Asia/Shanghai") - utc_today).days) <= 1
    with pytest.raises(InputError, match="未知时区"):
        today_in("Mars/Olympus")


# ------------------------------------------------------------------ 随机输入

BASE_DAY = date(2026, 1, 1)


@st.composite
def version_lists(draw: st.DrawFn) -> list[dict[str, Any]]:
    count = draw(st.integers(min_value=1, max_value=5))
    versions = []
    for number in range(1, count + 1):
        start = BASE_DAY + timedelta(days=draw(st.integers(min_value=0, max_value=60)))
        length = draw(st.one_of(st.none(), st.integers(min_value=0, max_value=60)))
        until = None if length is None else (start + timedelta(days=length)).isoformat()
        versions.append(
            make_fact(
                "F-RANDOM",
                value=600 + number,
                valid_from=start.isoformat(),
                valid_until=until,
                status=draw(st.sampled_from(["active", "active", "retracted", "disputed"])),
                zone=draw(st.sampled_from(["fact", "fact", "opinion"])),
                evidence=make_evidence() if draw(st.booleans()) else None,
                version=number,
            )
        )
    return draw(st.permutations(versions))


def _brute_force_valid(fact: dict[str, Any], day: date) -> bool:
    start = date.fromisoformat(fact["valid_from"])
    end = date.max if fact["valid_until"] is None else date.fromisoformat(fact["valid_until"])
    return start <= day <= end


@given(versions=version_lists(), offset=st.integers(min_value=-5, max_value=130))
def test_effective_version_is_unique_valid_and_highest(
    versions: list[dict[str, Any]], offset: int
) -> None:
    day = BASE_DAY + timedelta(days=offset)
    valid = [fact for fact in versions if _brute_force_valid(fact, day)]
    chosen = effective_version(versions, day)
    if not valid:
        assert chosen is None
    else:
        assert chosen is not None
        assert _brute_force_valid(chosen, day)
        assert chosen["version"] == max(fact["version"] for fact in valid)
        assert sum(1 for fact in versions if fact is chosen) == 1
    view = basis_view(memory_store(versions), day)
    assert list(view.statuses) == ["F-RANDOM"]
    status = view.statuses["F-RANDOM"]
    assert (status.state == "effective") == bool(valid)
    assert len(view.usable) + len(view.unusable) == 1
    for fact in view.usable:
        assert fact is chosen
        assert fact["zone"] == "fact"
        assert fact["status"] == "active"
        assert fact["evidence"] is not None
    # 可用与否和原因列表必须一致：没有原因才可用。
    assert status.usable == (
        bool(valid)
        and status.fact["zone"] == "fact"
        and status.fact["status"] == "active"
        and status.fact["evidence"] is not None
    )


@given(versions=version_lists())
def test_live_versions_match_day_by_day_scan(versions: list[dict[str, Any]]) -> None:
    seen: dict[int, dict[str, Any]] = {}
    for offset in range(-1, 125):  # 生效日最晚第 60 天、有效期最长 60 天：扫到第 124 天足够
        current = effective_version(versions, BASE_DAY + timedelta(days=offset))
        if current is not None:
            seen[current["version"]] = current
    assert live_versions(versions) == [seen[number] for number in sorted(seen)]
