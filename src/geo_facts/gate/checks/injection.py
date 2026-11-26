"""G-PROMPT-INJECTION：针对模型的指令（block）。规则见 geo_facts.gate.instructions。

逐类扫描：可见正文与代码块（可豁免引用示例、弱特征要求语境）、隐藏文本、HTML 注释、
脚本与样式、属性值、解析器未消费的残余标记（后五类不豁免）。重叠的命中合并为一条。
"""

from __future__ import annotations

from geo_facts.gate.document import (
    ATTRIBUTE,
    CODE,
    COMMENT,
    HIDDEN,
    MARKUP,
    SCRIPT,
    VISIBLE,
    Draft,
)
from geo_facts.gate.instructions import InstructionMatch, find_instructions
from geo_facts.gate.meta import Meta
from geo_facts.gate.model import (
    CLAUSE_SELF_DEFINED,
    RULE_PROMPT_INJECTION,
    Check,
    Finding,
    GateContext,
)

_SCANS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("正文", (VISIBLE, CODE), True),
    ("隐藏文本", (HIDDEN,), False),
    ("HTML 注释", (COMMENT,), False),
    ("脚本或样式", (SCRIPT,), False),
    ("属性值", (ATTRIBUTE,), False),
    ("未解析的标记", (MARKUP,), False),
)


def _merge(matches: list[InstructionMatch]) -> list[list[InstructionMatch]]:
    groups: list[list[InstructionMatch]] = []
    for match in matches:
        if groups and match.start < max(item.end for item in groups[-1]):
            groups[-1].append(match)
        else:
            groups.append([match])
    return groups


def run(draft: Draft, meta: Meta | None, ctx: GateContext) -> list[Finding]:
    findings: list[Finding] = []
    for where, channels, lenient in _SCANS:
        index = draft.index(*channels)
        for group in _merge(find_instructions(index, draft, lenient=lenient)):
            start = min(item.start for item in group)
            end = max(item.end for item in group)
            span = index.raw_span(start, end)
            if span is None:  # pragma: no cover - 命中总含有非分隔符字符
                continue
            labels = "、".join(dict.fromkeys(item.label for item in group))
            findings.append(
                Finding.at(
                    draft.raw,
                    RULE_PROMPT_INJECTION,
                    span,
                    reason=f"{where}中出现针对模型的指令（{labels}）",
                    suggestion="删除针对模型的指令性内容",
                    clause=CLAUSE_SELF_DEFINED,
                    details={
                        "channel": where,
                        "pattern_ids": sorted({item.pattern_id for item in group}),
                        "strength": sorted({item.strength for item in group}),
                    },
                )
            )
    return findings


CHECK = Check(
    name="prompt_injection",
    rule_ids=(RULE_PROMPT_INJECTION,),
    summary="针对模型的提示词注入（中英文）",
    run=run,
)
