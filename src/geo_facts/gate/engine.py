"""闸门引擎：依次调用已登记的检查项，汇总状态。

- 有任何 block 发现 → status = block（退出 1）；否则有 review → review（退出 5）；否则 pass（退出 0）。
- 缺少必要输入的检查项列入 skipped_checks（附原因），不参与汇总；status 只说明“已执行的检查”。
- 发现按（整篇级别在前，再按原文位置、规则编号）排序，同输入输出逐字节一致。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from geo_facts.gate.document import Draft
from geo_facts.gate.meta import Meta
from geo_facts.gate.model import (
    NOTICE,
    RESULT_BLOCK,
    RESULT_REVIEW,
    STATUS_BLOCK,
    STATUS_LABELS,
    STATUS_PASS,
    STATUS_REVIEW,
    Check,
    Finding,
    GateContext,
)


@dataclass(frozen=True)
class CheckOutcome:
    check: Check
    skipped_reason: str | None
    findings: tuple[Finding, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check.name,
            "rule_ids": list(self.check.rule_ids),
            "summary": self.check.summary,
            "status": "skipped" if self.skipped_reason is not None else "ran",
            "findings": len(self.findings),
        }


@dataclass(frozen=True)
class GateReport:
    findings: tuple[Finding, ...]
    outcomes: tuple[CheckOutcome, ...]

    @property
    def counts(self) -> dict[str, int]:
        results = Counter(finding.result for finding in self.findings)
        return {"block": results[RESULT_BLOCK], "review": results[RESULT_REVIEW]}

    @property
    def status(self) -> str:
        counts = self.counts
        if counts["block"]:
            return STATUS_BLOCK
        if counts["review"]:
            return STATUS_REVIEW
        return STATUS_PASS

    @property
    def skipped(self) -> list[CheckOutcome]:
        return [outcome for outcome in self.outcomes if outcome.skipped_reason is not None]

    def to_dict(self) -> dict[str, Any]:
        status = self.status
        return {
            "status": status,
            "status_label": STATUS_LABELS[status],
            "findings": [finding.to_dict() for finding in self.findings],
            "counts": self.counts,
            "rule_counts": dict(sorted(Counter(f.rule_id for f in self.findings).items())),
            "checks": [outcome.to_dict() for outcome in self.outcomes],
            "skipped_checks": [
                {
                    "check": outcome.check.name,
                    "rule_ids": list(outcome.check.rule_ids),
                    "reason": outcome.skipped_reason,
                }
                for outcome in self.skipped
            ],
            "notice": NOTICE,
        }


def evaluate(
    draft: Draft, meta: Meta | None, ctx: GateContext, checks: Sequence[Check] | None = None
) -> GateReport:
    if checks is None:
        from geo_facts.gate.checks import ALL_CHECKS

        checks = ALL_CHECKS
    names = [check.name for check in checks]
    if len(set(names)) != len(names):
        raise ValueError(f"检查项名称重复：{names}")
    outcomes: list[CheckOutcome] = []
    findings: list[Finding] = []
    for check in checks:
        reason = check.unavailable(meta, ctx)
        if reason is not None:
            outcomes.append(CheckOutcome(check, reason, ()))
            continue
        produced = tuple(check.run(draft, meta, ctx))
        stray = sorted({f.rule_id for f in produced} - set(check.rule_ids))
        if stray:
            raise ValueError(f"检查项 {check.name} 产生了未声明的规则：{stray}")
        outcomes.append(CheckOutcome(check, None, produced))
        findings.extend(produced)
    unique = {(f.rule_id, f.span, f.reason): f for f in findings}
    ordered = sorted(unique.values(), key=Finding.sort_key)
    return GateReport(findings=tuple(ordered), outcomes=tuple(outcomes))
