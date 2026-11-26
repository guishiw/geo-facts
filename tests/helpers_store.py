"""事实库测试的辅助函数：手工构造实体、事实和事实库目录（全部为虚构数据）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from geo_facts.store import FactStore, StorePaths, commit_records, init_store, load_store

PROJECT_ID = "auto-demo"
NOW = "2026-10-08T02:00:00Z"

_UNSET: Any = object()


def make_evidence(
    evidence_id: str = "EV-S7-SPEC",
    *,
    source_name: str = "澜序汽车官网 S7 参数页（虚构）",
    source_ref: str = "https://lanxu-auto.example/s7/spec",
    collected_at: str = "2026-09-20T02:00:00Z",
    last_verified_at: str = "2026-10-01T02:00:00Z",
    reviewer: str = "事实库审核人（虚构）",
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "source_name": source_name,
        "source_ref": source_ref,
        "collected_at": collected_at,
        "last_verified_at": last_verified_at,
        "reviewer": reviewer,
    }


def make_fact(
    fact_id: str,
    *,
    entity_id: str = "product-lanxu-s7",
    attribute: str = "续航里程",
    value: Any = 620,
    unit: str | None = "km",
    conditions: dict[str, str] | None = None,
    zone: str = "fact",
    source_tier: str | None = "C",
    content_use: str = "specification",
    is_core: bool = False,
    evidence: Any = _UNSET,
    valid_from: str = "2026-03-18",
    valid_until: str | None = None,
    status: str = "active",
    version: int = 1,
    aliases: list[str] | None = None,
    project_id: str = PROJECT_ID,
) -> dict[str, Any]:
    fact: dict[str, Any] = {
        "schema_version": "1.0",
        "fact_id": fact_id,
        "project_id": project_id,
        "entity_id": entity_id,
        "attribute": attribute,
        "value": value,
        "unit": unit,
        "conditions": dict(conditions or {}),
        "zone": zone,
        "source_tier": source_tier,
        "content_use": content_use,
        "is_core": is_core,
        "evidence": make_evidence() if evidence is _UNSET else evidence,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "status": status,
        "version": version,
        "synthetic": True,
    }
    if aliases is not None:
        fact["aliases"] = aliases
    return fact


def make_entity(
    entity_id: str,
    name: str,
    *,
    kind: str = "product",
    role: str = "target",
    parent: str | None = None,
    aliases: tuple[str, ...] = (),
    owned_domains: tuple[str, ...] = (),
    project_id: str = PROJECT_ID,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "entity_id": entity_id,
        "project_id": project_id,
        "kind": kind,
        "role": role,
        "name": name,
        "aliases": list(aliases),
        "exclusions": [],
        "owned_domains": list(owned_domains),
        "parent_entity_id": parent,
        "synthetic": True,
    }


def base_entities() -> list[dict[str, Any]]:
    """澜序品牌、S7 与 E3 两款车型，以及竞品岑野及其同名车型 S7。"""
    return [
        make_entity(
            "brand-lanxu",
            "澜序",
            kind="brand",
            aliases=("澜序汽车", "LANXU", "Lan-Xu"),
            owned_domains=("lanxu-auto.example",),
        ),
        make_entity("product-lanxu-s7", "澜序 S7", parent="brand-lanxu", aliases=("S7",)),
        make_entity("product-lanxu-e3", "澜序 E3", parent="brand-lanxu", aliases=("E3",)),
        make_entity(
            "brand-cenye",
            "岑野",
            kind="brand",
            role="competitor",
            aliases=("岑野汽车", "CENYE"),
            owned_domains=("cenye-auto.example",),
        ),
        make_entity("product-cenye-s7", "岑野 S7", role="competitor", parent="brand-cenye"),
    ]


def memory_store(
    facts: list[dict[str, Any]], entities: list[dict[str, Any]] | None = None
) -> FactStore:
    """不落盘的事实库，只用于时效、冲突这类纯计算的测试。"""
    records = base_entities() if entities is None else entities
    return FactStore(
        paths=StorePaths(Path("unused-store")),
        meta={
            "schema_version": "1.0",
            "store_id": "store-auto-demo",
            "project_id": PROJECT_ID,
            "timezone": "Asia/Shanghai",
            "created_at": NOW,
            "synthetic": True,
        },
        entities={entity["entity_id"]: entity for entity in records},
        facts=list(facts),
    )


def build_store(
    root: Path,
    facts: list[dict[str, Any]],
    entities: list[dict[str, Any]] | None = None,
    *,
    project_id: str = PROJECT_ID,
) -> FactStore:
    """在磁盘上建一个事实库：初始化后经由 commit_records 追加记录，审计链保持完整。"""
    init_store(root, project_id=project_id, now=NOW, synthetic=True)
    store = load_store(root)
    commit_records(
        store,
        entities=base_entities() if entities is None else entities,
        facts=facts,
        actor="测试夹具",
        reason="写入测试数据",
        now=NOW,
        details={"fact_versions": len(facts)},
    )
    return load_store(root, strict=False)
