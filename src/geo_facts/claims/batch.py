"""批量拆解：读取 observations.jsonl（observation 契约），只处理 status = valid 且回答非空的观测。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from geo_facts import contracts
from geo_facts.claims.plugins import ClaimExtractor
from geo_facts.errors import InputError
from geo_facts.jsonl import iter_jsonl


@dataclass
class ObservationStats:
    total: int = 0
    used: int = 0
    skipped_invalid: int = 0
    skipped_empty: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "used": self.used,
            "skipped_invalid": self.skipped_invalid,
            "skipped_empty": self.skipped_empty,
        }


@dataclass
class Usable:
    """可拆解的观测及其回答原文。"""

    observations: list[dict[str, Any]] = field(default_factory=list)
    stats: ObservationStats = field(default_factory=ObservationStats)


def load_observations(path: Path) -> list[dict[str, Any]]:
    """读取并逐行按 observation 契约校验；任何一行不合契约都按输入错误拒绝。"""
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for number, record in iter_jsonl(path):
        contracts.require_valid("observation", record, where=f"{path.name} 第 {number} 行")
        if record["observation_id"] in seen:
            raise InputError(
                f"{path.name} 第 {number} 行：observation_id {record['observation_id']} 重复"
            )
        seen.add(record["observation_id"])
        records.append(record)
    return records


def usable_observations(records: list[dict[str, Any]]) -> Usable:
    result = Usable()
    for record in records:
        result.stats.total += 1
        if record["status"] != "valid":
            result.stats.skipped_invalid += 1
            continue
        answer = record["answer_text"]
        if not isinstance(answer, str) or not answer.strip():
            result.stats.skipped_empty += 1
            continue
        result.stats.used += 1
        result.observations.append(record)
    return result


def validate_claims(claims: list[dict[str, Any]], *, extractor: str) -> None:
    """插件输出也必须逐条合契约，且 text 必须等于原文切片（由调用方另行核对）。"""
    for claim in claims:
        issues = contracts.iter_issues("claim", claim)
        if issues:
            raise InputError(
                f"拆解器 {extractor} 输出的陈述不符合 claim 契约（{issues[0].path or '/'}：{issues[0].message}）",
                details=[issue.to_dict() for issue in issues],
            )


def check_spans(claims: list[dict[str, Any]], text: str, *, extractor: str) -> None:
    for claim in claims:
        start, end = claim["span"]["start"], claim["span"]["end"]
        if not (0 <= start < end <= len(text)) or text[start:end] != claim["text"]:
            raise InputError(
                f"拆解器 {extractor} 输出的陈述 {claim['claim_id']} 的 text 与原文位置不一致"
            )


def extract_text(
    extractor: ClaimExtractor,
    text: str,
    *,
    source: Mapping[str, str],
    project_id: str | None,
    synthetic: bool,
) -> list[dict[str, Any]]:
    claims = extractor.extract(text, source=source, project_id=project_id, synthetic=synthetic)
    validate_claims(claims, extractor=extractor.name)
    check_spans(claims, text, extractor=extractor.name)
    return claims


def extract_observations(
    extractor: ClaimExtractor,
    observations: list[dict[str, Any]],
    *,
    project_id: str | None,
    synthetic: bool,
) -> list[dict[str, Any]]:
    """按观测顺序拆解；每条观测内按原文位置排序。"""
    claims: list[dict[str, Any]] = []
    for record in observations:
        claims.extend(
            extract_text(
                extractor,
                record["answer_text"],
                source={"type": "observation", "id": record["observation_id"]},
                project_id=project_id,
                synthetic=synthetic or bool(record.get("synthetic", False)),
            )
        )
    return claims


def summarize(claims: list[dict[str, Any]]) -> dict[str, Any]:
    types = Counter(claim["claim_type"] for claim in claims)
    resolutions = Counter(
        str(claim.get("ext", {}).get("entity_resolution", "unknown")) for claim in claims
    )
    return {
        "claims": len(claims),
        "by_type": {name: types.get(name, 0) for name in ("numeric", "model", "date", "free_text")},
        "by_entity_resolution": dict(sorted(resolutions.items())),
        "without_entity": sum(1 for claim in claims if claim["entity_id"] is None),
    }
