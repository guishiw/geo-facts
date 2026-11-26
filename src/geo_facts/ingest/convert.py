"""把一行导入数据转换成 fact 契约记录，并逐列报告问题。

只做格式上的转换与校验，不分配版本号（版本号由 importer 按“只追加”的规则分配）。
来源等级（source_tier）与内容用途（content_use）分别读取，互不推导；来源等级留空时只有一种
默认：来源链接属于该实体（或其上级实体）的自有域名 → C 级（026），并标记为默认值。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

from geo_facts.errors import InputError
from geo_facts.ingest.columns import EVIDENCE_COLUMNS, VERSION_COLUMN
from geo_facts.ingest.readers import RawRow
from geo_facts.store.export import format_number
from geo_facts.store.model import STORE_SCHEMA_VERSION, record_issues
from geo_facts.store.timeline import parse_day
from geo_facts.timeutil import parse_utc

FACT_SCHEMA_VERSION = STORE_SCHEMA_VERSION
DEFAULT_OWNED_TIER = "C"
TIER_DEFAULTED_KEY = "source_tier_defaulted"
SOURCE_TYPE_KEY = "source_type"

_TRUE = frozenset({"true", "yes", "y", "1", "是"})
_FALSE = frozenset({"false", "no", "n", "0", "否"})
ZONES: dict[str, str] = {
    "fact": "fact",
    "事实": "fact",
    "opinion": "opinion",
    "观点": "opinion",
    "marketing": "marketing",
    "营销": "marketing",
    "营销表达": "marketing",
}
STATUSES: dict[str, str] = {
    "active": "active",
    "有效": "active",
    "retracted": "retracted",
    "撤回": "retracted",
    "已撤回": "retracted",
    "disputed": "disputed",
    "争议": "disputed",
    "有争议": "disputed",
}
CONTENT_USES: dict[str, str] = {
    "specification": "specification",
    "规格参数": "specification",
    "pricing": "pricing",
    "价格": "pricing",
    "policy": "policy",
    "政策": "policy",
    "test_result": "test_result",
    "测试结果": "test_result",
    "review": "review",
    "评测": "review",
    "promotion": "promotion",
    "宣传": "promotion",
    "other": "other",
    "其他": "other",
}
VALUE_TYPES: dict[str, str] = {
    "number": "number",
    "数字": "number",
    "range": "range",
    "范围": "range",
    "boolean": "boolean",
    "布尔": "boolean",
    "text": "text",
    "文本": "text",
}
TIERS: frozenset[str] = frozenset({"A", "B", "C", "D"})

_PLAIN_NUMBER = re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
_RANGE = re.compile(r"(\S+?)\s*[~～]\s*(\S+)")
_LIST_SPLIT = re.compile(r"[;；\n]")
_PAIR_SPLIT = re.compile(r"\s*[=＝]\s*")


class CellError(Exception):
    """某一列的值不合法。"""

    def __init__(self, column: str, message: str) -> None:
        super().__init__(message)
        self.column = column
        self.message = message


@dataclass
class Context:
    """转换一行时需要的事实库信息。entities 含同一份文件里先导入的实体。"""

    project_id: str
    entities: Mapping[str, Mapping[str, Any]]
    reviewer: str | None = None
    synthetic: bool = False


@dataclass
class Converted:
    """一行的转换结果：fact 为 None 表示该行非法。"""

    row: int
    fact: dict[str, Any] | None
    version: int | None
    errors: list[dict[str, Any]] = field(default_factory=list)
    notices: list[dict[str, Any]] = field(default_factory=list)


# ------------------------------------------------------------------ 单元格


def cell_text(value: Any) -> str | None:
    """单元格的文字；空白为 None。数字按最短写法，日期按 ISO 写法。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return format_number(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _required(cells: Mapping[str, Any], column: str) -> str:
    text = cell_text(cells.get(column))
    if text is None:
        raise CellError(column, "必填，不能为空")
    return text


def _choice(
    cells: Mapping[str, Any], column: str, table: Mapping[str, str], default: str | None
) -> str:
    text = cell_text(cells.get(column))
    if text is None:
        if default is None:
            raise CellError(column, "必填，不能为空")
        return default
    found = table.get(text.lower())
    if found is None:
        allowed = " / ".join(sorted(set(table.values())))
        raise CellError(column, f"不认识的取值 {text!r}；可选：{allowed}")
    return found


def parse_bool(column: str, value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = cell_text(value)
    if text is None:
        return default
    lowered = text.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise CellError(column, f"应为 true 或 false：{text!r}")


def parse_plain_number(text: str) -> int | float | None:
    """纯数字（可带千分位与负号）；其他写法返回 None。"""
    if _PLAIN_NUMBER.fullmatch(text) is None:
        return None
    clean = text.replace(",", "")
    return float(clean) if "." in clean else int(clean)


def _range_value(value: Any) -> dict[str, int | float]:
    if isinstance(value, Mapping):
        low, high = value.get("min"), value.get("max")
        numbers = [
            item
            for item in (low, high)
            if isinstance(item, int | float) and not isinstance(item, bool)
        ]
        if set(value) != {"min", "max"} or len(numbers) != 2:
            raise CellError("value", "范围必须是 {min: 数字, max: 数字}")
        low_number, high_number = numbers
    else:
        text = cell_text(value) or ""
        match = _RANGE.fullmatch(text)
        parsed = (
            (parse_plain_number(match.group(1)), parse_plain_number(match.group(2)))
            if match
            else (None, None)
        )
        if parsed[0] is None or parsed[1] is None:
            raise CellError("value", f"范围应写成 下限~上限，如 600~650：{text!r}")
        low_number, high_number = parsed[0], parsed[1]
    if low_number > high_number:
        raise CellError("value", f"范围的下限 {low_number} 大于上限 {high_number}")
    return {"min": low_number, "max": high_number}


def parse_value(raw: Any, value_type: str | None) -> Any:
    """按 value_type 解析值；value_type 为 None 时自动识别。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise CellError("value", "必填，不能为空")
    if isinstance(raw, list):
        raise CellError("value", "值不能是数组")
    if value_type == "range" or (value_type is None and isinstance(raw, Mapping)):
        return _range_value(raw)
    if isinstance(raw, Mapping):
        raise CellError("value", f"value_type 为 {value_type} 时值不能是对象")
    if value_type == "text":
        return cell_text(raw)
    if value_type == "boolean" or (value_type is None and isinstance(raw, bool)):
        return parse_bool("value", raw, default=False)
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return raw
    text = cell_text(raw)
    assert text is not None
    if value_type == "number":
        number = parse_plain_number(text)
        if number is None:
            raise CellError("value", f"value_type 为 number，但值不是数字：{text!r}")
        return number
    number = parse_plain_number(text)
    if number is not None:
        return number
    if _RANGE.fullmatch(text):
        try:
            return _range_value(text)
        except CellError:
            return text
    return text


def parse_conditions(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        pairs = [(str(key), cell_text(item)) for key, item in raw.items()]
    else:
        text = cell_text(raw)
        if text is None:
            return {}
        if text.startswith("{"):
            try:
                loaded = json.loads(text)
            except json.JSONDecodeError as exc:
                raise CellError("conditions", f"不是合法 JSON：{exc.msg}") from exc
            assert isinstance(loaded, dict)  # 以“{”开头的合法 JSON 只能是对象
            return parse_conditions(loaded)
        pairs = []
        for part in _LIST_SPLIT.split(text):
            if not part.strip():
                continue
            pieces = _PAIR_SPLIT.split(part.strip(), maxsplit=1)
            if len(pieces) != 2:
                raise CellError("conditions", f"应写成 键=值：{part.strip()!r}")
            pairs.append((pieces[0], cell_text(pieces[1])))
    result: dict[str, str] = {}
    for key, value in pairs:
        name = key.strip()
        if not name or value is None:
            raise CellError("conditions", f"条件的键和值都不能为空：{key!r}")
        if name in result:
            raise CellError("conditions", f"条件 {name} 重复")
        result[name] = value
    return result


def parse_list(column: str, raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        items = [cell_text(item) for item in raw]
    else:
        text = cell_text(raw)
        items = [] if text is None else [part.strip() for part in _LIST_SPLIT.split(text)]
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result


def parse_date_cell(column: str, raw: Any, *, required: bool) -> str | None:
    if isinstance(raw, datetime):
        if raw.time() != datetime.min.time():
            raise CellError(column, f"应为日期 YYYY-MM-DD，不带时刻：{raw.isoformat()}")
        return raw.date().isoformat()
    if isinstance(raw, date):
        return raw.isoformat()
    text = cell_text(raw)
    if text is None:
        if required:
            raise CellError(column, "必填，不能为空")
        return None
    try:
        return parse_day(text, what="").isoformat()
    except InputError as exc:
        raise CellError(column, f"应为真实存在的日期 YYYY-MM-DD：{text!r}") from exc


def parse_time_cell(column: str, raw: Any) -> str:
    if isinstance(raw, datetime | date):
        raise CellError(
            column, "表格里的日期时间没有时区，请写成 UTC 文本，如 2026-09-20T02:00:00Z"
        )
    text = cell_text(raw)
    assert text is not None
    try:
        parse_utc(text)
    except InputError as exc:
        raise CellError(column, f"应为 UTC 时间，如 2026-09-20T02:00:00Z：{text!r}") from exc
    return text


def parse_ext(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return dict(raw)
    text = cell_text(raw)
    if text is None:
        return {}
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CellError("ext", f"不是合法 JSON：{exc.msg}") from exc
    if not isinstance(loaded, dict):
        raise CellError("ext", "必须是 JSON 对象")
    return loaded


def parse_version(raw: Any) -> int | None:
    text = cell_text(raw)
    if text is None:
        return None
    if isinstance(raw, int) and not isinstance(raw, bool):
        number: int | None = raw
    else:
        number = int(text) if text.isdigit() else None
    if number is None or number < 1:
        raise CellError(VERSION_COLUMN, f"版本号应为正整数：{text!r}")
    return number


# ------------------------------------------------------------------ 来源等级与自有域名


def owned_domains(entity_id: str, entities: Mapping[str, Mapping[str, Any]]) -> set[str]:
    """实体及其上级实体（如车型所属的品牌）的自有域名。"""
    domains: set[str] = set()
    seen: set[str] = set()
    current: str | None = entity_id
    while current is not None and current not in seen and current in entities:
        seen.add(current)
        entity = entities[current]
        domains.update(str(domain).lower() for domain in entity.get("owned_domains", []))
        parent = entity.get("parent_entity_id")
        current = parent if isinstance(parent, str) else None
    return domains


def source_host(source_ref: str) -> str | None:
    parts = urlsplit(source_ref.strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return parts.hostname.lower()


def owned_source(source_ref: str | None, domains: set[str]) -> str | None:
    """来源链接若属于自有域名（含子域名），返回该域名。"""
    if source_ref is None:
        return None
    host = source_host(source_ref)
    if host is None:
        return None
    for domain in sorted(domains):
        if host == domain or host.endswith("." + domain):
            return domain
    return None


# ------------------------------------------------------------------ 一行 → fact 记录


def _evidence(
    cells: Mapping[str, Any], ctx: Context, errors: list[CellError]
) -> dict[str, Any] | None:
    nested = cells.get("evidence")
    flat_given = any(cell_text(cells.get(column)) is not None for column in EVIDENCE_COLUMNS)
    if nested is not None:
        if flat_given:
            errors.append(CellError("evidence", "evidence 对象与证据列不能同时填写"))
            return None
        if not isinstance(nested, Mapping):
            errors.append(CellError("evidence", "evidence 必须是对象或 null"))
            return None
        source: Mapping[str, Any] = nested
    elif not flat_given:
        return None
    else:
        source = cells
    evidence: dict[str, Any] = {}
    for column in EVIDENCE_COLUMNS:
        raw = source.get(column)
        try:
            if column == "reviewer" and cell_text(raw) is None and ctx.reviewer:
                evidence[column] = ctx.reviewer
            elif cell_text(raw) is None:
                raise CellError(
                    column, "填写了证据，这一项也必须填写（证据的六项要么全填，要么全空）"
                )
            elif column in ("collected_at", "last_verified_at"):
                evidence[column] = parse_time_cell(column, raw)
            else:
                evidence[column] = cell_text(raw)
        except CellError as exc:
            errors.append(exc)
    if len(evidence) != len(EVIDENCE_COLUMNS):
        return None
    if evidence["last_verified_at"] < evidence["collected_at"]:
        errors.append(CellError("last_verified_at", "最近核验时间早于采集时间"))
        return None
    return evidence


def _issue_column(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if not parts:
        return ""
    if parts[0] == "evidence" and len(parts) > 1:
        return parts[1]
    return parts[0]


def convert_fact_row(raw: RawRow, ctx: Context) -> Converted:
    """把一行转换成 fact 记录（version 暂填 1，由导入流程改写）。"""
    cells = raw.cells
    result = Converted(row=raw.row, fact=None, version=None)
    fact_id = cell_text(cells.get("fact_id"))
    errors: list[CellError] = [CellError(column, message) for column, message in raw.problems]
    if errors and not cells:
        # 整行无法读取（不是对象、不是合法 JSON）：只报这一条，不再逐列罗列“必填”。
        result.errors = [_error(raw.row, None, exc) for exc in errors]
        return result

    def attempt(func: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except CellError as exc:
            errors.append(exc)
            return None

    fact_id_value = attempt(_required, cells, "fact_id")
    entity_id = attempt(_required, cells, "entity_id")
    if entity_id is not None and entity_id not in ctx.entities:
        errors.append(CellError("entity_id", f"实体 {entity_id} 不在事实库的 entities.jsonl 中"))
    attribute = attempt(_required, cells, "attribute")
    value_type = attempt(_choice, cells, "value_type", VALUE_TYPES, "") or None
    value = attempt(parse_value, cells.get("value"), value_type)
    unit = cell_text(cells.get("unit"))
    conditions = attempt(parse_conditions, cells.get("conditions"))
    zone = attempt(_choice, cells, "zone", ZONES, None)
    content_use = attempt(_choice, cells, "content_use", CONTENT_USES, None)
    status = attempt(_choice, cells, "status", STATUSES, "active")
    is_core = attempt(parse_bool, "is_core", cells.get("is_core"), default=False)
    synthetic = attempt(parse_bool, "synthetic", cells.get("synthetic"), default=False)
    valid_from = attempt(parse_date_cell, "valid_from", cells.get("valid_from"), required=True)
    valid_until = attempt(parse_date_cell, "valid_until", cells.get("valid_until"), required=False)
    if valid_from and valid_until and valid_until < valid_from:
        errors.append(CellError("valid_until", "失效日期早于生效日期"))
    aliases = attempt(parse_list, "aliases", cells.get("aliases"))
    ext = attempt(parse_ext, cells.get("ext"))
    result.version = attempt(parse_version, cells.get(VERSION_COLUMN))
    before_evidence = len(errors)
    evidence = _evidence(cells, ctx, errors)
    evidence_failed = len(errors) > before_evidence
    tier_text = cell_text(cells.get("source_tier"))
    tier: str | None = None
    if tier_text is not None:
        tier = tier_text.upper().removesuffix("级")
        if tier not in TIERS:
            errors.append(CellError("source_tier", f"来源等级应为 A～D：{tier_text!r}"))
    project = cells.get("project_id")
    if project is not None and project != ctx.project_id:
        errors.append(
            CellError("project_id", f"project_id {project!r} 与事实库的 {ctx.project_id} 不一致")
        )
    schema = cells.get("schema_version")
    if schema is not None and (
        not isinstance(schema, str) or re.fullmatch(r"1\.[0-9]+", schema) is None
    ):
        errors.append(CellError("schema_version", f"不支持的 schema_version：{schema!r}"))
    source_type = cell_text(cells.get(SOURCE_TYPE_KEY))
    notices: list[dict[str, Any]] = []
    # 证据本身有问题、实体未知时无法判断来源是否属于自有域名，先不下结论，只报那些问题。
    if tier_text is None and entity_id in ctx.entities and not evidence_failed:
        domain = owned_source(
            None if evidence is None else evidence["source_ref"],
            owned_domains(entity_id, ctx.entities),
        )
        if domain is not None:
            tier = DEFAULT_OWNED_TIER
            ext = {**(ext or {}), TIER_DEFAULTED_KEY: True}
            notices.append(
                {
                    "row": raw.row,
                    "fact_id": fact_id,
                    "code": "source_tier_defaulted",
                    "message": f"来源 {domain} 是企业自有域名，来源等级按 026 默认为 C 级"
                    "（这是默认值，请人工确认）",
                }
            )
        elif zone == "fact":
            errors.append(
                CellError(
                    "source_tier",
                    "事实区条目必须给出来源等级 A～D（只有来源属于企业自有域名时才默认 C 级）",
                )
            )
    if errors:
        result.errors = [_error(raw.row, fact_id, exc) for exc in errors]
        return result
    record: dict[str, Any] = {
        "schema_version": FACT_SCHEMA_VERSION,
        "fact_id": fact_id_value,
        "project_id": ctx.project_id,
        "entity_id": entity_id,
        "attribute": attribute,
        "value": value,
        "unit": unit,
        "conditions": conditions,
        "zone": zone,
        "source_tier": tier,
        "content_use": content_use,
        "is_core": is_core,
        "evidence": evidence,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "status": status,
        "version": result.version or 1,
    }
    extra = dict(ext or {})
    if source_type is not None:
        extra[SOURCE_TYPE_KEY] = source_type
    if aliases:
        record["aliases"] = aliases
    note = cell_text(cells.get("note"))
    if note is not None:
        record["note"] = note
    if synthetic or ctx.synthetic:
        record["synthetic"] = True
    if extra:
        record["ext"] = extra
    issues = record_issues("fact", record)
    if issues:
        result.errors = [
            _error(raw.row, fact_id, CellError(_issue_column(issue["path"]), issue["message"]))
            for issue in issues
        ]
        return result
    result.fact = record
    result.notices = notices
    return result


def _error(row: int, fact_id: str | None, exc: CellError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "row": row,
        "kind": "fact",
        "column": exc.column,
        "message": exc.message,
    }
    if fact_id is not None:
        payload["fact_id"] = fact_id
    return payload


def convert_entity_row(
    raw: RawRow, ctx: Context
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """实体记录（entity 契约）：缺 project_id 时补上事实库的；合成事实库里一律标 synthetic。"""
    record = dict(raw.cells)
    entity_id = record.get("entity_id")
    errors: list[dict[str, Any]] = []

    def add(column: str, message: str) -> None:
        payload: dict[str, Any] = {
            "row": raw.row,
            "kind": "entity",
            "column": column,
            "message": message,
        }
        if isinstance(entity_id, str):
            payload["entity_id"] = entity_id
        errors.append(payload)

    for column, message in raw.problems:
        add(column, message)
    if errors and not record:
        return None, errors
    record.setdefault("schema_version", FACT_SCHEMA_VERSION)
    project = record.setdefault("project_id", ctx.project_id)
    if project != ctx.project_id:
        add("project_id", f"project_id {project!r} 与事实库的 {ctx.project_id} 不一致")
    if ctx.synthetic:
        record["synthetic"] = True
    for issue in record_issues("entity", record):
        add(_issue_column(issue["path"]), issue["message"])
    if errors:
        return None, errors
    return record, []
