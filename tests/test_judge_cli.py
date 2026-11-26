"""`geo-facts judge` 命令：判定文件、摘要的两个口径与呈现率（手工算例）、可复现性。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from conftest import run_cli, run_cli_json, valid_example
from helpers_store import NOW

from geo_facts import contracts, jsonl
from geo_facts.store import init_store


def observation(observation_id: str, query_id: str, answer: str | None) -> dict[str, Any]:
    record = valid_example("observation")
    record["observation_id"] = observation_id
    record["trace_id"] = "trace-" + observation_id
    record["query_id"] = query_id
    record["answer_text"] = answer
    if answer is None:
        record["status"] = "invalid"
        record["invalid_reason"] = "timeout"
        record["null_reasons"] = {**record["null_reasons"], "answer_text": "平台超时（虚构）"}
    return record


def prompt_set(cores: dict[str, list[str] | None]) -> dict[str, Any]:
    record = valid_example("prompt_set")
    template = record["prompts"][0]
    prompts = []
    for query_id, core in cores.items():
        prompt = {key: value for key, value in template.items() if key != "core_fact_ids"}
        prompt["query_id"] = query_id
        prompt["text"] = f"关于澜序的问题 {query_id}（虚构）"
        if core is not None:
            prompt["core_fact_ids"] = core
        prompts.append(prompt)
    record["prompts"] = prompts
    return record


OBSERVATIONS = [
    observation(
        "obs-1",
        "Q001",
        "澜序 S7 的 CLTC 续航为 620 公里，售价 23.98 万元。澜序 M9 百公里加速 4.5 秒。",
    ),
    observation("obs-2", "Q002", "岑野 K5 续航 580 公里。澜序 S7 于 2026 年 3 月上市。"),
    observation("obs-3", "Q003", "澜序 S7 售价 21.98 万元。"),
    observation("obs-4", "Q001", None),
]
CORES: dict[str, list[str] | None] = {
    "Q001": ["F-S7-RANGE-CLTC", "F-S7-PRICE", "F-M9-ACCEL"],
    "Q002": ["F-S7-LAUNCH", "F-S7-PRICE"],
    "Q003": None,
}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    init_store(tmp_path / "store", project_id=None, now=NOW, example="auto")
    jsonl.append_jsonl(tmp_path / "observations.jsonl", OBSERVATIONS)
    jsonl.write_json(tmp_path / "prompts.json", prompt_set(CORES))
    return tmp_path


def judge_args(root: Path, out: str = "judgments.jsonl", *extra: str) -> list[str]:
    return [
        "judge",
        "--store",
        str(root / "store"),
        "--observations",
        str(root / "observations.jsonl"),
        "--as-of",
        "2026-10-08",
        "--out",
        str(root / out),
        *extra,
    ]


def test_summary_matches_hand_computation(workspace: Path) -> None:
    code, data = run_cli_json(
        *judge_args(
            workspace,
            "judgments.jsonl",
            "--prompt-set",
            str(workspace / "prompts.json"),
            "--claims-out",
            str(workspace / "claims.jsonl"),
            "--now",
            NOW,
        )
    )
    assert code == 0
    assert data["as_of"] == "2026-10-08"
    assert data["observations"] == 3
    assert data["observation_stats"] == {
        "total": 4,
        "used": 3,
        "skipped_invalid": 1,
        "skipped_empty": 0,
    }
    assert data["claims"] == 6
    # 回答 → 事实库：支持 3（S7 续航、S7 上市、S7 现价）、矛盾 1（过期旧价）、
    # 不可核验 2（M9 加速无证据、K5 没有事实）。
    answer = data["answer_to_facts"]
    assert (
        answer["supported"],
        answer["contradicted"],
        answer["unverifiable"],
        answer["total"],
    ) == (3, 1, 2, 6)
    assert answer["accuracy_verifiable"] == 3 / 4
    assert answer["accuracy_strict"] == 3 / 6
    assert answer["unverifiable_rate"] == 2 / 6
    assert answer["null_reasons"] == {}
    assert answer["unverifiable_reasons"] == {"fact_unusable": 1, "no_fact": 1}
    # 事实库 → 回答：Q001 续航正确、售价错误（M9 加速无证据，排除出分母）；Q002 上市正确、售价缺失；
    # Q003 没有配置核心事实，不进分母。
    presence = data["facts_to_answer"]
    assert (
        presence["present_correct"],
        presence["present_wrong"],
        presence["missing"],
        presence["total"],
    ) == (2, 1, 1, 4)
    assert presence["presence_rate"] == 2 / 4
    assert presence["null_reason"] is None
    assert presence["excluded"] == 1
    assert presence["excluded_core_facts"] == [
        {
            "observation_id": "obs-1",
            "query_id": "Q001",
            "fact_id": "F-M9-ACCEL",
            "reasons": ["no_evidence"],
            "reason_text": "没有绑定证据",
        }
    ]
    assert "事前登记" in data["basis_note"]
    assert data["outputs"] == {
        "judgments": str(workspace / "judgments.jsonl"),
        "claims": str(workspace / "claims.jsonl"),
    }
    judgments = jsonl.read_jsonl(workspace / "judgments.jsonl")
    assert len(judgments) == 10
    for judgment in judgments:
        assert contracts.iter_issues("judgment", judgment) == []
        assert judgment["as_of"] == "2026-10-08"
        assert judgment["created_at"] == NOW
        assert judgment["judge"] == {
            "type": "rule",
            "id": "geo-facts-judge-rules",
            "version": "1.0",
        }
        assert judgment["project_id"] == "auto-demo"
        assert judgment["synthetic"] is True
        assert judgment["judgment_id"].startswith("jdg-")
    assert len({judgment["judgment_id"] for judgment in judgments}) == 10
    rows = [
        (
            j["source"]["id"],
            j["query_id"],
            j["direction"],
            j["fact_id"],
            j["fact_version"],
            j["label"],
        )
        for j in judgments
    ]
    assert rows == [
        ("obs-1", "Q001", "answer_to_facts", "F-S7-RANGE-CLTC", 1, "supported"),
        ("obs-1", "Q001", "answer_to_facts", "F-S7-PRICE", 2, "contradicted"),
        ("obs-1", "Q001", "answer_to_facts", "F-M9-ACCEL", 1, "unverifiable"),
        ("obs-1", "Q001", "facts_to_answer", "F-S7-RANGE-CLTC", 1, "present_correct"),
        ("obs-1", "Q001", "facts_to_answer", "F-S7-PRICE", 2, "present_wrong"),
        ("obs-2", "Q002", "answer_to_facts", None, None, "unverifiable"),
        ("obs-2", "Q002", "answer_to_facts", "F-S7-LAUNCH", 1, "supported"),
        ("obs-2", "Q002", "facts_to_answer", "F-S7-LAUNCH", 1, "present_correct"),
        ("obs-2", "Q002", "facts_to_answer", "F-S7-PRICE", 2, "missing"),
        ("obs-3", "Q003", "answer_to_facts", "F-S7-PRICE", 2, "supported"),
    ]
    claims = jsonl.read_jsonl(workspace / "claims.jsonl")
    assert len(claims) == 6
    claim_ids = {claim["claim_id"] for claim in claims}
    for judgment in judgments:
        if judgment["direction"] == "answer_to_facts":
            assert judgment["claim_id"] in claim_ids
            assert judgment["claim_type"] in ("numeric", "date")
        else:
            assert judgment["claim_id"] is None
    assert "与已过期的版本一致（过期信息）" in judgments[1]["reason"]
    assert judgments[4]["ext"]["claim_ids"] == [claims[1]["claim_id"]]


def test_text_output_and_reproducibility(workspace: Path) -> None:
    extra = ("--prompt-set", str(workspace / "prompts.json"), "--now", NOW)
    code, out, _ = run_cli(*judge_args(workspace, "a.jsonl", *extra))
    assert code == 0
    assert "可核验口径 0.75（3 / 4）" in out
    assert "严格口径 0.5（3 / 6）" in out
    assert "呈现率 0.5（2 / 4）" in out
    assert "事前登记" in out
    run_cli(*judge_args(workspace, "b.jsonl", *extra))
    assert (workspace / "a.jsonl").read_bytes() == (workspace / "b.jsonl").read_bytes()


def test_without_prompt_set_and_without_now(workspace: Path) -> None:
    code, data = run_cli_json(*judge_args(workspace))
    assert code == 0
    presence = data["facts_to_answer"]
    assert (presence["total"], presence["presence_rate"]) == (0, None)
    assert "--prompt-set" in presence["null_reason"]
    assert data["outputs"]["claims"] is None
    judgments = jsonl.read_jsonl(workspace / "judgments.jsonl")
    assert {judgment["direction"] for judgment in judgments} == {"answer_to_facts"}
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", judgments[0]["created_at"])


def test_zero_denominators_give_null_with_reasons(tmp_path: Path) -> None:
    init_store(tmp_path / "store", project_id=None, now=NOW, example="auto")
    jsonl.append_jsonl(
        tmp_path / "observations.jsonl", [observation("obs-1", "Q001", "澜序 S7 外观好看。")]
    )
    code, data = run_cli_json(*judge_args(tmp_path, "j.jsonl", "--now", NOW))
    assert code == 0
    answer = data["answer_to_facts"]
    assert (answer["total"], answer["accuracy_verifiable"], answer["accuracy_strict"]) == (
        0,
        None,
        None,
    )
    assert answer["unverifiable_rate"] is None
    assert set(answer["null_reasons"]) == {
        "accuracy_verifiable",
        "accuracy_strict",
        "unverifiable_rate",
    }
    assert (tmp_path / "j.jsonl").read_text(encoding="utf-8") == ""
    # 只有不可核验：严格口径为 0，可核验口径分母为 0。
    jsonl.append_jsonl(
        tmp_path / "k5.jsonl", [observation("obs-2", "Q001", "岑野 K5 续航 580 公里。")]
    )
    code, data = run_cli_json(
        "judge",
        "--store",
        str(tmp_path / "store"),
        "--observations",
        str(tmp_path / "k5.jsonl"),
        "--as-of",
        "2026-10-08",
        "--out",
        str(tmp_path / "k5-out.jsonl"),
    )
    answer = data["answer_to_facts"]
    assert (answer["accuracy_strict"], answer["accuracy_verifiable"]) == (0.0, None)
    assert list(answer["null_reasons"]) == ["accuracy_verifiable"]


def test_judge_refuses_to_overwrite_and_rejects_bad_input(workspace: Path) -> None:
    (workspace / "judgments.jsonl").write_text("旧内容\n", encoding="utf-8")
    code, data = run_cli_json(*judge_args(workspace))
    assert code == 2
    assert "拒绝覆盖" in data["message"]
    assert (workspace / "judgments.jsonl").read_text(encoding="utf-8") == "旧内容\n"
    (workspace / "claims-old.jsonl").write_text("", encoding="utf-8")
    code, data = run_cli_json(
        *judge_args(workspace, "new.jsonl", "--claims-out", str(workspace / "claims-old.jsonl"))
    )
    assert code == 2
    assert not (workspace / "new.jsonl").exists()
    code, _, err = run_cli(
        *judge_args(workspace, "x.jsonl")[:-2],
        "--as-of",
        "2026/10/08",
        "--out",
        str(workspace / "x.jsonl"),
    )
    assert code == 2
    code, _, err = run_cli(
        "judge",
        "--store",
        str(workspace / "store"),
        "--observations",
        str(workspace / "observations.jsonl"),
        "--out",
        str(workspace / "y.jsonl"),
    )
    assert code == 2
    assert "--as-of" in err
    broken = prompt_set(CORES)
    del broken["sealed_by"]
    jsonl.write_json(workspace / "broken.json", broken)
    code, _, err = run_cli(
        *judge_args(workspace, "z.jsonl", "--prompt-set", str(workspace / "broken.json"))
    )
    assert code == 2
    assert "prompt_set" in err
    jsonl.write_json(workspace / "unknown.json", prompt_set({"Q001": ["F-NOPE"]}))
    code, _, err = run_cli(
        *judge_args(workspace, "w.jsonl", "--prompt-set", str(workspace / "unknown.json"))
    )
    assert code == 2
    assert "F-NOPE" in err
    assert not (workspace / "w.jsonl").exists()


def test_presence_null_reasons(workspace: Path) -> None:
    jsonl.write_json(workspace / "q3.json", prompt_set({"Q001": None, "Q003": ["F-S7-PRICE"]}))
    code, data = run_cli_json(
        *judge_args(workspace, "a.jsonl", "--prompt-set", str(workspace / "q3.json"))
    )
    assert code == 0
    assert data["facts_to_answer"]["total"] == 1  # 只有 obs-3（Q003）进入分母
    jsonl.write_json(workspace / "none.json", prompt_set({"Q009": ["F-S7-PRICE"]}))
    code, data = run_cli_json(
        *judge_args(workspace, "b.jsonl", "--prompt-set", str(workspace / "none.json"))
    )
    presence = data["facts_to_answer"]
    assert (presence["total"], presence["observations_with_core_facts"]) == (0, 0)
    assert "没有观测对应配置了核心事实的题" in presence["null_reason"]
    jsonl.write_json(workspace / "bad.json", prompt_set({"Q001": ["F-M9-ACCEL"]}))
    code, data = run_cli_json(
        *judge_args(workspace, "c.jsonl", "--prompt-set", str(workspace / "bad.json"))
    )
    presence = data["facts_to_answer"]
    assert (presence["total"], presence["excluded"]) == (0, 1)
    assert "都不可用" in presence["null_reason"]


def test_out_and_claims_out_must_differ(workspace: Path) -> None:
    code, _, err = run_cli(
        *judge_args(workspace, "same.jsonl", "--claims-out", str(workspace / "same.jsonl"))
    )
    assert code == 2
    assert "不能是同一个文件" in err
