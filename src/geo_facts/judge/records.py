"""判定记录（judgment 契约）与摘要（两个口径的准确率、不可核验率、呈现率）。

judgment_id 由内容哈希派生（方向、来源、陈述或核心事实、查询日、判定者），不含生成时间，
同输入同查询日得到同一编号；传入 --now 时整份输出逐字节可复现。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from geo_facts.canon import hash_obj
from geo_facts.judge.rules import (
    CONTRADICTED,
    JUDGE,
    MISSING,
    PRESENT_CORRECT,
    PRESENT_WRONG,
    SUPPORTED,
    UNVERIFIABLE,
    Exclusion,
    PresenceVerdict,
    Verdict,
)
from geo_facts.store import describe_reasons

SCHEMA_VERSION = "1.0"
BASIS_NOTE = (
    "准确率有两个口径：可核验口径 = 支持 /（支持 + 矛盾），严格口径 = 支持 / 全部陈述；"
    "验收用哪个口径须在项目预先登记里事前登记，不得看到结果后再选。"
    "不可核验不等于缺失，也不等于错误。呈现率 = 呈现正确 / 应呈现的核心事实（按题配置，"
    "查询日不可用的核心事实已排除），与准确率的分母互不相干。"
)


def judgment_id(direction: str, source: Mapping[str, str], subject: str, as_of: date) -> str:
    return (
        "jdg-"
        + hash_obj(
            {
                "direction": direction,
                "source": dict(source),
                "subject": subject,
                "as_of": as_of.isoformat(),
                "judge": JUDGE,
            }
        )[:32]
    )


def _base(
    direction: str,
    observation: Mapping[str, Any],
    *,
    subject: str,
    as_of: date,
    created_at: str,
    project_id: str,
    synthetic: bool,
) -> dict[str, Any]:
    source = {"type": "observation", "id": observation["observation_id"]}
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "judgment_id": judgment_id(direction, source, subject, as_of),
        "project_id": project_id,
        "direction": direction,
        "source": source,
        "query_id": observation.get("query_id"),
        "as_of": as_of.isoformat(),
        "judge": dict(JUDGE),
        "created_at": created_at,
    }
    if synthetic or observation.get("synthetic"):
        record["synthetic"] = True
    return record


def answer_judgment(
    verdict: Verdict,
    observation: Mapping[str, Any],
    *,
    as_of: date,
    created_at: str,
    project_id: str,
    synthetic: bool,
) -> dict[str, Any]:
    claim = verdict.claim
    record = _base(
        "answer_to_facts",
        observation,
        subject="claim:" + str(claim["claim_id"]),
        as_of=as_of,
        created_at=created_at,
        project_id=project_id,
        synthetic=synthetic,
    )
    fact = verdict.fact
    ext: dict[str, Any] = {
        "reason_code": verdict.reason_code,
        "compared": [
            {"fact_id": item["fact_id"], "version": item["version"]} for item in verdict.compared
        ],
        "claim_text": claim["text"],
        "claim_span": dict(claim["span"]),
    }
    if verdict.stale is not None:
        ext["stale_match"] = dict(verdict.stale)
    if verdict.unusable:
        ext["unusable"] = [
            {"fact_id": status.fact_id, "version": status.version, "reasons": list(status.reasons)}
            for status in verdict.unusable
        ]
    record.update(
        {
            "claim_id": claim["claim_id"],
            "fact_id": None if fact is None else fact["fact_id"],
            "fact_version": None if fact is None else int(fact["version"]),
            "label": verdict.label,
            "claim_type": claim.get("claim_type"),
            "reason": verdict.reason,
            "ext": ext,
        }
    )
    return record


def presence_judgment(
    verdict: PresenceVerdict,
    observation: Mapping[str, Any],
    *,
    as_of: date,
    created_at: str,
    project_id: str,
    synthetic: bool,
) -> dict[str, Any]:
    fact = verdict.fact
    record = _base(
        "facts_to_answer",
        observation,
        subject="fact:" + str(fact["fact_id"]),
        as_of=as_of,
        created_at=created_at,
        project_id=project_id,
        synthetic=synthetic,
    )
    record.update(
        {
            "claim_id": None,
            "fact_id": fact["fact_id"],
            "fact_version": int(fact["version"]),
            "label": verdict.label,
            "claim_type": None,
            "reason": verdict.reason,
            "ext": {"claim_ids": [claim["claim_id"] for claim in verdict.claims]},
        }
    )
    return record


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def summarize_answers(verdicts: Sequence[Verdict]) -> dict[str, Any]:
    counts = Counter(verdict.label for verdict in verdicts)
    supported, contradicted = counts[SUPPORTED], counts[CONTRADICTED]
    unverifiable, total = counts[UNVERIFIABLE], len(verdicts)
    null_reasons: dict[str, str] = {}
    if supported + contradicted == 0:
        null_reasons["accuracy_verifiable"] = "支持与矛盾均为 0，可核验口径的分母为 0"
    if total == 0:
        null_reasons["accuracy_strict"] = "没有拆出任何陈述，严格口径的分母为 0"
        null_reasons["unverifiable_rate"] = "没有拆出任何陈述，不可核验率的分母为 0"
    reasons = Counter(verdict.reason_code for verdict in verdicts if verdict.label == UNVERIFIABLE)
    return {
        "supported": supported,
        "contradicted": contradicted,
        "unverifiable": unverifiable,
        "total": total,
        "accuracy_verifiable": _ratio(supported, supported + contradicted),
        "accuracy_strict": _ratio(supported, total),
        "unverifiable_rate": _ratio(unverifiable, total),
        "null_reasons": null_reasons,
        "unverifiable_reasons": dict(sorted(reasons.items())),
        "stale_contradictions": sum(
            1 for verdict in verdicts if verdict.reason_code == "stale_match"
        ),
    }


def summarize_presence(
    verdicts: Sequence[PresenceVerdict],
    excluded: Sequence[tuple[Mapping[str, Any], Exclusion]],
    *,
    prompt_set_given: bool,
    observations_with_core: int,
) -> dict[str, Any]:
    counts = Counter(verdict.label for verdict in verdicts)
    total = len(verdicts)
    null_reason: str | None = None
    if total == 0:
        if not prompt_set_given:
            null_reason = "未提供题库（--prompt-set），没有按题配置的核心事实，不计算呈现率"
        elif observations_with_core == 0:
            null_reason = "没有观测对应配置了核心事实的题，呈现率的分母为 0"
        else:
            null_reason = "核心事实在查询日都不可用（已排除出分母），呈现率的分母为 0"
    return {
        "present_correct": counts[PRESENT_CORRECT],
        "present_wrong": counts[PRESENT_WRONG],
        "missing": counts[MISSING],
        "total": total,
        "presence_rate": _ratio(counts[PRESENT_CORRECT], total),
        "null_reason": null_reason,
        "observations_with_core_facts": observations_with_core,
        "excluded": len(excluded),
        "excluded_core_facts": [
            {
                "observation_id": observation["observation_id"],
                "query_id": observation.get("query_id"),
                "fact_id": item.fact_id,
                "reasons": list(item.reasons),
                "reason_text": describe_reasons(item.reasons),
            }
            for observation, item in excluded
        ],
    }
