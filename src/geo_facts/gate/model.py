"""闸门的公共类型：规则编号、结果、条款号、检查项签名与上下文。

检查项插件的统一签名见 `Check`：

    run(draft: Draft, meta: Meta | None, ctx: GateContext) -> list[Finding]

每个检查项一个模块（`geo_facts.gate.checks.<名>`），模块里定义 `CHECK = Check(...)`，
并登记到 `geo_facts.gate.checks.ALL_CHECKS`。引擎只负责调用、汇总状态和排序，
检查项之间互不依赖。某个检查项缺少必要输入时由 `unavailable(ctx)` 返回原因，
引擎把它列入 `skipped_checks`，不会把“没检查”说成“没问题”。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from geo_facts.gate.corpus import Corpus
    from geo_facts.gate.document import Draft
    from geo_facts.gate.entities import Entity
    from geo_facts.gate.lexicon import Lexicon
    from geo_facts.gate.meta import Meta

RESULT_BLOCK = "block"
RESULT_REVIEW = "review"

STATUS_PASS = "pass"
STATUS_REVIEW = "review"
STATUS_BLOCK = "block"
STATUS_LABELS: Mapping[str, str] = {
    STATUS_PASS: "未发现问题",
    STATUS_REVIEW: "需复核",
    STATUS_BLOCK: "拦截",
}

# gate 专用退出码：只有 review 时退出 5（有 block 退出 1，无发现退出 0）。
EXIT_REVIEW = 5

NOTICE = "闸门只给建议和状态，不负责发布"

RULE_FACT_CONTRADICTION = "G-FACT-CONTRADICTION"
RULE_FACT_STALE = "G-FACT-STALE"
RULE_HIDDEN_TEXT = "G-HIDDEN-TEXT"
RULE_PROMPT_INJECTION = "G-PROMPT-INJECTION"
RULE_TIME_FORGED = "G-TIME-FORGED"
RULE_LEXICON = "G-LEXICON"
RULE_COMPETITOR_COMPARISON = "G-COMPETITOR-COMPARISON"
RULE_DRUG_COMPARISON = "G-DRUG-COMPARISON"
RULE_RX_PROMOTION = "G-RX-PROMOTION"
RULE_NEAR_DUPLICATE = "G-NEAR-DUPLICATE"
RULE_AI_LABEL = "G-AI-LABEL"

# 每条规则的结果是固定的：finding 的 result 由规则编号决定，检查项无法自行改成 block。
# 词库类规则（G-LEXICON / G-DRUG-COMPARISON / G-RX-PROMOTION）永远只产生 review（046 §8.3.5）。
RULE_RESULTS: Mapping[str, str] = {
    RULE_FACT_CONTRADICTION: RESULT_BLOCK,
    RULE_FACT_STALE: RESULT_BLOCK,
    RULE_HIDDEN_TEXT: RESULT_BLOCK,
    RULE_PROMPT_INJECTION: RESULT_BLOCK,
    RULE_TIME_FORGED: RESULT_BLOCK,
    RULE_LEXICON: RESULT_REVIEW,
    RULE_COMPETITOR_COMPARISON: RESULT_REVIEW,
    RULE_DRUG_COMPARISON: RESULT_REVIEW,
    RULE_RX_PROMOTION: RESULT_REVIEW,
    RULE_NEAR_DUPLICATE: RESULT_REVIEW,
    RULE_AI_LABEL: RESULT_REVIEW,
}

# 只能引用 GOAL.md 中出现过的条款号；其余情况用下面两个固定说法，不得编造条款号。
ALLOWED_CLAUSES: frozenset[str] = frozenset(
    {
        "046 §7.1.4",
        "046 §8.3.5",
        "046 §8.4.4",
        "047 §8.3",
        "047 §9.3",
        "047 §10.1",
        "047 §13.2",
        "047 §14",
        "047 §15.2",
        "026 §6.2",
        "026 §7.3",
    }
)
CLAUSE_SELF_DEFINED = "无对应条款，本工具自定规则"
CLAUSE_TO_VERIFY = "待人工核对"
CLAUSE_LEXICON = "046 §8.3.5"
CLAUSE_TIME = "026 §7.3"

RISK_HIGHEST = "highest"
REQUIRES_LEGAL = "legal_confirmation"


def clause_or_placeholder(clause: str | None) -> str:
    """外部给出的条款号只有在登记范围内才照用，否则一律写“待人工核对”。"""
    if clause is not None and clause.strip() in ALLOWED_CLAUSES:
        return clause.strip()
    return CLAUSE_TO_VERIFY


@dataclass(frozen=True)
class Finding:
    """一条闸门发现。span 是稿件原文的码点区间（左闭右开）；整篇级别的发现 span 为 None。"""

    rule_id: str
    span: tuple[int, int] | None
    excerpt: str
    reason: str
    suggestion: str
    clause: str
    risk_level: str | None = None
    requires: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.rule_id not in RULE_RESULTS:
            raise ValueError(f"未登记的规则编号：{self.rule_id}")

    @property
    def result(self) -> str:
        return RULE_RESULTS[self.rule_id]

    @classmethod
    def at(
        cls,
        raw: str,
        rule_id: str,
        span: tuple[int, int] | None,
        *,
        reason: str,
        suggestion: str,
        clause: str,
        risk_level: str | None = None,
        requires: tuple[str, ...] = (),
        details: Mapping[str, Any] | None = None,
    ) -> Finding:
        """按原文区间构造 finding，excerpt 恒等于 原文[start:end]。"""
        if span is not None:
            start, end = span
            if not 0 <= start <= end <= len(raw):
                raise ValueError(f"区间越界：{span}")
            excerpt = raw[start:end]
        else:
            excerpt = ""
        return cls(
            rule_id=rule_id,
            span=span,
            excerpt=excerpt,
            reason=reason,
            suggestion=suggestion,
            clause=clause,
            risk_level=risk_level,
            requires=tuple(sorted(set(requires))),
            details=dict(details or {}),
        )

    def sort_key(self) -> tuple[int, int, int, str, str]:
        if self.span is None:
            return (0, 0, 0, self.rule_id, self.reason)
        return (1, self.span[0], self.span[1], self.rule_id, self.reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "result": self.result,
            "span": list(self.span) if self.span is not None else None,
            "excerpt": self.excerpt,
            "reason": self.reason,
            "suggestion": self.suggestion,
            "clause": self.clause,
            "risk_level": self.risk_level,
            "requires": list(self.requires),
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class GateContext:
    """检查项共用的上下文。

    - store：`--store` 目录（事实类检查要用；本分支只读 entities.jsonl / store.json）；
    - as_of：按哪一天取有效事实（事实类检查要用）；
    - now：判断“时间在未来”的基准（`--now` 或当前时间，UTC）；
    - timezone：把 UTC 时间换算成自然日的时区（store.json 的 timezone，缺省 Asia/Shanghai）；
    - entities：事实库实体（entities.jsonl），None 表示没有提供 `--store`；
    - lexicons：已加载的词库（内置通用示例词库 + `--lexicon-dir`）；
    - corpus：`--corpus` 既有稿件，None 表示未提供；
    - draft_path：稿件路径（只用于在 corpus 中排除稿件自身，不写入结果）。
    """

    now: datetime
    as_of: date
    timezone: str
    store: Path | None = None
    entities: tuple[Entity, ...] | None = None
    lexicons: tuple[Lexicon, ...] = ()
    corpus: Corpus | None = None
    draft_path: Path | None = None


CheckFn = Callable[["Draft", "Meta | None", GateContext], list[Finding]]
UnavailableFn = Callable[["Meta | None", GateContext], str | None]


def always_available(meta: Meta | None, ctx: GateContext) -> str | None:
    return None


@dataclass(frozen=True)
class Check:
    """检查项插件。name 唯一；rule_ids 是它可能产生的规则编号。"""

    name: str
    rule_ids: tuple[str, ...]
    summary: str
    run: CheckFn
    unavailable: UnavailableFn = always_available
