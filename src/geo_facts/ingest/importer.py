"""导入流程：逐行转换、分配版本号、只追加写入，并记一条审计事件。

版本规则（写进 docs/methodology.md）：
- 同一 fact_id 按文件顺序处理，与“事实库里的最新版本”（含本文件里刚写入的）比较；
- 内容（除 version 外的全部字段）有变化 → 版本号 = 最新版本号 + 1，旧版本原样保留；
- 内容不变 → 跳过并计数；
- 行里填了 version：该版本已存在且内容相同 → 跳过；内容不同 → 报错（已有版本不改写）；
  不存在 → 必须等于“最新版本号 + 1”。这样导出的文件（带 version 列）可以原样再导入。
- 合法行写入、非法行不写入；只要有非法行，命令退出 1。被拒绝的导入同样记审计事件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from geo_facts.ingest.convert import Context, convert_entity_row, convert_fact_row
from geo_facts.ingest.readers import RawSource, read_source
from geo_facts.store import FactStore, commit_records, load_store
from geo_facts.store.writer import SYSTEM_ACTOR


def _content(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "version"}


def import_source(
    store: FactStore,
    source: RawSource,
    *,
    input_name: str,
    reviewer: str | None,
    now: str,
) -> dict[str, Any]:
    """把读好的行导入事实库，返回结果摘要（不含绝对路径）。"""
    ctx = Context(
        project_id=store.project_id,
        entities=dict(store.entities),
        reviewer=reviewer,
        synthetic=store.synthetic,
    )
    known: dict[str, dict[str, Any]] = dict(store.entities)
    errors: list[dict[str, Any]] = []
    notices: list[dict[str, Any]] = []
    new_entities: list[dict[str, Any]] = []
    entity_counts = {"created": 0, "updated": 0, "unchanged": 0, "invalid": 0}
    for raw in (row for row in source.rows if row.kind == "entity"):
        entity, problems = convert_entity_row(raw, ctx)
        if entity is not None:
            parent = entity["parent_entity_id"]
            if parent is not None and parent not in known and parent != entity["entity_id"]:
                problems = [
                    {
                        "row": raw.row,
                        "kind": "entity",
                        "column": "parent_entity_id",
                        "message": f"上级实体 {parent} 不存在（请先导入上级实体）",
                        "entity_id": entity["entity_id"],
                    }
                ]
                entity = None
        if entity is None:
            errors.extend(problems)
            entity_counts["invalid"] += 1
            continue
        previous = known.get(entity["entity_id"])
        if previous == entity:
            entity_counts["unchanged"] += 1
            continue
        entity_counts["created" if previous is None else "updated"] += 1
        known[entity["entity_id"]] = entity
        new_entities.append(entity)
    ctx.entities = known

    versions = {fact_id: list(items) for fact_id, items in store.by_fact_id().items()}
    new_facts: list[dict[str, Any]] = []
    written: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    fact_counts = {"created": 0, "new_versions": 0, "unchanged": 0, "invalid": 0}
    for raw in (row for row in source.rows if row.kind == "fact"):
        converted = convert_fact_row(raw, ctx)
        fact = converted.fact
        if fact is None:
            errors.extend(converted.errors)
            fact_counts["invalid"] += 1
            continue
        fact_id = fact["fact_id"]
        history = versions.setdefault(fact_id, [])
        latest = max((int(item["version"]) for item in history), default=0)
        if converted.version is not None:
            same = [item for item in history if int(item["version"]) == converted.version]
            if same:
                if _content(same[0]) == _content(fact):
                    fact_counts["unchanged"] += 1
                    unchanged.append(
                        {"row": raw.row, "fact_id": fact_id, "version": converted.version}
                    )
                else:
                    errors.append(
                        {
                            "row": raw.row,
                            "kind": "fact",
                            "column": "version",
                            "fact_id": fact_id,
                            "message": f"版本 {converted.version} 已存在且内容不同；已有版本只追加、不改写，"
                            "要更新请清空 version（自动取最新版本号 + 1）",
                        }
                    )
                    fact_counts["invalid"] += 1
                continue
            if converted.version != latest + 1:
                errors.append(
                    {
                        "row": raw.row,
                        "kind": "fact",
                        "column": "version",
                        "fact_id": fact_id,
                        "message": f"版本号必须等于最新版本号 + 1（{latest + 1}），实际为 {converted.version}",
                    }
                )
                fact_counts["invalid"] += 1
                continue
        elif history:
            current = max(history, key=lambda item: int(item["version"]))
            if _content(current) == _content(fact):
                fact_counts["unchanged"] += 1
                unchanged.append({"row": raw.row, "fact_id": fact_id, "version": latest})
                continue
        fact["version"] = latest + 1
        action = "created" if latest == 0 else "new_version"
        fact_counts["created" if latest == 0 else "new_versions"] += 1
        history.append(fact)
        new_facts.append(fact)
        written.append(
            {"row": raw.row, "fact_id": fact_id, "version": fact["version"], "action": action}
        )
        notices.extend(converted.notices)

    rows = len(source.rows)
    invalid_rows = fact_counts["invalid"] + entity_counts["invalid"]
    summary = {
        "input": input_name,
        "format": source.format,
        "rows": rows,
        "facts": fact_counts,
        "entities": entity_counts,
        "invalid_rows": invalid_rows,
        "defaulted_source_tiers": len(notices),
    }
    event = commit_records(
        store,
        entities=new_entities,
        facts=new_facts,
        actor=reviewer or SYSTEM_ACTOR,
        reason=f"导入 {input_name}",
        now=now,
        details={
            **summary,
            "written": [
                {"fact_id": item["fact_id"], "version": item["version"]} for item in written
            ],
        },
    )
    errors.sort(key=lambda item: (item["kind"] != "entity", item["row"], item["column"]))
    return {
        **summary,
        "ok": invalid_rows == 0,
        "written": written,
        "unchanged": unchanged,
        "errors": errors,
        "notices": notices,
        "audit_event_id": event["event_id"],
    }


def import_file(
    store_root: str | Path,
    input_path: str | Path,
    *,
    fmt: str | None,
    reviewer: str | None,
    now: str,
) -> dict[str, Any]:
    """读取文件并导入；事实库或文件整体不合格时抛 InputError（退出码 2）。"""
    store = load_store(store_root)
    path = Path(input_path)
    source = read_source(path, fmt)
    return import_source(store, source, input_name=path.name, reviewer=reviewer, now=now)
