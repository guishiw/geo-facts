"""事实库的创建与追加写入。facts.jsonl 与 entities.jsonl 只追加，不改写已有行。"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from geo_facts.errors import InputError
from geo_facts.jsonl import append_jsonl, write_json
from geo_facts.store.audit import append_event, state_hash
from geo_facts.store.model import (
    STORE_SCHEMA_VERSION,
    FactStore,
    StorePaths,
    is_valid_id,
    record_issues,
)
from geo_facts.timeutil import DEFAULT_TIMEZONE, parse_utc, zone

EXAMPLES: tuple[str, ...] = ("auto",)
EXAMPLE_PROJECT_ID = "auto-demo"
SYSTEM_ACTOR = "geo-facts 命令行"


def load_example(name: str, project_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """读取包内样例数据（全部虚构），并把 project_id 换成指定值。"""
    if name not in EXAMPLES:
        raise InputError(f"未知样例：{name}；可选：{', '.join(EXAMPLES)}")
    base = resources.files("geo_facts").joinpath("examples_data", name)
    entities: list[dict[str, Any]] = json.loads(
        base.joinpath("entities.json").read_text(encoding="utf-8")
    )
    facts: list[dict[str, Any]] = json.loads(
        base.joinpath("facts.json").read_text(encoding="utf-8")
    )
    for record in (*entities, *facts):
        record["project_id"] = project_id
    return entities, facts


def _require_records(contract: str, records: list[dict[str, Any]]) -> None:
    for record in records:
        issues = record_issues(contract, record)
        if issues:
            label = record.get("fact_id") or record.get("entity_id")
            raise InputError(
                f"{label} 不符合契约 {contract}"
                f"（{issues[0]['path'] or '/'}：{issues[0]['message']}）",
                details=issues,
            )


def init_store(
    root: str | Path,
    *,
    project_id: str | None,
    now: str,
    example: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
    store_id: str | None = None,
    synthetic: bool = False,
) -> dict[str, Any]:
    """创建事实库目录。目录已存在且非空时拒绝，避免覆盖已有数据。"""
    base = Path(root)
    if base.exists() and (not base.is_dir() or any(base.iterdir())):
        raise InputError(f"目标已存在且非空，拒绝覆盖：{base}")
    resolved_id = project_id or (EXAMPLE_PROJECT_ID if example else None)
    if resolved_id is None:
        raise InputError("请用 --project-id 指定项目标识（使用 --example 时可省略）")
    if not is_valid_id(resolved_id):
        raise InputError(f"project_id 不合法：{resolved_id!r}")
    resolved_store_id = store_id or f"store-{resolved_id}"
    if not is_valid_id(resolved_store_id):
        raise InputError(f"store_id 不合法：{resolved_store_id!r}（可用 --store-id 指定）")
    zone(timezone)
    parse_utc(now)
    entities: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    if example is not None:
        entities, facts = load_example(example, resolved_id)
    _require_records("entity", entities)
    _require_records("fact", facts)
    is_synthetic = synthetic or example is not None
    paths = StorePaths(base)
    base.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {
        "schema_version": STORE_SCHEMA_VERSION,
        "store_id": resolved_store_id,
        "project_id": resolved_id,
        "timezone": timezone,
        "created_at": now,
    }
    if is_synthetic:
        meta["synthetic"] = True
    write_json(paths.meta, meta)
    append_jsonl(paths.entities, entities)
    append_jsonl(paths.facts, facts)
    append_event(
        paths.audit,
        project_id=resolved_id,
        actor=SYSTEM_ACTOR,
        action="register",
        target_id=resolved_store_id,
        reason=f"初始化事实库（样例 {example}）" if example else "初始化事实库",
        occurred_at=now,
        before_hash=None,
        after_hash=state_hash(paths),
        details={"entities": len(entities), "fact_versions": len(facts), "example": example},
        synthetic=is_synthetic,
    )
    return {
        "store": str(base),
        "store_id": resolved_store_id,
        "project_id": resolved_id,
        "timezone": timezone,
        "example": example,
        "entities": len(entities),
        "fact_versions": len(facts),
        "fact_ids": len({fact["fact_id"] for fact in facts}),
        "synthetic": is_synthetic,
    }


def commit_records(
    store: FactStore,
    *,
    entities: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    actor: str,
    reason: str,
    now: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    """把新的实体和事实版本追加进事实库，并记一条审计事件。返回该事件。

    调用方负责版本号；这里只做契约校验并保证“只追加”。即使没有任何新记录也会记事件，
    这样被拒绝的导入同样留痕。
    """
    parse_utc(now)
    _require_records("entity", entities)
    _require_records("fact", facts)
    paths = store.paths
    before = state_hash(paths)
    if entities:
        append_jsonl(paths.entities, entities)
    if facts:
        append_jsonl(paths.facts, facts)
    return append_event(
        paths.audit,
        project_id=store.project_id,
        actor=actor,
        action="import",
        target_id=store.store_id,
        reason=reason,
        occurred_at=now,
        before_hash=before,
        after_hash=state_hash(paths),
        details=details,
        synthetic=store.synthetic,
    )
