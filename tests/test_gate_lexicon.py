"""词库：内置通用示例词库、外部词库目录、格式校验；命中只产生 review（046 §8.3.5）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from helpers_gate import TZ, assert_excerpts, evaluate, run_check
from hypothesis import given, settings
from hypothesis import strategies as st

from geo_facts.errors import InputError
from geo_facts.gate.checks import lexicon_hits
from geo_facts.gate.lexicon import (
    CATEGORIES,
    RISK_LEVELS,
    load_builtin,
    load_lexicons,
    parse_lexicon,
)
from geo_facts.gate.meta import parse_meta
from geo_facts.gate.model import ALLOWED_CLAUSES

CHECK = lexicon_hits.CHECK
BUILTIN = (load_builtin(),)


def lexicon(entries: list[dict[str, Any]], lexicon_id: str = "test-lexicon") -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "lexicon_id": lexicon_id,
        "industry": "test",
        "description": "测试词库，未经法务审阅",
        "entries": entries,
    }


def entry(term: str, **fields: Any) -> dict[str, Any]:
    base = {
        "term": term,
        "match": "literal",
        "category": "superlative",
        "risk_level": "medium",
        "clause": "046 §8.3.5",
    }
    base.update(fields)
    return base


def parsed(*entries: dict[str, Any]) -> Any:
    return parse_lexicon(lexicon(list(entries)), file="t.json", source="external", digest="x")


# ------------------------------------------------------------------ 内置词库


def test_builtin_lexicon_is_a_labelled_sample() -> None:
    builtin = load_builtin()
    assert builtin.lexicon_id == "generic-sample"
    assert "未经法务审阅，仅作演示" in builtin.description
    assert builtin.source == "builtin"
    assert len(builtin.entries) >= 10
    assert {e.category for e in builtin.entries} >= {
        "superlative",
        "exaggerated_promise",
        "industry_sensitive",
    }
    assert all(e.clause in ALLOWED_CLAUSES for e in builtin.entries)
    raw = (
        Path(__file__).resolve().parent.parent / "src/geo_facts/lexicons/generic.json"
    ).read_text(encoding="utf-8")
    for word in ("官方", "认证", "对标"):
        assert word not in raw


def test_superlative_hit_is_review_with_exact_span() -> None:
    raw = "澜序 S7 是同级最佳选择。"
    findings = run_check(CHECK, raw, lexicons=BUILTIN)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "G-LEXICON"
    assert finding.result == "review"
    assert finding.span == (9, 11)
    assert finding.excerpt == "最佳"
    assert finding.risk_level == "medium"
    assert finding.clause == "046 §8.3.5"
    assert finding.details["category"] == "superlative"
    assert finding.details["lexicon_id"] == "generic-sample"
    assert finding.details["term"] == "最佳"


def test_regex_entries_and_repeated_hits() -> None:
    raw = "销量第一，全国 第一，保证满意；100% 有效，再说一次保证。"
    findings = run_check(CHECK, raw, lexicons=BUILTIN)
    excerpts = sorted(f.excerpt for f in findings)
    assert excerpts == ["100% 有效", "保证", "保证", "全国 第一", "销量第一"]


def test_case_and_width_are_folded() -> None:
    lex = (parsed(entry("BEST"), entry("劲爆", match="regex")),)
    findings = run_check(CHECK, "ｂｅｓｔ choice, Best, 劲爆", lexicons=lex)
    assert [f.excerpt for f in findings] == ["ｂｅｓｔ", "Best", "劲爆"]


def test_code_blocks_are_not_scanned_but_hidden_text_is() -> None:
    raw = '`最佳实践`\n\n<span style="display:none">最佳</span>'
    findings = run_check(CHECK, raw, "md", lexicons=BUILTIN)
    assert len(findings) == 1
    assert findings[0].span == (raw.index("最佳</span>"), raw.index("</span>"))


def test_drug_and_rx_categories_are_highest_and_need_legal() -> None:
    lex = (
        parsed(
            entry("疗效优于", category="drug_comparison", risk_level="low"),
            entry("处方药直购", category="prescription_promotion", risk_level="medium"),
            entry(
                "根治",
                category="exaggerated_promise",
                risk_level="high",
                requires=["legal_confirmation"],
            ),
        ),
    )
    findings = run_check(CHECK, "本品疗效优于同类产品，可处方药直购，能根治咽炎。", lexicons=lex)
    by_rule = {f.rule_id: f for f in findings}
    assert set(by_rule) == {"G-DRUG-COMPARISON", "G-RX-PROMOTION", "G-LEXICON"}
    for rule in ("G-DRUG-COMPARISON", "G-RX-PROMOTION"):
        assert by_rule[rule].risk_level == "highest"
        assert by_rule[rule].requires == ("legal_confirmation",)
        assert by_rule[rule].result == "review"
        assert "法务" in by_rule[rule].suggestion
    assert by_rule["G-LEXICON"].risk_level == "high"
    assert by_rule["G-LEXICON"].requires == ("legal_confirmation",)
    assert "法务" in by_rule["G-LEXICON"].suggestion


def test_unregistered_clause_becomes_placeholder() -> None:
    no_clause = {k: v for k, v in entry("神车").items() if k != "clause"}
    lex = (parsed(entry("极品", clause="某某办法第九条"), no_clause),)
    findings = run_check(CHECK, "极品神车", lexicons=lex)
    assert [f.clause for f in findings] == ["待人工核对", "待人工核对"]
    assert findings[0].details["lexicon_clause"] == "某某办法第九条"
    assert "lexicon_clause" not in findings[1].details


def test_zero_length_regex_matches_are_ignored() -> None:
    lex = (parsed(entry("x*", match="regex")),)
    assert run_check(CHECK, "没有", lexicons=lex) == []


# ------------------------------------------------------------------ 外部目录与格式错误


def write_lexicon(directory: Path, name: str, value: Any) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_lexicons_from_directories(tmp_path: Path) -> None:
    write_lexicon(tmp_path / "a", "b.json", lexicon([entry("乙")], "lex-b"))
    write_lexicon(tmp_path / "a", "a.json", lexicon([entry("甲")], "lex-a"))
    write_lexicon(tmp_path / "c", "c.json", lexicon([], "lex-c"))
    (tmp_path / "a" / "readme.txt").write_text("忽略", encoding="utf-8")
    loaded = load_lexicons([tmp_path / "a", tmp_path / "c"])
    assert [lex.lexicon_id for lex in loaded] == ["generic-sample", "lex-a", "lex-b", "lex-c"]
    assert loaded[1].summary()["file"] == "a.json"
    assert loaded[1].summary()["source"] == "external"


BAD = [
    ([], "顶层必须是 JSON 对象"),
    ({**lexicon([]), "extra": 1}, "未知字段"),
    ({k: v for k, v in lexicon([]).items() if k != "entries"}, "缺少 entries"),
    ({**lexicon([]), "schema_version": "2.0"}, "schema_version"),
    ({**lexicon([]), "lexicon_id": " "}, "lexicon_id"),
    ({**lexicon([]), "description": 1}, "description"),
    ({**lexicon([]), "entries": {}}, "entries 必须是数组"),
    (lexicon(["最佳"]), "不是 JSON 对象"),
    (lexicon([{**entry("最佳"), "weight": 1}]), "未知字段"),
    (lexicon([{k: v for k, v in entry("最佳").items() if k != "category"}]), "缺少 category"),
    (lexicon([entry("")]), "缺少 term"),
    (lexicon([entry("x" * 201)]), "超过"),
    (lexicon([entry("最佳", match="glob")]), "match 取值不合法"),
    (lexicon([entry("最佳", category="politics")]), "category 取值不合法"),
    (lexicon([entry("最佳", risk_level="extreme")]), "risk_level 取值不合法"),
    (lexicon([entry("最佳", clause=5)]), "clause 必须是字符串"),
    (lexicon([entry("最佳", requires="legal")]), "requires"),
    (lexicon([entry("最佳", requires=[""])]), "requires"),
    (lexicon([entry("(未闭合", match="regex")]), "正则无法编译"),
]


@pytest.mark.parametrize(("value", "message"), BAD)
def test_malformed_lexicons_are_input_errors(tmp_path: Path, value: Any, message: str) -> None:
    write_lexicon(tmp_path / "lex", "bad.json", value)
    with pytest.raises(InputError, match=message) as info:
        load_lexicons([tmp_path / "lex"])
    assert info.value.exit_code == 2


def test_other_lexicon_errors(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="词库目录不存在"):
        load_lexicons([tmp_path / "none"])
    (tmp_path / "lex").mkdir()
    (tmp_path / "lex" / "bad.json").write_text("{", encoding="utf-8")
    with pytest.raises(InputError, match="不是合法 JSON"):
        load_lexicons([tmp_path / "lex"])
    write_lexicon(tmp_path / "dup", "dup.json", lexicon([], "generic-sample"))
    with pytest.raises(InputError, match="lexicon_id 重复"):
        load_lexicons([tmp_path / "dup"])


# ------------------------------------------------------------------ 词库命中永远不会单独变成 block

SAFE_TEXT = st.text(
    alphabet="澜序岑野续航公里售价万元最佳第一绝对保证全网最低领先，。 \nS7K5", max_size=80
)
TERMS = st.text(alphabet="最佳第一绝对保证全网低领先澜序续航", min_size=1, max_size=4)


@st.composite
def random_lexicon(draw: st.DrawFn) -> dict[str, Any]:
    entries = []
    for term in draw(st.lists(TERMS, min_size=1, max_size=6)):
        entries.append(
            entry(
                term,
                match=draw(st.sampled_from(["literal", "regex"])),
                category=draw(st.sampled_from(CATEGORIES)),
                risk_level=draw(st.sampled_from(RISK_LEVELS)),
                requires=draw(st.sampled_from([[], ["legal_confirmation"]])),
            )
        )
    return lexicon(entries, "random")


@settings(max_examples=150, deadline=None)
@given(random_lexicon(), SAFE_TEXT, st.sampled_from(["md", "txt", "html"]))
def test_lexicon_hits_never_block(value: dict[str, Any], text: str, fmt: str) -> None:
    lex = parse_lexicon(value, file="random.json", source="external", digest="x")
    meta = parse_meta({"ai_generated": False, "product_type": "prescription_drug"}, TZ)
    report = evaluate(text, fmt, meta_value=meta, lexicons=(load_builtin(), lex))
    assert_excerpts(text, report.findings)
    assert report.status != "block"
    assert all(f.result == "review" for f in report.findings)
