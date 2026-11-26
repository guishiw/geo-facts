"""`judge` 命令的处理流程：读取输入 → 拆解陈述 → 两个方向分别判定 → 判定记录与摘要。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from geo_facts import contracts
from geo_facts.claims.batch import extract_observations, load_observations, usable_observations
from geo_facts.claims.plugins import RuleExtractor
from geo_facts.errors import InputError
from geo_facts.jsonl import read_json
from geo_facts.judge.records import (
    BASIS_NOTE,
    answer_judgment,
    presence_judgment,
    summarize_answers,
    summarize_presence,
)
from geo_facts.judge.rules import (
    Exclusion,
    JudgeContext,
    PresenceVerdict,
    Verdict,
    judge_claims,
    presence_for,
)
from geo_facts.store import load_store


@dataclass
class JudgeRun:
    judgments: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    summary: dict[str, Any]


def load_core_facts(path: Path, ctx: JudgeContext) -> dict[str, list[str]]:
    """题库（prompt_set 契约）里配置了 core_fact_ids 的题：query_id → 核心事实编号。"""
    prompt_set = read_json(path)
    contracts.require_valid("prompt_set", prompt_set, where=path.name)
    cores: dict[str, list[str]] = {}
    for prompt in prompt_set["prompts"]:
        ids = list(dict.fromkeys(prompt.get("core_fact_ids") or []))
        if not ids:
            continue
        missing = [fact_id for fact_id in ids if ctx.view.status(fact_id) is None]
        if missing:
            raise InputError(
                f"{path.name}：题 {prompt['query_id']} 的核心事实不在事实库中：{', '.join(missing)}"
            )
        cores[prompt["query_id"]] = ids
    return cores


def run_judge(
    store_root: str | Path,
    observations_path: str | Path,
    *,
    as_of: date,
    created_at: str,
    prompt_set_path: str | Path | None = None,
) -> JudgeRun:
    store = load_store(store_root)
    ctx = JudgeContext.from_store(store, as_of)
    usable = usable_observations(load_observations(Path(observations_path)))
    cores = load_core_facts(Path(prompt_set_path), ctx) if prompt_set_path is not None else {}
    extractor = RuleExtractor(ctx.lexicon)
    judgments: list[dict[str, Any]] = []
    all_claims: list[dict[str, Any]] = []
    answers: list[Verdict] = []
    presences: list[PresenceVerdict] = []
    excluded: list[tuple[dict[str, Any], Exclusion]] = []
    with_core = 0
    for observation in usable.observations:
        claims = extract_observations(
            extractor, [observation], project_id=store.project_id, synthetic=store.synthetic
        )
        all_claims.extend(claims)
        verdicts = judge_claims(claims, ctx)
        answers.extend(verdicts)
        judgments.extend(
            answer_judgment(
                verdict,
                observation,
                as_of=as_of,
                created_at=created_at,
                project_id=store.project_id,
                synthetic=store.synthetic,
            )
            for verdict in verdicts
        )
        core = cores.get(observation["query_id"])
        if core is None:
            continue
        with_core += 1
        found, skipped = presence_for(core, claims, ctx)
        presences.extend(found)
        excluded.extend((observation, item) for item in skipped)
        judgments.extend(
            presence_judgment(
                verdict,
                observation,
                as_of=as_of,
                created_at=created_at,
                project_id=store.project_id,
                synthetic=store.synthetic,
            )
            for verdict in found
        )
    for judgment in judgments:
        contracts.require_valid("judgment", judgment, where=f"判定 {judgment['judgment_id']}")
    summary = {
        "as_of": as_of.isoformat(),
        "observations": usable.stats.used,
        "observation_stats": usable.stats.to_dict(),
        "claims": len(all_claims),
        "answer_to_facts": summarize_answers(answers),
        "facts_to_answer": summarize_presence(
            presences,
            excluded,
            prompt_set_given=prompt_set_path is not None,
            observations_with_core=with_core,
        ),
        "basis_note": BASIS_NOTE,
    }
    return JudgeRun(judgments=judgments, claims=all_claims, summary=summary)
