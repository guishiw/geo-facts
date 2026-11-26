"""把四种格式的导入文件读成统一的“行”：xlsx（openpyxl）、csv、json、md（Markdown 表格）。

行号的含义（逐行报错时使用）：
- csv、md、.jsonl：文件里的行号（从 1 开始；csv 的表头是第 1 行）；
- xlsx：工作表的行号（表头是第 1 行）；读取名为 facts 的工作表，没有则读第一张；
- json 数组或 {"facts": [...], "entities": [...]}：该记录在数组里是第几条（从 1 开始）。

整份文件层面的问题（文件不存在、编码不对、缺少必填列、出现未知列、没有表格）按输入错误
（退出码 2）拒绝；单行的问题记在该行上，由导入流程逐行报告。
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from geo_facts.errors import InputError
from geo_facts.ingest.columns import KNOWN_COLUMNS, REQUIRED_HEADERS, normalize_header
from geo_facts.store.export import unguard_cell

FORMATS: tuple[str, ...] = ("xlsx", "csv", "json", "md")
_SUFFIXES: dict[str, str] = {
    ".xlsx": "xlsx",
    ".csv": "csv",
    ".json": "json",
    ".jsonl": "json",
    ".md": "md",
    ".markdown": "md",
}
XLSX_SHEET = "facts"
# JSON 形式的事实行除模板列外，还接受 fact 契约记录本身的这些字段（导出的 JSONL 可直接再导入）。
JSON_FACT_EXTRA_KEYS: frozenset[str] = frozenset({"schema_version", "project_id", "evidence"})

_MD_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")


@dataclass(frozen=True)
class RawRow:
    """读出来的一行。kind 为 fact（事实行）或 entity（实体记录，只有 json 格式支持）。"""

    row: int
    cells: dict[str, Any]
    kind: str = "fact"
    problems: tuple[tuple[str, str], ...] = field(default=())


@dataclass(frozen=True)
class RawSource:
    format: str
    rows: tuple[RawRow, ...]


def detect_format(path: Path, given: str | None) -> str:
    if given is not None:
        if given not in FORMATS:
            raise InputError(f"未知格式：{given}；可选：{', '.join(FORMATS)}")
        return given
    found = _SUFFIXES.get(path.suffix.lower())
    if found is None:
        raise InputError(
            f"无法从扩展名判断格式：{path.name}；请用 --format 指定（{', '.join(FORMATS)}）"
        )
    return found


def read_source(path: Path, fmt: str | None = None) -> RawSource:
    resolved = detect_format(path, fmt)
    if not path.is_file():
        raise InputError(f"输入文件不存在：{path}")
    if resolved == "xlsx":
        rows = _read_xlsx(path)
    elif resolved == "csv":
        rows = _read_csv(path)
    elif resolved == "md":
        rows = _read_markdown(path)
    else:
        rows = _read_json(path)
    return RawSource(format=resolved, rows=tuple(rows))


# ------------------------------------------------------------------ 表格类：表头与行


def _header(cells: list[Any], where: str) -> list[str]:
    names = [normalize_header(str(cell)) if cell is not None else "" for cell in cells]
    while names and names[-1] == "":
        names.pop()
    if not names:
        raise InputError(f"{where}：没有表头")
    if "" in names:
        raise InputError(f"{where}：表头第 {names.index('') + 1} 列为空")
    unknown = sorted({name for name in names if name not in KNOWN_COLUMNS})
    if unknown:
        raise InputError(
            f"{where}：未知列 {', '.join(unknown)}（列名见 geo-facts import --template csv）"
        )
    duplicated = sorted({name for name in names if names.count(name) > 1})
    if duplicated:
        raise InputError(f"{where}：列名重复 {', '.join(duplicated)}")
    missing = [name for name in REQUIRED_HEADERS if name not in names]
    if missing:
        raise InputError(f"{where}：缺少必填列 {', '.join(missing)}")
    return names


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _table_row(number: int, header: list[str], values: list[Any]) -> RawRow | None:
    """表格的一行；整行为空时返回 None（跳过，不计数）。"""
    if all(_blank(value) for value in values):
        return None
    problems: list[tuple[str, str]] = []
    extra = values[len(header) :]
    if any(not _blank(value) for value in extra):
        problems.append(("", f"单元格比表头多 {len(extra)} 个"))
    cells = {
        name: values[index] if index < len(values) else None for index, name in enumerate(header)
    }
    return RawRow(row=number, cells=cells, problems=tuple(problems))


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputError(f"{path.name} 不是 UTF-8 编码：{exc.reason}") from exc


def _read_csv(path: Path) -> list[RawRow]:
    reader = csv.reader(io.StringIO(_read_text(path), newline=""))
    try:
        records = []
        previous_end = 0
        for record in reader:
            records.append((previous_end + 1, record))
            previous_end = reader.line_num
    except csv.Error as exc:
        raise InputError(f"{path.name} 第 {reader.line_num} 行不是合法 CSV：{exc}") from exc
    if not records:
        raise InputError(f"{path.name} 是空文件")
    header = _header(list(records[0][1]), f"{path.name} 第 1 行")
    rows: list[RawRow] = []
    for number, record in records[1:]:
        # 导出时为防公式注入加的单引号，导入时去掉。
        row = _table_row(number, header, [unguard_cell(cell) for cell in record])
        if row is not None:
            rows.append(row)
    return rows


def _read_xlsx(path: Path) -> list[RawRow]:
    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException

    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, KeyError, OSError) as exc:
        raise InputError(f"{path.name} 不是可读取的 xlsx 文件：{exc}") from exc
    try:
        sheet = (
            workbook[XLSX_SHEET] if XLSX_SHEET in workbook.sheetnames else workbook.worksheets[0]
        )
        values = [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()
    if not values:
        raise InputError(f"{path.name} 的工作表 {sheet.title} 是空的")
    header = _header(values[0], f"{path.name} 工作表 {sheet.title} 第 1 行")
    rows: list[RawRow] = []
    for index, record in enumerate(values[1:], start=2):
        row = _table_row(index, header, record)
        if row is not None:
            rows.append(row)
    return rows


def split_markdown_row(line: str) -> list[str]:
    """按未转义的竖线切分 Markdown 表格行；`\\|` 表示单元格里的竖线。"""
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    cells: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] == "|":
            current.append("|")
            index += 2
            continue
        if char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    cells.append("".join(current).strip())
    return cells


def _read_markdown(path: Path) -> list[RawRow]:
    lines = _read_text(path).splitlines()
    for index in range(len(lines) - 1):
        line = lines[index].strip()
        if not line.startswith("|") or _MD_SEPARATOR.match(lines[index + 1].strip()) is None:
            continue
        names = [normalize_header(cell) for cell in split_markdown_row(line)]
        if "fact_id" not in names:
            continue
        header = _header(list(names), f"{path.name} 第 {index + 1} 行")
        rows: list[RawRow] = []
        number = index + 3
        for raw in lines[index + 2 :]:
            if not raw.strip().startswith("|"):
                break
            row = _table_row(number, header, list(split_markdown_row(raw)))
            if row is not None:
                rows.append(row)
            number += 1
        return rows
    raise InputError(f"{path.name} 里没有含 fact_id 列的 Markdown 表格")


# ------------------------------------------------------------------ json


def _classify(number: int, item: Any, *, forced: str | None = None) -> RawRow:
    if not isinstance(item, dict):
        return RawRow(row=number, cells={}, problems=(("", "不是 JSON 对象"),))
    kind = forced
    if kind is None:
        if "fact_id" in item:
            kind = "fact"
        elif "entity_id" in item and "kind" in item:
            kind = "entity"
        else:
            return RawRow(
                row=number,
                cells={},
                problems=(
                    ("", "既不是事实行（缺少 fact_id），也不是实体记录（缺少 entity_id、kind）"),
                ),
            )
    if kind == "entity":
        return RawRow(row=number, cells=dict(item), kind="entity")
    unknown = sorted(key for key in item if key not in KNOWN_COLUMNS | JSON_FACT_EXTRA_KEYS)
    problems = tuple((key, f"未知字段 {key}") for key in unknown)
    return RawRow(row=number, cells=dict(item), problems=problems)


def _read_json(path: Path) -> list[RawRow]:
    text = _read_text(path)
    if path.suffix.lower() == ".jsonl":
        rows: list[RawRow] = []
        for number, raw in enumerate(text.splitlines(), start=1):
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError as exc:
                rows.append(
                    RawRow(row=number, cells={}, problems=(("", f"不是合法 JSON：{exc.msg}"),))
                )
                continue
            rows.append(_classify(number, item))
        return rows
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"{path.name} 不是合法 JSON：{exc.msg}（第 {exc.lineno} 行）") from exc
    if isinstance(data, list):
        return [_classify(number, item) for number, item in enumerate(data, start=1)]
    if not isinstance(data, dict) or not ({"facts", "entities"} & set(data)):
        raise InputError(f"{path.name} 必须是数组，或含 facts / entities 数组的对象")
    result: list[RawRow] = []
    for key, kind in (("entities", "entity"), ("facts", "fact")):
        items = data.get(key, [])
        if not isinstance(items, list):
            raise InputError(f"{path.name} 的 {key} 必须是数组")
        result.extend(
            _classify(number, item, forced=kind) for number, item in enumerate(items, start=1)
        )
    return result
