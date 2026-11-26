"""G-TIME-FORGED：伪造或早于内容创建的更新时间（block）。只检查，不改写任何时间。

规则：
1. meta.updated_at 早于 meta.created_at；
2. meta 的 created_at / updated_at / published_at 晚于 `--now`（未给时为当前时间）；
3. 可见正文声称的更新时间（“更新于 2026-10-01”“最后更新：2026年10月1日”“更新时间 2026/10/01”
   “Last updated: 2026-10-01”等）与 meta.updated_at 不一致、早于 meta.created_at、或晚于当前日期。
   正文只写到年月（“更新于 2026 年 10 月”）时按年月比较。
比较时，两边都是带时区的时刻就比较时刻；有一边只有日期就比较在项目时区下的自然日。
"""

from __future__ import annotations

import re
from datetime import date

from geo_facts.gate.document import VISIBLE, Draft
from geo_facts.gate.meta import Meta, TimeValue
from geo_facts.gate.model import CLAUSE_TIME, RULE_TIME_FORGED, Check, Finding, GateContext
from geo_facts.timeutil import zone

SUGGESTION = "核对并如实填写时间；本工具不改写任何时间"

_LABEL = (
    r"(?:最后更新(?:时间|日期)?|最近更新(?:时间|日期)?|更新(?:时间|日期)|更新于|修订于|修改于|"
    r"最后修改(?:时间|日期)?|last\s+updated(?:\s+on)?|last\s+modified(?:\s+on)?|updated\s+on|updated)"
)
_CLAIM = re.compile(
    _LABEL + r"\s*(?:于|为|在|是)?\s*:?\s*"
    r"(\d{4})\s*(?:[-/.]|年)\s*(\d{1,2})(?!\d)"
    r"(?:\s*(?:[-/.]|月)\s*(\d{1,2})(?!\d)(?:\s*日)?|\s*月)?"
)

_FIELD_LABELS = {
    "created_at": "创建时间",
    "updated_at": "更新时间",
    "published_at": "发布时间",
}


def _meta_findings(draft: Draft, meta: Meta, ctx: GateContext) -> list[Finding]:
    findings: list[Finding] = []
    if (
        meta.created_at is not None
        and meta.updated_at is not None
        and meta.updated_at.earlier_than(meta.created_at)
    ):
        findings.append(
            Finding.at(
                draft.raw,
                RULE_TIME_FORGED,
                None,
                reason=(
                    f"meta.updated_at（{meta.updated_at.text}）早于 "
                    f"meta.created_at（{meta.created_at.text}）"
                ),
                suggestion=SUGGESTION,
                clause=CLAUSE_TIME,
                details={
                    "kind": "updated_before_created",
                    "updated_at": meta.updated_at.text,
                    "created_at": meta.created_at.text,
                },
            )
        )
    for field in ("created_at", "updated_at", "published_at"):
        value: TimeValue | None = getattr(meta, field)
        if value is not None and value.after(ctx.now, ctx.timezone):
            findings.append(
                Finding.at(
                    draft.raw,
                    RULE_TIME_FORGED,
                    None,
                    reason=f"meta.{field}（{_FIELD_LABELS[field]} {value.text}）晚于当前时间",
                    suggestion=SUGGESTION,
                    clause=CLAUSE_TIME,
                    details={
                        "kind": "in_future",
                        "field": field,
                        "value": value.text,
                        "now": ctx.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                )
            )
    return findings


def _key(day: date, precision: int) -> tuple[int, ...]:
    return (day.year, day.month, day.day)[:precision]


def _claim_findings(draft: Draft, meta: Meta | None, ctx: GateContext) -> list[Finding]:
    index = draft.index(VISIBLE)
    today = ctx.now.astimezone(zone(ctx.timezone)).date()
    findings: list[Finding] = []
    for match in _CLAIM.finditer(index.text):
        year, month = int(match.group(1)), int(match.group(2))
        day_text = match.group(3)
        try:
            claimed = date(year, month, int(day_text) if day_text else 1)
        except ValueError:
            continue
        precision = 3 if day_text else 2
        claimed_key = _key(claimed, precision)
        claimed_text = claimed.isoformat() if day_text else f"{year:04d}-{month:02d}"
        problems: list[str] = []
        updated = meta.updated_at if meta is not None else None
        created = meta.created_at if meta is not None else None
        if updated is not None and claimed_key != _key(updated.day, precision):
            problems.append(f"与 meta.updated_at（{updated.text}）不一致")
        if created is not None and claimed_key < _key(created.day, precision):
            problems.append(f"早于 meta.created_at（{created.text}）")
        if claimed_key > _key(today, precision):
            problems.append(f"晚于当前日期（{today.isoformat()}）")
        if not problems:
            continue
        span = index.raw_span(*match.span())
        if span is None:  # pragma: no cover - 命中总含有非分隔符字符
            continue
        findings.append(
            Finding.at(
                draft.raw,
                RULE_TIME_FORGED,
                span,
                reason=f"正文声称的更新时间 {claimed_text} " + "，且".join(problems),
                suggestion=SUGGESTION,
                clause=CLAUSE_TIME,
                details={
                    "kind": "claimed_update_time",
                    "claimed": claimed_text,
                    "meta_updated_at": updated.text if updated is not None else None,
                    "meta_created_at": created.text if created is not None else None,
                    "problems": problems,
                },
            )
        )
    return findings


def run(draft: Draft, meta: Meta | None, ctx: GateContext) -> list[Finding]:
    findings = _meta_findings(draft, meta, ctx) if meta is not None else []
    return findings + _claim_findings(draft, meta, ctx)


CHECK = Check(
    name="time_forged",
    rule_ids=(RULE_TIME_FORGED,),
    summary="伪造或早于内容创建的更新时间",
    run=run,
)
