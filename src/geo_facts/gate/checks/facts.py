"""事实类检查：G-FACT-CONTRADICTION、G-FACT-STALE（block）。

对稿件的**可见正文**做陈述拆解与判定（与 `geo-facts judge` 同一套规则，source.type = draft），
按 `--as-of` 当日取事实库的有效版本（GateContext.as_of）：

- G-FACT-CONTRADICTION：陈述与当日有效的事实矛盾（判定为 contradicted）。理由写明事实库的值、
  fact_id 与版本；陈述没写条件、与该属性所有条件的值都不一致时，列出全部比对过的值。
- G-FACT-STALE：陈述的值与下列之一一致（单位换算、约数容差、日期精度同判定规则）：
  - 已过期的事实版本（expired，如过期价格）；
  - 同一有效期内已被更高版本更正的旧版本（superseded）；
  - 已撤回的事实版本（retracted）；
  - 没有绑定证据的**核心**事实（no_evidence；非核心事实无证据时只是不可核验，不拦截）。
  只看事实区的记录，条件须与陈述可比。理由写明是哪种情况，并给出当日有效的值（如有）。
- 一条陈述最多报一条：满足 G-FACT-STALE 时不再报 G-FACT-CONTRADICTION（过期价格既与当日有效
  事实不一致、又与过期版本一致，只报“使用过期信息”，理由里写明当日有效的值）。
  多种情况同时成立时按 expired、retracted、superseded、no_evidence 的顺序取第一种，全部列在
  details.matches。
- 不拦截：与有效事实一致；条件不同；不可核验（没有实体、事实库没有该属性、值不可比、
  可比的事实都不可用而值也对不上、与无证据的非核心事实一致等）；自由文本陈述。
- 只拆可见正文：隐藏文本、注释、代码、脚本、属性值里的内容不拆陈述（隐藏内容由
  G-HIDDEN-TEXT 等检查负责）。不可见字符先去掉再拆，span 仍指向原文。
- 没给 `--store` 时整项检查列入 skipped_checks。

span 为陈述在稿件原文中的码点区间（`Finding.at` 保证 excerpt == 原文[start:end]）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from geo_facts.canon import sha256_bytes
from geo_facts.claims import extract_claims
from geo_facts.gate.document import VISIBLE, Draft
from geo_facts.gate.meta import Meta
from geo_facts.gate.model import (
    CLAUSE_TO_VERIFY,
    RULE_FACT_CONTRADICTION,
    RULE_FACT_STALE,
    Check,
    Finding,
    GateContext,
)
from geo_facts.judge.rules import CONTRADICTED, JudgeContext, Verdict, judge_claims
from geo_facts.normalize import (
    NormValue,
    Outcome,
    compare_conditions,
    compare_values,
    normalize_claim_value,
    normalize_fact_value,
)
from geo_facts.store import FactStatus

NO_STORE = "未提供 --store，未检查“与有效事实矛盾”和“使用过期、撤回、无证据的核心事实”"
CLAUSE_EVIDENCE = "026 §6.2"

EXPIRED = "expired"
SUPERSEDED = "superseded"
RETRACTED = "retracted"
NO_EVIDENCE = "no_evidence"
KIND_ORDER: tuple[str, ...] = (EXPIRED, RETRACTED, SUPERSEDED, NO_EVIDENCE)
# 判定走到“与事实比值”这一步、且没有判为支持的 reason_code；其余（支持、自由文本、无实体、
# 无属性、无事实、值无法归一、条件不同）不查过期与无证据。
COMPARED_CODES = frozenset(
    {"mismatch", "stale_match", "fact_unusable", "matches_unusable", "incomparable"}
)


@dataclass(frozen=True)
class StaleHit:
    """陈述的值与之一致的“不该再用”的事实版本。"""

    kind: str
    fact: Mapping[str, Any]


def unavailable(meta: Meta | None, ctx: GateContext) -> str | None:
    return NO_STORE if ctx.fact_store is None else None


def _conditions_text(conditions: Mapping[str, str]) -> str:
    return "、".join(f"{key}={value}" for key, value in sorted(conditions.items())) or "无"


def _value_text(fact: Mapping[str, Any]) -> str:
    value = fact["value"]
    if isinstance(value, dict):
        value = f"{value['min']}~{value['max']}"
    unit = "" if fact["unit"] is None else f" {fact['unit']}"
    return f"{value}{unit}"


def _label(fact: Mapping[str, Any]) -> str:
    """“F-S7-PRICE 版本 2：21.98 万元（条件 无）”。"""
    return (
        f"{fact['fact_id']} 版本 {fact['version']}：{_value_text(fact)}"
        f"（条件 {_conditions_text(fact['conditions'])}）"
    )


def _fact_dict(fact: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "fact_id": fact["fact_id"],
        "version": fact["version"],
        "value": fact["value"],
        "unit": fact["unit"],
        "conditions": dict(fact["conditions"]),
    }


def _claim_dict(claim: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "text": claim["text"],
        "entity_id": claim["entity_id"],
        "attribute": claim["attribute"],
        "value": claim["value"],
        "unit": claim["unit"],
        "conditions": dict(claim["conditions"]),
        "claim_type": claim["claim_type"],
    }


def _matches(value: NormValue, fact: Mapping[str, Any]) -> bool:
    fact_value = normalize_fact_value(fact["value"], fact["unit"])
    return fact_value is not None and compare_values(value, fact_value).outcome is Outcome.MATCH


def _comparable(claim: Mapping[str, Any], jctx: JudgeContext) -> list[FactStatus]:
    """同实体、同属性概念、条件可比的事实（与判定规则取候选的口径相同）。"""
    conditions: Mapping[str, str] = claim["conditions"] or {}
    statuses = jctx.candidates(claim["entity_id"], jctx.claim_concept(claim["attribute"]))
    return [
        status
        for status in statuses
        if compare_conditions(conditions, status.fact["conditions"])[0]
    ]


def _unusable_kind(status: FactStatus) -> str | None:
    if status.usable or status.fact["zone"] != "fact":
        return None
    if RETRACTED in status.reasons:
        return RETRACTED
    if EXPIRED in status.reasons:
        return EXPIRED
    if NO_EVIDENCE in status.reasons and status.fact["is_core"]:
        return NO_EVIDENCE
    return None


def stale_hits(verdict: Verdict, jctx: JudgeContext) -> list[StaleHit]:
    """陈述的值与哪些过期、更正、撤回或无证据的核心事实版本一致（已排序、去重）。"""
    if verdict.reason_code not in COMPARED_CODES:
        return []
    claim = verdict.claim
    value = normalize_claim_value(claim)
    assert value is not None  # 走到比值这一步的陈述必有可归一的值
    conditions: Mapping[str, str] = claim["conditions"] or {}
    found: dict[tuple[str, int], StaleHit] = {}
    for status in _comparable(claim, jctx):
        kind = _unusable_kind(status)
        if kind is not None and _matches(value, status.fact):
            found.setdefault((status.fact_id, status.version), StaleHit(kind, status.fact))
        for old, old_kind in jctx.view.stale(status.fact_id):
            if old["zone"] != "fact" or not compare_conditions(conditions, old["conditions"])[0]:
                continue
            if _matches(value, old):
                label = RETRACTED if old["status"] == RETRACTED else old_kind
                found.setdefault((old["fact_id"], int(old["version"])), StaleHit(label, old))
    return sorted(
        found.values(),
        key=lambda hit: (
            KIND_ORDER.index(hit.kind),
            hit.fact["fact_id"],
            -int(hit.fact["version"]),
        ),
    )


def _effective(claim: Mapping[str, Any], jctx: JudgeContext) -> list[Mapping[str, Any]]:
    """条件可比、当日可作为确定依据的事实（当日有效版本）。"""
    return [status.fact for status in _comparable(claim, jctx) if status.usable]


def _mismatched(verdict: Verdict) -> list[Mapping[str, Any]]:
    value = normalize_claim_value(verdict.claim)
    assert value is not None  # 判为矛盾的陈述必有可归一的值
    found: list[Mapping[str, Any]] = []
    for fact in verdict.compared:
        fact_value = normalize_fact_value(fact["value"], fact["unit"])
        if fact_value is not None and compare_values(value, fact_value).outcome is Outcome.MISMATCH:
            found.append(fact)
    return found


def _correction(facts: Sequence[Mapping[str, Any]]) -> str:
    return "更正为有效事实：" + "；".join(_label(fact) for fact in facts)


def _contradiction(
    draft: Draft, span: tuple[int, int], verdict: Verdict, jctx: JudgeContext
) -> Finding:
    facts = _mismatched(verdict)
    day = jctx.as_of.isoformat()
    return Finding.at(
        draft.raw,
        RULE_FACT_CONTRADICTION,
        span,
        reason=f"与 {day} 当日有效的事实矛盾：事实库 "
        + "；".join(_label(fact) for fact in facts)
        + f"。判定说明：{verdict.reason}",
        suggestion=_correction(facts),
        clause=CLAUSE_TO_VERIFY,
        details={
            "claim": _claim_dict(verdict.claim),
            "as_of": day,
            "facts": [_fact_dict(fact) for fact in facts],
            "judge_reason_code": verdict.reason_code,
            "judge_reason": verdict.reason,
        },
    )


def _period(fact: Mapping[str, Any]) -> str:
    return f"有效期 {fact['valid_from']} 至 {fact['valid_until'] or '未定'}"


def _stale_reason(hit: StaleHit, day: str, current: Sequence[Mapping[str, Any]]) -> str:
    fact = hit.fact
    name = f"{fact['fact_id']} 版本 {fact['version']}"
    if hit.kind == EXPIRED:
        head = f"使用了已过期的事实：陈述的值与 {name}（{_period(fact)}，{day} 已过期）一致"
    elif hit.kind == SUPERSEDED:
        head = f"使用了已被更正的旧版本：陈述的值与 {name}（{_period(fact)}）一致，该版本已被更高版本取代"
    elif hit.kind == RETRACTED:
        head = f"使用了已撤回的事实：陈述的值与 {name}（已撤回）一致"
    else:
        return (
            f"使用了没有证据的核心事实：陈述的值与核心事实 {name} 一致，"
            "但该事实没有绑定证据，不得作为确定依据"
        )
    if current:
        return f"{head}；{day} 当日有效的是 " + "；".join(_label(item) for item in current)
    return f"{head}；事实库没有 {day} 当日有效的对应事实"


def _stale_suggestion(hit: StaleHit, current: Sequence[Mapping[str, Any]]) -> str:
    if hit.kind == NO_EVIDENCE:
        return f"为核心事实 {hit.fact['fact_id']} 补充证据并复核后再使用，或删除该内容"
    if current:
        return _correction(current)
    return "删除或更新该内容：事实库没有当日有效的对应事实，需先补充有效事实"


def _stale(
    draft: Draft,
    span: tuple[int, int],
    verdict: Verdict,
    hits: Sequence[StaleHit],
    jctx: JudgeContext,
) -> Finding:
    hit = hits[0]
    current = _effective(verdict.claim, jctx)
    day = jctx.as_of.isoformat()
    matched = {
        **_fact_dict(hit.fact),
        "status": hit.fact["status"],
        "is_core": hit.fact["is_core"],
        "valid_from": hit.fact["valid_from"],
        "valid_until": hit.fact["valid_until"],
    }
    return Finding.at(
        draft.raw,
        RULE_FACT_STALE,
        span,
        reason=_stale_reason(hit, day, current),
        suggestion=_stale_suggestion(hit, current),
        clause=CLAUSE_EVIDENCE if hit.kind == NO_EVIDENCE else CLAUSE_TO_VERIFY,
        details={
            "kind": hit.kind,
            "claim": _claim_dict(verdict.claim),
            "as_of": day,
            "matched": matched,
            "matches": [
                {
                    "kind": item.kind,
                    "fact_id": item.fact["fact_id"],
                    "version": item.fact["version"],
                }
                for item in hits
            ],
            "facts": [_fact_dict(fact) for fact in current],
            "judge_reason_code": verdict.reason_code,
            "judge_reason": verdict.reason,
        },
    )


def run(draft: Draft, meta: Meta | None, ctx: GateContext) -> list[Finding]:
    store = ctx.fact_store
    assert store is not None  # unavailable() 已保证
    jctx = JudgeContext(store, ctx.as_of)
    plain = draft.plain_index(VISIBLE)
    source = {"type": "draft", "id": "draft-" + sha256_bytes(draft.raw.encode("utf-8"))[:16]}
    claims = extract_claims(
        plain.text,
        lexicon=jctx.lexicon,
        source=source,
        project_id=store.project_id,
        synthetic=store.synthetic,
    )
    findings: list[Finding] = []
    for verdict in judge_claims(claims, jctx):
        span = plain.raw_span(verdict.claim["span"]["start"], verdict.claim["span"]["end"])
        assert span is not None  # 陈述文本不会只由块间分隔符组成
        hits = stale_hits(verdict, jctx)
        if hits:
            findings.append(_stale(draft, span, verdict, hits, jctx))
        elif verdict.label == CONTRADICTED:
            findings.append(_contradiction(draft, span, verdict, jctx))
    return findings


CHECK = Check(
    name="facts",
    rule_ids=(RULE_FACT_CONTRADICTION, RULE_FACT_STALE),
    summary="与有效事实矛盾；使用过期、撤回、无证据的核心事实",
    run=run,
    unavailable=unavailable,
)
