"""`geo-facts claims extract` 命令与拆解器插件接口。"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from conftest import run_cli, run_cli_json, valid_example
from helpers_store import NOW

from geo_facts import contracts, jsonl
from geo_facts.claims import Lexicon
from geo_facts.claims.plugins import (
    available_extractors,
    get_extractor,
    register_extractor,
    unregister_extractor,
)
from geo_facts.errors import InputError
from geo_facts.store import init_store


def observation(
    observation_id: str, answer: str | None, *, status: str = "valid"
) -> dict[str, Any]:
    record = valid_example("observation")
    record["observation_id"] = observation_id
    record["trace_id"] = "trace-" + observation_id
    record["answer_text"] = answer
    record["status"] = status
    record["invalid_reason"] = None if status == "valid" else "timeout"
    if answer is None:
        record["null_reasons"] = {
            **record["null_reasons"],
            "answer_text": "平台超时，没有回答（虚构）",
        }
    return record


ANSWER = "澜序 S7 的 CLTC 续航为 620 公里，售价 21.98 万元。它的 WLTC 续航约 540 公里。"


@pytest.fixture
def store(tmp_path: Path) -> Path:
    root = tmp_path / "store"
    init_store(root, project_id=None, now=NOW, example="auto")
    return root


@pytest.fixture
def observations(tmp_path: Path) -> Path:
    path = tmp_path / "observations.jsonl"
    jsonl.append_jsonl(
        path,
        [
            observation("obs-1", ANSWER),
            observation("obs-2", None, status="invalid"),
            observation("obs-3", "  "),
            observation("obs-4", "岑野 K5 续航 580 公里。"),
        ],
    )
    return path


def test_extract_from_observations(tmp_path: Path, store: Path, observations: Path) -> None:
    out = tmp_path / "claims.jsonl"
    code, data = run_cli_json(
        "claims",
        "extract",
        "--store",
        str(store),
        "--observations",
        str(observations),
        "--out",
        str(out),
    )
    assert code == 0
    assert data["observations"] == {"total": 4, "used": 2, "skipped_invalid": 1, "skipped_empty": 1}
    assert data["claims"] == 4
    assert data["by_type"] == {"numeric": 4, "model": 0, "date": 0, "free_text": 0}
    assert data["without_entity"] == 0
    assert "未提供模型实现" in data["notice"]
    claims = jsonl.read_jsonl(out)
    for claim in claims:
        assert contracts.iter_issues("claim", claim) == []
        assert claim["synthetic"] is True
        assert claim["project_id"] == "auto-demo"
    assert [(claim["source"]["id"], claim["attribute"], claim["value"]) for claim in claims] == [
        ("obs-1", "续航里程", 620),
        ("obs-1", "售价", 21.98),
        ("obs-1", "续航里程", 540),
        ("obs-4", "续航里程", 580),
    ]
    assert [claim["entity_id"] for claim in claims] == [
        "product-lanxu-s7",
        "product-lanxu-s7",
        "product-lanxu-s7",
        "product-cenye-k5",
    ]
    for claim in claims[:3]:
        start, end = claim["span"]["start"], claim["span"]["end"]
        assert ANSWER[start:end] == claim["text"]
    # claim_id 由内容哈希派生：再跑一次逐字节一致。
    again = tmp_path / "again.jsonl"
    run_cli(
        "claims",
        "extract",
        "--store",
        str(store),
        "--observations",
        str(observations),
        "--out",
        str(again),
    )
    assert again.read_bytes() == out.read_bytes()
    code, err_data = run_cli_json(
        "claims",
        "extract",
        "--store",
        str(store),
        "--observations",
        str(observations),
        "--out",
        str(out),
    )
    assert code == 2
    assert "拒绝覆盖" in err_data["message"]


def test_extract_from_text(tmp_path: Path, store: Path) -> None:
    draft = tmp_path / "draft.md"
    draft.write_text(
        "# 澜序 S7\n\n上市时间：2026年3月18日。整车质保 5 年或 15 万公里。\n", encoding="utf-8"
    )
    out = tmp_path / "out" / "claims.jsonl"
    code, out_text, _ = run_cli(
        "claims",
        "extract",
        "--store",
        str(store),
        "--text",
        str(draft),
        "--source-id",
        "draft-1",
        "--out",
        str(out),
    )
    assert code == 0
    assert "已拆出 2 条陈述" in out_text
    assert "日期 1" in out_text
    claims = jsonl.read_jsonl(out)
    assert [claim["source"] for claim in claims] == [{"type": "draft", "id": "draft-1"}] * 2
    assert [(claim["claim_type"], claim["value"]) for claim in claims] == [
        ("date", "2026-03-18"),
        ("numeric", "5 年或 15 万公里"),
    ]
    other = tmp_path / "other.jsonl"
    code, data = run_cli_json(
        "claims",
        "extract",
        "--store",
        str(store),
        "--text",
        str(draft),
        "--source-id",
        "obs-9",
        "--source-type",
        "observation",
        "--out",
        str(other),
    )
    assert code == 0
    assert data["observations"] is None
    assert jsonl.read_jsonl(other)[0]["source"] == {"type": "observation", "id": "obs-9"}
    empty = tmp_path / "empty.txt"
    empty.write_text("没有可核验的内容。", encoding="utf-8")
    nothing = tmp_path / "nothing.jsonl"
    code, data = run_cli_json(
        "claims",
        "extract",
        "--store",
        str(store),
        "--text",
        str(empty),
        "--source-id",
        "d2",
        "--out",
        str(nothing),
    )
    assert (code, data["claims"], nothing.read_text(encoding="utf-8")) == (0, 0, "")


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ((), "必须且只能给一个"),
        (("--text", "x.txt"), "--source-id"),
        (("--text", "x.txt", "--source-id", "坏 id"), "--source-id 不合法"),
        (("--observations", "o.jsonl", "--source-id", "a"), "只用于 --text"),
        (("--text", "missing.txt", "--source-id", "a"), "文本文件不存在"),
        (("--text", "x.txt", "--source-id", "a", "--extractor", "llm"), "未知拆解器"),
    ],
)
def test_extract_argument_errors(
    tmp_path: Path, store: Path, extra: tuple[str, ...], message: str
) -> None:
    (tmp_path / "x.txt").write_text("澜序 S7 续航 620 公里", encoding="utf-8")
    args = [
        item if not item.endswith((".txt", ".jsonl")) else str(tmp_path / item) for item in extra
    ]
    code, _, err = run_cli(
        "claims", "extract", "--store", str(store), *args, "--out", str(tmp_path / "out.jsonl")
    )
    assert code == 2
    assert message in err


def test_extract_rejects_bad_observation_files(tmp_path: Path, store: Path) -> None:
    broken = observation("obs-1", ANSWER)
    del broken["platform"]
    bad = tmp_path / "bad.jsonl"
    jsonl.append_jsonl(bad, [broken])
    code, _, err = run_cli(
        "claims",
        "extract",
        "--store",
        str(store),
        "--observations",
        str(bad),
        "--out",
        str(tmp_path / "a.jsonl"),
    )
    assert code == 2
    assert "bad.jsonl 第 1 行" in err
    duplicate = tmp_path / "dup.jsonl"
    jsonl.append_jsonl(duplicate, [observation("obs-1", ANSWER), observation("obs-1", ANSWER)])
    code, _, err = run_cli(
        "claims",
        "extract",
        "--store",
        str(store),
        "--observations",
        str(duplicate),
        "--out",
        str(tmp_path / "b.jsonl"),
    )
    assert (code, "重复" in err) == (2, True)
    gbk = tmp_path / "gbk.txt"
    gbk.write_bytes("续航".encode("gb18030"))
    code, _, err = run_cli(
        "claims",
        "extract",
        "--store",
        str(store),
        "--text",
        str(gbk),
        "--source-id",
        "d",
        "--out",
        str(tmp_path / "c.jsonl"),
    )
    assert (code, "UTF-8" in err) == (2, True)
    assert not (tmp_path / "a.jsonl").exists()


# ------------------------------------------------------------------ 插件接口


class _FakeExtractor:
    def __init__(self, lexicon: Lexicon, claims: list[dict[str, Any]]) -> None:
        self.name = "fake"
        self.claims = claims

    def extract(
        self, text: str, *, source: Mapping[str, str], project_id: str | None, synthetic: bool
    ) -> list[dict[str, Any]]:
        return [dict(claim, source=dict(source)) for claim in self.claims]


@pytest.fixture
def fake_plugin() -> Iterator[list[dict[str, Any]]]:
    claims: list[dict[str, Any]] = []
    register_extractor("fake", lambda lexicon: _FakeExtractor(lexicon, claims))
    try:
        yield claims
    finally:
        unregister_extractor("fake")


def _free_text_claim(text: str, start: int, end: int) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "claim_id": "clm-fake-1",
        "source": {"type": "draft", "id": "d"},
        "text": text[start:end],
        "span": {"start": start, "end": end},
        "entity_id": None,
        "attribute": None,
        "value": None,
        "unit": None,
        "conditions": {},
        "claim_type": "free_text",
        "extractor": {"type": "model", "id": "fake", "version": "0"},
    }


def test_plugin_registry(tmp_path: Path, store: Path, fake_plugin: list[dict[str, Any]]) -> None:
    assert available_extractors() == ("fake", "rules")
    with pytest.raises(ValueError, match="已注册"):
        register_extractor("rules", lambda lexicon: _FakeExtractor(lexicon, []))
    with pytest.raises(ValueError, match="不能为空"):
        register_extractor(" ", lambda lexicon: _FakeExtractor(lexicon, []))
    with pytest.raises(ValueError, match="不能注销"):
        unregister_extractor("rules")
    text = "这款车很安静。"
    draft = tmp_path / "d.txt"
    draft.write_text(text, encoding="utf-8")

    def extract(name: str) -> tuple[int, Any]:
        return run_cli_json(
            "claims",
            "extract",
            "--store",
            str(store),
            "--text",
            str(draft),
            "--source-id",
            "d",
            "--extractor",
            "fake",
            "--out",
            str(tmp_path / name),
        )

    fake_plugin.append(_free_text_claim(text, 0, 6))
    code, data = extract("ok.jsonl")
    assert (code, data["by_type"]["free_text"], data["extractor"]) == (0, 1, {"name": "fake"})
    fake_plugin[0] = dict(fake_plugin[0], text="对不上")
    code, data = extract("mismatch.jsonl")
    assert code == 2
    assert "与原文位置不一致" in data["message"]
    fake_plugin[0] = dict(fake_plugin[0], claim_type="opinion")
    code, data = extract("invalid.jsonl")
    assert code == 2
    assert "不符合 claim 契约" in data["message"]


def test_get_extractor_unknown() -> None:
    with pytest.raises(InputError, match="未提供模型实现"):
        get_extractor("llm", Lexicon({}, [], {}, [], {}, {}, frozenset()))
    assert json.dumps(available_extractors()) == '["rules"]'
