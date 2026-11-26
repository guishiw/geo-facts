"""G-RX-PROMOTION（由 meta 触发）：处方药面向大众推广（review，最高风险，需法务确认）。

meta.product_type = "prescription_drug" 且 meta.audience = "public" → 一条整篇级别的发现。
正文里的处方药推广用语由词库（category = prescription_promotion）另行识别。
"""

from __future__ import annotations

from geo_facts.gate.document import Draft
from geo_facts.gate.meta import Meta
from geo_facts.gate.model import (
    CLAUSE_TO_VERIFY,
    REQUIRES_LEGAL,
    RISK_HIGHEST,
    RULE_RX_PROMOTION,
    Check,
    Finding,
    GateContext,
)


def run(draft: Draft, meta: Meta | None, ctx: GateContext) -> list[Finding]:
    if meta is None or meta.product_type != "prescription_drug" or meta.audience != "public":
        return []
    return [
        Finding.at(
            draft.raw,
            RULE_RX_PROMOTION,
            None,
            reason="meta 标明处方药（product_type = prescription_drug）面向大众（audience = public）推广",
            suggestion="交法务确认",
            clause=CLAUSE_TO_VERIFY,
            risk_level=RISK_HIGHEST,
            requires=(REQUIRES_LEGAL,),
            details={"kind": "meta", "product_type": meta.product_type, "audience": meta.audience},
        )
    ]


CHECK = Check(
    name="rx_audience",
    rule_ids=(RULE_RX_PROMOTION,),
    summary="处方药面向大众推广（meta）",
    run=run,
)
