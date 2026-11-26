"""双向判定：回答 → 事实库（准确率）与事实库 → 回答（呈现率）。

期望值都是人工判断：先按虚构样例的事实写出“应当支持 / 矛盾 / 不可核验”，再与程序对照。
查询日默认 2026-10-08（S7 售价当前有效版本为 21.98 万元，23.98 万元已于 2026-08-31 过期）。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from helpers_store import memory_store
from hypothesis import given, settings
from hypothesis import strategies as st

from geo_facts.claims import build_lexicon, extract_claims
from geo_facts.errors import InputError
from geo_facts.judge.rules import (
    APPROX_RELATIVE,
    NEAR_RELATIVE,
    JudgeContext,
    judge_claim,
    judge_claims,
    presence_for,
)
from geo_facts.store import FactStore, load_example

AS_OF = date(2026, 10, 8)
SOURCE = {"type": "observation", "id": "obs-1"}


def example_store(mutate: Any = None) -> FactStore:
    entities, facts = load_example("auto", "auto-demo")
    if mutate is not None:
        facts = mutate(facts)
    return memory_store(facts, entities)


def judge_text(text: str, store: FactStore | None = None, as_of: date = AS_OF) -> list[Any]:
    target = store or example_store()
    claims = extract_claims(
        text, lexicon=build_lexicon(target), source=SOURCE, project_id="auto-demo"
    )
    return judge_claims(claims, JudgeContext.from_store(target, as_of))


def one(text: str, store: FactStore | None = None, as_of: date = AS_OF) -> Any:
    verdicts = judge_text(text, store, as_of)
    assert len(verdicts) == 1, [verdict.claim["text"] for verdict in verdicts]
    return verdicts[0]


def labels(text: str, store: FactStore | None = None) -> list[str]:
    return [verdict.label for verdict in judge_text(text, store)]


def with_value(fact_id: str, value: Any) -> Any:
    def mutate(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for fact in facts:
            if fact["fact_id"] == fact_id:
                fact["value"] = value
        return facts

    return mutate


def without(*fact_ids: str) -> Any:
    def mutate(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [fact for fact in facts if fact["fact_id"] not in fact_ids]

    return mutate


def claim(**fields: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": "1.0",
        "claim_id": "clm-hand-1",
        "source": dict(SOURCE),
        "text": "手写的陈述",
        "span": {"start": 0, "end": 5},
        "entity_id": "product-lanxu-s7",
        "attribute": "续航里程",
        "value": 620,
        "unit": "km",
        "conditions": {},
        "claim_type": "numeric",
        "extractor": {"type": "human", "id": "测试", "version": "1"},
    }
    record.update(fields)
    return record


def judge_one(record: dict[str, Any], store: FactStore | None = None, as_of: date = AS_OF) -> Any:
    return judge_claim(record, JudgeContext.from_store(store or example_store(), as_of))


# ------------------------------------------------------------------ S12：错误参数、过期价格、不同测试工况


def test_s12_wrong_parameter_is_contradicted() -> None:
    verdict = one("澜序 S7 的 CLTC 续航为 700 公里")
    assert verdict.label == "contradicted"
    assert verdict.reason_code == "mismatch"
    assert (verdict.fact["fact_id"], verdict.fact["version"]) == ("F-S7-RANGE-CLTC", 1)
    assert "不一致" in verdict.reason


def test_s12_expired_price_is_contradicted_and_marked_stale() -> None:
    verdict = one("澜序 S7 售价 23.98 万元")
    assert verdict.label == "contradicted"
    assert verdict.reason_code == "stale_match"
    # 依据是当前有效的版本 2；与之一致的是已过期的版本 1。
    assert (verdict.fact["fact_id"], verdict.fact["version"]) == ("F-S7-PRICE", 2)
    assert "与已过期的版本一致（过期信息）" in verdict.reason
    assert verdict.stale == {
        "fact_id": "F-S7-PRICE",
        "version": 1,
        "kind": "expired",
        "valid_from": "2026-03-18",
        "valid_until": "2026-08-31",
    }
    # 旧价有效期内（08-31）同样的说法是支持。
    assert one("澜序 S7 售价 23.98 万元", as_of=date(2026, 8, 31)).label == "supported"


def test_s12_different_test_cycle_is_not_a_contradiction() -> None:
    only_cltc = example_store(without("F-S7-RANGE-WLTC"))
    verdict = one("澜序 S7 的 WLTC 续航为 500 公里", only_cltc)
    assert verdict.label == "unverifiable"
    assert verdict.reason_code == "condition_differs"
    assert "条件不同" in verdict.reason
    assert "不得判为矛盾" in verdict.reason
    # 事实库有 WLTC 的值时照常判定。
    assert labels("澜序 S7 的 WLTC 续航为 540 公里") == ["supported"]
    assert labels("澜序 S7 的 WLTC 续航为 500 公里") == ["contradicted"]
    # 版本配置不同同样不可比：长续航版的 CLTC 事实不能拿来判“标准版”。
    assert one("澜序 S7 标准版 CLTC 续航 520 公里").reason_code == "condition_differs"


# ------------------------------------------------------------------ 单位换算、写法变体


@pytest.mark.parametrize(
    "text",
    [
        "澜序 S7 指导价 219,800 元",
        "澜序 S7 售价 21.98 万元",
        "澜序 S7 卖 21.98 万",
        "澜序 S7 起售价二十一万九千八百元",
        "澜序 S7 的 CLTC 续航为 620 千米",
        "澜序 S7 的 CLTC 续航为六百二十公里",
        "澜序 S7 电池 82 度电",
        "澜序 S7 整车质保 5 年或 15 万公里",
        "澜序 S7 质保 5 年",
    ],
)
def test_unit_conversion_and_writing_variants_are_supported(text: str) -> None:
    assert one(text).label == "supported"


def test_warranty_mismatch() -> None:
    assert one("澜序 S7 整车质保 3 年或 10 万公里").label == "contradicted"
    assert one("澜序 S7 整车质保 5 年或 10 万公里").label == "contradicted"


# ------------------------------------------------------------------ 约数与范围的容差边界


def test_tolerance_constants_are_named() -> None:
    assert str(APPROX_RELATIVE) == "0.02"
    assert str(NEAR_RELATIVE) == "0.1"


@pytest.mark.parametrize(
    ("text", "fact_value", "label"),
    [
        # 约 620：容差 max(末位一半 5, 2% × 620 = 12.4) = 12.4 → [607.6, 632.4]
        ("约 620 公里", 632.4, "supported"),
        ("约 620 公里", 632.5, "contradicted"),
        ("约 620 公里", 607.6, "supported"),
        ("约 620 公里", 607.5, "contradicted"),
        # 约 600：末位在百位，容差 max(50, 12) = 50 → [550, 650]
        ("约 600 公里", 650, "supported"),
        ("约 600 公里", 651, "contradicted"),
        # 600 多：[600, 700)
        ("600 多公里", 699.9, "supported"),
        ("600 多公里", 700, "contradicted"),
        ("600 多公里", 599, "contradicted"),
        # 近 650：[0.9 × 650, 650] = [585, 650]
        ("近 650 公里", 585, "supported"),
        ("近 650 公里", 584.9, "contradicted"),
        ("近 650 公里", 650, "supported"),
        ("近 650 公里", 650.1, "contradicted"),
        # 范围 600～650：闭区间
        ("600～650 公里", 650, "supported"),
        ("600～650 公里", 650.5, "contradicted"),
        # 上下界
        ("超过 600 公里", 600, "contradicted"),
        ("超过 600 公里", 600.1, "supported"),
        ("不低于 600 公里", 600, "supported"),
        ("不到 600 公里", 600, "contradicted"),
        # 精确值不加容差
        ("620 公里", 620.1, "contradicted"),
    ],
)
def test_approximation_and_range_boundaries(text: str, fact_value: float, label: str) -> None:
    store = example_store(with_value("F-S7-RANGE-CLTC", fact_value))
    assert one(f"澜序 S7 的 CLTC 续航{text}", store).label == label


def test_fact_range_contains_point_claim() -> None:
    store = example_store(with_value("F-S7-RANGE-CLTC", {"min": 600, "max": 650}))
    assert one("澜序 S7 的 CLTC 续航为 620 公里", store).label == "supported"
    assert one("澜序 S7 的 CLTC 续航为 660 公里", store).label == "contradicted"
    # 只部分重叠的范围不可比。
    assert one("澜序 S7 的 CLTC 续航为 640～700 公里", store).reason_code == "incomparable"


# ------------------------------------------------------------------ 日期精度、型号写法


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("澜序 S7 于 2026 年 3 月上市", "supported"),
        ("澜序 S7 于 2026-03-18 上市", "supported"),
        ("澜序 S7 于 2026 年上市", "supported"),
        ("澜序 S7 于 2026 年 4 月上市", "contradicted"),
        ("澜序 S7 于 2026-03-19 上市", "contradicted"),
        ("澜序 S7 于 2025 年上市", "contradicted"),
    ],
)
def test_date_precision(text: str, label: str) -> None:
    assert one(text).label == label


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("澜序 S7 的电机型号为 LX-M220", "supported"),
        ("澜序 S7 的电机型号为 ＬＸ－Ｍ２２０", "supported"),
        ("澜序 S7 的电机型号为 lx m220", "supported"),
        ("澜序 S7 的电机型号为 LX-M330", "contradicted"),
    ],
)
def test_model_codes_across_widths(text: str, label: str) -> None:
    assert one(text).label == label


# ------------------------------------------------------------------ 实体：竞品同名车型


def test_competitor_with_the_same_model_name_uses_its_own_facts() -> None:
    verdict = one("岑野 S7 的续航为 620 公里")
    assert verdict.claim["entity_id"] == "product-cenye-s7"
    assert (
        verdict.label == "contradicted"
    )  # 岑野 S7 的 CLTC 续航是 580，不能拿澜序 S7 的 620 来支持
    assert verdict.fact["fact_id"] == "F-CENYE-S7-RANGE-CLTC"
    assert one("岑野 S7 的续航为 580 公里").label == "supported"


def test_unknown_or_unsupported_entities_are_unverifiable() -> None:
    assert one("澜序 S7 很好。朔风 S7 续航 620 公里").reason_code == "no_entity"
    verdict = one("岑野 K5 续航 580 公里")
    assert (verdict.label, verdict.reason_code, verdict.fact) == ("unverifiable", "no_fact", None)
    assert "不等于错误" in verdict.reason


# ------------------------------------------------------------------ 没写条件时的取舍


def test_claim_without_condition_against_several_conditions() -> None:
    supported = one("澜序 S7 续航 540 公里")
    assert supported.label == "supported"
    assert supported.fact["fact_id"] == "F-S7-RANGE-WLTC"
    contradicted = one("澜序 S7 续航 700 公里")
    assert contradicted.label == "contradicted"
    assert [fact["fact_id"] for fact in contradicted.compared] == [
        "F-S7-RANGE-CLTC",
        "F-S7-RANGE-WLTC",
    ]
    assert "F-S7-RANGE-CLTC" in contradicted.reason
    assert "F-S7-RANGE-WLTC" in contradicted.reason


def test_claim_without_condition_against_a_single_conditional_fact() -> None:
    assert one("澜序 X5 续航 510 公里").label == "supported"
    verdict = one("澜序 X5 续航 500 公里")
    assert verdict.label == "contradicted"
    assert "test_cycle=CLTC" in verdict.reason


# ------------------------------------------------------------------ 不可核验的各种原因


def test_free_text_is_never_judged() -> None:
    verdict = judge_one(
        claim(claim_type="free_text", attribute=None, value=None, unit=None, entity_id=None)
    )
    assert (verdict.label, verdict.reason_code) == ("unverifiable", "free_text")
    assert "自由文本判定未验证" in verdict.reason


def test_unusable_facts_make_claims_unverifiable() -> None:
    no_evidence = one("澜序 M9 百公里加速 4.5 秒")
    assert (no_evidence.label, no_evidence.reason_code) == ("unverifiable", "fact_unusable")
    assert no_evidence.fact["fact_id"] == "F-M9-ACCEL"
    assert "没有绑定证据" in no_evidence.reason
    conflict = one("澜序 E3 整备质量 1850 kg")
    assert conflict.reason_code == "fact_unusable"
    assert "冲突" in conflict.reason
    early = one("澜序 X5 续航 510 公里", as_of=date(2026, 3, 18))
    assert early.reason_code == "fact_unusable"
    assert "尚未生效" in early.reason


def test_wholly_expired_or_retracted_facts_cannot_contradict() -> None:
    def expire(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for fact in facts:
            if fact["fact_id"] == "F-E3-PRICE":
                fact["valid_until"] = "2026-06-30"
        return facts

    verdict = one("澜序 E3 售价 9.99 万元", example_store(expire))
    assert (verdict.label, verdict.reason_code) == ("unverifiable", "fact_unusable")
    assert "已过期" in verdict.reason

    def retract(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        extra = next(fact for fact in facts if fact["fact_id"] == "F-E3-PRICE")
        return [*facts, {**extra, "status": "retracted", "version": 2}]

    assert one("澜序 E3 售价 9.99 万元", example_store(retract)).reason_code == "fact_unusable"


def test_matching_an_unusable_sibling_is_not_a_contradiction() -> None:
    def strip_evidence(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for fact in facts:
            if fact["fact_id"] == "F-S7-RANGE-WLTC":
                fact["evidence"] = None
        return facts

    verdict = one("澜序 S7 续航 540 公里", example_store(strip_evidence))
    assert (verdict.label, verdict.reason_code) == ("unverifiable", "matches_unusable")


def test_expired_fact_with_its_own_id_counts_as_stale() -> None:
    def split(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for fact in facts:
            if fact["fact_id"] == "F-S7-PRICE" and fact["version"] == 1:
                result.append({**fact, "fact_id": "F-S7-PRICE-LAUNCH"})
            elif fact["fact_id"] == "F-S7-PRICE":
                result.append({**fact, "version": 1})
            else:
                result.append(fact)
        return result

    verdict = one("澜序 S7 售价 23.98 万元", example_store(split))
    assert (verdict.label, verdict.reason_code) == ("contradicted", "stale_match")
    assert verdict.stale["fact_id"] == "F-S7-PRICE-LAUNCH"


def test_superseded_version_counts_as_stale() -> None:
    def typo(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        old = next(fact for fact in facts if fact["fact_id"] == "F-E3-RANGE-CLTC")
        return [
            *[fact for fact in facts if fact["fact_id"] != "F-E3-RANGE-CLTC"],
            {**old, "value": 506},
            {**old, "version": 2},
        ]

    verdict = one("澜序 E3 的 CLTC 续航为 506 公里", example_store(typo))
    assert (verdict.label, verdict.reason_code) == ("contradicted", "stale_match")
    assert verdict.stale["kind"] == "superseded"
    assert "已被更正的旧版本" in verdict.reason


def test_hand_built_claims_edge_cases() -> None:
    assert judge_one(claim(value=None)).reason_code == "value_unparsed"
    assert judge_one(claim(attribute=None)).reason_code == "no_attribute"
    assert judge_one(claim(unit="kg")).reason_code == "incomparable"
    assert judge_one(claim(attribute="不存在的属性")).reason_code == "no_fact"
    opinion = judge_one(
        claim(attribute="乘坐舒适性", value="很舒适", unit=None, claim_type="model")
    )
    assert opinion.reason_code == "fact_unusable"
    assert "不在事实区" in opinion.reason


def test_unnormalizable_fact_value_is_incomparable() -> None:
    store = example_store(with_value("F-S7-RANGE-CLTC", "很远"))
    assert one("澜序 S7 的 CLTC 续航为 620 公里", store).reason_code == "incomparable"


# ------------------------------------------------------------------ 事实库 → 回答（呈现率）


def presence(
    text: str, core: list[str], store: FactStore | None = None
) -> tuple[list[Any], list[Any]]:
    target = store or example_store()
    claims = extract_claims(text, lexicon=build_lexicon(target), source=SOURCE)
    return presence_for(core, claims, JudgeContext.from_store(target, AS_OF))


def test_presence_labels() -> None:
    verdicts, excluded = presence(
        "澜序 S7 的 CLTC 续航为 620 公里，售价 23.98 万元。",
        ["F-S7-RANGE-CLTC", "F-S7-PRICE", "F-S7-LAUNCH"],
    )
    assert excluded == []
    assert [(item.fact["fact_id"], item.fact["version"], item.label) for item in verdicts] == [
        ("F-S7-RANGE-CLTC", 1, "present_correct"),
        ("F-S7-PRICE", 2, "present_wrong"),  # 说的是过期旧价
        ("F-S7-LAUNCH", 1, "missing"),
    ]
    assert len(verdicts[0].claims) == 1
    assert "一致" in verdicts[0].reason
    assert "不一致" in verdicts[1].reason
    assert "没有" in verdicts[2].reason


def test_presence_does_not_count_a_sibling_condition_as_wrong() -> None:
    # 540 是 WLTC 的值：它呈现的是 WLTC 事实，不算 CLTC 核心事实“呈现错误”。
    verdicts, _ = presence("澜序 S7 续航 540 公里", ["F-S7-RANGE-CLTC", "F-S7-RANGE-WLTC"])
    assert [item.label for item in verdicts] == ["missing", "present_correct"]
    # 明写 WLTC 的陈述与 CLTC 事实条件不同，同样不算。
    verdicts, _ = presence(
        "澜序 S7 的 WLTC 续航为 500 公里", ["F-S7-RANGE-CLTC", "F-S7-RANGE-WLTC"]
    )
    assert [item.label for item in verdicts] == ["missing", "present_wrong"]
    # 竞品同名车型的陈述不算目标车型的呈现。
    verdicts, _ = presence("岑野 S7 续航 620 公里", ["F-S7-RANGE-CLTC"])
    assert [item.label for item in verdicts] == ["missing"]


def test_presence_excludes_unusable_core_facts_and_rejects_unknown_ids() -> None:
    verdicts, excluded = presence("澜序 M9 百公里加速 4.5 秒", ["F-M9-ACCEL", "F-M9-PRICE"])
    assert [item.label for item in verdicts] == ["missing"]
    assert [(item.fact_id, item.reasons) for item in excluded] == [("F-M9-ACCEL", ("no_evidence",))]
    with pytest.raises(InputError, match="F-NOPE"):
        presence("澜序 S7 续航 620 公里", ["F-NOPE"])


# ------------------------------------------------------------------ 随机输入：同一个值的不同写法


_RANGE_WRITINGS = st.sampled_from(
    [
        "620 公里",
        "620km",
        "620 KM",
        "６２０ 公里",
        "620 千米",
        "六百二十公里",
        "约 620 公里",
        "620 公里左右",
        "600～650 公里",
        "600-650km",
        "600 多公里",
        "近 630 公里",
    ]
)
_PRICE_WRITINGS = st.sampled_from(
    [
        "21.98 万元",
        "21.98万",
        "２１.９８ 万元",
        "219,800 元",
        "219800 元",
        "二十一万九千八百元",
        "约 22 万元",
    ]
)
_ENTITY = st.sampled_from(["澜序 S7", "澜序S7", "澜序 Ｓ７", "LANXU S7", "澜序 s-7"])


@settings(max_examples=60, deadline=None)
@given(entity=_ENTITY, range_text=_RANGE_WRITINGS, price_text=_PRICE_WRITINGS)
def test_equivalent_writings_stay_supported(entity: str, range_text: str, price_text: str) -> None:
    text = f"{entity} 的 CLTC 续航为 {range_text}，售价 {price_text}。"
    verdicts = judge_text(text)
    assert [verdict.claim["attribute"] for verdict in verdicts] == ["续航里程", "售价"]
    assert [verdict.label for verdict in verdicts] == ["supported", "supported"]


# ------------------------------------------------------------------ 其他边界


def test_reversed_range_fact_cannot_be_normalized() -> None:
    store = example_store(with_value("F-S7-RANGE-CLTC", {"min": 650, "max": 600}))
    verdict = one("澜序 S7 的 CLTC 续航为 620 公里", store)
    assert verdict.reason_code == "incomparable"
    assert "无法归一" in verdict.reason


def test_contradiction_lists_range_facts_readably() -> None:
    store = example_store(with_value("F-S7-RANGE-CLTC", {"min": 600, "max": 650}))
    verdict = one("澜序 S7 续航 700 公里", store)
    assert verdict.label == "contradicted"
    assert "600~650 km" in verdict.reason
    assert "540 km" in verdict.reason


def test_stale_version_with_other_conditions_is_not_used() -> None:
    def typo(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        old = next(fact for fact in facts if fact["fact_id"] == "F-E3-RANGE-CLTC")
        return [
            *[fact for fact in facts if fact["fact_id"] != "F-E3-RANGE-CLTC"],
            {**old, "value": 506, "conditions": {"test_cycle": "WLTC"}},
            {**old, "version": 2},
        ]

    verdict = one("澜序 E3 的 CLTC 续航为 506 公里", example_store(typo))
    assert (verdict.label, verdict.reason_code, verdict.stale) == ("contradicted", "mismatch", None)


def test_presence_ignores_incomparable_and_unparsed_claims() -> None:
    store = example_store(with_value("F-S7-RANGE-CLTC", {"min": 600, "max": 650}))
    verdicts, _ = presence("澜序 S7 的 CLTC 续航为 640～700 公里", ["F-S7-RANGE-CLTC"], store)
    assert [item.label for item in verdicts] == ["missing"]
    ctx = JudgeContext.from_store(example_store(), AS_OF)
    found, _ = presence_for(
        ["F-S7-RANGE-CLTC"], [claim(value=None), claim(claim_type="free_text")], ctx
    )
    assert [item.label for item in found] == ["missing"]
    found, _ = presence_for(["F-S7-RANGE-CLTC", "F-S7-RANGE-CLTC"], [claim()], ctx)
    assert [item.label for item in found] == ["present_correct"]  # 重复的编号只判一次


def test_judgment_records_without_synthetic_flag() -> None:
    from geo_facts.judge.records import answer_judgment, presence_judgment

    ctx = JudgeContext.from_store(example_store(), AS_OF)
    verdict = judge_claim(claim(), ctx)
    observation = {"observation_id": "obs-1", "query_id": "Q001"}
    record = answer_judgment(
        verdict,
        observation,
        as_of=AS_OF,
        created_at="2026-10-08T02:00:00Z",
        project_id="p1",
        synthetic=False,
    )
    assert "synthetic" not in record
    assert record["fact_id"] == "F-S7-RANGE-CLTC"
    assert record["ext"]["reason_code"] == "match"
    found, _ = presence_for(["F-S7-RANGE-CLTC"], [claim()], ctx)
    flagged = presence_judgment(
        found[0],
        {**observation, "synthetic": True},
        as_of=AS_OF,
        created_at="2026-10-08T02:00:00Z",
        project_id="p1",
        synthetic=False,
    )
    assert flagged["synthetic"] is True
    assert flagged["claim_id"] is None
    assert flagged["ext"]["claim_ids"] == ["clm-hand-1"]
