"""拆解用的词表：由事实库的实体（名称、别名、排除词）和事实（属性名、别名、条件、取值类型）生成。

实体名的写法：
- 名称与别名按 `compact` 折叠后匹配（全半角、大小写、空格、连字符不敏感）；
- 车型名以上级品牌名开头时，另生成“品牌别名 + 型号”（如 LANXU S7、澜序汽车 S7）；
  去掉品牌后的型号（如 S7）只有在不与其他实体冲突时才作为别名；
- 同一写法对应多个实体时，显式别名优先于生成的写法；仍有歧义的写法匹配到后不归属任何实体。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from geo_facts.claims.attributes import CONCEPT_PHRASES, DATE_CONCEPTS, attribute_concept
from geo_facts.normalize import (
    Compound,
    DateValue,
    Measure,
    TextValue,
    canonical_value,
    compact,
    fold,
    normalize_fact_value,
)
from geo_facts.store import FactStore

# 没有事实时各概念默认的量纲。
DEFAULT_DIMENSIONS: dict[str, frozenset[str | None]] = {
    "range": frozenset({"length"}),
    "price": frozenset({"currency"}),
    "acceleration": frozenset({"duration"}),
    "battery": frozenset({"energy"}),
    "warranty": frozenset({"calendar", "length"}),
    "fast_charge": frozenset({"duration"}),
}
# 只有紧跟数量时才算属性词的说法（单字动词）。
TIGHT_PHRASES: frozenset[str] = frozenset({"卖"})
# 同一概念下事实的取值类型不一致时取排位高的（日期 > 型号 > 数值 > 文本）。
_KIND_RANK: dict[str, int] = {"text": 0, "numeric": 1, "model": 2, "date": 3}
_MODEL_CODE = re.compile(r"^(?=.*[a-z])(?=.*\d)[a-z0-9]+(?:[-_. ][a-z0-9]+)*$")


def looks_like_model_code(text: str) -> bool:
    """型号写法：英文字母与数字组成（可含连字符），如 LX-M220、ＬＸ－Ｍ２２０。"""
    return _MODEL_CODE.match(fold(text).strip()) is not None


@dataclass
class Concept:
    """一个属性概念：各实体在事实库里用的属性名、取值类型与量纲。"""

    key: str
    kind: str  # numeric / date / model / text
    names: dict[str, str] = field(default_factory=dict)  # entity_id → 属性名
    default_name: str = ""
    dimensions: frozenset[str | None] = frozenset()

    def name_for(self, entity_id: str | None) -> str:
        if entity_id is not None and entity_id in self.names:
            return self.names[entity_id]
        return self.default_name


@dataclass
class Lexicon:
    entities: dict[str, dict[str, Any]]
    entity_keys: list[tuple[str, str | None]]  # (写法键, entity_id 或 None 表示有歧义)，按长度降序
    exclusions: dict[str, tuple[str, ...]]
    attribute_keys: list[tuple[str, str, bool]]  # (写法键, 概念, 是否只在紧跟数量时成立)
    concepts: dict[str, Concept]
    vocabulary: dict[str, frozenset[str]]
    condition_keys: frozenset[str]

    def is_product(self, entity_id: str | None) -> bool:
        return entity_id is not None and self.entities[entity_id]["kind"] == "product"

    def parent(self, entity_id: str) -> str | None:
        parent = self.entities[entity_id]["parent_entity_id"]
        return parent if isinstance(parent, str) else None


def _value_kind(fact: Mapping[str, Any]) -> tuple[str, frozenset[str | None]]:
    value = normalize_fact_value(fact["value"], fact["unit"])
    if isinstance(value, DateValue):
        return "date", frozenset()
    if isinstance(value, Measure):
        return "numeric", frozenset({value.dimension})
    if isinstance(value, Compound):
        return "numeric", frozenset(part.dimension for part in value.parts)
    if isinstance(value, TextValue) and looks_like_model_code(value.raw):
        return "model", frozenset()
    return "text", frozenset()


def _entity_keys(entities: Mapping[str, Mapping[str, Any]]) -> list[tuple[str, str | None]]:
    explicit: dict[str, set[str]] = {}
    derived: dict[str, set[str]] = {}

    def add(table: dict[str, set[str]], name: str, entity_id: str) -> None:
        key = compact(name)
        if key:
            table.setdefault(key, set()).add(entity_id)

    for entity_id, entity in entities.items():
        for name in (entity["name"], *entity["aliases"]):
            add(explicit, name, entity_id)
    for entity_id, entity in entities.items():
        parent_id = entity.get("parent_entity_id")
        if (
            entity["kind"] != "product"
            or not isinstance(parent_id, str)
            or parent_id not in entities
        ):
            continue
        parent = entities[parent_id]
        name_key = compact(entity["name"])
        brand_key = compact(parent["name"])
        if not name_key.startswith(brand_key) or len(name_key) == len(brand_key):
            continue
        model = name_key[len(brand_key) :]
        for brand_name in (parent["name"], *parent["aliases"]):
            add(derived, compact(brand_name) + model, entity_id)
        add(derived, model, entity_id)
    resolved: dict[str, str | None] = {}
    for key, owners in explicit.items():
        # 显式写法有歧义时仍然收录（匹配到后不归属任何实体），避免被更短的写法误认。
        resolved[key] = next(iter(owners)) if len(owners) == 1 else None
    for key, owners in derived.items():
        if key not in resolved and len(owners) == 1:
            resolved[key] = next(iter(owners))
    return sorted(resolved.items(), key=lambda item: (-len(item[0]), item[0]))


def build_lexicon(store: FactStore) -> Lexicon:
    """由事实库生成词表。"""
    entities = {entity_id: dict(entity) for entity_id, entity in store.entities.items()}
    concepts: dict[str, Concept] = {}
    name_counts: dict[str, Counter[str]] = {}
    per_entity: dict[tuple[str, str], Counter[str]] = {}
    phrase_keys: dict[str, str] = {}
    vocabulary: dict[str, set[str]] = {}
    condition_keys: set[str] = set()
    for fact in store.facts:
        aliases = list(fact.get("aliases", []))
        concept_key = attribute_concept(fact["attribute"], aliases)
        kind, dimensions = _value_kind(fact)
        concept = concepts.setdefault(concept_key, Concept(key=concept_key, kind=kind))
        if _KIND_RANK[kind] > _KIND_RANK[concept.kind]:
            concept.kind = kind
        concept.dimensions = concept.dimensions | dimensions
        name_counts.setdefault(concept_key, Counter())[fact["attribute"]] += 1
        per_entity.setdefault((concept_key, fact["entity_id"]), Counter())[fact["attribute"]] += 1
        for name in (fact["attribute"], *aliases):
            phrase_keys.setdefault(compact(name), concept_key)
        for key, value in fact["conditions"].items():
            normalized = key.strip().lower()
            condition_keys.add(normalized)
            vocabulary.setdefault(normalized, set()).add(canonical_value(normalized, value))
    for concept_key, phrases in CONCEPT_PHRASES.items():
        if concept_key not in concepts:
            concepts[concept_key] = Concept(
                key=concept_key,
                kind="date" if concept_key in DATE_CONCEPTS else "numeric",
                default_name=phrases[0],
                dimensions=DEFAULT_DIMENSIONS.get(concept_key, frozenset()),
            )
        for phrase in phrases:
            phrase_keys.setdefault(compact(phrase), concept_key)
    for concept_key, counts in name_counts.items():
        concepts[concept_key].default_name = sorted(
            counts.items(), key=lambda item: (-item[1], item[0])
        )[0][0]
    for (concept_key, entity_id), counts in per_entity.items():
        concepts[concept_key].names[entity_id] = sorted(
            counts.items(), key=lambda item: (-item[1], item[0])
        )[0][0]
    # 型号类属性：“电机型号”也可以只说“电机”。
    for concept in concepts.values():
        if concept.kind != "model":
            continue
        for name in list(name_counts.get(concept.key, Counter())):
            key = compact(name)
            if key.endswith("型号") and len(key) >= 4:
                phrase_keys.setdefault(key[: -len("型号")], concept.key)
    attribute_keys = sorted(
        (
            (key, concept_key, key in TIGHT_PHRASES)
            for key, concept_key in phrase_keys.items()
            if key
        ),
        key=lambda item: (-len(item[0]), item[0]),
    )
    exclusions = {
        entity_id: tuple(compact(item) for item in entity.get("exclusions", []) if compact(item))
        for entity_id, entity in entities.items()
    }
    return Lexicon(
        entities=entities,
        entity_keys=_entity_keys(entities),
        exclusions=exclusions,
        attribute_keys=attribute_keys,
        concepts=concepts,
        vocabulary={key: frozenset(values) for key, values in vocabulary.items()},
        condition_keys=frozenset(condition_keys),
    )
