"""词库：内置通用示例词库（geo_facts/lexicons/generic.json）与外部行业词库（--lexicon-dir）。

格式（与 geo-private 行业词库之间的接口）：

    {"schema_version": "1.x", "lexicon_id": "...", "industry": "...", "description": "...",
     "entries": [{"term": "...", "match": "literal" | "regex", "category": "...",
                  "risk_level": "low" | "medium" | "high" | "highest",
                  "clause": "046 §8.3.5", "note": "...", "requires": ["legal_confirmation"]}]}

- 顶层必填：schema_version（1.x）、lexicon_id、industry、entries；可选：description、synthetic、ext。
- 词条必填：term、match、category、risk_level；可选：clause、note、requires、id。
- 未知字段、取值不合法、正则无法编译、lexicon_id 重复 → 输入错误（退出 2）。
- 匹配在折叠文本（NFKC + 小写）上进行：literal 把 term 同样折叠后查找；regex 以忽略大小写方式匹配。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from geo_facts.canon import sha256_bytes
from geo_facts.errors import InputError
from geo_facts.jsonl import read_json
from geo_facts.normalize.text import fold

CATEGORIES = (
    "superlative",
    "exaggerated_promise",
    "industry_sensitive",
    "drug_comparison",
    "prescription_promotion",
)
CATEGORY_LABELS = {
    "superlative": "极限词",
    "exaggerated_promise": "夸大承诺",
    "industry_sensitive": "行业敏感词",
    "drug_comparison": "药品功效比较",
    "prescription_promotion": "处方药推广",
}
RISK_LEVELS = ("low", "medium", "high", "highest")
MATCH_KINDS = ("literal", "regex")
MAX_TERM_LENGTH = 200

_TOP_REQUIRED = ("schema_version", "lexicon_id", "industry", "entries")
_TOP_ALLOWED = frozenset({*_TOP_REQUIRED, "description", "synthetic", "ext"})
_ENTRY_REQUIRED = ("term", "match", "category", "risk_level")
_ENTRY_ALLOWED = frozenset({*_ENTRY_REQUIRED, "clause", "note", "requires", "id"})
_VERSION = re.compile(r"^1\.\d+$")

BUILTIN_FILE = "generic.json"


@dataclass(frozen=True)
class Entry:
    term: str
    match: str
    category: str
    risk_level: str
    clause: str | None
    note: str | None
    requires: tuple[str, ...]
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Lexicon:
    lexicon_id: str
    industry: str
    description: str
    entries: tuple[Entry, ...]
    source: str  # builtin | external
    file: str
    sha256: str

    def summary(self) -> dict[str, Any]:
        return {
            "lexicon_id": self.lexicon_id,
            "industry": self.industry,
            "entries": len(self.entries),
            "source": self.source,
            "file": self.file,
            "sha256": self.sha256,
        }


def _fail(file: str, message: str, **details: Any) -> InputError:
    return InputError(f"词库 {file} 格式错误：{message}", details={"file": file, **details})


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _entry(file: str, position: int, value: Any) -> Entry:
    where = f"第 {position} 个词条"
    if not isinstance(value, dict):
        raise _fail(file, f"{where}不是 JSON 对象", entry=position)
    unknown = sorted(set(value) - _ENTRY_ALLOWED)
    if unknown:
        raise _fail(file, f"{where}含未知字段 {unknown}", entry=position)
    for name in _ENTRY_REQUIRED:
        if not _text(value.get(name)):
            raise _fail(file, f"{where}缺少 {name} 或不是非空字符串", entry=position, field=name)
    term: str = value["term"]
    if len(term) > MAX_TERM_LENGTH:
        raise _fail(file, f"{where}的 term 超过 {MAX_TERM_LENGTH} 个字符", entry=position)
    checks = (("match", MATCH_KINDS), ("category", CATEGORIES), ("risk_level", RISK_LEVELS))
    for name, allowed in checks:
        if value[name] not in allowed:
            raise _fail(
                file, f"{where}的 {name} 取值不合法：{value[name]!r}", entry=position, field=name
            )
    for name in ("clause", "note"):
        if name in value and not isinstance(value[name], str):
            raise _fail(file, f"{where}的 {name} 必须是字符串", entry=position, field=name)
    requires = value.get("requires", [])
    if not isinstance(requires, list) or not all(_text(item) for item in requires):
        raise _fail(file, f"{where}的 requires 必须是非空字符串数组", entry=position)
    if value["match"] == "literal":
        pattern = re.compile(re.escape(fold(term)))
    else:
        try:
            pattern = re.compile(term, re.IGNORECASE)
        except re.error as exc:
            raise _fail(file, f"{where}的正则无法编译：{exc}", entry=position) from exc
    return Entry(
        term=term,
        match=value["match"],
        category=value["category"],
        risk_level=value["risk_level"],
        clause=value.get("clause"),
        note=value.get("note"),
        requires=tuple(requires),
        pattern=pattern,
    )


def parse_lexicon(value: Any, *, file: str, source: str, digest: str) -> Lexicon:
    if not isinstance(value, dict):
        raise _fail(file, "顶层必须是 JSON 对象")
    unknown = sorted(set(value) - _TOP_ALLOWED)
    if unknown:
        raise _fail(file, f"含未知字段 {unknown}")
    for name in _TOP_REQUIRED:
        if name not in value:
            raise _fail(file, f"缺少 {name}", field=name)
    if not isinstance(value["schema_version"], str) or not _VERSION.match(value["schema_version"]):
        raise _fail(file, f"schema_version 必须是 1.x：{value['schema_version']!r}")
    for name in ("lexicon_id", "industry"):
        if not _text(value[name]):
            raise _fail(file, f"{name} 必须是非空字符串", field=name)
    description = value.get("description", "")
    if not isinstance(description, str):
        raise _fail(file, "description 必须是字符串")
    entries = value["entries"]
    if not isinstance(entries, list):
        raise _fail(file, "entries 必须是数组")
    return Lexicon(
        lexicon_id=value["lexicon_id"],
        industry=value["industry"],
        description=description,
        entries=tuple(_entry(file, i, item) for i, item in enumerate(entries, start=1)),
        source=source,
        file=file,
        sha256=digest,
    )


def load_builtin() -> Lexicon:
    data = resources.files("geo_facts").joinpath("lexicons", BUILTIN_FILE).read_bytes()
    return parse_lexicon(
        json.loads(data.decode("utf-8")),
        file=BUILTIN_FILE,
        source="builtin",
        digest=sha256_bytes(data),
    )


def load_file(path: Path) -> Lexicon:
    value = read_json(path)
    return parse_lexicon(
        value, file=path.name, source="external", digest=sha256_bytes(path.read_bytes())
    )


def load_lexicons(directories: Sequence[Path]) -> tuple[Lexicon, ...]:
    """内置词库在前；每个外部目录按文件名顺序加载其中所有 *.json。"""
    loaded = [load_builtin()]
    for directory in directories:
        if not directory.is_dir():
            raise InputError(f"词库目录不存在：{directory.name}", details={"dir": directory.name})
        for path in sorted(directory.glob("*.json")):
            loaded.append(load_file(path))
    seen: dict[str, str] = {}
    for lexicon in loaded:
        if lexicon.lexicon_id in seen:
            raise InputError(
                f"词库 lexicon_id 重复：{lexicon.lexicon_id}（{seen[lexicon.lexicon_id]} 与 {lexicon.file}）",
                details={"lexicon_id": lexicon.lexicon_id},
            )
        seen[lexicon.lexicon_id] = lexicon.file
    return tuple(loaded)
