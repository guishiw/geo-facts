"""事实类检查的注册位置：G-FACT-CONTRADICTION、G-FACT-STALE（block）。

本分支没有事实库与判定模块，这两条规则**尚未启用**：`unavailable` 恒返回原因，
引擎把它们列入输出的 `skipped_checks`，不会当作“已检查、无问题”。

接入方式（合并后的收尾阶段）：在本模块实现 `run(draft, meta, ctx)`——用 ctx.store 与 ctx.as_of
取事实库视图，对稿件做陈述拆解与判定（source.type = draft），矛盾的陈述产出
G-FACT-CONTRADICTION，与已过期 / 已撤回 / 无证据的核心事实一致的陈述产出 G-FACT-STALE；
把 `unavailable` 改为“未提供 --store 时返回原因，否则返回 None”。span 用陈述在原文中的位置，
用 `Finding.at(draft.raw, ...)` 构造即可保证 excerpt == 原文[start:end]。
"""

from __future__ import annotations

from geo_facts.gate.document import Draft
from geo_facts.gate.meta import Meta
from geo_facts.gate.model import (
    RULE_FACT_CONTRADICTION,
    RULE_FACT_STALE,
    Check,
    Finding,
    GateContext,
)

NOT_ENABLED = "未启用：本版本闸门尚未接入事实库判定，未检查“与有效事实矛盾”和“使用过期、撤回、无证据的核心事实”"


def unavailable(meta: Meta | None, ctx: GateContext) -> str | None:
    return NOT_ENABLED


def run(draft: Draft, meta: Meta | None, ctx: GateContext) -> list[Finding]:
    raise NotImplementedError("事实类检查尚未启用")


CHECK = Check(
    name="facts",
    rule_ids=(RULE_FACT_CONTRADICTION, RULE_FACT_STALE),
    summary="与有效事实矛盾；使用过期、撤回、无证据的核心事实",
    run=run,
    unavailable=unavailable,
)
