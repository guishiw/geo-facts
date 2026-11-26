"""事实库导出：JSONL（fact 契约原样）或 CSV（与导入模板同列，可再导入）。"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from typing import Any

from geo_facts.jsonl import dumps_line
from geo_facts.store.model import FactStore
from geo_facts.store.timeline import effective_version

# 导入模板与 CSV 导出共用的列（顺序即模板列顺序）。
FACT_COLUMNS: tuple[str, ...] = (
    "fact_id",
    "entity_id",
    "attribute",
    "value",
    "value_type",
    "unit",
    "conditions",
    "zone",
    "source_tier",
    "source_type",
    "content_use",
    "is_core",
    "evidence_id",
    "source_name",
    "source_ref",
    "collected_at",
    "last_verified_at",
    "reviewer",
    "valid_from",
    "valid_until",
    "status",
    "aliases",
    "note",
    "synthetic",
    "ext",
)
EXPORT_COLUMNS: tuple[str, ...] = (*FACT_COLUMNS[:1], "version", *FACT_COLUMNS[1:])

# 以这些字符开头的单元格会被表格软件当成公式：导出时前面加单引号。
FORMULA_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")
LIST_SEPARATOR = "; "
UTF8_BOM = chr(0xFEFF)

_PLAIN_NUMBER = re.compile(r"-?[0-9]+(\.[0-9]+)?")


def select_facts(store: FactStore, as_of: date | None) -> list[dict[str, Any]]:
    """不给日期：全部版本（按 fact_id、版本号排序）。给日期：每个 fact_id 当日有效的版本。"""
    grouped = store.by_fact_id()
    selected: list[dict[str, Any]] = []
    for fact_id in sorted(grouped):
        versions = grouped[fact_id]
        if as_of is None:
            selected.extend(versions)
            continue
        current = effective_version(versions, as_of)
        if current is not None:
            selected.append(current)
    return selected


def format_number(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return repr(value) if isinstance(value, float) else str(value)


def value_cells(value: Any) -> tuple[str, str]:
    """把事实的值写成（value 单元格, value_type 单元格）。"""
    if isinstance(value, bool):
        return ("true" if value else "false"), "boolean"
    if isinstance(value, int | float):
        return format_number(value), "number"
    if isinstance(value, dict):
        return f"{format_number(value['min'])}~{format_number(value['max'])}", "range"
    return str(value), "text"


def guard_cell(text: str) -> str:
    """防公式注入：以 = + - @ 制表符 回车 开头的单元格前面加单引号；纯数字（如 -5）除外。"""
    if text.startswith(FORMULA_PREFIXES) and _PLAIN_NUMBER.fullmatch(text) is None:
        return "'" + text
    return text


def unguard_cell(text: str) -> str:
    """`guard_cell` 的逆操作：导入时去掉为防公式注入而加的单引号。"""
    if len(text) > 1 and text[0] == "'" and text[1:].startswith(FORMULA_PREFIXES):
        return text[1:]
    return text


def fact_row(fact: dict[str, Any]) -> dict[str, str]:
    evidence = fact["evidence"] or {}
    value, value_type = value_cells(fact["value"])
    ext = dict(fact.get("ext") or {})
    source_type = ext.pop("source_type", "")
    row = {
        "fact_id": fact["fact_id"],
        "version": str(fact["version"]),
        "entity_id": fact["entity_id"],
        "attribute": fact["attribute"],
        "value": value,
        "value_type": value_type,
        "unit": fact["unit"] or "",
        "conditions": LIST_SEPARATOR.join(
            f"{key}={item}" for key, item in sorted(fact["conditions"].items())
        ),
        "zone": fact["zone"],
        "source_tier": fact["source_tier"] or "",
        "source_type": source_type if isinstance(source_type, str) else "",
        "content_use": fact["content_use"],
        "is_core": "true" if fact["is_core"] else "false",
        "evidence_id": evidence.get("evidence_id", ""),
        "source_name": evidence.get("source_name", ""),
        "source_ref": evidence.get("source_ref", ""),
        "collected_at": evidence.get("collected_at", ""),
        "last_verified_at": evidence.get("last_verified_at", ""),
        "reviewer": evidence.get("reviewer", ""),
        "valid_from": fact["valid_from"],
        "valid_until": fact["valid_until"] or "",
        "status": fact["status"],
        "aliases": LIST_SEPARATOR.join(fact.get("aliases", [])),
        "note": fact.get("note", ""),
        "synthetic": "true" if fact.get("synthetic") else "",
        "ext": json.dumps(ext, ensure_ascii=False, sort_keys=True) if ext else "",
    }
    return {key: guard_cell(text) for key, text in row.items()}


def to_csv(facts: list[dict[str, Any]]) -> str:
    """CSV 文本（不含 BOM；写文件时由调用方加 UTF-8 BOM）。"""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(EXPORT_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for fact in facts:
        writer.writerow(fact_row(fact))
    return buffer.getvalue()


def to_jsonl(facts: list[dict[str, Any]]) -> str:
    return "".join(dumps_line(fact) + "\n" for fact in facts)
