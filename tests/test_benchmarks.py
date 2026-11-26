"""benchmarks/：合成标注集生成器与基准脚本。

- 生成物逐条通过契约、数量达到门槛、同 seed 逐字节可重放、开发集与保留集不同；
- 金标自洽：span 能定位回原文；用测试里独立写的极简解析抽查数值与事实值（不 import geo_facts 的归一逻辑）；
- benchmarks/ 不 import geo_facts；
- 基准脚本的对齐与指标用伪造的判定结果做手工算例；注入类部分用真实子进程跑一个小样本。
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from geo_facts import contracts

BENCH = Path(__file__).resolve().parent.parent / "benchmarks"
NOW = "2026-10-08T02:00:00Z"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"test_bench_{name}", BENCH / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


generator = load("generate")
bench = load("run_benchmark")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("bench")
    out = {}
    for split in ("dev", "holdout"):
        generator.generate(split, root / split)
        out[split] = root / split
    return out


def golds_of(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path / "gold.jsonl")


def observations_of(path: Path) -> dict[str, dict[str, Any]]:
    return {item["observation_id"]: item for item in read_jsonl(path / "observations.jsonl")}


def facts_of(path: Path) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = json.loads(
        (path / "store-src" / "facts.json").read_text(encoding="utf-8")
    )
    return facts


def current(facts: list[dict[str, Any]], entity: str, attribute: str) -> list[dict[str, Any]]:
    as_of = generator.AS_OF
    return [
        f
        for f in facts
        if f["entity_id"] == entity
        and f["attribute"] == attribute
        and f["valid_from"] <= as_of
        and (f["valid_until"] is None or as_of <= f["valid_until"])
    ]


# ------------------------------------------------------------------ 生成物


@pytest.mark.parametrize("split", ["dev", "holdout"])
def test_generated_records_pass_contracts(data: dict[str, Path], split: str) -> None:
    path = data[split]
    for name, records in (
        ("entity", read_jsonl(path / "store-src" / "entities.jsonl")),
        ("fact", facts_of(path)),
        ("observation", list(observations_of(path).values())),
    ):
        assert records
        for record in records:
            assert contracts.iter_issues(name, record) == [], (name, record)
            assert record["synthetic"] is True
    for item in golds_of(path) + read_jsonl(path / "injection" / "labels.jsonl"):
        assert item["synthetic"] is True
    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    assert meta["synthetic"] is True
    assert meta["split"] == split


@pytest.mark.parametrize("split", ["dev", "holdout"])
def test_counts_meet_the_required_sizes(data: dict[str, Path], split: str) -> None:
    path = data[split]
    golds = golds_of(path)
    expected = {
        label: sum(1 for g in golds if g["expected"] == label)
        for label in ("supported", "contradicted", "unverifiable")
    }
    assert len(golds) >= 300
    assert sum(1 for g in golds if g["hard"]) >= 100
    assert expected["contradicted"] >= 100
    assert expected["supported"] >= 100
    assert expected["unverifiable"] >= 50
    assert {g["category"] for g in golds} == {"numeric", "model", "date"}
    assert all(g["hard"] == (g["hard_kind"] is not None) for g in golds)
    labels = read_jsonl(path / "injection" / "labels.jsonl")
    assert sum(1 for item in labels if item["label"] == "attack") >= 40
    assert sum(1 for item in labels if item["label"] == "benign") >= 60
    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    assert meta["counts"]["gold"] == len(golds)
    assert meta["counts"]["attack"] + meta["counts"]["benign"] == len(labels)
    observations = observations_of(path)
    per_observation: dict[str, int] = {}
    for g in golds:
        per_observation[g["observation_id"]] = per_observation.get(g["observation_id"], 0) + 1
    assert set(per_observation) == set(observations)
    assert max(per_observation.values()) <= 4


def test_required_hard_kinds_are_present(data: dict[str, Path]) -> None:
    required = {
        "unit_conversion",
        "approx",
        "range",
        "chinese_numeral",
        "thousands_separator",
        "width_case",
        "condition_differs",
        "expired_version",
        "competitor_same_name",
        "date_precision",
        "entity_omission",
        "multi_claim",
        "tolerance_boundary",
    }
    for split in ("dev", "holdout"):
        kinds = {g["hard_kind"] for g in golds_of(data[split])}
        assert required <= kinds


def digest(path: Path) -> dict[str, str]:
    return {
        str(item.relative_to(path)): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def test_same_split_and_seed_replay_byte_for_byte(data: dict[str, Path], tmp_path: Path) -> None:
    generator.generate("dev", tmp_path / "again")
    assert digest(tmp_path / "again") == digest(data["dev"])
    generator.generate("dev", tmp_path / "other-seed", seed=1)
    assert digest(tmp_path / "other-seed") != digest(data["dev"])


def test_dev_and_holdout_differ(data: dict[str, Path]) -> None:
    dev_meta = json.loads((data["dev"] / "meta.json").read_text(encoding="utf-8"))
    holdout_meta = json.loads((data["holdout"] / "meta.json").read_text(encoding="utf-8"))
    assert dev_meta["seed"] != holdout_meta["seed"]
    dev_golds, holdout_golds = golds_of(data["dev"]), golds_of(data["holdout"])
    assert {g["text"] for g in dev_golds} != {g["text"] for g in holdout_golds}
    dev_pairs = {(g["entity_id"], g["attribute"]) for g in dev_golds}
    holdout_pairs = {(g["entity_id"], g["attribute"]) for g in holdout_golds}
    assert holdout_pairs - dev_pairs  # 保留集有开发集没有的实体 / 属性组合
    dev_entities = {
        e["entity_id"] for e in read_jsonl(data["dev"] / "store-src" / "entities.jsonl")
    }
    holdout_entities = {
        e["entity_id"] for e in read_jsonl(data["holdout"] / "store-src" / "entities.jsonl")
    }
    assert holdout_entities - dev_entities
    dev_values = {(f["entity_id"], f["attribute"]): f["value"] for f in facts_of(data["dev"])}
    holdout_values = {
        (f["entity_id"], f["attribute"]): f["value"] for f in facts_of(data["holdout"])
    }
    shared = set(dev_values) & set(holdout_values)
    assert shared
    assert any(dev_values[key] != holdout_values[key] for key in shared)


@pytest.mark.parametrize("split", ["dev", "holdout"])
def test_gold_spans_locate_the_sentence(data: dict[str, Path], split: str) -> None:
    observations = observations_of(data[split])
    for g in golds_of(data[split]):
        text = observations[g["observation_id"]]["answer_text"]
        start, end = g["span"]
        assert 0 <= start < end <= len(text)
        assert text[start:end] == g["text"]
        assert g["text"].endswith("。") or g["text"].startswith("- ")


def test_facts_csv_matches_facts_json(data: dict[str, Path]) -> None:
    import csv

    with (data["dev"] / "store-src" / "facts.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    facts = facts_of(data["dev"])
    assert len(rows) == len(facts)
    for row, fact in zip(rows, facts, strict=True):
        assert row["fact_id"] == fact["fact_id"]
        assert row["attribute"] == fact["attribute"]
        assert row["value"] == str(fact["value"])
        assert row["valid_until"] == (fact["valid_until"] or "")
        conditions = dict(item.split("=", 1) for item in row["conditions"].split(";") if item)
        assert conditions == fact["conditions"]


# ------------------------------------------------------------------ 金标自洽（独立的极简解析）

NUMBER = re.compile(r"(?<![A-Za-z0-9.\-])\d+(?:\.\d+)?(?![\d.])")
PLAIN_ATTRS = ("续航里程", "售价", "百公里加速", "电池容量")


def numbers_in(text: str) -> set[Decimal]:
    cleaned = text.replace("0-100km/h", " ")
    return {Decimal(item) for item in NUMBER.findall(cleaned)}


@pytest.mark.parametrize("split", ["dev", "holdout"])
def test_plain_numeric_golds_agree_with_the_fact_values(data: dict[str, Path], split: str) -> None:
    facts = facts_of(data[split])
    checked = {"supported": 0, "contradicted": 0}
    for g in golds_of(data[split]):
        if g["hard"] or g["attribute"] not in PLAIN_ATTRS or g["expected"] == "unverifiable":
            continue
        rows = current(facts, g["entity_id"], g["attribute"])
        if g["attribute"] == "续航里程":
            rows = [f for f in rows if f["conditions"].get("test_cycle") == "CLTC"]
        assert len(rows) == 1, g
        value = Decimal(str(rows[0]["value"]))
        found = numbers_in(g["text"])
        assert found, g
        if g["expected"] == "supported":
            assert value in found, g
        else:
            assert value not in found, g
        checked[g["expected"]] += 1
    # 抽查样本量的下限（保留集的普通题分散在更多属性上，所以不设得太高）
    assert checked["supported"] >= 20
    assert checked["contradicted"] >= 20


def written_place(written: str) -> Decimal:
    if "." in written:
        return Decimal(1).scaleb(-len(written.split(".")[1]))
    return Decimal(10) ** (len(written) - len(written.rstrip("0")))


@pytest.mark.parametrize("split", ["dev", "holdout"])
def test_approx_golds_follow_the_documented_tolerance(data: dict[str, Path], split: str) -> None:
    facts = facts_of(data[split])
    seen = 0
    for g in golds_of(data[split]):
        match = re.search(r"(约|大约|约为)\s*(\d+)|(\d+)\s*(?:公里|km)\s*(左右|上下)", g["text"])
        if g["hard_kind"] not in ("approx", "tolerance_boundary") or match is None:
            continue
        written = match.group(2) or match.group(3)
        x = Decimal(written)
        tolerance = max(written_place(written) / 2, x * Decimal("0.02"))
        fact = next(
            f
            for f in current(facts, g["entity_id"], "续航里程")
            if f["conditions"]["test_cycle"] == "CLTC"
        )
        distance = abs(Decimal(str(fact["value"])) - x)
        assert (distance <= tolerance) == (g["expected"] == "supported"), g
        assert not (Decimal("0.9") * tolerance < distance < Decimal("1.1") * tolerance), g
        seen += 1
    assert seen >= 8


@pytest.mark.parametrize("split", ["dev", "holdout"])
def test_special_kinds_are_consistent(data: dict[str, Path], split: str) -> None:
    facts = facts_of(data[split])
    for g in golds_of(data[split]):
        if g["condition_differs"]:
            assert g["expected"] == "unverifiable"
            assert g["hard_kind"] == "condition_differs"
            cycles = {
                f["conditions"].get("test_cycle")
                for f in current(facts, g["entity_id"], g["attribute"])
            }
            for cycle in ("WLTC", "NEDC"):
                if cycle in g["text"]:
                    assert cycle not in cycles, g
        if g["hard_kind"] == "expired_version":
            assert g["expected"] == "contradicted"
            expired = [
                f
                for f in facts
                if f["entity_id"] == g["entity_id"]
                and f["attribute"] == "售价"
                and f["valid_until"] is not None
                and f["valid_until"] < generator.AS_OF
            ]
            assert Decimal(str(expired[0]["value"])) in numbers_in(g["text"])
        if g["hard_kind"] == "competitor_same_name":
            assert g["entity_id"] == "product-cenye-s7"
            assert re.search("岑野|cenye", g["text"], re.IGNORECASE)


def test_independent_chinese_numerals() -> None:
    assert generator.cn_int(620) == "六百二十"
    assert generator.cn_int(219800) == "二十一万九千八百"
    assert generator.cn_int(105800) == "十万五千八百"
    assert generator.cn_int(100800) == "十万零八百"
    assert generator.cn_int(1005) == "一千零五"
    assert generator.cn_int(15) == "十五"
    assert generator.cn_int(0) == "零"
    assert generator.cn_decimal(Decimal("5.9")) == "五点九"
    assert generator.cn_year(2026) == "二〇二六"
    assert generator.thousands(219800) == "219,800"
    assert generator.approx_tolerance("620") == Decimal("12.4")
    assert generator.approx_tolerance("600") == Decimal("50")
    assert generator.last_place("5.9") == Decimal("0.1")


# ------------------------------------------------------------------ 注入类样本


@pytest.mark.parametrize("split", ["dev", "holdout"])
def test_injection_samples(data: dict[str, Path], split: str) -> None:
    folder = data[split] / "injection"
    labels = read_jsonl(folder / "labels.jsonl")
    for item in labels:
        assert (folder / item["file"]).is_file()
        assert item["file"].startswith(item["label"] + "-")
    for label in ("attack", "benign"):
        formats = {item["file"].rsplit(".", 1)[1] for item in labels if item["label"] == label}
        assert formats == {"md", "html", "txt"}
    attack_kinds = {item["kind"] for item in labels if item["label"] == "attack"}
    benign_kinds = {item["kind"] for item in labels if item["label"] == "benign"}
    assert len(attack_kinds) >= 15
    assert {
        "prompt_engineering_article",
        "injection_defense_article",
        "collapsible_menu",
        "css_code_example",
        "emoji_zwj",
        "normal_must_notice",
        "quoted_counterexample",
    } <= benign_kinds


@pytest.mark.parametrize("split", ["dev", "holdout"])
def test_generated_data_uses_only_reserved_domains(data: dict[str, Path], split: str) -> None:
    for path in data[split].rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            for host in re.findall(r"https?://([^/\s\"'<>]+)", text):
                assert host.endswith((".example", ".test")), (path.name, host)


def test_benchmarks_do_not_import_geo_facts() -> None:
    sources = sorted(BENCH.glob("*.py"))
    assert {path.name for path in sources} >= {"generate.py", "run_benchmark.py"}
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(name.split(".")[0] == "geo_facts" for name in names), path.name
        assert 'import_module("geo_facts' not in path.read_text(encoding="utf-8")


# ------------------------------------------------------------------ 对齐与指标（手工算例）


def gold(
    gid: str,
    expected: str,
    span: tuple[int, int] = (0, 10),
    *,
    obs: str = "o1",
    entity: str = "e1",
    attribute: str = "续航里程",
    condition: bool = False,
    category: str = "numeric",
) -> dict[str, Any]:
    return {
        "id": gid,
        "observation_id": obs,
        "span": list(span),
        "entity_id": entity,
        "attribute": attribute,
        "category": category,
        "expected": expected,
        "hard": condition,
        "hard_kind": "condition_differs" if condition else None,
        "condition_differs": condition,
    }


def claim(
    cid: str,
    span: tuple[int, int],
    *,
    obs: str = "o1",
    entity: str = "e1",
    attribute: str = "续航里程",
) -> dict[str, Any]:
    return {
        "claim_id": cid,
        "source": {"type": "observation", "id": obs},
        "span": {"start": span[0], "end": span[1]},
        "entity_id": entity,
        "attribute": attribute,
    }


def judgment(
    jid: str, cid: str | None, label: str, *, obs: str = "o1", direction: str = "answer_to_facts"
) -> dict[str, Any]:
    return {
        "judgment_id": jid,
        "claim_id": cid,
        "label": label,
        "direction": direction,
        "source": {"type": "observation", "id": obs},
    }


def test_aggregate_rule() -> None:
    assert bench.aggregate(["supported", "contradicted", "unverifiable"]) == "contradicted"
    assert bench.aggregate(["unverifiable", "supported"]) == "supported"
    assert bench.aggregate(["unverifiable"]) == "unverifiable"
    assert bench.aggregate([]) == "unverifiable"


def test_align_by_span_containment() -> None:
    golds = [gold("g1", "supported", (0, 10)), gold("g2", "contradicted", (10, 20))]
    claims = [
        claim("c1", (2, 8)),
        claim("c2", (12, 18)),
        claim("c3", (11, 19)),
        claim("c4", (5, 15)),  # 跨两句：不落在任何一句内
        claim("c5", (2, 8), obs="o2"),  # 别的观测
    ]
    judgments = [
        judgment("j1", "c1", "supported"),
        judgment("j2", "c2", "supported"),
        judgment("j3", "c3", "contradicted"),
        judgment("j4", "c4", "contradicted"),
        judgment("j5", "c5", "contradicted", obs="o2"),
        judgment("j6", None, "missing", direction="facts_to_answer"),
        judgment("j7", "c-unknown", "contradicted"),
    ]
    aligned = bench.align(golds, claims, judgments)
    assert aligned["g1"] == {"predicted": "supported", "judgment_ids": ["j1"]}
    assert aligned["g2"] == {"predicted": "contradicted", "judgment_ids": ["j2", "j3"]}


def test_align_without_judgments_is_unverifiable() -> None:
    aligned = bench.align([gold("g1", "contradicted")], [], [])
    assert aligned["g1"] == {"predicted": "unverifiable", "judgment_ids": []}


def test_align_one_sentence_with_several_golds() -> None:
    golds = [
        gold("g1", "supported", (0, 30), entity="e1", attribute="续航里程"),
        gold("g2", "contradicted", (0, 30), entity="e1", attribute="售价"),
    ]
    claims = [
        claim("c1", (0, 12), entity="e1", attribute="续航里程"),
        claim("c2", (13, 29), entity="e1", attribute="售价"),
        claim("c3", (20, 25), entity=None, attribute=None),  # type: ignore[arg-type]
    ]
    judgments = [
        judgment("j1", "c1", "supported"),
        judgment("j2", "c2", "contradicted"),
        judgment("j3", "c3", "unverifiable"),
    ]
    aligned = bench.align(golds, claims, judgments)
    # 实体属性相同者优先；c3 没有实体属性，归给该句全部金标
    assert aligned["g1"] == {"predicted": "supported", "judgment_ids": ["j1", "j3"]}
    assert aligned["g2"] == {"predicted": "contradicted", "judgment_ids": ["j2", "j3"]}


def test_judgment_metrics_by_hand() -> None:
    # 金标：矛盾 4 条（判对 3 条）、支持 3 条（其中 1 条被判成矛盾）、条件不同 2 条（1 条被判成矛盾）
    golds = [
        gold("c1", "contradicted"),
        gold("c2", "contradicted"),
        gold("c3", "contradicted"),
        gold("c4", "contradicted"),
        gold("s1", "supported"),
        gold("s2", "supported"),
        gold("s3", "supported"),
        gold("u1", "unverifiable", condition=True),
        gold("u2", "unverifiable", condition=True),
        gold("f1", "unverifiable", category="free_text"),
    ]
    predicted = {
        "c1": "contradicted",
        "c2": "contradicted",
        "c3": "contradicted",
        "c4": "unverifiable",
        "s1": "supported",
        "s2": "contradicted",
        "s3": "unverifiable",
        "u1": "contradicted",
        "u2": "unverifiable",
        "f1": "contradicted",
    }
    aligned = {gid: {"predicted": label, "judgment_ids": []} for gid, label in predicted.items()}
    metrics = bench.judgment_metrics(golds, aligned)
    assert metrics["golds"] == 9  # free_text 不计入
    assert metrics["excluded_categories"] == 1
    assert metrics["contradiction_recall"] == pytest.approx(3 / 4)
    assert metrics["contradiction_precision"] == pytest.approx(3 / 5)  # c1 c2 c3 s2 u1
    assert metrics["condition_false_contradiction_rate"] == pytest.approx(1 / 2)
    assert metrics["agreement"] == pytest.approx(5 / 9)  # c1 c2 c3 s1 u2
    assert metrics["failures"]["missed_contradictions"] == ["c4"]
    assert metrics["failures"]["false_contradictions"] == ["s2", "u1"]
    assert metrics["failures"]["condition_false_contradictions"] == ["u1"]
    assert metrics["failures"]["other_disagreements"] == ["s3"]
    assert metrics["confusion"]["supported"] == {
        "supported": 1,
        "contradicted": 1,
        "unverifiable": 1,
    }
    assert metrics["by_kind"]["condition_differs"] == {"total": 2, "correct": 1}


def test_judgment_metrics_with_empty_denominators() -> None:
    metrics = bench.judgment_metrics([gold("s1", "supported")], {})
    assert metrics["contradiction_recall"] is None
    assert metrics["contradiction_precision"] is None
    assert metrics["condition_false_contradiction_rate"] is None


def outcome(code: int, data: Any = None, stderr: str = "") -> Any:
    stdout = data if isinstance(data, str) else json.dumps(data or {})
    return bench.Outcome(code, stdout, stderr)


def test_gate_outcome_parsing() -> None:
    item = {"file": "attack-001.html", "label": "attack", "kind": "x"}
    findings = {
        "status": "block",
        "findings": [
            {"rule_id": "G-HIDDEN-TEXT", "result": "block"},
            {"rule_id": "G-TIME-FORGED", "result": "block"},
            {"rule_id": "G-LEXICON", "result": "review"},
        ],
    }
    assert bench.gate_outcome(item, outcome(1, findings))["blocking_rules"] == ["G-HIDDEN-TEXT"]
    assert bench.gate_outcome(item, outcome(5, {"findings": []}))["blocking_rules"] == []
    assert "error" in bench.gate_outcome(item, outcome(4, {}, "Traceback"))
    assert bench.gate_outcome(item, outcome(0, "not json"))["error"] == "闸门输出不是 JSON"


def test_injection_metrics_by_hand() -> None:
    results = [
        {
            "file": "attack-001.md",
            "label": "attack",
            "kind": "a",
            "blocking_rules": ["G-HIDDEN-TEXT"],
        },
        {"file": "attack-002.md", "label": "attack", "kind": "a", "blocking_rules": []},
        {"file": "attack-003.md", "label": "attack", "kind": "b", "exit_code": 4, "error": "boom"},
        {
            "file": "attack-004.md",
            "label": "attack",
            "kind": "b",
            "blocking_rules": ["G-PROMPT-INJECTION"],
        },
        {"file": "benign-001.md", "label": "benign", "kind": "c", "blocking_rules": []},
        {
            "file": "benign-002.md",
            "label": "benign",
            "kind": "c",
            "blocking_rules": ["G-HIDDEN-TEXT"],
        },
    ]
    metrics = bench.injection_metrics(results)
    assert metrics["attack_detection_rate"] == pytest.approx(2 / 4)
    assert metrics["benign_false_block_rate"] == pytest.approx(1 / 2)
    assert metrics["errors"] == 1
    assert metrics["failures"]["missed_attacks"] == ["attack-002.md", "attack-003.md"]
    assert metrics["failures"]["false_blocks"] == ["benign-002.md"]
    assert metrics["by_kind"]["attack:b"] == {"total": 2, "flagged": 1}


def test_thresholds_at_the_boundary() -> None:
    parts = {
        "judgment": {
            "status": "ok",
            "metrics": {
                "contradiction_recall": 0.95,
                "contradiction_precision": 0.9499,
                "condition_false_contradiction_rate": 0.02,
            },
        },
        "injection": {
            "status": "ok",
            "metrics": {"attack_detection_rate": 1.0, "benign_false_block_rate": None, "errors": 0},
        },
    }
    checks = {c["metric"]: c for c in bench.check_thresholds(parts)}
    assert checks["contradiction_recall"]["passed"] is True
    assert checks["contradiction_precision"]["passed"] is False
    assert checks["condition_false_contradiction_rate"]["passed"] is True
    assert checks["attack_detection_rate"]["passed"] is True
    assert checks["benign_false_block_rate"]["passed"] is False
    assert checks["benign_false_block_rate"]["note"] == "分母为 0，无法计算"
    parts["injection"]["metrics"]["errors"] = 2
    assert not any(c["passed"] for c in bench.check_thresholds(parts) if c["part"] == "injection")
    failed = bench.check_thresholds(
        {"judgment": {"status": "error"}, "injection": {"status": "skipped"}}
    )
    assert [c["part"] for c in failed] == ["judgment"] * 3
    assert all(c["note"] == "该部分运行失败" for c in failed)


# ------------------------------------------------------------------ 端到端（伪造的 CLI）


class FakeCLI:
    """模拟 geo-facts 子命令：judge 按 predict(gold) 给出判定，gate 按文件名给出结果。"""

    def __init__(
        self, predict: Any = None, fail: str | None = None, false_block: str | None = None
    ):
        self.predict = predict or (lambda g: g["expected"])
        self.fail = fail
        self.false_block = false_block
        self.calls: list[list[str]] = []

    def __call__(self, args: Any) -> Any:
        args = list(args)
        self.calls.append(args)
        command = args[0]
        if command == self.fail:
            return bench.Outcome(2, "", f"输入错误：invalid choice: '{command}'")
        if command == "init":
            Path(args[1]).mkdir(parents=True)
            return bench.Outcome(0, "{}", "")
        if command == "import":
            return bench.Outcome(0, "{}", "")
        if command == "judge":
            data = Path(args[args.index("--observations") + 1]).parent
            claims, judgments = [], []
            for g in read_jsonl(data / "gold.jsonl"):
                label = self.predict(g)
                if label is None:
                    continue
                claims.append(
                    claim(
                        f"c-{g['id']}",
                        tuple(g["span"]),
                        obs=g["observation_id"],
                        entity=g["entity_id"],
                        attribute=g["attribute"],
                    )
                )
                judgments.append(
                    judgment(f"j-{g['id']}", f"c-{g['id']}", label, obs=g["observation_id"])
                )
            out = Path(args[args.index("--out") + 1])
            out.write_text("".join(json.dumps(j) + "\n" for j in judgments), encoding="utf-8")
            claims_out = Path(args[args.index("--claims-out") + 1])
            claims_out.write_text("".join(json.dumps(c) + "\n" for c in claims), encoding="utf-8")
            return bench.Outcome(0, "{}", "")
        assert command == "gate"
        name = Path(args[args.index("--draft") + 1]).name
        blocked = name.startswith("attack-") or name == self.false_block
        findings = [{"rule_id": "G-HIDDEN-TEXT", "result": "block"}] if blocked else []
        return bench.Outcome(1 if blocked else 0, json.dumps({"findings": findings}), "")


COMMIT = {"commit": "abc1234", "dirty": False}


def test_run_with_a_perfect_fake_passes_and_records(tmp_path: Path) -> None:
    runs = tmp_path / "runs.jsonl"
    fake = FakeCLI()
    result = bench.run(
        "dev", workdir=tmp_path / "work", now=NOW, runner=fake, runs_file=runs, commit=COMMIT
    )
    assert result["passed"] is True
    assert result["complete"] is True
    judgment_metrics = result["parts"]["judgment"]["metrics"]
    assert judgment_metrics["contradiction_recall"] == 1.0
    assert judgment_metrics["contradiction_precision"] == 1.0
    assert judgment_metrics["agreement"] == 1.0  # 一句多陈述也按实体属性分开对齐
    assert result["parts"]["injection"]["metrics"]["attack_detection_rate"] == 1.0
    store_entities = (tmp_path / "work" / "store" / "entities.jsonl").read_text(encoding="utf-8")
    assert "product-lanxu-s7" in store_entities
    assert [call[0] for call in fake.calls[:3]] == ["init", "import", "judge"]
    records = read_jsonl(runs)
    assert len(records) == 1
    assert records[0]["time"] == NOW
    assert records[0]["commit"] == "abc1234"
    assert records[0]["passed"] is True
    assert records[0]["holdout_details_viewed"] is False
    assert "合成集结果只能说明实现符合预期" in bench.render(result)


def test_run_reports_failures_on_dev(tmp_path: Path) -> None:
    def predict(g: dict[str, Any]) -> str | None:
        if g["condition_differs"]:
            return "contradicted"
        return None if g["expected"] == "contradicted" else g["expected"]

    result = bench.run(
        "dev",
        workdir=tmp_path / "work",
        now=NOW,
        runner=FakeCLI(predict, false_block="benign-001.md"),
        runs_file=tmp_path / "runs.jsonl",
        commit=COMMIT,
    )
    assert result["passed"] is False
    metrics = result["parts"]["judgment"]["metrics"]
    assert metrics["contradiction_recall"] == 0.0
    assert metrics["condition_false_contradiction_rate"] == 1.0
    assert metrics["failures"]["condition_false_contradictions"]
    assert result["parts"]["injection"]["metrics"]["failures"]["false_blocks"] == ["benign-001.md"]
    text = bench.render(result)
    assert "未达门槛" in text
    assert "benign-001.md" in text


def test_holdout_hides_details_unless_asked(tmp_path: Path) -> None:
    runs = tmp_path / "runs.jsonl"
    common = {
        "now": NOW,
        "runner": FakeCLI(false_block="benign-002.md"),
        "runs_file": runs,
        "commit": COMMIT,
    }
    hidden = bench.run("holdout", workdir=tmp_path / "a", **common)
    assert hidden["details_shown"] is False
    assert "failures" not in hidden["parts"]["judgment"]["metrics"]
    assert "by_kind" not in hidden["parts"]["injection"]["metrics"]
    assert "benign-002.md" not in json.dumps(hidden, ensure_ascii=False)
    assert "benign-002.md" not in bench.render(hidden)
    shown = bench.run("holdout", workdir=tmp_path / "b", show_failures=True, **common)
    assert shown["holdout_details_viewed"] is True
    assert "已查看保留集明细" in bench.render(shown)
    records = read_jsonl(runs)
    assert [r["holdout_details_viewed"] for r in records] == [False, True]
    assert [r["split"] for r in records] == ["holdout", "holdout"]


def test_failed_cli_step_fails_the_run(tmp_path: Path) -> None:
    result = bench.run(
        "dev",
        workdir=tmp_path / "work",
        now=NOW,
        only="judgment",
        runner=FakeCLI(fail="init"),
        runs_file=tmp_path / "runs.jsonl",
        commit=COMMIT,
    )
    assert result["passed"] is False
    assert result["complete"] is False
    assert result["parts"]["judgment"]["error"]["step"] == "init"
    assert result["parts"]["injection"] == {"status": "skipped"}
    text = bench.render(result)
    assert "运行失败——步骤 init 退出码 2" in text
    assert "注入类：未运行" in text


def test_only_injection_marks_the_run_incomplete(tmp_path: Path) -> None:
    result = bench.run(
        "dev",
        workdir=tmp_path / "w",
        now=NOW,
        only="injection",
        runner=FakeCLI(),
        runs_file=tmp_path / "runs.jsonl",
        commit=COMMIT,
    )
    assert result["passed"] is True
    assert result["complete"] is False
    assert {c["part"] for c in result["thresholds"]} == {"injection"}


def test_workdir_safety(tmp_path: Path) -> None:
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit, match="拒绝覆盖"):
        bench.prepare_workdir(precious)
    assert (precious / "keep.txt").exists()
    work = bench.prepare_workdir(tmp_path / "work")
    (work / "old.txt").write_text("x", encoding="utf-8")
    bench.prepare_workdir(work)
    assert not (work / "old.txt").exists()
    assert (work / bench.MARKER).exists()


def test_main_exit_codes_and_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(bench, "cli_runner", FakeCLI())
    monkeypatch.setattr(bench, "RUNS_FILE", tmp_path / "runs.jsonl")
    monkeypatch.setattr(bench, "git_commit", lambda: COMMIT)
    code = bench.main(["--split", "dev", "--workdir", str(tmp_path / "w"), "--now", NOW, "--json"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True
    monkeypatch.setattr(bench, "cli_runner", FakeCLI(fail="judge"))
    code = bench.main(["--split", "dev", "--workdir", str(tmp_path / "w"), "--now", NOW])
    assert code == 1
    assert "步骤 judge" in capsys.readouterr().out
    assert len(read_jsonl(tmp_path / "runs.jsonl")) == 2


def test_git_commit_reports_the_repository() -> None:
    info = bench.git_commit()
    assert set(info) == {"commit", "dirty"}
    assert info["commit"]


# ------------------------------------------------------------------ 注入类：真实子进程（小样本）


def test_injection_part_through_real_cli(data: dict[str, Path], tmp_path: Path) -> None:
    source = data["dev"] / "injection"
    labels = read_jsonl(source / "labels.jsonl")
    sample = [
        next(item for item in labels if item["kind"] == "css_display_none"),
        next(item for item in labels if item["kind"] == "collapsible_menu"),
    ]
    folder = tmp_path / "data" / "injection"
    folder.mkdir(parents=True)
    for item in sample:
        (folder / item["file"]).write_bytes((source / item["file"]).read_bytes())
    (folder / "labels.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in sample), encoding="utf-8"
    )
    part = bench.run_injection(tmp_path / "data", bench.cli_runner, NOW, jobs=2)
    metrics = part["metrics"]
    assert metrics["errors"] == 0
    assert metrics["attack_detection_rate"] == 1.0
    assert metrics["benign_false_block_rate"] == 0.0
