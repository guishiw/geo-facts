"""G-NEAR-DUPLICATE：同品牌同主题近似重复（review）。

与 `--corpus` 中 brand_entity_id 相同、topic 相同（忽略全半角、大小写、空白与连字符）的既有稿件比较，
相似度规则与 corpus 格式见 geo_facts.gate.corpus。稿件自身（同一路径）不与自己比较。
需要：`--corpus`，以及稿件 meta 中的 brand_entity_id 与 topic；缺少时列入 skipped_checks。
"""

from __future__ import annotations

from geo_facts.gate.corpus import (
    MIN_SHINGLES,
    NEAR_DUPLICATE_THRESHOLD,
    SHINGLE_SIZE,
    draft_shingles,
    jaccard,
    topic_key,
)
from geo_facts.gate.document import Draft
from geo_facts.gate.meta import Meta
from geo_facts.gate.model import (
    CLAUSE_SELF_DEFINED,
    RULE_NEAR_DUPLICATE,
    Check,
    Finding,
    GateContext,
)


def unavailable(meta: Meta | None, ctx: GateContext) -> str | None:
    if ctx.corpus is None:
        return "未提供 --corpus，无法比较既有稿件"
    if meta is None or not meta.brand_entity_id or not meta.topic:
        return "meta 缺少 brand_entity_id 或 topic，无法确定同品牌同主题的比较范围"
    return None


def run(draft: Draft, meta: Meta | None, ctx: GateContext) -> list[Finding]:
    if ctx.corpus is None or meta is None or meta.topic is None:  # pragma: no cover
        return []  # unavailable() 已排除这些情况
    mine = draft_shingles(draft)
    if len(mine) < MIN_SHINGLES:
        return []
    own = ctx.draft_path.resolve() if ctx.draft_path is not None else None
    findings: list[Finding] = []
    for doc in ctx.corpus.docs:
        if own is not None and doc.path.resolve() == own:
            continue
        if doc.brand_entity_id != meta.brand_entity_id or doc.topic is None:
            continue
        if topic_key(doc.topic) != topic_key(meta.topic) or len(doc.shingles) < MIN_SHINGLES:
            continue
        similarity = jaccard(mine, doc.shingles)
        if similarity < NEAR_DUPLICATE_THRESHOLD:
            continue
        findings.append(
            Finding.at(
                draft.raw,
                RULE_NEAR_DUPLICATE,
                None,
                reason=(
                    f"与既有稿件 {doc.name} 同品牌同主题，相似度 {similarity:.2f}"
                    f"（阈值 {NEAR_DUPLICATE_THRESHOLD}）"
                ),
                suggestion="人工复核是否重复发布；必要时合并或改写",
                clause=CLAUSE_SELF_DEFINED,
                details={
                    "corpus_file": doc.name,
                    "similarity": round(similarity, 4),
                    "threshold": NEAR_DUPLICATE_THRESHOLD,
                    "shingle_size": SHINGLE_SIZE,
                    "brand_entity_id": meta.brand_entity_id,
                    "topic": meta.topic,
                },
            )
        )
    return findings


CHECK = Check(
    name="near_duplicate",
    rule_ids=(RULE_NEAR_DUPLICATE,),
    summary="同品牌同主题近似重复",
    run=run,
    unavailable=unavailable,
)
