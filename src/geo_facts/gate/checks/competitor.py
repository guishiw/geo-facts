"""G-COMPETITOR-COMPARISON：涉及竞品的比较、测评（review，须补充证据）。

竞品取自 `--store` 的 entities.jsonl 中 role = competitor 的实体（名称与别名，识别规则见 entities 模块）。
按分句（，,。！？；;换行，以及后跟空白的英文句点）检查：分句里提到竞品，并且
- 含比较 / 测评表达（比……更、优于、不如、对比、相比、横评、评测、碾压、吊打、PK、vs、better than……），或
- 含“高于、低于、超过、领先、落后”等数量比较词，且同一分句提到两个及以上不同实体，
则产生一条 review，span 为该分句。

meta.product_type 为药品（prescription_drug / otc_drug / drug）时，同一处另产生
G-DRUG-COMPARISON（risk_level = highest，requires 含 legal_confirmation）。
"""

from __future__ import annotations

import re

from geo_facts.gate.document import HIDDEN, VISIBLE, Draft, TextIndex
from geo_facts.gate.entities import find_mentions
from geo_facts.gate.meta import Meta
from geo_facts.gate.model import (
    CLAUSE_SELF_DEFINED,
    CLAUSE_TO_VERIFY,
    REQUIRES_LEGAL,
    RISK_HIGHEST,
    RULE_COMPETITOR_COMPARISON,
    RULE_DRUG_COMPARISON,
    Check,
    Finding,
    GateContext,
)

DRUG_PRODUCT_TYPES = frozenset({"prescription_drug", "otc_drug", "drug"})

_CLAUSE_END = re.compile(r"[，,。!?;；\n]|\.(?=\s|$)")
_STRONG = re.compile(
    r"比起|相比|相较|较之|对比|比较|横评|横向(?:对比|评测|测评)|评测|测评|对决|"
    r"优于|胜过|胜于|强于|好于|逊于|逊色于|弱于|不如|比不上|赶不上|不及|"
    r"碾压|吊打|完胜|完爆|秒杀|甩开|甩出|输给|赢过|击败|打败|远超|更胜一筹|"
    r"比(?!如|例|赛|分|率|重)" + r"[^，,。!?;；、\n]{1,15}?(?:更|还要|还|要)|"
    r"比(?!如|例|赛|分|率|重)[^，,。!?;；、\n]{1,15}?"
    r"(?:好|强|快|慢|高|低|长|短|多|少|大|小|贵|便宜|省|优秀|出色)|"
    r"(?<![a-z])(?:pk|vs\.?)(?![a-z])|\bbetter\s+than\b|\bworse\s+than\b|"
    r"\bcompared\s+(?:to|with)\b|\bversus\b|\boutperforms?\b|\bbeats?\b|"
    r"\bsuperior\s+to\b|\binferior\s+to\b"
)
_WEAK = re.compile(
    r"高于|低于|超过|超越|大于|小于|多于|少于|领先|落后|\bmore\s+than\b|\bless\s+than\b"
)


def _clauses(text: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start = 0
    for match in _CLAUSE_END.finditer(text):
        out.append((start, match.start()))
        start = match.end()
    out.append((start, len(text)))
    return [(left, right) for left, right in out if text[left:right].strip()]


def _raw_text(draft: Draft, index: TextIndex, start: int, end: int) -> str:
    span = index.raw_span(start, end)
    return draft.raw[span[0] : span[1]] if span is not None else ""


def unavailable(meta: Meta | None, ctx: GateContext) -> str | None:
    if ctx.entities is None:
        return "未提供 --store，无法识别竞品名称"
    return None


def run(draft: Draft, meta: Meta | None, ctx: GateContext) -> list[Finding]:
    entities = ctx.entities or ()
    index = draft.index(VISIBLE, HIDDEN)
    mentions = find_mentions(index, entities)
    is_drug = meta is not None and meta.product_type in DRUG_PRODUCT_TYPES
    findings: list[Finding] = []
    for left, right in _clauses(index.text):
        inside = [m for m in mentions if left <= m.start and m.end <= right]
        competitors = [m for m in inside if m.entity.role == "competitor"]
        if not competitors:
            continue
        clause = index.text[left:right]
        strong = _STRONG.search(clause)
        weak = _WEAK.search(clause)
        distinct = {m.entity.entity_id for m in inside}
        expression = strong or (weak if len(distinct) >= 2 else None)
        if expression is None:
            continue
        span = index.raw_span(left, right)
        if span is None:  # pragma: no cover - 分句含实体提及，不可能只有分隔符
            continue
        start, end = span
        while start < end and draft.raw[start].isspace():
            start += 1
        while end > start and draft.raw[end - 1].isspace():
            end -= 1
        competitor_ids = sorted({m.entity.entity_id for m in competitors})
        names = sorted({_raw_text(draft, index, m.start, m.end) for m in competitors})
        details = {
            "competitor_entity_ids": competitor_ids,
            "competitor_mentions": names,
            "comparison": expression.group(0),
        }
        findings.append(
            Finding.at(
                draft.raw,
                RULE_COMPETITOR_COMPARISON,
                (start, end),
                reason=f"涉及竞品（{'、'.join(names)}）的比较或测评，需补充可核验的对比证据",
                suggestion="补充可核验的对比证据（测试条件、来源、日期）",
                clause=CLAUSE_SELF_DEFINED,
                details=details,
            )
        )
        if is_drug:
            findings.append(
                Finding.at(
                    draft.raw,
                    RULE_DRUG_COMPARISON,
                    (start, end),
                    reason=f"药品与其他药品（{'、'.join(names)}）比较功效，最高风险",
                    suggestion="交法务确认；补充可核验的证据",
                    clause=CLAUSE_TO_VERIFY,
                    risk_level=RISK_HIGHEST,
                    requires=(REQUIRES_LEGAL,),
                    details=details,
                )
            )
    return findings


CHECK = Check(
    name="competitor_comparison",
    rule_ids=(RULE_COMPETITOR_COMPARISON, RULE_DRUG_COMPARISON),
    summary="涉及竞品的比较、测评；药品与其他药品比较功效",
    run=run,
    unavailable=unavailable,
)
