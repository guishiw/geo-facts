"""G-NEAR-DUPLICATE：corpus 目录格式、同品牌同主题范围、Jaccard 相似度（手工算例）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from helpers_gate import TZ, evaluate, meta

from geo_facts.errors import InputError
from geo_facts.gate.checks import near_duplicate
from geo_facts.gate.corpus import (
    NEAR_DUPLICATE_THRESHOLD,
    SHINGLE_SIZE,
    jaccard,
    load_corpus,
    shingles,
    topic_key,
)

ARTICLE = (
    "澜序 S7 是一款中大型纯电轿车，CLTC 续航 620 公里，百公里加速 5.9 秒，"
    "电池容量 82 kWh，整车质保 5 年或 15 万公里。"
)
OTHER = "岑野 K5 的内饰采用环保材料，座椅支持通风和加热，后排空间宽敞，适合家庭出行使用。"
SCOPE = {"brand_entity_id": "brand-lanxu", "topic": "澜序 S7 参数"}


def add(corpus: Path, name: str, text: str, meta_value: dict[str, Any] | None) -> Path:
    corpus.mkdir(parents=True, exist_ok=True)
    path = corpus / name
    path.write_text(text, encoding="utf-8")
    if meta_value is not None:
        stem = name.rsplit(".", 1)[0]
        (corpus / f"{stem}.meta.json").write_text(
            json.dumps(meta_value, ensure_ascii=False), encoding="utf-8"
        )
    return path


def duplicates(raw: str, corpus: Path, meta_fields: dict[str, Any], **kwargs: Any) -> list[Any]:
    report = evaluate(
        raw,
        "md",
        meta_value=meta(ai_generated=False, **meta_fields),
        checks=[near_duplicate.CHECK],
        corpus=load_corpus(corpus, TZ),
        **kwargs,
    )
    return list(report.findings)


# ------------------------------------------------------------------ 相似度


def test_shingles_and_jaccard_by_hand() -> None:
    # 只保留字母数字与汉字、折叠大小写后：“abcdefgh” 与 “abcdefgx”
    first = shingles("AB-cd efgh")
    second = shingles("abcdefgx")
    assert first == {"abcd", "bcde", "cdef", "defg", "efgh"}
    assert second == {"abcd", "bcde", "cdef", "defg", "efgx"}
    assert jaccard(first, second) == pytest.approx(4 / 6)
    assert jaccard(frozenset(), frozenset()) == 0.0
    assert shingles("abc") == frozenset()
    assert SHINGLE_SIZE == 4
    assert NEAR_DUPLICATE_THRESHOLD == 0.5


def test_topic_key_folds_width_case_and_spaces() -> None:
    assert topic_key(" 澜序  Ｓ７ 参数 ") == topic_key("澜序s7参数") == "澜序s7参数"
    assert topic_key("澜序 S-7 参数") == topic_key("澜序S7参数")
    assert topic_key("澜序 S7 售价") != topic_key("澜序 S7 参数")


# ------------------------------------------------------------------ 检查


def test_identical_article_in_scope_is_a_near_duplicate(tmp_path: Path) -> None:
    add(tmp_path / "corpus", "old.md", ARTICLE, SCOPE)
    findings = duplicates(ARTICLE, tmp_path / "corpus", SCOPE)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "G-NEAR-DUPLICATE"
    assert finding.result == "review"
    assert finding.span is None
    assert finding.details["corpus_file"] == "old.md"
    assert finding.details["similarity"] == 1.0


def test_lightly_edited_article_is_a_near_duplicate(tmp_path: Path) -> None:
    edited = ARTICLE.replace("整车质保 5 年或 15 万公里。", "欢迎到店试驾。")
    add(tmp_path / "corpus", "old.html", f"<p>{ARTICLE}</p>", SCOPE)
    findings = duplicates(edited, tmp_path / "corpus", {**SCOPE, "topic": "澜序Ｓ７ 参数"})
    assert len(findings) == 1
    expected = jaccard(shingles(edited), shingles(ARTICLE))
    assert expected > NEAR_DUPLICATE_THRESHOLD
    assert findings[0].details["similarity"] == round(expected, 4)


def test_unrelated_or_out_of_scope_articles_are_not_flagged(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    add(corpus, "other.md", OTHER, SCOPE)
    add(corpus, "brand.md", ARTICLE, {**SCOPE, "brand_entity_id": "brand-cenye"})
    add(corpus, "topic.md", ARTICLE, {**SCOPE, "topic": "澜序 S7 售价"})
    add(corpus, "nometa.txt", ARTICLE, None)
    add(corpus, "notopic.md", ARTICLE, {"brand_entity_id": "brand-lanxu"})
    add(corpus, "short.md", "澜序", SCOPE)
    assert duplicates(ARTICLE, corpus, SCOPE) == []
    assert load_corpus(corpus, TZ).without_scope == 2


def test_short_draft_is_not_compared(tmp_path: Path) -> None:
    add(tmp_path / "corpus", "old.md", "澜序 S7", SCOPE)
    assert duplicates("澜序 S7", tmp_path / "corpus", SCOPE) == []


def test_draft_inside_corpus_is_not_compared_with_itself(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    path = add(corpus, "draft.md", ARTICLE, SCOPE)
    assert duplicates(ARTICLE, corpus, SCOPE, draft_path=path) == []
    add(corpus, "copy.md", ARTICLE, SCOPE)
    findings = duplicates(ARTICLE, corpus, SCOPE, draft_path=path)
    assert [f.details["corpus_file"] for f in findings] == ["copy.md"]


def test_skipped_without_corpus_or_scope(tmp_path: Path) -> None:
    add(tmp_path / "corpus", "old.md", ARTICLE, SCOPE)
    corpus = load_corpus(tmp_path / "corpus", TZ)
    cases = [
        ({}, None, "未提供 --corpus"),
        (SCOPE, None, "未提供 --corpus"),
        ({"topic": "x"}, corpus, "缺少 brand_entity_id 或 topic"),
        ({"brand_entity_id": "brand-lanxu"}, corpus, "缺少 brand_entity_id 或 topic"),
    ]
    for fields, corpus_value, reason in cases:
        report = evaluate(
            ARTICLE,
            meta_value=meta(**fields),
            checks=[near_duplicate.CHECK],
            corpus=corpus_value,
        )
        assert reason in report.to_dict()["skipped_checks"][0]["reason"]
    report = evaluate(ARTICLE, checks=[near_duplicate.CHECK], corpus=corpus)
    assert "缺少 brand_entity_id 或 topic" in report.to_dict()["skipped_checks"][0]["reason"]


def test_corpus_errors(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="--corpus 目录不存在"):
        load_corpus(tmp_path / "missing", TZ)
    corpus = tmp_path / "corpus"
    add(corpus, "a.md", ARTICLE, SCOPE)
    add(corpus, "a.txt", ARTICLE, None)
    with pytest.raises(InputError, match="文件名主干相同"):
        load_corpus(corpus, TZ)
    bad = tmp_path / "bad"
    add(bad, "b.md", ARTICLE, None)
    (bad / "b.meta.json").write_text('{"topic": 1}', encoding="utf-8")
    with pytest.raises(InputError, match="topic 必须是字符串"):
        load_corpus(bad, TZ)
    (bad / "b.meta.json").write_text("{", encoding="utf-8")
    with pytest.raises(InputError, match="不是合法 JSON"):
        load_corpus(bad, TZ)
