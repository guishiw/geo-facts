"""事实库目录的只读访问（不依赖 store 模块，直接按契约读文件）与实体名称识别。

- `store.json` 的 timezone：把 UTC 时间换算成自然日；没有该文件或该字段时用 Asia/Shanghai。
- `entities.jsonl`：entity 契约；逐条校验，不合契约 → 输入错误。

名称识别（供竞品比较检查使用）：
- 名称与别名按“紧凑键”匹配：忽略全半角、大小写、空格与连字符（“岑野 K5”“岑野k5”“岑野Ｋ－５”等价）；
- 名称以字母或数字开头 / 结尾时，原文中紧挨着的字符不能也是字母或数字（“K5”不匹配“K50”）；
- 所有实体（目标与竞品）一起做“最长优先、互不重叠”的选择：“澜序 S7”整体归目标车型，
  其中的“S7”不会再被当作同名的竞品车型；
- 别名落在该实体的 exclusions 短语之内时不算提及。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from geo_facts import contracts
from geo_facts.errors import InputError
from geo_facts.gate.document import TextIndex
from geo_facts.jsonl import iter_jsonl, read_json
from geo_facts.normalize.text import HYPHENS, compact
from geo_facts.timeutil import DEFAULT_TIMEZONE, zone

_GAP = "[" + re.escape("".join(sorted(HYPHENS))) + r"\s]*"


def require_store(store: Path) -> Path:
    if not store.is_dir():
        raise InputError(f"事实库目录不存在：{store.name}", details={"store": store.name})
    return store


def store_timezone(store: Path) -> str:
    path = require_store(store) / "store.json"
    if not path.is_file():
        return DEFAULT_TIMEZONE
    data = read_json(path)
    if not isinstance(data, dict):
        raise InputError("store.json 必须是 JSON 对象")
    name = data.get("timezone")
    if name is None:
        return DEFAULT_TIMEZONE
    if not isinstance(name, str):
        raise InputError("store.json 的 timezone 必须是字符串")
    zone(name)
    return name


@dataclass(frozen=True)
class Entity:
    entity_id: str
    kind: str
    role: str
    name: str
    aliases: tuple[str, ...]
    exclusions: tuple[str, ...]
    parent_entity_id: str | None

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


def load_entities(store: Path) -> tuple[Entity, ...]:
    path = require_store(store) / "entities.jsonl"
    if not path.is_file():
        raise InputError("事实库缺少 entities.jsonl", details={"store": store.name})
    entities: list[Entity] = []
    for number, record in iter_jsonl(path):
        issues = contracts.iter_issues("entity", record)
        if issues:
            raise InputError(
                f"entities.jsonl 第 {number} 行不符合 entity 契约",
                details={"line": number, "issues": [issue.to_dict() for issue in issues]},
            )
        entities.append(
            Entity(
                entity_id=record["entity_id"],
                kind=record["kind"],
                role=record["role"],
                name=record["name"],
                aliases=tuple(record["aliases"]),
                exclusions=tuple(record["exclusions"]),
                parent_entity_id=record["parent_entity_id"],
            )
        )
    return tuple(entities)


def name_pattern(name: str) -> re.Pattern[str] | None:
    """名称的匹配式：在折叠文本上匹配，字与字之间允许空白和连字符。"""
    key = compact(name)
    if not key:
        return None
    body = _GAP.join(re.escape(char) for char in key)
    left = r"(?<![a-z0-9])" if key[0].isascii() and key[0].isalnum() else ""
    right = r"(?![a-z0-9])" if key[-1].isascii() and key[-1].isalnum() else ""
    return re.compile(left + body + right)


@dataclass(frozen=True)
class Mention:
    """折叠文本上的一处实体提及。"""

    start: int
    end: int
    entity: Entity


def _spans(pattern: re.Pattern[str] | None, text: str) -> list[tuple[int, int]]:
    if pattern is None:
        return []
    return [match.span() for match in pattern.finditer(text) if match.end() > match.start()]


def find_mentions(index: TextIndex, entities: tuple[Entity, ...]) -> list[Mention]:
    text = index.text
    candidates: list[Mention] = []
    for entity in entities:
        excluded = [
            span for phrase in entity.exclusions for span in _spans(name_pattern(phrase), text)
        ]
        for alias in entity.names:
            for start, end in _spans(name_pattern(alias), text):
                if any(left <= start and end <= right for left, right in excluded):
                    continue
                candidates.append(Mention(start, end, entity))
    candidates.sort(key=lambda m: (-(m.end - m.start), m.start, m.entity.entity_id))
    chosen: list[Mention] = []
    for mention in candidates:
        if all(mention.end <= other.start or other.end <= mention.start for other in chosen):
            chosen.append(mention)
    return sorted(chosen, key=lambda m: (m.start, m.end))
