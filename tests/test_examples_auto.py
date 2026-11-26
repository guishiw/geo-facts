"""虚构汽车品牌完整样例（init --example auto 与 examples/auto/run.sh）的端到端测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from geo_facts import contracts
from geo_facts.store.writer import load_example

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = REPO_ROOT / "examples" / "auto"
RUN_SCRIPT = EXAMPLE_DIR / "run.sh"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_builtin_auto_example_is_complete_and_synthetic() -> None:
    """内置 --example auto 含带条件续航、过期旧价、三区条目、无证据核心事实与冲突，全部合成。"""
    entities, facts = load_example("auto", "auto-demo")
    entity_ids = {item["entity_id"] for item in entities}
    assert {"brand-lanxu", "product-lanxu-s7", "brand-cenye", "product-cenye-s7"} <= entity_ids
    assert all(item.get("synthetic") is True for item in entities)
    assert all(item.get("synthetic") is True for item in facts)

    zones = {item["zone"] for item in facts}
    assert zones == {"fact", "opinion", "marketing"}

    # 带条件的续航（CLTC 与 WLTC）
    s7_ranges = [
        item
        for item in facts
        if item["entity_id"] == "product-lanxu-s7" and item["attribute"] == "续航里程"
    ]
    cycles = {item["conditions"].get("test_cycle") for item in s7_ranges}
    assert {"CLTC", "WLTC"} <= cycles

    # 过期旧价与现行售价两个版本
    s7_prices = sorted(
        (item for item in facts if item["fact_id"] == "F-S7-PRICE"),
        key=lambda item: int(item["version"]),
    )
    assert len(s7_prices) == 2
    assert s7_prices[0]["value"] == 23.98
    assert s7_prices[0]["valid_until"] == "2026-08-31"
    assert s7_prices[1]["value"] == 21.98
    assert s7_prices[1]["valid_until"] is None

    # 至少一条无证据核心事实
    core_no_ev = [
        item
        for item in facts
        if item["zone"] == "fact" and item["is_core"] and item["evidence"] is None
    ]
    assert core_no_ev

    # 至少一对冲突（同实体、同属性、同条件、不同值）
    weight_ids = {item["fact_id"] for item in facts if item["attribute"] == "整备质量"}
    assert {"F-E3-WEIGHT-A", "F-E3-WEIGHT-B"} <= weight_ids


def test_example_input_files_conform_to_contracts() -> None:
    """examples/auto 下的观察样本和封存题库均通过契约校验并标记 synthetic。"""
    observations = _read_jsonl(EXAMPLE_DIR / "observations.jsonl")
    assert len(observations) == 4
    for idx, obs in enumerate(observations, start=1):
        contracts.require_valid("observation", obs, where=f"observations.jsonl:{idx}")
        assert obs.get("synthetic") is True
        for cite in obs["citations"]:
            assert cite["domain"].endswith((".example", ".test"))

    prompt_set = _read_json(EXAMPLE_DIR / "prompt_set.json")
    contracts.require_valid("prompt_set", prompt_set, where="prompt_set.json")
    assert prompt_set.get("synthetic") is True
    assert any(prompt.get("core_fact_ids") for prompt in prompt_set["prompts"])


def test_example_run_script_end_to_end(tmp_path: Path) -> None:
    """通过 CLI 跑通 examples/auto/run.sh 全流程并断言关键核验与闸门结果。"""
    assert RUN_SCRIPT.is_file()
    assert os.access(RUN_SCRIPT, os.X_OK), "examples/auto/run.sh 必须具有可执行权限"

    out_dir = tmp_path / "auto-out"
    cli_bin = Path(sys.executable).parent / "geo-facts"
    env = dict(os.environ)
    env["GEO_FACTS"] = str(cli_bin)

    completed = subprocess.run(
        ["sh", str(RUN_SCRIPT), str(out_dir)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"

    expected_outputs = [
        "store/store.json",
        "store/entities.jsonl",
        "store/facts.jsonl",
        "store/audit.jsonl",
        "validate-store.json",
        "conflicts.json",
        "facts-as-of.csv",
        "claims-extracted.jsonl",
        "claims.jsonl",
        "judgments.jsonl",
        "judge-summary.json",
        "calibrate.json",
        "gate.json",
    ]
    for rel in expected_outputs:
        assert (out_dir / rel).is_file(), f"缺少输出文件：{rel}"

    # 1. validate 与 conflicts 检出样例内置的无证据核心事实与冲突
    validate_data = _read_json(out_dir / "validate-store.json")
    assert validate_data["ok"] is False
    error_codes = {item["code"] for item in validate_data["errors"]}
    assert {"core_without_evidence", "conflict"} <= error_codes

    conflicts_data = _read_json(out_dir / "conflicts.json")
    assert conflicts_data["count"] == 1
    conflict_ids = {item["fact_id"] for item in conflicts_data["conflicts"][0]["facts"]}
    assert conflict_ids == {"F-E3-WEIGHT-A", "F-E3-WEIGHT-B"}

    # 2. judge：过期价格判矛盾、错误参数判矛盾、不同工况不判矛盾、呈现率完整
    summary = _read_json(out_dir / "judge-summary.json")
    a2f = summary["answer_to_facts"]
    assert a2f["supported"] == 7
    assert a2f["contradicted"] == 3
    assert a2f["stale_contradictions"] == 1
    assert a2f["unverifiable"] == 1
    assert a2f["total"] == 11
    assert a2f["accuracy_verifiable"] == pytest.approx(7 / 10)
    assert a2f["accuracy_strict"] == pytest.approx(7 / 11)

    f2a = summary["facts_to_answer"]
    assert f2a["present_correct"] == 4
    assert f2a["present_wrong"] == 2
    assert f2a["missing"] == 1
    assert f2a["total"] == 7
    assert f2a["presence_rate"] == pytest.approx(4 / 7)

    judgments = _read_jsonl(out_dir / "judgments.jsonl")
    assert len(judgments) == 18
    for item in judgments:
        contracts.require_valid("judgment", item, where=item["judgment_id"])

    ans_judgments = [item for item in judgments if item["direction"] == "answer_to_facts"]
    # 过期价格（23.98 万元）判为 contradicted，且标明 stale_match
    stale_items = [
        item for item in ans_judgments if item["ext"].get("reason_code") == "stale_match"
    ]
    assert len(stale_items) == 1
    assert stale_items[0]["label"] == "contradicted"
    assert stale_items[0]["fact_id"] == "F-S7-PRICE"
    assert "过期" in stale_items[0]["reason"]

    # 不同测试工况（NEDC）不判矛盾，判为 unverifiable 且标明 condition_differs
    cond_items = [
        item for item in ans_judgments if item["ext"].get("reason_code") == "condition_differs"
    ]
    assert len(cond_items) == 1
    assert cond_items[0]["label"] == "unverifiable"
    assert "条件不同" in cond_items[0]["reason"]

    # 3. calibrate：样本量不足以证明 98% 准确率，返回判定精度不足
    cal = _read_json(out_dir / "calibrate.json")
    assert cal["counts"]["aligned"] == 20
    assert cal["counts"]["errors"] == 1
    assert cal["conclusion"]["code"] == "insufficient_precision"
    assert cal["conclusion"]["text"] == "判定精度不足，无法证明达标"

    # 4. gate：稿件被拦截，且隐藏指令、极限词、竞品比较三类发现（以及过期价格、AI 标识）都在
    gate = _read_json(out_dir / "gate.json")
    assert gate["status"] == "block"
    draft_raw = (EXAMPLE_DIR / "draft.md").read_text(encoding="utf-8")
    rules_found = {item["rule_id"] for item in gate["findings"]}
    assert {
        "G-HIDDEN-TEXT",
        "G-PROMPT-INJECTION",
        "G-FACT-STALE",
        "G-LEXICON",
        "G-COMPETITOR-COMPARISON",
        "G-AI-LABEL",
    } <= rules_found

    for finding in gate["findings"]:
        if finding["rule_id"] == "G-LEXICON":
            assert finding["result"] == "review"
        if finding["span"] is not None:
            start, end = finding["span"]
            assert draft_raw[start:end] == finding["excerpt"]
