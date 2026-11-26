"""init、validate STORE、conflicts、export 四个命令。期望值来自对内置样例的手工清点。"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from conftest import run_cli, run_cli_json
from helpers_store import NOW, build_store, make_fact

from geo_facts import contracts, jsonl
from geo_facts.errors import InputError
from geo_facts.store import (
    StorePaths,
    commit_records,
    init_store,
    load_example,
    load_meta,
    load_store,
    resolve_now,
)
from geo_facts.store.audit import append_event, file_hash

EXAMPLE_ARGS = ("--example", "auto", "--now", NOW)


def example_store(tmp_path: Path, name: str = "store") -> Path:
    root = tmp_path / name
    code, _, err = run_cli("init", str(root), *EXAMPLE_ARGS)
    assert code == 0, err
    return root


# ------------------------------------------------------------------ init


def test_init_example_writes_contract_valid_synthetic_records(tmp_path: Path) -> None:
    root = tmp_path / "store"
    code, data = run_cli_json("init", str(root), *EXAMPLE_ARGS)
    assert code == 0
    assert data == {
        "store": str(root),
        "store_id": "store-auto-demo",
        "project_id": "auto-demo",
        "timezone": "Asia/Shanghai",
        "example": "auto",
        "entities": 10,
        "fact_versions": 26,
        "fact_ids": 25,
        "synthetic": True,
    }
    meta = jsonl.read_json(root / "store.json")
    assert meta == {
        "schema_version": "1.0",
        "store_id": "store-auto-demo",
        "project_id": "auto-demo",
        "timezone": "Asia/Shanghai",
        "created_at": NOW,
        "synthetic": True,
    }
    for name, contract in (("entities.jsonl", "entity"), ("facts.jsonl", "fact")):
        records = jsonl.read_jsonl(root / name)
        assert records
        for record in records:
            assert contracts.iter_issues(contract, record) == []
            assert record["synthetic"] is True
            assert record["project_id"] == "auto-demo"
    events = jsonl.read_jsonl(root / "audit.jsonl")
    assert len(events) == 1
    assert events[0]["action"] == "register"
    assert events[0]["occurred_at"] == NOW
    assert contracts.iter_issues("audit_event", events[0]) == []


def test_example_data_matches_the_shared_fictional_world() -> None:
    entities, facts = load_example("auto", "auto-demo")
    by_id = {entity["entity_id"]: entity for entity in entities}
    assert by_id["brand-lanxu"]["aliases"] == ["澜序汽车", "LANXU", "Lan-Xu"]
    assert by_id["brand-lanxu"]["owned_domains"] == ["lanxu-auto.example"]
    assert by_id["brand-cenye"]["owned_domains"] == ["cenye-auto.example"]
    assert by_id["product-cenye-s7"]["name"] == "岑野 S7"
    for product in ("s7", "x5", "e3", "m9"):
        assert by_id[f"product-lanxu-{product}"]["parent_entity_id"] == "brand-lanxu"
    values = {(fact["fact_id"], fact["version"]): (fact["value"], fact["unit"]) for fact in facts}
    assert values[("F-S7-RANGE-CLTC", 1)] == (620, "km")
    assert values[("F-S7-RANGE-WLTC", 1)] == (540, "km")
    assert values[("F-S7-PRICE", 1)] == (23.98, "万元")
    assert values[("F-S7-PRICE", 2)] == (21.98, "万元")
    assert values[("F-S7-ACCEL", 1)] == (5.9, "s")
    assert values[("F-S7-BATTERY", 1)] == (82, "kWh")
    assert values[("F-S7-LAUNCH", 1)] == ("2026-03-18", None)
    assert values[("F-S7-WARRANTY", 1)] == ("5 年或 15 万公里", None)
    assert values[("F-X5-RANGE-CLTC", 1)] == (510, "km")
    assert values[("F-X5-PRICE", 1)] == (15.98, "万元")
    assert values[("F-E3-PRICE", 1)] == (13.58, "万元")
    assert values[("F-M9-RANGE-CLTC", 1)] == (650, "km")
    assert values[("F-M9-PRICE", 1)] == (32.98, "万元")
    old, new = (fact for fact in facts if fact["fact_id"] == "F-S7-PRICE")
    assert (old["valid_from"], old["valid_until"]) == ("2026-03-18", "2026-08-31")
    assert (new["valid_from"], new["valid_until"]) == ("2026-09-01", None)
    # 域名只用 .example；样例里不出现本机路径。
    text = json.dumps([entities, facts], ensure_ascii=False)
    assert "/Users/" not in text
    for fact in facts:
        if fact["evidence"] is not None and fact["evidence"]["source_ref"].startswith("http"):
            assert ".example/" in fact["evidence"]["source_ref"]


def test_init_with_same_now_is_byte_for_byte_reproducible(tmp_path: Path) -> None:
    first = example_store(tmp_path, "a")
    second = example_store(tmp_path, "b")
    for name in ("store.json", "entities.jsonl", "facts.jsonl", "audit.jsonl"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_init_empty_store_and_text_output(tmp_path: Path) -> None:
    root = tmp_path / "store"
    code, out, _ = run_cli(
        "init", str(root), "--project-id", "demo-x", "--now", NOW, "--timezone", "UTC"
    )
    assert code == 0
    assert "已创建事实库" in out
    assert "store-demo-x" in out
    assert "合成数据" not in out
    meta = load_meta(root)
    assert (meta["project_id"], meta["timezone"], "synthetic" in meta) == ("demo-x", "UTC", False)
    store = load_store(root)
    assert (store.entities, store.facts) == ({}, [])
    code, out, _ = run_cli("init", str(tmp_path / "ex"), *EXAMPLE_ARGS)
    assert "已写入样例 auto：10 个实体，25 条事实（共 26 个版本）" in out
    assert "全部为合成数据" in out
    code, data = run_cli_json(
        "init", str(tmp_path / "syn"), "--project-id", "p1", "--synthetic", "--store-id", "s1"
    )
    assert code == 0
    assert (data["store_id"], data["synthetic"]) == ("s1", True)


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ((), "--project-id"),
        (("--project-id", "坏 id"), "project_id 不合法"),
        (("--project-id", "p1", "--store-id", "坏 id"), "store_id 不合法"),
        (("--project-id", "p1", "--timezone", "Mars/Olympus"), "未知时区"),
        (("--project-id", "p1", "--now", "2026-10-08 02:00"), "UTC"),
    ],
)
def test_init_rejects_bad_arguments(tmp_path: Path, extra: tuple[str, ...], message: str) -> None:
    code, _, err = run_cli("init", str(tmp_path / "store"), *extra)
    assert code == 2
    assert message in err


def test_init_refuses_to_overwrite(tmp_path: Path) -> None:
    root = example_store(tmp_path)
    before = (root / "facts.jsonl").read_bytes()
    code, data = run_cli_json("init", str(root), *EXAMPLE_ARGS)
    assert code == 2
    assert "拒绝覆盖" in data["message"]
    assert (root / "facts.jsonl").read_bytes() == before
    plain_file = tmp_path / "file.txt"
    plain_file.write_text("x", encoding="utf-8")
    code, _, _err = run_cli("init", str(plain_file), *EXAMPLE_ARGS)
    assert code == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    code, _, _ = run_cli("init", str(empty), *EXAMPLE_ARGS)
    assert code == 0


def test_store_writer_and_reader_reject_bad_input(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="未知样例"):
        load_example("pharma", "p1")
    with pytest.raises(InputError, match="未知样例"):
        init_store(tmp_path / "x", project_id="p1", now=NOW, example="pharma")
    with pytest.raises(InputError, match="事实库目录不存在"):
        load_store(tmp_path / "missing")
    (tmp_path / "bare").mkdir()
    with pytest.raises(InputError, match=r"缺少 store\.json"):
        load_store(tmp_path / "bare")
    for name, meta, message in (
        ("list", [], "不是 JSON 对象"),
        ("noid", {"project_id": "p1", "timezone": "UTC"}, "store_id 缺失或不合法"),
        ("notz", {"store_id": "s1", "project_id": "p1"}, "缺少 timezone"),
        ("badtz", {"store_id": "s1", "project_id": "p1", "timezone": "X/Y"}, "未知时区"),
    ):
        (tmp_path / name).mkdir()
        jsonl.write_json(tmp_path / name / "store.json", meta)
        with pytest.raises(InputError, match=message):
            load_meta(tmp_path / name)
    store = build_store(tmp_path / "store", [])
    bad = make_fact("F-BAD")
    del bad["zone"]
    with pytest.raises(InputError, match="F-BAD 不符合契约 fact"):
        commit_records(
            store, entities=[], facts=[bad], actor="测试", reason="坏记录", now=NOW, details={}
        )
    with pytest.raises(InputError, match="审计事件不符合契约"):
        append_event(
            store.paths.audit,
            project_id=store.project_id,
            actor=" ",
            action="import",
            target_id=store.store_id,
            reason="空白的变更人",
            occurred_at=NOW,
            before_hash=None,
            after_hash=None,
        )


def test_resolve_now_and_file_hash(tmp_path: Path) -> None:
    assert resolve_now(NOW) == NOW
    generated = resolve_now(None)
    assert generated.endswith("Z")
    assert len(generated) == 20
    with pytest.raises(InputError):
        resolve_now("2026-10-08")
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert file_hash(empty) is None
    assert file_hash(tmp_path / "missing.jsonl") is None
    assert StorePaths(tmp_path).audit == tmp_path / "audit.jsonl"


# ------------------------------------------------------------------ validate STORE


def test_validate_example_store_lists_core_without_evidence_and_conflict(tmp_path: Path) -> None:
    root = example_store(tmp_path)
    code, data = run_cli_json("validate", str(root), "--as-of", "2026-10-08")
    assert code == 1
    assert data["ok"] is False
    assert data["as_of"] == "2026-10-08"
    assert data["as_of_given"] is True
    assert [item["code"] for item in data["errors"]] == ["core_without_evidence", "conflict"]
    assert data["core_without_evidence"] == [{"fact_id": "F-M9-ACCEL", "version": 1}]
    assert len(data["conflicts"]) == 1
    code, out, _ = run_cli("validate", str(root), "--as-of", "2026-10-08")
    assert code == 1
    assert out.startswith("未通过：事实库 store-auto-demo（项目 auto-demo），10 个实体，25 条事实")
    assert "三区：事实 21，观点 2，营销表达 2" in out
    assert "2026-10-08 可作为确定依据 18 条，不可用 7 条；冲突 1 处；没有证据的核心事实 1 条" in out
    assert "[core_without_evidence]" in out
    assert "提示（" in out


def test_validate_clean_store_passes_and_defaults_to_today(tmp_path: Path) -> None:
    root = tmp_path / "store"
    build_store(root, [make_fact("F-S7-RANGE", is_core=True)])
    code, out, _ = run_cli("validate", str(root), "--as-of", "2026-10-08")
    assert code == 0
    assert out.startswith("通过：")
    assert "错误（" not in out
    code, data = run_cli_json("validate", str(root))
    assert code == 0
    assert data["as_of_given"] is False


def test_validate_argument_errors(tmp_path: Path) -> None:
    root = example_store(tmp_path)
    code, _, err = run_cli("validate", str(root), "--as-of", "2026/10/08")
    assert code == 2
    assert "--as-of" in err
    code, _, err = run_cli("validate", str(root), "--type", "fact")
    assert code == 2
    assert "是目录" in err
    code, _, err = run_cli("validate", str(root / "facts.jsonl"))
    assert code == 2
    assert "--type" in err
    code, _, err = run_cli(
        "validate", str(root / "facts.jsonl"), "--type", "fact", "--as-of", "2026-10-08"
    )
    assert code == 2
    assert "--as-of 只用于事实库检查" in err
    code, data = run_cli_json("validate", str(root / "facts.jsonl"), "--type", "fact")
    assert (code, data["records"], data["invalid"]) == (0, 26, 0)


# ------------------------------------------------------------------ conflicts


def test_conflicts_lists_the_example_conflict_without_ruling(tmp_path: Path) -> None:
    root = example_store(tmp_path)
    code, data = run_cli_json("conflicts", str(root))
    assert code == 1
    assert data["count"] == 1
    assert data["as_of"] is None
    assert "不自动裁决" in data["notice"]
    conflict = data["conflicts"][0]
    assert conflict["entity_id"] == "product-lanxu-e3"
    assert conflict["attribute"] == "整备质量"
    assert conflict["conditions"] == {}
    assert conflict["overlap"] == {"from": "2026-03-18", "until": None}
    assert [(item["fact_id"], item["version"], item["value"]) for item in conflict["facts"]] == [
        ("F-E3-WEIGHT-A", 1, 1850),
        ("F-E3-WEIGHT-B", 1, 1790),
    ]
    code, out, _ = run_cli("conflicts", str(root), "--as-of", "2026-10-08")
    assert code == 1
    assert "发现 1 处冲突（检查范围：2026-10-08）" in out
    assert "实体 product-lanxu-e3，属性 整备质量，条件 无，重叠时段 2026-03-18 至 未定" in out
    assert "F-E3-WEIGHT-A（版本 1）：1850 kg，来源等级 C" in out
    code, out, _ = run_cli("conflicts", str(root), "--as-of", "2026-03-17")
    assert (code, out.strip()) == (0, "未发现冲突（检查范围：2026-03-17）。")


def test_conflicts_text_shows_ranges_conditions_and_missing_evidence(tmp_path: Path) -> None:
    root = tmp_path / "store"
    first = make_fact(
        "F-A", value={"min": 600, "max": 650}, conditions={"test_cycle": "CLTC"}, source_tier="B"
    )
    second = make_fact("F-B", value=700, conditions={"test_cycle": "cltc"}, evidence=None)
    build_store(root, [first, second])
    code, out, _ = run_cli("conflicts", str(root))
    assert code == 1
    assert "条件 test_cycle=CLTC" in out
    assert "F-A（版本 1）：600~650 km，来源等级 B，证据 EV-S7-SPEC" in out
    assert "F-B（版本 1）：700 km，来源等级 C，无证据" in out


# ------------------------------------------------------------------ export


def test_export_csv_with_bom_and_as_of(tmp_path: Path) -> None:
    root = example_store(tmp_path)
    out_file = tmp_path / "out" / "facts.csv"
    code, data = run_cli_json("export", str(root), "--format", "csv", "--out", str(out_file))
    assert code == 0
    assert (data["count"], data["fact_ids"], data["format"]) == (26, 25, "csv")
    raw = out_file.read_bytes()
    assert raw.startswith(chr(0xFEFF).encode("utf-8"))
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8")[1:])))
    assert len(rows) == 26
    code, out, _ = run_cli(
        "export", str(root), "--format", "csv", "--as-of", "2026-03-18", "--out", str(out_file)
    )
    assert code == 0
    assert "已导出 22 条（2026-03-18 当日有效的版本，csv）" in out
    prices = list(csv.DictReader(io.StringIO(out_file.read_text(encoding="utf-8-sig"))))
    assert [row["value"] for row in prices if row["fact_id"] == "F-S7-PRICE"] == ["23.98"]


def test_export_jsonl_to_stdout_and_file(tmp_path: Path) -> None:
    root = example_store(tmp_path)
    code, out, _ = run_cli("export", str(root), "--as-of", "2026-10-08")
    assert code == 0
    lines = out.strip().splitlines()
    assert len(lines) == 25
    records = [json.loads(line) for line in lines]
    assert {record["fact_id"]: record["version"] for record in records}["F-S7-PRICE"] == 2
    code, data = run_cli_json("export", str(root))
    assert code == 0
    assert data["count"] == 26
    assert len(data["records"]) == 26
    target = tmp_path / "facts.jsonl"
    code, out, _ = run_cli("export", str(root), "--out", str(target))
    assert code == 0
    assert "已导出 26 条（全部版本，jsonl）" in out
    stored = sorted(
        jsonl.read_jsonl(root / "facts.jsonl"), key=lambda item: (item["fact_id"], item["version"])
    )
    assert jsonl.read_jsonl(target) == stored
