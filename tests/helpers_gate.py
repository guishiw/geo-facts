"""闸门测试的公共设施。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from typing import Any

from geo_facts.gate import engine
from geo_facts.gate.document import parse_draft
from geo_facts.gate.meta import Meta, parse_meta
from geo_facts.gate.model import Check, Finding, GateContext

NOW = datetime(2026, 10, 8, 2, 0, tzinfo=UTC)  # 北京时间 2026-10-08 10:00
NOW_TEXT = "2026-10-08T02:00:00Z"
TZ = "Asia/Shanghai"

ZWSP = chr(0x200B)
ZWNJ = chr(0x200C)
ZWJ = chr(0x200D)
WORD_JOINER = chr(0x2060)
BOM = chr(0xFEFF)
RLO = chr(0x202E)
SOFT_HYPHEN = chr(0x00AD)


def make_ctx(**overrides: Any) -> GateContext:
    values: dict[str, Any] = {"now": NOW, "as_of": date(2026, 10, 8), "timezone": TZ}
    values.update(overrides)
    return GateContext(**values)


def meta(**fields: Any) -> Meta:
    return parse_meta(fields, TZ)


def evaluate(
    raw: str,
    fmt: str = "md",
    *,
    meta_value: Meta | None = None,
    checks: Sequence[Check] | None = None,
    **ctx: Any,
) -> engine.GateReport:
    return engine.evaluate(parse_draft(raw, fmt), meta_value, make_ctx(**ctx), checks)


def run_check(
    check: Check, raw: str, fmt: str = "md", *, meta_value: Meta | None = None, **ctx: Any
) -> list[Finding]:
    findings = check.run(parse_draft(raw, fmt), meta_value, make_ctx(**ctx))
    assert_excerpts(raw, findings)
    return findings


def rule_ids(findings: Iterable[Finding]) -> list[str]:
    return [finding.rule_id for finding in findings]


def assert_excerpts(raw: str, findings: Iterable[Finding]) -> None:
    for finding in findings:
        if finding.span is None:
            assert finding.excerpt == ""
            continue
        start, end = finding.span
        assert 0 <= start <= end <= len(raw)
        assert finding.excerpt == raw[start:end]
