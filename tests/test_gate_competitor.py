"""G-COMPETITOR-COMPARISON：竞品名称识别（别名、全半角、同名车型、排除词）与比较表达。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from helpers_gate import evaluate, meta, run_check

from geo_facts.errors import InputError
from geo_facts.gate.checks import competitor
from geo_facts.gate.document import parse_draft
from geo_facts.gate.entities import Entity, find_mentions, load_entities, name_pattern

CHECK = competitor.CHECK


def entity(entity_id: str, name: str, role: str, **fields: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": "1.0",
        "entity_id": entity_id,
        "project_id": "auto-demo",
        "kind": "product" if entity_id.startswith("product-") else "brand",
        "role": role,
        "name": name,
        "aliases": [],
        "exclusions": [],
        "owned_domains": [],
        "parent_entity_id": None,
        "synthetic": True,
    }
    record.update(fields)
    return record


WORLD = [
    entity("brand-lanxu", "澜序", "target", aliases=["澜序汽车", "LANXU", "Lan-Xu"]),
    entity(
        "product-lanxu-s7", "澜序 S7", "target", aliases=["澜序S7"], parent_entity_id="brand-lanxu"
    ),
    entity("product-lanxu-x5", "澜序 X5", "target", parent_entity_id="brand-lanxu"),
    entity("brand-cenye", "岑野", "competitor", aliases=["岑野汽车", "CENYE"]),
    entity(
        "product-cenye-k5", "岑野 K5", "competitor", aliases=["K5"], parent_entity_id="brand-cenye"
    ),
    entity(
        "product-cenye-s7", "岑野 S7", "competitor", aliases=["S7"], parent_entity_id="brand-cenye"
    ),
    entity(
        "brand-shuofeng",
        "朔风",
        "competitor",
        aliases=["朔风汽车", "SHUOFENG"],
        exclusions=["朔风凛冽"],
    ),
    entity("product-shuofeng-t6", "朔风 T6", "competitor", parent_entity_id="brand-shuofeng"),
]


def to_entity(record: dict[str, Any]) -> Entity:
    return Entity(
        entity_id=record["entity_id"],
        kind=record["kind"],
        role=record["role"],
        name=record["name"],
        aliases=tuple(record["aliases"]),
        exclusions=tuple(record["exclusions"]),
        parent_entity_id=record["parent_entity_id"],
    )


ENTITIES = tuple(to_entity(record) for record in WORLD)


def compare(raw: str, fmt: str = "md", **kwargs: Any) -> list[Any]:
    return run_check(CHECK, raw, fmt, entities=ENTITIES, **kwargs)


def write_store(path: Path, records: list[dict[str, Any]]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    lines = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    (path / "entities.jsonl").write_text(lines, encoding="utf-8")
    return path


# ------------------------------------------------------------------ 名称识别


def mention_ids(text: str) -> list[tuple[str, str]]:
    draft = parse_draft(text, "txt")
    index = draft.index("visible")
    out = []
    for mention in find_mentions(index, ENTITIES):
        span = index.raw_span(mention.start, mention.end)
        assert span is not None
        out.append((text[span[0] : span[1]], mention.entity.entity_id))
    return out


def test_same_name_models_are_not_confused() -> None:
    assert mention_ids("澜序 S7 与岑野 S7") == [
        ("澜序 S7", "product-lanxu-s7"),
        ("岑野 S7", "product-cenye-s7"),
    ]
    assert mention_ids("澜序S7、岑野S7") == [
        ("澜序S7", "product-lanxu-s7"),
        ("岑野S7", "product-cenye-s7"),
    ]


def test_width_case_space_and_hyphen_variants() -> None:
    assert mention_ids("岑野Ｋ－５") == [("岑野Ｋ－５", "product-cenye-k5")]
    assert mention_ids("cenye 与 lan-xu") == [("cenye", "brand-cenye"), ("lan-xu", "brand-lanxu")]


def test_ascii_boundaries() -> None:
    assert mention_ids("CENYES K50") == []
    assert mention_ids("型号 K5。") == [("K5", "product-cenye-k5")]


def test_exclusions() -> None:
    assert mention_ids("朔风凛冽的冬天") == []
    assert mention_ids("朔风 T6") == [("朔风 T6", "product-shuofeng-t6")]


def test_name_pattern_of_empty_key() -> None:
    assert name_pattern(" - ") is None


# ------------------------------------------------------------------ 比较表达


@pytest.mark.parametrize(
    ("raw", "clause"),
    [
        ("与岑野 K5 相比，澜序 S7 的续航更长。", "与岑野 K5 相比"),
        ("澜序 S7 的续航比岑野 K5 长 40 公里。", "澜序 S7 的续航比岑野 K5 长 40 公里"),
        ("澜序 S7 碾压岑野 K5！", "澜序 S7 碾压岑野 K5"),
        ("澜序 S7 对比朔风 T6：谁更值得买", "澜序 S7 对比朔风 T6：谁更值得买"),
        ("岑野汽车不如澜序", "岑野汽车不如澜序"),
        ("澜序 S7 和岑野 S7 横评", "澜序 S7 和岑野 S7 横评"),
        ("Lanxu S7 vs CENYE K5", "Lanxu S7 vs CENYE K5"),
        ("LANXU is better than CENYE. Done", "LANXU is better than CENYE"),
        ("岑野 S7 的售价高于澜序 S7", "岑野 S7 的售价高于澜序 S7"),
        ("实测：岑野 K5 比澜序 X5 更费电", "实测：岑野 K5 比澜序 X5 更费电"),
    ],
)
def test_comparisons_with_competitors_need_evidence(raw: str, clause: str) -> None:
    findings = compare(raw)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "G-COMPETITOR-COMPARISON"
    assert finding.result == "review"
    assert finding.excerpt == clause
    assert "补充可核验的对比证据" in finding.suggestion
    assert finding.details["competitor_entity_ids"]


@pytest.mark.parametrize(
    "raw",
    [
        "岑野 K5 是一款紧凑型 SUV。",
        "澜序 S7 与澜序 X5 对比",  # 没有竞品
        "岑野 K5 售价低于 20 万元",  # 数量比较词，但只提到一个实体
        "澜序 S7 续航优于同级车型",  # “S7”整体归澜序 S7，不是岑野 S7
        "朔风凛冽，澜序 S7 比别的车更暖和",  # 排除词
        "比如岑野 K5 的车主",  # “比如”不是比较
        "",
    ],
)
def test_no_comparison_no_finding(raw: str) -> None:
    assert compare(raw) == []


def test_one_finding_per_clause_listing_all_competitors() -> None:
    findings = compare("岑野 K5 与朔风 T6 横向对比，澜序 S7 胜出")
    assert len(findings) == 1
    assert findings[0].details["competitor_entity_ids"] == [
        "product-cenye-k5",
        "product-shuofeng-t6",
    ]
    assert findings[0].details["competitor_mentions"] == ["岑野 K5", "朔风 T6"]


def test_clause_span_is_trimmed() -> None:
    raw = "开头。  澜序 S7 碾压岑野 K5  \n结尾"
    findings = compare(raw)
    assert findings[0].excerpt == "澜序 S7 碾压岑野 K5"


def test_drug_products_also_get_drug_comparison() -> None:
    pharma = (
        to_entity(entity("product-likang", "栎康清咽含片", "target")),
        to_entity(entity("product-lanze", "岚泽润喉片", "competitor")),
    )
    raw = "栎康清咽含片的效果比岚泽润喉片更快。"
    findings = run_check(CHECK, raw, entities=pharma, meta_value=meta(product_type="otc_drug"))
    assert sorted(f.rule_id for f in findings) == ["G-COMPETITOR-COMPARISON", "G-DRUG-COMPARISON"]
    drug = next(f for f in findings if f.rule_id == "G-DRUG-COMPARISON")
    assert drug.risk_level == "highest"
    assert drug.requires == ("legal_confirmation",)
    assert run_check(CHECK, raw, entities=pharma, meta_value=meta(product_type="car")) != []
    assert len(run_check(CHECK, raw, entities=pharma)) == 1


def test_skipped_without_store() -> None:
    data = evaluate("澜序 S7 碾压岑野 K5").to_dict()
    skipped = {item["check"]: item["reason"] for item in data["skipped_checks"]}
    assert "未提供 --store" in skipped["competitor_comparison"]


# ------------------------------------------------------------------ 读取 entities.jsonl


def test_load_entities_from_store(tmp_path: Path) -> None:
    store = write_store(tmp_path / "store", WORLD)
    loaded = load_entities(store)
    assert [e.entity_id for e in loaded] == [record["entity_id"] for record in WORLD]
    assert loaded[3].role == "competitor"
    assert loaded[0].names == ("澜序", "澜序汽车", "LANXU", "Lan-Xu")


def test_load_entities_errors(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="事实库目录不存在"):
        load_entities(tmp_path / "missing")
    (tmp_path / "empty").mkdir()
    with pytest.raises(InputError, match=r"缺少 entities\.jsonl"):
        load_entities(tmp_path / "empty")
    bad = dict(WORLD[0], role="partner")
    store = write_store(tmp_path / "bad", [WORLD[1], bad])
    with pytest.raises(InputError, match="第 2 行不符合 entity 契约") as info:
        load_entities(store)
    assert info.value.details["issues"][0]["path"] == "/role"
