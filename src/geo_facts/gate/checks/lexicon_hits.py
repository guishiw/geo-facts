"""G-LEXICON / G-DRUG-COMPARISON / G-RX-PROMOTION：词库命中（review，永远不会是 block）。

- 扫描可见正文与隐藏文本（代码块、注释、脚本、属性值不扫）；同一词条的每处命中各算一条。
- category = drug_comparison → G-DRUG-COMPARISON；prescription_promotion → G-RX-PROMOTION：
  两者一律 risk_level = highest，requires 含 legal_confirmation（不论词条自己写的级别）。
- 其余 category → G-LEXICON，risk_level / requires 取自词条。
- clause：词条给的条款号在登记范围内才照用，否则写“待人工核对”，原值放在 details.lexicon_clause。
"""

from __future__ import annotations

from typing import Any

from geo_facts.gate.document import HIDDEN, VISIBLE, Draft
from geo_facts.gate.lexicon import CATEGORY_LABELS, Entry, Lexicon
from geo_facts.gate.meta import Meta
from geo_facts.gate.model import (
    REQUIRES_LEGAL,
    RISK_HIGHEST,
    RULE_DRUG_COMPARISON,
    RULE_LEXICON,
    RULE_RX_PROMOTION,
    Check,
    Finding,
    GateContext,
    clause_or_placeholder,
)

SUGGESTION_REVIEW = "交人工复核：改为可核验的客观表述，或补充证据"
SUGGESTION_LEGAL = "交法务确认；补充可核验的证据"

_RULE_BY_CATEGORY = {
    "drug_comparison": RULE_DRUG_COMPARISON,
    "prescription_promotion": RULE_RX_PROMOTION,
}


def _finding(draft: Draft, span: tuple[int, int], lexicon: Lexicon, entry: Entry) -> Finding:
    rule = _RULE_BY_CATEGORY.get(entry.category, RULE_LEXICON)
    requires = set(entry.requires)
    risk = entry.risk_level
    if rule != RULE_LEXICON:
        requires.add(REQUIRES_LEGAL)
        risk = RISK_HIGHEST
    clause = clause_or_placeholder(entry.clause)
    details: dict[str, Any] = {
        "lexicon_id": lexicon.lexicon_id,
        "category": entry.category,
        "term": entry.term,
        "match": entry.match,
    }
    if entry.note:
        details["note"] = entry.note
    if entry.clause is not None and clause != entry.clause.strip():
        details["lexicon_clause"] = entry.clause
    label = CATEGORY_LABELS[entry.category]
    return Finding.at(
        draft.raw,
        rule,
        span,
        reason=f"命中词库 {lexicon.lexicon_id} 的{label}词条「{entry.term}」，需人工复核",
        suggestion=SUGGESTION_LEGAL if REQUIRES_LEGAL in requires else SUGGESTION_REVIEW,
        clause=clause,
        risk_level=risk,
        requires=tuple(requires),
        details=details,
    )


def run(draft: Draft, meta: Meta | None, ctx: GateContext) -> list[Finding]:
    index = draft.index(VISIBLE, HIDDEN)
    findings: list[Finding] = []
    for lexicon in ctx.lexicons:
        for entry in lexicon.entries:
            for match in entry.pattern.finditer(index.text):
                if match.end() == match.start():
                    continue
                span = index.raw_span(*match.span())
                if span is not None:
                    findings.append(_finding(draft, span, lexicon, entry))
    return findings


CHECK = Check(
    name="lexicon",
    rule_ids=(RULE_LEXICON, RULE_DRUG_COMPARISON, RULE_RX_PROMOTION),
    summary="词库命中（极限词、夸大承诺、行业敏感词、药品功效比较、处方药推广）",
    run=run,
)
