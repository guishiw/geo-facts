"""事实导入：四种格式、逐行报错、版本递增、来源等级默认值与模板。期望值均为手工写出。"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import run_cli, run_cli_json
from helpers_store import NOW, base_entities, build_store, make_entity
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from openpyxl import Workbook

from geo_facts import contracts, jsonl
from geo_facts.ingest import TEMPLATE_COLUMNS, Context, RawRow, convert_fact_row, read_source
from geo_facts.ingest.convert import (
    CellError,
    cell_text,
    owned_domains,
    owned_source,
    parse_conditions,
    parse_list,
    parse_value,
    parse_version,
)
from geo_facts.ingest.readers import split_markdown_row
from geo_facts.store import init_store, load_store
from geo_facts.store.export import EXPORT_COLUMNS, fact_row, unguard_cell

EVIDENCE = {
    "evidence_id": "EV-S7-SPEC",
    "source_name": "澜序汽车官网 S7 参数页（虚构）",
    "source_ref": "https://lanxu-auto.example/s7/spec",
    "collected_at": "2026-09-20T02:00:00Z",
    "last_verified_at": "2026-10-01T02:00:00Z",
    "reviewer": "事实库审核人（虚构）",
}


def row(**cells: str) -> dict[str, str]:
    """一行导入数据：默认是 S7 的 CLTC 续航（全部列都给出）。"""
    base = {
        "fact_id": "F-S7-RANGE-CLTC",
        "entity_id": "product-lanxu-s7",
        "attribute": "续航里程",
        "value": "620",
        "value_type": "",
        "unit": "km",
        "conditions": "test_cycle=CLTC; trim=长续航版",
        "zone": "fact",
        "source_tier": "C",
        "source_type": "",
        "content_use": "specification",
        "is_core": "true",
        **EVIDENCE,
        "valid_from": "2026-03-18",
        "valid_until": "",
        "status": "",
        "aliases": "续航; 续航能力",
        "note": "",
        "synthetic": "",
        "ext": "",
    }
    base.update(cells)
    return base


def expected_cltc(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": "1.0",
        "fact_id": "F-S7-RANGE-CLTC",
        "project_id": "auto-demo",
        "entity_id": "product-lanxu-s7",
        "attribute": "续航里程",
        "value": 620,
        "unit": "km",
        "conditions": {"test_cycle": "CLTC", "trim": "长续航版"},
        "zone": "fact",
        "source_tier": "C",
        "content_use": "specification",
        "is_core": True,
        "evidence": dict(EVIDENCE),
        "valid_from": "2026-03-18",
        "valid_until": None,
        "status": "active",
        "version": 1,
        "aliases": ["续航", "续航能力"],
        "synthetic": True,
    }
    record.update(overrides)
    return record


def price_row(**cells: str) -> dict[str, str]:
    values = {
        "fact_id": "F-S7-PRICE",
        "attribute": "售价",
        "value": "21.98",
        "unit": "万元",
        "conditions": "",
        "content_use": "pricing",
        "evidence_id": "EV-S7-PRICE",
        "source_name": "澜序汽车官网 S7 价格页（虚构）",
        "source_ref": "https://lanxu-auto.example/s7/price",
        "valid_from": "2026-09-01",
        "aliases": "指导价; 起售价",
    }
    values.update(cells)
    return row(**values)


def slogan_row() -> dict[str, str]:
    blank_evidence = dict.fromkeys(EVIDENCE, "")
    return row(
        fact_id="M-S7-SLOGAN",
        attribute="宣传语",
        value="安静得像一片湖",
        unit="",
        conditions="",
        zone="marketing",
        source_tier="",
        content_use="promotion",
        is_core="false",
        aliases="",
        **blank_evidence,
    )


def write_csv(
    path: Path, rows: list[dict[str, str]], columns: tuple[str, ...] | None = None
) -> Path:
    names = list(columns or rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_md(path: Path, rows: list[dict[str, str]], *, preamble: str = "") -> Path:
    names = list(rows[0].keys())
    lines = [preamble, "| " + " | ".join(names) + " |", "|" + "---|" * len(names)]
    for item in rows:
        lines.append("| " + " | ".join(item[name].replace("|", "\\|") for name in names) + " |")
    lines.extend(["", "表格之后的说明文字。"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_xlsx(
    path: Path, rows: list[list[Any]], *, sheet: str = "facts", decoy: bool = False
) -> Path:
    workbook = Workbook()
    first = workbook.active
    assert first is not None
    if decoy:
        first.title = "说明"
        first.append(["这张表不是数据"])
        target = workbook.create_sheet(sheet)
    else:
        first.title = sheet
        target = first
    for values in rows:
        target.append(values)
    workbook.save(path)
    return path


def new_store(tmp_path: Path, name: str = "store") -> Path:
    root = tmp_path / name
    build_store(root, [])
    return root


def import_json(store: Path, path: Path, *extra: str) -> tuple[int, Any]:
    return run_cli_json("import", str(store), "--input", str(path), "--now", NOW, *extra)


def stored_facts(store: Path) -> list[dict[str, Any]]:
    return jsonl.read_jsonl(store / "facts.jsonl")


# ------------------------------------------------------------------ csv：写入、跳过、版本递增


def test_csv_import_writes_contract_records(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    source = write_csv(tmp_path / "facts.csv", [row(), price_row(), slogan_row()])
    code, data = import_json(store, source)
    assert code == 0
    assert data["ok"] is True
    assert data["facts"] == {"created": 3, "new_versions": 0, "unchanged": 0, "invalid": 0}
    assert data["rows"] == 3
    assert data["errors"] == []
    assert data["notices"] == []
    assert data["input"] == "facts.csv"
    assert [(item["row"], item["fact_id"], item["version"]) for item in data["written"]] == [
        (2, "F-S7-RANGE-CLTC", 1),
        (3, "F-S7-PRICE", 1),
        (4, "M-S7-SLOGAN", 1),
    ]
    facts = stored_facts(store)
    assert facts[0] == expected_cltc()
    assert facts[1]["value"] == 21.98
    assert facts[1]["conditions"] == {}
    assert facts[1]["aliases"] == ["指导价", "起售价"]
    assert facts[2] == {
        "schema_version": "1.0",
        "fact_id": "M-S7-SLOGAN",
        "project_id": "auto-demo",
        "entity_id": "product-lanxu-s7",
        "attribute": "宣传语",
        "value": "安静得像一片湖",
        "unit": None,
        "conditions": {},
        "zone": "marketing",
        "source_tier": None,
        "content_use": "promotion",
        "is_core": False,
        "evidence": None,
        "valid_from": "2026-03-18",
        "valid_until": None,
        "status": "active",
        "version": 1,
        "synthetic": True,
    }
    for fact in facts:
        assert contracts.iter_issues("fact", fact) == []
    events = jsonl.read_jsonl(store / "audit.jsonl")
    assert events[-1]["action"] == "import"
    assert events[-1]["reason"] == "导入 facts.csv"
    assert events[-1]["details"]["written"] == [
        {"fact_id": "F-S7-RANGE-CLTC", "version": 1},
        {"fact_id": "F-S7-PRICE", "version": 1},
        {"fact_id": "M-S7-SLOGAN", "version": 1},
    ]
    assert "/" not in json.dumps(events[-1]["details"]["input"])  # 审计里只记文件名


def test_reimport_skips_unchanged_and_changed_content_gets_next_version(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    source = write_csv(tmp_path / "facts.csv", [row(), price_row()])
    assert import_json(store, source)[0] == 0
    code, data = import_json(store, source)
    assert code == 0
    assert data["facts"] == {"created": 0, "new_versions": 0, "unchanged": 2, "invalid": 0}
    assert data["unchanged"] == [
        {"row": 2, "fact_id": "F-S7-RANGE-CLTC", "version": 1},
        {"row": 3, "fact_id": "F-S7-PRICE", "version": 1},
    ]
    assert len(stored_facts(store)) == 2
    events = jsonl.read_jsonl(store / "audit.jsonl")
    assert len(events) == 4  # 初始化、夹具写入、两次导入：内容没变的导入同样留痕
    changed = write_csv(tmp_path / "changed.csv", [row(value="630"), price_row()])
    code, data = import_json(store, changed)
    assert code == 0
    assert data["facts"] == {"created": 0, "new_versions": 1, "unchanged": 1, "invalid": 0}
    assert data["written"] == [
        {"row": 2, "fact_id": "F-S7-RANGE-CLTC", "version": 2, "action": "new_version"}
    ]
    facts = stored_facts(store)
    assert [(fact["fact_id"], fact["version"], fact["value"]) for fact in facts] == [
        ("F-S7-RANGE-CLTC", 1, 620),
        ("F-S7-PRICE", 1, 21.98),
        ("F-S7-RANGE-CLTC", 2, 630),
    ]
    # 只追加：旧版本那一行原样保留。
    assert facts[0] == expected_cltc()


def test_same_fact_id_twice_in_one_file_builds_history(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    old = price_row(value="23.98", valid_from="2026-03-18", valid_until="2026-08-31")
    new = price_row()
    code, data = import_json(store, write_csv(tmp_path / "history.csv", [old, new, new]))
    assert code == 0
    assert data["facts"] == {"created": 1, "new_versions": 1, "unchanged": 1, "invalid": 0}
    assert [
        (fact["version"], fact["value"], fact["valid_until"]) for fact in stored_facts(store)
    ] == [
        (1, 23.98, "2026-08-31"),
        (2, 21.98, None),
    ]


def test_reviewer_fills_blank_reviewer_and_is_the_audit_actor(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    source = write_csv(tmp_path / "facts.csv", [row(reviewer="")])
    code, data = import_json(store, source)
    assert code == 1
    assert [(item["row"], item["column"]) for item in data["errors"]] == [(2, "reviewer")]
    code, data = import_json(store, source, "--reviewer", "王审核（虚构）")
    assert code == 0
    assert stored_facts(store)[0]["evidence"]["reviewer"] == "王审核（虚构）"
    assert jsonl.read_jsonl(store / "audit.jsonl")[-1]["actor"] == "王审核（虚构）"
    # 行里写了 reviewer 时以行为准。
    other = write_csv(tmp_path / "other.csv", [price_row()])
    import_json(store, other, "--reviewer", "王审核（虚构）")
    assert stored_facts(store)[1]["evidence"]["reviewer"] == "事实库审核人（虚构）"


def test_import_with_same_now_is_reproducible(tmp_path: Path) -> None:
    source = write_csv(tmp_path / "facts.csv", [row(), price_row(), slogan_row()])
    roots = []
    for name in ("a", "b"):
        root = tmp_path / name
        init_store(root, project_id="auto-demo", now=NOW, synthetic=True)
        entities = tmp_path / "entities.jsonl"
        if not entities.exists():
            jsonl.append_jsonl(entities, base_entities())
        assert import_json(root, entities)[0] == 0
        assert import_json(root, source)[0] == 0
        roots.append(root)
    for name in ("entities.jsonl", "facts.jsonl", "audit.jsonl"):
        assert (roots[0] / name).read_bytes() == (roots[1] / name).read_bytes()


def test_non_synthetic_store_keeps_row_flag(tmp_path: Path) -> None:
    root = tmp_path / "store"
    init_store(root, project_id="auto-demo", now=NOW)
    entities = tmp_path / "entities.json"
    jsonl.write_json(entities, {"entities": base_entities()})
    assert import_json(root, entities)[0] == 0
    source = write_csv(tmp_path / "facts.csv", [row(), price_row(synthetic="true")])
    assert import_json(root, source)[0] == 0
    facts = stored_facts(root)
    assert "synthetic" not in facts[0]
    assert facts[1]["synthetic"] is True


# ------------------------------------------------------------------ 逐行报错


def test_invalid_rows_are_reported_and_not_written(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    rows = [
        row(),  # 第 2 行：合法
        row(fact_id="F-NO-VALUE", value=""),  # 第 3 行
        row(fact_id="F-BAD-ZONE", zone="事实区"),  # 第 4 行
        row(fact_id="F-NO-ENTITY", entity_id="product-unknown"),  # 第 5 行
        row(fact_id="F-PARTIAL", source_name=""),  # 第 6 行
        row(fact_id="F-BAD-DAY", valid_from="2026-02-30"),  # 第 7 行
        row(fact_id="F-NO-TIER", source_tier="", source_ref="https://auto-review.example/s7"),
        row(fact_id="F-BAD-TIME", collected_at="2026-09-20 10:00"),  # 第 9 行
        row(fact_id="坏 id"),  # 第 10 行
        row(fact_id="F-BACKWARDS", valid_until="2026-01-01"),  # 第 11 行
        row(fact_id="F-VERIFY-ORDER", last_verified_at="2026-09-01T00:00:00Z"),  # 第 12 行
        row(fact_id="F-BAD-USE", content_use="广告"),  # 第 13 行
        row(fact_id="F-TIER", source_tier="E"),  # 第 14 行
        row(fact_id="F-CORE", is_core="maybe"),  # 第 15 行
        row(fact_id="F-COND", conditions="CLTC"),  # 第 16 行
        row(fact_id="F-NUM", value="六百", value_type="number"),  # 第 17 行
        row(fact_id="F-EXT", ext="[1]"),  # 第 18 行
    ]
    code, data = import_json(store, write_csv(tmp_path / "bad.csv", rows))
    assert code == 1
    assert data["ok"] is False
    assert data["facts"] == {"created": 1, "new_versions": 0, "unchanged": 0, "invalid": 16}
    assert data["invalid_rows"] == 16
    assert [(item["row"], item["column"]) for item in data["errors"]] == [
        (3, "value"),
        (4, "zone"),
        (5, "entity_id"),
        (6, "source_name"),
        (7, "valid_from"),
        (8, "source_tier"),
        (9, "collected_at"),
        (10, "fact_id"),
        (11, "valid_until"),
        (12, "last_verified_at"),
        (13, "content_use"),
        (14, "source_tier"),
        (15, "is_core"),
        (16, "conditions"),
        (17, "value"),
        (18, "ext"),
    ]
    messages = {item["row"]: item["message"] for item in data["errors"]}
    assert "必填" in messages[3]
    assert "fact / marketing / opinion" in messages[4]
    assert "product-unknown" in messages[5]
    assert "六项要么全填" in messages[6]
    assert "真实存在的日期" in messages[7]
    assert "自有域名" in messages[8]
    assert "UTC" in messages[9]
    assert "早于生效日期" in messages[11]
    assert "早于采集时间" in messages[12]
    assert data["errors"][0]["fact_id"] == "F-NO-VALUE"
    assert [fact["fact_id"] for fact in stored_facts(store)] == ["F-S7-RANGE-CLTC"]
    code, out, _ = run_cli("import", str(store), "--input", str(tmp_path / "bad.csv"), "--now", NOW)
    assert code == 1
    assert "导入未完全通过：bad.csv（csv，17 行）" in out
    assert "第 3 行 [value]：必填，不能为空" in out
    assert "内容未变跳过 1 条，非法未写入 16 行" in out


def test_a_row_can_carry_several_errors(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    source = write_csv(tmp_path / "bad.csv", [row(attribute="", zone="", valid_from="")])
    code, data = import_json(store, source)
    assert code == 1
    assert [item["column"] for item in data["errors"]] == ["attribute", "valid_from", "zone"]


def test_too_many_cells_and_blank_rows(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    path = tmp_path / "ragged.csv"
    header = ",".join(TEMPLATE_COLUMNS)
    good = write_csv(tmp_path / "tmp.csv", [row()]).read_text(encoding="utf-8").splitlines()[1]
    path.write_text("\n".join([header, "", good + ",多出来的", ",,,"]) + "\n", encoding="utf-8")
    code, data = import_json(store, path)
    assert code == 1
    assert data["rows"] == 1  # 空行不计数
    assert data["errors"][0]["row"] == 3
    assert "比表头多" in data["errors"][0]["message"]


def test_version_column(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    columns = (*TEMPLATE_COLUMNS, "version")
    first = write_csv(tmp_path / "v.csv", [{**row(), "version": "1"}], columns)
    assert import_json(store, first)[1]["facts"]["created"] == 1
    code, data = import_json(store, first)
    assert (code, data["facts"]["unchanged"]) == (0, 1)
    rewrite = write_csv(tmp_path / "rewrite.csv", [{**row(value="630"), "version": "1"}], columns)
    code, data = import_json(store, rewrite)
    assert code == 1
    assert data["errors"][0]["column"] == "version"
    assert "不改写" in data["errors"][0]["message"]
    gap = write_csv(tmp_path / "gap.csv", [{**row(value="630"), "version": "3"}], columns)
    code, data = import_json(store, gap)
    assert code == 1
    assert "最新版本号 + 1（2）" in data["errors"][0]["message"]
    bad = write_csv(tmp_path / "bad.csv", [{**row(), "version": "第一版"}], columns)
    assert import_json(store, bad)[1]["errors"][0]["column"] == "version"
    nxt = write_csv(tmp_path / "next.csv", [{**row(value="630"), "version": "2"}], columns)
    code, data = import_json(store, nxt)
    assert code == 0
    assert data["written"][0]["version"] == 2


# ------------------------------------------------------------------ 来源等级：默认 C 级


def test_owned_domain_defaults_to_tier_c_with_notice(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    rows = [
        row(source_tier=""),  # 品牌自有域名（车型的上级实体）
        price_row(source_tier="", source_ref="https://www.lanxu-auto.example/price"),  # 子域名
        row(fact_id="F-FAKE", source_tier="", source_ref="https://lanxu-auto.example.evil.test/x"),
        row(
            fact_id="F-CENYE",
            entity_id="product-cenye-s7",
            source_tier="",
            source_ref="https://lanxu-auto.example/s7",
        ),
        row(
            fact_id="O-REVIEW",
            zone="opinion",
            content_use="review",
            source_tier="",
            source_ref="https://auto-review.example/s7",
        ),
        row(fact_id="F-FILE", source_tier="", source_ref="资料/参数表.pdf"),
        row(fact_id="F-GIVEN", source_tier="b级", source_ref="https://lanxu-auto.example/s7"),
    ]
    code, data = import_json(store, write_csv(tmp_path / "tiers.csv", rows))
    assert code == 1
    assert [(item["row"], item["fact_id"]) for item in data["errors"]] == [
        (4, "F-FAKE"),
        (5, "F-CENYE"),
        (7, "F-FILE"),
    ]
    assert [(item["row"], item["code"]) for item in data["notices"]] == [
        (2, "source_tier_defaulted"),
        (3, "source_tier_defaulted"),
    ]
    assert "默认为 C 级" in data["notices"][0]["message"]
    assert data["defaulted_source_tiers"] == 2
    facts = {fact["fact_id"]: fact for fact in stored_facts(store)}
    assert facts["F-S7-RANGE-CLTC"]["source_tier"] == "C"
    assert facts["F-S7-RANGE-CLTC"]["ext"] == {"source_tier_defaulted": True}
    assert facts["O-REVIEW"]["source_tier"] is None
    assert facts["F-GIVEN"]["source_tier"] == "B"
    assert "ext" not in facts["F-GIVEN"]
    # 来源等级与内容用途互不推导：同为 C 级，用途照样按行里写的记录。
    assert facts["F-S7-PRICE"]["content_use"] == "pricing"
    assert facts["F-S7-RANGE-CLTC"]["content_use"] == "specification"
    code, out, _ = run_cli(
        "import", str(store), "--input", str(tmp_path / "tiers.csv"), "--now", NOW
    )
    assert "提示（" not in out  # 第二次导入内容不变，不再提示
    # validate 会把默认的来源等级列为提示。
    code, report = run_cli_json("validate", str(store), "--as-of", "2026-10-08")
    assert sorted(
        item["fact_id"] for item in report["warnings"] if item["code"] == "source_tier_defaulted"
    ) == ["F-S7-PRICE", "F-S7-RANGE-CLTC"]


def test_owned_domain_helpers() -> None:
    entities = {entity["entity_id"]: entity for entity in base_entities()}
    entities["loop-a"] = make_entity(
        "loop-a", "环 A", parent="loop-b", owned_domains=("a.example",)
    )
    entities["loop-b"] = make_entity("loop-b", "环 B", parent="loop-a")
    assert owned_domains("product-lanxu-s7", entities) == {"lanxu-auto.example"}
    assert owned_domains("loop-b", entities) == {"a.example"}  # 上级链成环也能结束
    assert owned_domains("missing", entities) == set()
    domains = {"lanxu-auto.example"}
    assert owned_source("https://LANXU-AUTO.example/x", domains) == "lanxu-auto.example"
    assert owned_source("ftp://lanxu-auto.example/x", domains) is None
    assert owned_source("lanxu-auto.example/x", domains) is None
    assert owned_source(None, domains) is None
    assert owned_source("https://xlanxu-auto.example/", domains) is None


# ------------------------------------------------------------------ 四种格式


def test_four_formats_produce_the_same_records(tmp_path: Path) -> None:
    rows = [row(note="说明里有|竖线"), price_row(), slogan_row()]
    csv_path = write_csv(tmp_path / "facts.csv", rows)
    md_path = write_md(
        tmp_path / "facts.md",
        rows,
        preamble="| 说明 | 内容 |\n|---|---|\n| 这张表 | 没有 fact_id 列，跳过 |\n",
    )
    json_path = tmp_path / "facts.json"
    json_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    xlsx_rows: list[list[Any]] = [list(TEMPLATE_COLUMNS)]
    for item in rows:
        values: list[Any] = [item[name] or None for name in TEMPLATE_COLUMNS]
        xlsx_rows.append(values)
    # xlsx 里用原生类型：数字、布尔、日期单元格。
    xlsx_rows[1][TEMPLATE_COLUMNS.index("value")] = 620
    xlsx_rows[1][TEMPLATE_COLUMNS.index("is_core")] = True
    xlsx_rows[1][TEMPLATE_COLUMNS.index("valid_from")] = datetime(2026, 3, 18)
    xlsx_rows[2][TEMPLATE_COLUMNS.index("value")] = 21.98
    xlsx_rows[3][TEMPLATE_COLUMNS.index("valid_from")] = date(2026, 3, 18)
    xlsx_path = write_xlsx(tmp_path / "facts.xlsx", xlsx_rows, decoy=True)
    results = {}
    for name, path in (
        ("csv", csv_path),
        ("md", md_path),
        ("json", json_path),
        ("xlsx", xlsx_path),
    ):
        store = new_store(tmp_path, f"store-{name}")
        code, data = import_json(store, path)
        assert code == 0, (name, data["errors"])
        assert data["format"] == name
        results[name] = stored_facts(store)
    assert results["csv"][0] == expected_cltc(note="说明里有|竖线")
    for name in ("md", "json", "xlsx"):
        assert results[name] == results["csv"], name


def test_row_numbers_follow_each_format(tmp_path: Path) -> None:
    rows = [row(), row(fact_id="F-BAD", zone="")]
    md_path = write_md(tmp_path / "f.md", rows, preamble="# 标题\n\n正文\n")
    source = read_source(md_path)
    # 标题 1 行、空行、正文、空行（preamble 结尾的换行）后，表头在第 5 行，数据从第 7 行开始。
    assert [item.row for item in source.rows] == [7, 8]
    json_path = tmp_path / "f.json"
    json_path.write_text(json.dumps({"facts": rows}, ensure_ascii=False), encoding="utf-8")
    assert [item.row for item in read_source(json_path).rows] == [1, 2]
    jsonl_path = tmp_path / "f.jsonl"
    jsonl_path.write_text(
        "\n".join(
            [json.dumps(rows[0], ensure_ascii=False), "", json.dumps(rows[1], ensure_ascii=False)]
        )
        + "\n",
        encoding="utf-8",
    )
    assert [item.row for item in read_source(jsonl_path).rows] == [1, 3]
    xlsx_path = write_xlsx(
        tmp_path / "f.xlsx",
        [
            list(TEMPLATE_COLUMNS),
            [None] * len(TEMPLATE_COLUMNS),
            [rows[0][name] for name in TEMPLATE_COLUMNS],
        ],
        sheet="Sheet1",
    )
    assert [item.row for item in read_source(xlsx_path).rows] == [3]


def test_xlsx_rejects_datetime_without_timezone(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    values: list[Any] = [row()[name] for name in TEMPLATE_COLUMNS]
    values[TEMPLATE_COLUMNS.index("collected_at")] = datetime(2026, 9, 20, 2, 0)
    values[TEMPLATE_COLUMNS.index("valid_until")] = datetime(2026, 12, 31, 8, 30)
    path = write_xlsx(tmp_path / "f.xlsx", [list(TEMPLATE_COLUMNS), values])
    code, data = import_json(store, path)
    assert code == 1
    messages = {item["column"]: item["message"] for item in data["errors"]}
    assert set(messages) == {"collected_at", "valid_until"}
    assert "没有时区" in messages["collected_at"]
    assert "不带时刻" in messages["valid_until"]


def test_json_object_imports_entities_first_and_contract_records(tmp_path: Path) -> None:
    root = tmp_path / "store"
    init_store(root, project_id="auto-demo", now=NOW, synthetic=True)
    k5 = make_entity("product-cenye-k5", "岑野 K5", role="competitor", parent="brand-cenye")
    del k5["project_id"]  # 缺 project_id 时补上事实库的
    payload = {
        "columns": "说明字段会被忽略",
        "facts": [
            row(fact_id="F-K5-RANGE", entity_id="product-cenye-k5", source_tier="B"),
            {**row(fact_id="F-ORPHAN", entity_id="product-x"), "typo_field": 1},
            "不是对象",
        ],
        "entities": [*base_entities(), k5, make_entity("orphan", "孤儿", parent="missing-parent")],
    }
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    code, data = import_json(root, path)
    assert code == 1
    assert data["entities"] == {"created": 6, "updated": 0, "unchanged": 0, "invalid": 1}
    assert data["facts"]["created"] == 1
    assert [(item["kind"], item["row"], item["column"]) for item in data["errors"]] == [
        ("entity", 7, "parent_entity_id"),
        ("fact", 2, "entity_id"),
        ("fact", 2, "typo_field"),
        ("fact", 3, ""),
    ]
    store = load_store(root)
    assert store.entities["product-cenye-k5"]["project_id"] == "auto-demo"
    assert store.entities["product-cenye-k5"]["synthetic"] is True
    code, out, _ = run_cli("import", str(root), "--input", str(path), "--now", NOW)
    assert "实体：新增 0 个，更新 0 个，未变 6 个，非法 1 个" in out
    assert "第 7 条实体记录 [parent_entity_id]" in out
    renamed = json.loads(json.dumps(payload))
    renamed["entities"] = [{**k5, "aliases": ["K5"]}]
    renamed["facts"] = []
    path.write_text(json.dumps(renamed, ensure_ascii=False), encoding="utf-8")
    code, data = import_json(root, path)
    assert (code, data["entities"]["updated"]) == (0, 1)
    assert load_store(root).entities["product-cenye-k5"]["aliases"] == ["K5"]


def test_jsonl_mixes_entities_and_exported_contract_records(tmp_path: Path) -> None:
    source_root = tmp_path / "example"
    init_store(source_root, project_id=None, now=NOW, example="auto")
    bundle = tmp_path / "bundle.jsonl"
    bundle.write_text(
        (source_root / "entities.jsonl").read_text(encoding="utf-8")
        + (source_root / "facts.jsonl").read_text(encoding="utf-8")
        + "not json\n"
        + json.dumps({"name": "无法识别"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    target = tmp_path / "target"
    init_store(target, project_id="auto-demo", now=NOW, synthetic=True)
    code, data = import_json(target, bundle)
    assert code == 1
    assert data["entities"]["created"] == 10
    assert data["facts"] == {"created": 25, "new_versions": 1, "unchanged": 0, "invalid": 2}
    assert [(item["row"], item["column"]) for item in data["errors"]] == [(37, ""), (38, "")]
    assert stored_facts(target) == stored_facts(source_root)
    other = tmp_path / "other"
    init_store(other, project_id="other-project", now=NOW)
    code, data = import_json(other, bundle)
    assert code == 1
    assert {item["column"] for item in data["errors"]} == {"project_id", "entity_id", ""}


def test_csv_export_round_trips_through_import(tmp_path: Path) -> None:
    source_root = tmp_path / "example"
    init_store(source_root, project_id=None, now=NOW, example="auto")
    exported = tmp_path / "export.csv"
    assert run_cli("export", str(source_root), "--format", "csv", "--out", str(exported))[0] == 0
    target = tmp_path / "target"
    init_store(target, project_id="auto-demo", now=NOW, synthetic=True)
    assert import_json(target, source_root / "entities.jsonl")[0] == 0
    code, data = import_json(target, exported)
    assert code == 0, data["errors"]
    assert data["facts"] == {"created": 25, "new_versions": 1, "unchanged": 0, "invalid": 0}

    def ordered(root: Path) -> list[dict[str, Any]]:
        return sorted(stored_facts(root), key=lambda item: (item["fact_id"], item["version"]))

    assert ordered(target) == ordered(source_root)
    code, data = import_json(target, exported)
    assert data["facts"] == {"created": 0, "new_versions": 0, "unchanged": 26, "invalid": 0}


def test_csv_formula_guard_is_removed_on_import(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    slogan = slogan_row()
    slogan["value"] = "'=1+1 安静"
    slogan["note"] = "'普通的单引号保留"
    assert import_json(store, write_csv(tmp_path / "f.csv", [slogan]))[0] == 0
    fact = stored_facts(store)[0]
    assert fact["value"] == "=1+1 安静"
    assert fact["note"] == "'普通的单引号保留"


# ------------------------------------------------------------------ 整份文件的输入错误（退出 2）


def test_file_level_errors_exit_2(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    cases: list[tuple[str, str, str]] = [
        (
            "unknown.csv",
            ",".join([*TEMPLATE_COLUMNS, "valid_untill"]) + "\n",
            "未知列 valid_untill",
        ),
        ("missing.csv", "fact_id,entity_id\nF-1,product-lanxu-s7\n", "缺少必填列"),
        ("dup.csv", ",".join([*TEMPLATE_COLUMNS, "fact_id"]) + "\n", "列名重复 fact_id"),
        ("blankhead.csv", "fact_id,,entity_id\n", "表头第 2 列为空"),
        ("empty.csv", "", "空文件"),
        ("nohead.csv", ",,\n", "没有表头"),
        ("notable.md", "# 只有标题\n\n没有表格\n", "没有含 fact_id 列的 Markdown 表格"),
        ("broken.json", "{", "不是合法 JSON"),
        ("scalar.json", "42", "必须是数组"),
        ("badfacts.json", '{"facts": {}}', "facts 必须是数组"),
        ("notes.txt", "x", "无法从扩展名判断格式"),
        ("fake.xlsx", "不是 zip", "不是可读取的 xlsx 文件"),
    ]
    for name, content, message in cases:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        code, _out, err = run_cli("import", str(store), "--input", str(path), "--now", NOW)
        assert code == 2, name
        assert message in err, (name, err)
    gbk = tmp_path / "gbk.csv"
    gbk.write_bytes("事实".encode("gb18030"))
    assert "不是 UTF-8 编码" in run_cli("import", str(store), "--input", str(gbk))[2]
    code, _, err = run_cli("import", str(store), "--input", str(tmp_path / "nope.csv"))
    assert (code, "输入文件不存在" in err) == (2, True)
    code, _, err = run_cli("import", str(tmp_path / "no-store"), "--input", str(gbk))
    assert code == 2
    empty_xlsx = write_xlsx(tmp_path / "empty.xlsx", [])
    assert "是空的" in run_cli("import", str(store), "--input", str(empty_xlsx))[2]
    # 没有任何东西被写入，审计也不记（整份文件被拒绝）。
    assert stored_facts(store) == []
    assert len(jsonl.read_jsonl(store / "audit.jsonl")) == 2


def test_import_argument_errors(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    assert run_cli("import", str(store))[0] == 2
    assert "--template" in run_cli("import", str(store))[2]
    code, _, err = run_cli("import", str(store), "--input", "x.csv", "--out", "y.csv")
    assert (code, "--out 只用于 --template" in err) == (2, True)
    code, _, err = run_cli("import", str(store), "--template", "csv")
    assert (code, "不能同时给出" in err) == (2, True)
    code, _, err = run_cli("import", "--template", "xlsx")
    assert code == 2


# ------------------------------------------------------------------ 模板


@pytest.mark.parametrize("fmt", ["csv", "json", "md"])
def test_templates_are_importable(tmp_path: Path, fmt: str) -> None:
    out = tmp_path / f"template.{fmt}"
    code, out_text, _ = run_cli("import", "--template", fmt, "--out", str(out))
    assert code == 0
    assert "列说明：" in out_text
    assert "source_tier（事实区必填）" in out_text
    store = new_store(tmp_path)
    code, data = import_json(store, out)
    assert code == 0, data["errors"]
    assert data["facts"]["created"] == 2
    facts = stored_facts(store)
    assert facts[0]["conditions"] == {"test_cycle": "CLTC", "trim": "长续航版"}
    assert facts[1]["zone"] == "marketing"
    assert facts[0]["ext"] == {"source_type": "企业官网"}
    code, data = run_cli_json("import", "--template", fmt, "--out", str(out))
    assert code == 2  # 不覆盖已有文件


def test_template_to_stdout_lists_columns(tmp_path: Path) -> None:
    code, out, _ = run_cli("import", "--template", "csv")
    assert code == 0
    assert out.splitlines()[0] == ",".join(TEMPLATE_COLUMNS)
    code, data = run_cli_json("import", "--template", "md")
    assert code == 0
    assert [item["name"] for item in data["columns"]] == [*TEMPLATE_COLUMNS, "version"]
    assert "## 列说明" in data["content"]
    assert "026 §6.2" in data["content"]
    code, data = run_cli_json("import", "--template", "json")
    assert json.loads(data["content"])["facts"][0]["value"] == 620


# ------------------------------------------------------------------ 单元格解析


@pytest.mark.parametrize(
    ("raw", "value_type", "expected"),
    [
        ("620", None, 620),
        ("219,800", None, 219800),
        ("21.98", None, 21.98),
        ("-5", None, -5),
        ("600~650", None, {"min": 600, "max": 650}),
        ("600 ～ 650.5", None, {"min": 600, "max": 650.5}),
        ("安静~平稳", None, "安静~平稳"),
        ("650~600", None, "650~600"),  # 自动识别时下限大于上限的不当作范围
        ("5 年或 15 万公里", None, "5 年或 15 万公里"),
        ("2026-03-18", None, "2026-03-18"),
        ("true", None, "true"),
        ("true", "boolean", True),
        ("否", "boolean", False),
        (True, None, True),
        (620, "text", "620"),
        (21.98, None, 21.98),
        (1, "boolean", True),
        ({"min": 1, "max": 2}, None, {"min": 1, "max": 2}),
        ("620", "text", "620"),
    ],
)
def test_parse_value(raw: Any, value_type: str | None, expected: Any) -> None:
    assert parse_value(raw, value_type) == expected


@pytest.mark.parametrize(
    ("raw", "value_type", "message"),
    [
        ("", None, "必填"),
        (None, None, "必填"),
        ([1], None, "数组"),
        ({"min": 1}, None, "范围必须是"),
        ({"min": True, "max": 2}, None, "范围必须是"),
        ({"min": 1, "max": 2}, "number", "不能是对象"),
        ("650~600", "range", "下限 650 大于上限 600"),
        ("六百", "range", "下限~上限"),
        ("六百", "number", "不是数字"),
        ("也许", "boolean", "true 或 false"),
    ],
)
def test_parse_value_errors(raw: Any, value_type: str | None, message: str) -> None:
    with pytest.raises(CellError, match=message):
        parse_value(raw, value_type)


def test_cell_and_list_helpers() -> None:
    assert cell_text(None) is None
    assert cell_text("  ") is None
    assert cell_text(False) == "false"
    assert cell_text(620.0) == "620"
    assert cell_text(date(2026, 3, 18)) == "2026-03-18"
    assert cell_text(["x"]) == "['x']"
    assert parse_conditions(None) == {}
    assert parse_conditions("  ") == {}
    assert parse_conditions("test_cycle＝CLTC；trim = 长续航版;") == {
        "test_cycle": "CLTC",
        "trim": "长续航版",
    }
    assert parse_conditions('{"test_cycle": "WLTC"}') == {"test_cycle": "WLTC"}
    assert parse_conditions({"soc_range": "30%-80%"}) == {"soc_range": "30%-80%"}
    for bad, message in (
        ("{", "不是合法 JSON"),
        ("[1]", "键=值"),
        ('{"a": ""}', "不能为空"),
        ("a=1; a=2", "重复"),
        ("=CLTC", "不能为空"),
    ):
        with pytest.raises(CellError, match=message):
            parse_conditions(bad)
    assert parse_list("aliases", "续航；续航; 续航能力\n里程") == ["续航", "续航能力", "里程"]
    assert parse_list("aliases", ["续航", " ", "续航"]) == ["续航"]
    assert parse_list("aliases", None) == []
    assert parse_version(None) is None
    assert parse_version(2) == 2
    assert parse_version("3") == 3
    for bad in ("0", "-1", "v2", 0):
        with pytest.raises(CellError):
            parse_version(bad)
    assert split_markdown_row("| a | b\\|c |") == ["a", "b|c"]
    assert split_markdown_row("a | b") == ["a", "b"]
    assert split_markdown_row("| a | b\\|") == ["a", "b|"]


def test_convert_contract_form_record_with_nested_evidence() -> None:
    entities = {entity["entity_id"]: entity for entity in base_entities()}
    ctx = Context(project_id="auto-demo", entities=entities, synthetic=True)
    record = expected_cltc(version=4)
    converted = convert_fact_row(RawRow(row=1, cells=dict(record)), ctx)
    assert converted.errors == []
    assert converted.version == 4
    assert converted.fact == record
    both = convert_fact_row(RawRow(row=1, cells={**record, "evidence_id": "EV-X"}), ctx)
    assert [item["column"] for item in both.errors] == ["evidence"]
    odd = convert_fact_row(RawRow(row=1, cells={**record, "evidence": "EV-X"}), ctx)
    assert [item["column"] for item in odd.errors] == ["evidence"]
    schema = convert_fact_row(RawRow(row=1, cells={**record, "schema_version": "2.0"}), ctx)
    assert [item["column"] for item in schema.errors] == ["schema_version"]
    empty = convert_fact_row(RawRow(row=1, cells={**record, "evidence": None}), ctx)
    assert empty.fact is not None
    assert empty.fact["evidence"] is None
    long_note = convert_fact_row(RawRow(row=1, cells={**record, "attribute": " "}), ctx)
    assert [item["column"] for item in long_note.errors] == ["attribute"]


# ------------------------------------------------------------------ 随机输入：导出的行可以原样导入


_TEXT = st.text(
    alphabet=st.characters(
        codec="utf-8", exclude_categories=("Cs", "Cc", "Zl", "Zp"), exclude_characters=";；\n'"
    ),
    min_size=1,
    max_size=12,
).filter(lambda text: text.strip() == text and text.strip() != "")
_VALUES = st.one_of(
    st.integers(min_value=-(10**9), max_value=10**9),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False).map(lambda number: round(number, 3)),
    st.booleans(),
    st.tuples(st.integers(0, 1000), st.integers(0, 1000)).map(
        lambda pair: {"min": min(pair), "max": max(pair)}
    ),
    _TEXT,
)


@settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
@given(
    value=_VALUES,
    unit=st.one_of(st.none(), st.sampled_from(["km", "万元", "kWh", "s"])),
    note=st.one_of(st.none(), _TEXT),
    aliases=st.lists(_TEXT, max_size=3, unique=True),
    conditions=st.dictionaries(
        st.sampled_from(["test_cycle", "trim", "soc_range"]),
        _TEXT.filter(lambda t: "=" not in t and "＝" not in t),
        max_size=2,
    ),
)
def test_exported_row_reimports_to_the_same_record(
    value: Any, unit: str | None, note: str | None, aliases: list[str], conditions: dict[str, str]
) -> None:
    entities = {entity["entity_id"]: entity for entity in base_entities()}
    ctx = Context(project_id="auto-demo", entities=entities, synthetic=True)
    record = expected_cltc(value=value, unit=unit, conditions=conditions)
    if aliases:
        record["aliases"] = aliases
    else:
        del record["aliases"]
    if note is not None:
        record["note"] = note
    cells: dict[str, Any] = dict(fact_row(record))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(EXPORT_COLUMNS), lineterminator="\n")
    writer.writeheader()
    writer.writerow(cells)
    parsed = next(csv.DictReader(io.StringIO(buffer.getvalue())))
    converted = convert_fact_row(
        RawRow(row=2, cells={k: unguard_cell(v) for k, v in parsed.items()}), ctx
    )
    assert converted.errors == []
    assert converted.fact == record
