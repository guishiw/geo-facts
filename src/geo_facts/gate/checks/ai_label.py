"""G-AI-LABEL：AI 生成内容缺少标识状态（review）。只看 meta：

- 没有提供 meta → 标识状态未知；
- meta 没有 ai_generated 字段 → 标识状态未知；
- ai_generated = true 而 ai_label 缺失、为 false 或为空字符串 → 缺少 AI 生成标识；
- ai_generated = false，或 ai_generated = true 且 ai_label 为 true / 非空字符串 → 不产生发现。
"""

from __future__ import annotations

from geo_facts.gate.document import Draft
from geo_facts.gate.meta import Meta
from geo_facts.gate.model import CLAUSE_SELF_DEFINED, RULE_AI_LABEL, Check, Finding, GateContext

SUGGESTION = "在 meta 中如实填写 ai_generated；AI 生成的内容补充 ai_label 标识"


def run(draft: Draft, meta: Meta | None, ctx: GateContext) -> list[Finding]:
    if meta is None:
        reason, kind = "未提供 meta，AI 生成标识状态未知", "meta_missing"
    elif not meta.has_ai_generated:
        reason, kind = "meta 缺少 ai_generated，AI 生成标识状态未知", "status_unknown"
    elif meta.ai_generated and not meta.ai_labeled:
        reason, kind = "meta.ai_generated 为 true，但缺少 AI 生成标识（ai_label）", "label_missing"
    else:
        return []
    return [
        Finding.at(
            draft.raw,
            RULE_AI_LABEL,
            None,
            reason=reason,
            suggestion=SUGGESTION,
            clause=CLAUSE_SELF_DEFINED,
            details={"kind": kind},
        )
    ]


CHECK = Check(
    name="ai_label",
    rule_ids=(RULE_AI_LABEL,),
    summary="AI 生成内容缺少标识状态",
    run=run,
)
