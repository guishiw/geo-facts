"""G-TIME-FORGED 与 meta 校验。当前时间固定为 2026-10-08T02:00:00Z（北京时间 2026-10-08 10:00）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from helpers_gate import TZ, meta, run_check

from geo_facts.errors import InputError
from geo_facts.gate.checks import time_forged
from geo_facts.gate.meta import load_meta, parse_meta, parse_time
from geo_facts.gate.model import CLAUSE_TIME, RULE_TIME_FORGED

CHECK = time_forged.CHECK


def kinds(raw: str = "正文", **fields: object) -> list[str]:
    findings = run_check(CHECK, raw, "md", meta_value=meta(**fields))
    assert all(f.rule_id == RULE_TIME_FORGED and f.result == "block" for f in findings)
    assert all(f.clause == CLAUSE_TIME for f in findings)
    return [str(f.details["kind"]) for f in findings]


# ------------------------------------------------------------------ meta 时间


def test_updated_before_created_is_blocked() -> None:
    findings = run_check(
        CHECK,
        "正文",
        meta_value=meta(created_at="2026-09-10T08:00:00Z", updated_at="2026-09-01T08:00:00Z"),
    )
    assert len(findings) == 1
    assert findings[0].span is None
    assert findings[0].excerpt == ""
    assert "早于" in findings[0].reason


def test_updated_equal_or_after_created_is_fine() -> None:
    assert kinds(created_at="2026-09-10T08:00:00Z", updated_at="2026-09-10T08:00:00Z") == []
    assert kinds(created_at="2026-09-10", updated_at="2026-09-11") == []


def test_mixed_precision_compares_local_days() -> None:
    # 同一自然日：只有日期的一边不能判早于
    assert kinds(created_at="2026-10-01T10:00:00+08:00", updated_at="2026-10-01") == []
    assert kinds(created_at="2026-10-01T10:00:00+08:00", updated_at="2026-09-30") == [
        "updated_before_created"
    ]
    # 2026-09-30T20:00Z 在北京时间是 10-01 凌晨：按项目时区不算早于 10-01
    assert kinds(created_at="2026-09-30T20:00:00Z", updated_at="2026-10-01") == []


@pytest.mark.parametrize("field", ["created_at", "updated_at", "published_at"])
def test_times_after_now_are_blocked(field: str) -> None:
    assert kinds(**{field: "2026-10-08T02:00:01Z"}) == ["in_future"]
    assert kinds(**{field: "2026-10-08T02:00:00Z"}) == []
    assert kinds(**{field: "2026-10-09"}) == ["in_future"]
    assert kinds(**{field: "2026-10-08"}) == []  # 北京时间当天，不算未来


def test_future_finding_names_the_field() -> None:
    findings = run_check(CHECK, "正文", meta_value=meta(published_at="2027-01-01"))
    assert findings[0].details["field"] == "published_at"
    assert findings[0].details["now"] == "2026-10-08T02:00:00Z"


def test_no_meta_means_no_meta_findings() -> None:
    assert run_check(CHECK, "正文") == []


# ------------------------------------------------------------------ 正文声称的更新时间


def test_claimed_update_time_consistent_with_meta() -> None:
    raw = "## 澜序 S7\n\n更新于 2026-10-01\n"
    assert kinds(raw, created_at="2026-09-01", updated_at="2026-10-01T09:00:00+08:00") == []


def test_claimed_update_time_inconsistent_with_meta() -> None:
    raw = "正文。\n\n最后更新：2026-10-05\n"
    findings = run_check(
        CHECK, raw, meta_value=meta(created_at="2026-09-01", updated_at="2026-10-01")
    )
    start = raw.index("最后更新")
    assert [f.span for f in findings] == [(start, len(raw) - 1)]
    assert findings[0].excerpt == "最后更新：2026-10-05"
    assert findings[0].details["claimed"] == "2026-10-05"
    assert findings[0].details["problems"] == ["与 meta.updated_at（2026-10-01）不一致"]


@pytest.mark.parametrize(
    "claim",
    [
        "更新于 2026年10月1日",
        "更新时间：2026/10/01",
        "更新日期 2026.10.1",
        "最近更新 2026 年 10 月 01 日",
        "Last updated: 2026-10-01",
        "Updated on 2026/10/1",
        "修订于2026-10-01 10:30",
    ],
)
def test_claim_formats(claim: str) -> None:
    raw = f"正文\n{claim}\n"
    assert kinds(raw, created_at="2026-09-01", updated_at="2026-10-01") == []
    assert kinds(raw, created_at="2026-09-01", updated_at="2026-10-02") == ["claimed_update_time"]


def test_month_precision_claim_is_compared_by_month() -> None:
    raw = "最后更新：2026年9月"
    assert kinds(raw, created_at="2026-09-15", updated_at="2026-09-20") == []
    assert kinds(raw, created_at="2026-08-15", updated_at="2026-10-02") == ["claimed_update_time"]


def test_claim_earlier_than_created() -> None:
    raw = "更新时间 2026/08/01"
    findings = run_check(CHECK, raw, meta_value=meta(created_at="2026-09-01"))
    assert findings[0].details["problems"] == ["早于 meta.created_at（2026-09-01）"]


def test_claim_in_future_even_without_meta() -> None:
    findings = run_check(CHECK, "Last updated: 2026-12-01")
    assert findings[0].details["problems"] == ["晚于当前日期（2026-10-08）"]
    assert run_check(CHECK, "更新于 2026-09-01") == []


def test_claim_with_several_problems() -> None:
    findings = run_check(
        CHECK,
        "更新于 2026-08-01",
        meta_value=meta(created_at="2026-09-01", updated_at="2026-09-05"),
    )
    assert findings[0].details["problems"] == [
        "与 meta.updated_at（2026-09-05）不一致",
        "早于 meta.created_at（2026-09-01）",
    ]


def test_invalid_dates_and_code_blocks_are_ignored() -> None:
    assert run_check(CHECK, "更新于 2026-13-01", meta_value=meta(updated_at="2026-10-01")) == []
    assert run_check(CHECK, "更新于 2026-02-30", meta_value=meta(updated_at="2026-10-01")) == []
    raw = "示例：\n\n```\n最后更新：2030-01-01\n```\n"
    assert run_check(CHECK, raw, meta_value=meta(updated_at="2026-10-01")) == []
    assert run_check(CHECK, "我们 updated 2026 款车型的配置") == []


def test_claim_inside_hidden_html_is_not_checked_here() -> None:
    raw = '<div style="display:none">最后更新：2030-01-01</div>'
    assert run_check(CHECK, raw, "html") == []


# ------------------------------------------------------------------ meta 校验


def test_parse_meta_fields() -> None:
    value = parse_meta(
        {
            "title": " 澜序 S7 ",
            "brand_entity_id": "brand-lanxu",
            "topic": "",
            "ai_generated": True,
            "ai_label": "本文由 AI 辅助生成",
            "unknown": 1,
        },
        TZ,
    )
    assert value.title == "澜序 S7"
    assert value.topic is None
    assert value.has_ai_generated is True
    assert value.ai_labeled is True
    assert parse_meta({}, TZ).has_ai_generated is False
    assert parse_meta({"ai_label": "  "}, TZ).ai_labeled is False
    assert parse_meta({"ai_label": True}, TZ).ai_labeled is True


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([], "必须是 JSON 对象"),
        ({"title": 1}, "meta.title 必须是字符串"),
        ({"ai_generated": "yes"}, "ai_generated 必须是 true 或 false"),
        ({"ai_label": 1}, "ai_label 必须是布尔值或字符串"),
        ({"created_at": 20261001}, "必须是字符串时间"),
        ({"created_at": "2026-10-01 10:00:00"}, "不是合法时间"),  # 不带时区
        ({"created_at": "2026-02-30"}, "不是合法时间"),
        ({"created_at": "2026/10/01"}, "不是合法时间"),
        ({"updated_at": "2026-10-01T25:00:00Z"}, "不是合法时间"),
    ],
)
def test_parse_meta_rejects_bad_values(value: object, message: str) -> None:
    with pytest.raises(InputError, match=message):
        parse_meta(value, TZ)


def test_parse_time_variants() -> None:
    plain = parse_time("2026-10-01", "created_at", TZ)
    assert plain.moment is None
    offset = parse_time("2026-10-01T01:00:00+0800", "created_at", TZ)
    assert offset.moment == datetime(2026, 9, 30, 17, 0, tzinfo=UTC)
    assert offset.day.isoformat() == "2026-10-01"
    assert parse_time("2026-10-01 01:00Z", "created_at", TZ).day.isoformat() == "2026-10-01"


def test_load_meta_from_file(tmp_path: Path) -> None:
    path = tmp_path / "a.meta.json"
    path.write_text(json.dumps({"topic": "续航"}, ensure_ascii=False), encoding="utf-8")
    assert load_meta(path, TZ).topic == "续航"
    with pytest.raises(InputError, match="不存在"):
        load_meta(tmp_path / "missing.json", TZ)
