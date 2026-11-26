"""事实库的目录约定与读取。

```text
STORE/
├── store.json        元数据：schema_version、store_id、project_id、timezone、created_at、synthetic?
├── entities.jsonl    entity 契约；同一 entity_id 以最后一条为准（只追加）
├── facts.jsonl       fact 契约；只追加，同一 fact_id 的多个 version 都保留
└── audit.jsonl       audit_event 契约；初始化、导入等变更记录，按哈希链串联
```
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from geo_facts import contracts
from geo_facts.errors import InputError
from geo_facts.jsonl import iter_jsonl, read_json
from geo_facts.timeutil import zone

STORE_SCHEMA_VERSION = "1.0"
META_FILE = "store.json"
ENTITIES_FILE = "entities.jsonl"
FACTS_FILE = "facts.jsonl"
AUDIT_FILE = "audit.jsonl"

# 与契约里的 id 规则一致：字母或数字开头，后接字母、数字、点、下划线、冒号、连字符，最长 128。
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def is_valid_id(text: str) -> bool:
    return _ID_PATTERN.match(text) is not None


@dataclass(frozen=True)
class StorePaths:
    root: Path

    @property
    def meta(self) -> Path:
        return self.root / META_FILE

    @property
    def entities(self) -> Path:
        return self.root / ENTITIES_FILE

    @property
    def facts(self) -> Path:
        return self.root / FACTS_FILE

    @property
    def audit(self) -> Path:
        return self.root / AUDIT_FILE


@dataclass(frozen=True)
class InvalidRecord:
    """不符合契约的一行：文件名、行号和问题。"""

    file: str
    line: int
    issues: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "line": self.line, "issues": list(self.issues)}


@dataclass
class FactStore:
    """读入内存的事实库。facts 保持文件顺序，包含全部版本。"""

    paths: StorePaths
    meta: dict[str, Any]
    entities: dict[str, dict[str, Any]]
    facts: list[dict[str, Any]]
    invalid: list[InvalidRecord] = field(default_factory=list)

    @property
    def store_id(self) -> str:
        return str(self.meta["store_id"])

    @property
    def project_id(self) -> str:
        return str(self.meta["project_id"])

    @property
    def timezone(self) -> str:
        return str(self.meta["timezone"])

    @property
    def synthetic(self) -> bool:
        return bool(self.meta.get("synthetic", False))

    def versions(self, fact_id: str) -> list[dict[str, Any]]:
        """某个 fact_id 的全部版本，按版本号升序。"""
        found = [fact for fact in self.facts if fact["fact_id"] == fact_id]
        return sorted(found, key=lambda fact: int(fact["version"]))

    def fact_ids(self) -> list[str]:
        """全部 fact_id，按首次出现的顺序。"""
        seen: dict[str, None] = {}
        for fact in self.facts:
            seen.setdefault(fact["fact_id"], None)
        return list(seen)

    def by_fact_id(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for fact in self.facts:
            grouped.setdefault(fact["fact_id"], []).append(fact)
        for versions in grouped.values():
            versions.sort(key=lambda fact: int(fact["version"]))
        return grouped


def load_meta(root: Path) -> dict[str, Any]:
    paths = StorePaths(root)
    if not root.is_dir():
        raise InputError(f"事实库目录不存在：{root}")
    if not paths.meta.exists():
        raise InputError(f"{root} 不是事实库目录：缺少 {META_FILE}（可用 geo-facts init 创建）")
    meta = read_json(paths.meta)
    if not isinstance(meta, dict):
        raise InputError(f"{paths.meta} 不是 JSON 对象")
    for key in ("store_id", "project_id"):
        value = meta.get(key)
        if not isinstance(value, str) or not is_valid_id(value):
            raise InputError(f"{paths.meta} 的 {key} 缺失或不合法")
    timezone = meta.get("timezone")
    if not isinstance(timezone, str):
        raise InputError(f"{paths.meta} 缺少 timezone")
    zone(timezone)
    return meta


def _is_day(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def record_issues(contract: str, record: dict[str, Any]) -> list[dict[str, str]]:
    """一条记录的问题：契约校验，加上契约的正则管不到的“日期必须真实存在”。"""
    issues = [issue.to_dict() for issue in contracts.iter_issues(contract, record)]
    if issues or contract != "fact":
        return issues
    for key in ("valid_from", "valid_until"):
        value = record[key]
        if value is not None and not _is_day(value):
            issues.append(
                {"path": f"/{key}", "keyword": "format", "message": f"{value!r} 不是真实存在的日期"}
            )
    return issues


def _load_records(
    path: Path, contract: str, *, strict: bool, invalid: list[InvalidRecord]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for number, record in iter_jsonl(path):
        issues = record_issues(contract, record)
        if issues:
            if strict:
                first = issues[0]
                raise InputError(
                    f"{path.name} 第 {number} 行不符合契约 {contract}"
                    f"（{first['path'] or '/'}：{first['message']}）",
                    details=issues,
                )
            invalid.append(InvalidRecord(file=path.name, line=number, issues=tuple(issues)))
            continue
        records.append(record)
    return records


def load_store(root: str | Path, *, strict: bool = True) -> FactStore:
    """读取事实库。strict=False 时不合契约的行不抛错，而是收集到 `invalid` 里。"""
    base = Path(root)
    meta = load_meta(base)
    paths = StorePaths(base)
    invalid: list[InvalidRecord] = []
    entity_records = _load_records(paths.entities, "entity", strict=strict, invalid=invalid)
    facts = _load_records(paths.facts, "fact", strict=strict, invalid=invalid)
    entities: dict[str, dict[str, Any]] = {}
    for entity in entity_records:
        # 只追加：同一 entity_id 的后一条记录取代前一条。
        entities.pop(entity["entity_id"], None)
        entities[entity["entity_id"]] = entity
    return FactStore(paths=paths, meta=meta, entities=entities, facts=facts, invalid=invalid)
