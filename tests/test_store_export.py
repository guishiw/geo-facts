"""事实库导出：版本选择、CSV 单元格写法与防公式注入。期望值均为手工推算。"""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from typing import Any

import pytest
from helpers_store import make_fact, memory_store

from geo_facts.jsonl import dumps_line
from geo_facts.store import EXPORT_COLUMNS, FACT_COLUMNS, select_facts, to_csv, to_jsonl
from geo_facts.store.export import format_number, guard_cell, unguard_cell, value_cells


def sample_facts() -> list[dict[str, Any]]:
    """文件顺序故意打乱：导出时按 fact_id、版本号排序。"""
    new_price = make_fact(
        "F-S7-PRICE",
        attribute="售价",
        value=21.98,
        unit="万元",
        content_use="pricing",
        valid_from="2026-09-01",
        version=2,
    )
    old_price = make_fact(
        "F-S7-PRICE",
        attribute="售价",
        value=23.98,
        unit="万元",
        content_use="pricing",
        valid_until="2026-08-31",
        version=1,
    )
    cltc = make_fact(
        "F-S7-RANGE-CLTC",
        conditions={"trim": "长续航版", "test_cycle": "CLTC"},
        is_core=True,
        aliases=["续航", "续航能力"],
    )
    cltc["ext"] = {"source_type": "企业官网", "batch": 3}
    cltc["note"] = "长续航版"
    slogan = make_fact(
        "M-S7-SLOGAN",
        attribute="宣传语",
        value="=1+1 安静得像一片湖",
        unit=None,
        zone="marketing",
        source_tier=None,
        content_use="promotion",
        evidence=None,
    )
    ranged = make_fact("F-RANGE", attribute="续航里程", value={"min": 600, "max": 650})
    flag = make_fact("F-BOOL", attribute="支持快充", value=True, unit=None)
    cold = make_fact("F-NEG", attribute="最低工作温度", value=-5, unit="℃")
    return [new_price, cltc, slogan, old_price, ranged, flag, cold]


def test_select_all_versions_sorted_by_fact_id_and_version() -> None:
    store = memory_store(sample_facts())
    selected = select_facts(store, None)
    assert [(fact["fact_id"], fact["version"]) for fact in selected] == [
        ("F-BOOL", 1),
        ("F-NEG", 1),
        ("F-RANGE", 1),
        ("F-S7-PRICE", 1),
        ("F-S7-PRICE", 2),
        ("F-S7-RANGE-CLTC", 1),
        ("M-S7-SLOGAN", 1),
    ]


@pytest.mark.parametrize(
    ("day", "price_version", "count"),
    [("2026-08-31", 1, 6), ("2026-09-01", 2, 6), ("2026-03-17", None, 0)],
)
def test_select_as_of_keeps_one_effective_version_per_fact_id(
    day: str, price_version: int | None, count: int
) -> None:
    selected = select_facts(memory_store(sample_facts()), date.fromisoformat(day))
    assert len(selected) == count
    prices = [fact["version"] for fact in selected if fact["fact_id"] == "F-S7-PRICE"]
    assert prices == ([] if price_version is None else [price_version])


@pytest.mark.parametrize(
    ("value", "cells"),
    [
        (620, ("620", "number")),
        (21.98, ("21.98", "number")),
        (620.0, ("620", "number")),
        (0.1, ("0.1", "number")),
        ({"min": 600, "max": 650.5}, ("600~650.5", "range")),
        (True, ("true", "boolean")),
        (False, ("false", "boolean")),
        ("2026-03-18", ("2026-03-18", "text")),
    ],
)
def test_value_cells(value: Any, cells: tuple[str, str]) -> None:
    assert value_cells(value) == cells


def test_format_number() -> None:
    assert format_number(5) == "5"
    assert format_number(5.0) == "5"
    assert format_number(-0.5) == "-0.5"


@pytest.mark.parametrize(
    ("text", "guarded"),
    [
        ("=HYPERLINK(1)", "'=HYPERLINK(1)"),
        ("+86 0000", "'+86 0000"),
        ("-x", "'-x"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tTAB", "'\tTAB"),
        ("\rCR", "'\rCR"),
        ("-5", "-5"),  # 纯数字（负数）不加单引号
        ("-0.5", "-0.5"),
        ("售价", "售价"),
        ("", ""),
    ],
)
def test_guard_cell_and_its_inverse(text: str, guarded: str) -> None:
    assert guard_cell(text) == guarded
    assert unguard_cell(guarded) == text


def test_unguard_leaves_ordinary_quotes_alone() -> None:
    assert unguard_cell("'普通引号") == "'普通引号"
    assert unguard_cell("'") == "'"


def test_csv_has_export_columns_and_guarded_cells() -> None:
    text = to_csv(select_facts(memory_store(sample_facts()), None))
    assert "\r" not in text  # 行尾统一为 \n
    reader = csv.DictReader(io.StringIO(text))
    assert tuple(reader.fieldnames or ()) == EXPORT_COLUMNS
    assert ("fact_id", "version", *FACT_COLUMNS[1:]) == EXPORT_COLUMNS
    rows = {(row["fact_id"], row["version"]): row for row in reader}
    assert len(rows) == 7
    cltc = rows[("F-S7-RANGE-CLTC", "1")]
    assert cltc["value"] == "620"
    assert cltc["value_type"] == "number"
    assert cltc["unit"] == "km"
    assert cltc["conditions"] == "test_cycle=CLTC; trim=长续航版"  # 按键名排序
    assert cltc["aliases"] == "续航; 续航能力"
    assert cltc["is_core"] == "true"
    assert cltc["source_type"] == "企业官网"
    assert json.loads(cltc["ext"]) == {"batch": 3}  # source_type 单独成列，不再留在 ext
    assert cltc["evidence_id"] == "EV-S7-SPEC"
    assert cltc["collected_at"] == "2026-09-20T02:00:00Z"
    assert cltc["valid_until"] == ""
    assert cltc["note"] == "长续航版"
    assert cltc["synthetic"] == "true"
    slogan = rows[("M-S7-SLOGAN", "1")]
    assert slogan["value"] == "'=1+1 安静得像一片湖"
    assert slogan["source_tier"] == ""
    assert slogan["evidence_id"] == ""
    assert slogan["reviewer"] == ""
    assert slogan["unit"] == ""
    assert slogan["ext"] == ""
    assert rows[("F-NEG", "1")]["value"] == "-5"
    assert rows[("F-RANGE", "1")]["value"] == "600~650"
    assert rows[("F-RANGE", "1")]["value_type"] == "range"
    assert rows[("F-BOOL", "1")]["value"] == "true"
    assert rows[("F-S7-PRICE", "1")]["valid_until"] == "2026-08-31"


def test_jsonl_is_the_contract_records_verbatim() -> None:
    facts = select_facts(memory_store(sample_facts()), None)
    text = to_jsonl(facts)
    lines = text.splitlines()
    assert len(lines) == 7
    assert lines[0] == dumps_line(facts[0])
    assert [json.loads(line) for line in lines] == facts
    assert to_jsonl([]) == ""
