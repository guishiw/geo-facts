"""陈述拆解：常见写法、原文位置、实体归属、竞品同名车型、否定与疑问。

期望值是人工逐句判断写出的（实体、属性、值、单位、条件、类型、原文片段），不是程序输出。
事实库用内置的虚构样例（init --example auto）。
"""

from __future__ import annotations

from typing import Any

import pytest
from helpers_store import memory_store
from hypothesis import given, settings
from hypothesis import strategies as st

from geo_facts import contracts
from geo_facts.claims import Lexicon, build_lexicon, extract_claims
from geo_facts.normalize import Measure, normalize_claim_value
from geo_facts.store import load_example

S7 = "product-lanxu-s7"
K5 = "product-cenye-k5"
CENYE_S7 = "product-cenye-s7"
SOURCE = {"type": "observation", "id": "obs-000001"}
CLTC = {"test_cycle": "CLTC"}
WLTC = {"test_cycle": "WLTC"}


def example_lexicon() -> Lexicon:
    entities, facts = load_example("auto", "auto-demo")
    return build_lexicon(memory_store(facts, entities))


LEXICON = example_lexicon()


def claims_of(text: str) -> list[dict[str, Any]]:
    claims = extract_claims(text, lexicon=LEXICON, source=SOURCE, project_id="auto-demo")
    for claim in claims:
        assert contracts.iter_issues("claim", claim) == []
        start, end = claim["span"]["start"], claim["span"]["end"]
        assert text[start:end] == claim["text"]
    return claims


def brief(text: str) -> list[tuple[Any, ...]]:
    """（实体, 属性, 值, 单位, 条件, 类型, 原文片段）。"""
    return [
        (
            claim["entity_id"],
            claim["attribute"],
            claim["value"],
            claim["unit"],
            claim["conditions"],
            claim["claim_type"],
            claim["text"],
        )
        for claim in claims_of(text)
    ]


CONTEXT = "澜序 S7 是一款中型轿车。"


# ------------------------------------------------------------------ 续航：条件的各种位置


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "澜序 S7 的 CLTC 续航为 620 公里",
            (S7, "续航里程", 620, "km", CLTC, "numeric", "澜序 S7 的 CLTC 续航为 620 公里"),
        ),
        (
            "澜序S7续航620km（CLTC工况）",
            (S7, "续航里程", 620, "km", CLTC, "numeric", "澜序S7续航620km（CLTC工况）"),
        ),
        (
            CONTEXT + "WLTC 续航约 540 公里",
            (S7, "续航里程", 540, "km", WLTC, "numeric", "WLTC 续航约 540 公里"),
        ),
        (
            CONTEXT + "续航（CLTC）：620 km",
            (S7, "续航里程", 620, "km", CLTC, "numeric", "续航（CLTC）：620 km"),
        ),
        (
            "在 WLTC 工况下，澜序 S7 的续航为 540 公里。",
            (S7, "续航里程", 540, "km", WLTC, "numeric", "澜序 S7 的续航为 540 公里"),
        ),
        (
            "澜序 S7 长续航版的 CLTC 续航为 620 公里",
            (
                S7,
                "续航里程",
                620,
                "km",
                {"test_cycle": "CLTC", "trim": "长续航版"},
                "numeric",
                "澜序 S7 长续航版的 CLTC 续航为 620 公里",
            ),
        ),
    ],
)
def test_range_with_conditions(text: str, expected: tuple[Any, ...]) -> None:
    assert brief(text) == [expected]


def test_approximate_value_keeps_the_qualifier() -> None:
    (claim,) = claims_of(CONTEXT + "WLTC 续航约 540 公里")
    assert claim["ext"]["quantity"]["qualifier"] == "approx"
    value = normalize_claim_value(claim)
    assert isinstance(value, Measure)
    # 约 540：容差 max(末位一半 5, 2% × 540 = 10.8) = 10.8 → [529.2, 550.8]
    assert str(value.interval.low) == "529.2"
    assert str(value.interval.high) == "550.8"


# ------------------------------------------------------------------ 价格


@pytest.mark.parametrize(
    ("text", "value", "unit", "span"),
    [
        ("澜序 S7 售价 21.98 万元起", 21.98, "万元", "澜序 S7 售价 21.98 万元"),
        ("澜序 S7 指导价 219,800 元", 219800, "元", "澜序 S7 指导价 219,800 元"),
        ("澜序 S7 起售价二十一万九千八百元", 219800, "元", "澜序 S7 起售价二十一万九千八百元"),
        ("澜序 S7 卖 21.98 万", 219800, None, "澜序 S7 卖 21.98 万"),
    ],
)
def test_price_phrasings(text: str, value: Any, unit: str | None, span: str) -> None:
    assert brief(text) == [(S7, "售价", value, unit, {}, "numeric", span)]


def test_scaled_price_without_unit_compares_as_yuan() -> None:
    (claim,) = claims_of("澜序 S7 卖 21.98 万")
    assert claim["ext"]["quantity"]["scaled"] is True


# ------------------------------------------------------------------ 加速、电池


@pytest.mark.parametrize(
    "text",
    ["澜序 S7 百公里加速 5.9 秒", "澜序 S7 零百加速 5.9s", "澜序 S7 的 0-100km/h 加速仅需 5.9 秒"],
)
def test_acceleration_phrasings(text: str) -> None:
    ((entity, attribute, value, unit, conditions, kind, span),) = brief(text)
    assert (entity, attribute, value, unit, conditions, kind) == (
        S7,
        "百公里加速",
        5.9,
        "s",
        {},
        "numeric",
    )
    assert span.startswith("澜序 S7")


@pytest.mark.parametrize(
    ("text", "span"),
    [
        ("澜序 S7 电池容量 82 kWh", "澜序 S7 电池容量 82 kWh"),
        ("澜序 S7 配备 82 度电的电池", "澜序 S7 配备 82 度电"),
        ("澜序 S7 的电池为 82 度电", "澜序 S7 的电池为 82 度电"),
    ],
)
def test_battery_phrasings(text: str, span: str) -> None:
    assert brief(text) == [(S7, "电池容量", 82, "kWh", {}, "numeric", span)]


# ------------------------------------------------------------------ 日期


@pytest.mark.parametrize(
    ("text", "value", "span"),
    [
        ("澜序 S7 于 2026 年 3 月上市", "2026-03", "澜序 S7 于 2026 年 3 月上市"),
        ("澜序 S7 于 2026-03-18 上市", "2026-03-18", "澜序 S7 于 2026-03-18 上市"),
        ("澜序 S7 上市时间：2026年3月18日", "2026-03-18", "澜序 S7 上市时间：2026年3月18日"),
        ("澜序 S7 于 2026/3/18 正式上市", "2026-03-18", "澜序 S7 于 2026/3/18 正式上市"),
        ("澜序 S7 在二〇二六年三月十八日上市", "2026-03-18", "澜序 S7 在二〇二六年三月十八日上市"),
        ("澜序 S7 于 2026 年上市", "2026年", "澜序 S7 于 2026 年上市"),
    ],
)
def test_launch_date_phrasings(text: str, value: str, span: str) -> None:
    assert brief(text) == [(S7, "上市时间", value, None, {}, "date", span)]


def test_dates_that_are_not_launch_dates_are_not_claims() -> None:
    # 生效日期只是背景：只拆出售价，不把 2026 年 9 月当成上市时间。
    assert brief("2026 年 9 月起，澜序 S7 售价调整为 21.98 万元") == [
        (S7, "售价", 21.98, "万元", {}, "numeric", "澜序 S7 售价调整为 21.98 万元")
    ]


# ------------------------------------------------------------------ 质保（组合数量）与范围、约数


def test_warranty_is_one_compound_claim() -> None:
    (claim,) = claims_of("澜序 S7 整车质保 5 年或 15 万公里")
    assert claim["entity_id"] == S7
    assert claim["attribute"] == "整车质保"
    assert claim["claim_type"] == "numeric"
    assert claim["value"] == "5 年或 15 万公里"
    assert claim["text"] == "澜序 S7 整车质保 5 年或 15 万公里"
    assert [part["unit"] for part in claim["ext"]["components"]] == ["年", "km"]
    assert [part["low"] for part in claim["ext"]["components"]] == ["5", "150000"]


@pytest.mark.parametrize(
    ("text", "value", "qualifier"),
    [
        ("澜序 S7 续航 600～650 公里", {"min": 600, "max": 650}, "range"),
        ("澜序 S7 续航约 620 公里", 620, "approx"),
        ("澜序 S7 续航 600 多公里", 600, "more"),
        ("澜序 S7 续航近 650 公里", 650, "near"),
    ],
)
def test_range_and_approximation(text: str, value: Any, qualifier: str) -> None:
    (claim,) = claims_of(text)
    assert (claim["entity_id"], claim["attribute"], claim["value"], claim["unit"]) == (
        S7,
        "续航里程",
        value,
        "km",
    )
    assert claim["conditions"] == {}
    assert claim["ext"]["quantity"]["qualifier"] == qualifier


# ------------------------------------------------------------------ 实体：全半角、大小写、空格、连字符、年款


@pytest.mark.parametrize(
    "name",
    [
        "澜序 S7",
        "澜序S7",
        "澜序 Ｓ７",
        "澜序 s 7",
        "澜序 S-7",
        "LANXU S7",
        "Lan-Xu S7",
        "澜序汽车 S7",
    ],
)
def test_entity_name_variants(name: str) -> None:
    assert [claim["entity_id"] for claim in claims_of(f"{name} 续航 620 公里")] == [S7]


def test_model_year_prefix() -> None:
    (claim,) = claims_of("2026 款澜序 S7 售价 21.98 万元")
    assert (claim["entity_id"], claim["attribute"], claim["value"]) == (S7, "售价", 21.98)
    # 事实库没有按年款区分的事实：年款记在 ext 里，不作为条件（否则所有事实都不可比）。
    assert claim["conditions"] == {}
    assert claim["ext"]["model_year"] == "2026"


def test_competitor_with_the_same_model_name_is_not_confused() -> None:
    assert [claim["entity_id"] for claim in claims_of("岑野 S7 的续航为 580 公里")] == [CENYE_S7]
    assert [claim["entity_id"] for claim in claims_of("岑野S7续航580km，澜序S7续航620km")] == [
        CENYE_S7,
        S7,
    ]


def test_unknown_model_of_a_known_brand_is_not_attributed_to_another_entity() -> None:
    # “朔风 S7”不存在：不得因为 S7 是澜序 S7 的别名就算到澜序 S7 头上。
    claims = claims_of(CONTEXT + "朔风 S7 续航 600 公里")
    assert [claim["entity_id"] for claim in claims] == [None]
    assert claims[0]["ext"]["entity_resolution"] == "unknown"
    # 品牌后面跟着事实库里没有的型号，同样不归属任何已知车型。
    assert [claim["entity_id"] for claim in claims_of(CONTEXT + "澜序 S9 续航 700 公里")] == [None]


# ------------------------------------------------------------------ 一句多陈述、列表、表格


def test_several_claims_in_one_sentence() -> None:
    assert brief("澜序 S7 续航 620 公里，售价 21.98 万元。") == [
        (S7, "续航里程", 620, "km", {}, "numeric", "澜序 S7 续航 620 公里"),
        (S7, "售价", 21.98, "万元", {}, "numeric", "售价 21.98 万元"),
    ]


def test_list_items() -> None:
    text = "澜序 S7 参数：\n- 续航：620 km\n- 售价：21.98 万元\n- 百公里加速：5.9 秒\n"
    assert [(claim["attribute"], claim["value"], claim["text"]) for claim in claims_of(text)] == [
        ("续航里程", 620, "续航：620 km"),
        ("售价", 21.98, "售价：21.98 万元"),
        ("百公里加速", 5.9, "百公里加速：5.9 秒"),
    ]
    assert {claim["entity_id"] for claim in claims_of(text)} == {S7}


def test_markdown_table_rows_use_the_header_as_attribute() -> None:
    text = (
        "| 车型 | CLTC 续航 | 售价 |\n"
        "|---|---|---|\n"
        "| 澜序 S7 | 620 km | 21.98 万元 |\n"
        "| 岑野 S7 | 580 km | 19.98 万元 |\n"
    )
    assert [
        (claim["entity_id"], claim["attribute"], claim["value"], claim["conditions"], claim["text"])
        for claim in claims_of(text)
    ] == [
        (S7, "续航里程", 620, CLTC, "620 km"),
        (S7, "售价", 21.98, {}, "21.98 万元"),
        (CENYE_S7, "续航里程", 580, CLTC, "580 km"),
        (CENYE_S7, "售价", 19.98, {}, "19.98 万元"),
    ]


def test_key_value_table() -> None:
    text = "澜序 S7 参数表\n\n| 项目 | 数值 |\n|---|---|\n| 续航（CLTC） | 620 km |\n| 售价 | 21.98 万元 |\n"
    assert [
        (claim["attribute"], claim["value"], claim["conditions"]) for claim in claims_of(text)
    ] == [
        ("续航里程", 620, CLTC),
        ("售价", 21.98, {}),
    ]


# ------------------------------------------------------------------ 实体省略与对比句


@pytest.mark.parametrize(
    "follow",
    ["它的续航为 620 公里", "该车续航 620 公里", "这款车的续航为 620 公里", "续航为 620 公里"],
)
def test_omitted_entity_goes_to_the_most_recent_mention(follow: str) -> None:
    (claim,) = claims_of(CONTEXT + follow)
    assert claim["entity_id"] == S7
    assert claim["ext"]["entity_resolution"] == "context"


def test_most_recent_entity_wins() -> None:
    text = "澜序 S7 售价 21.98 万元。岑野 K5 是它的竞品。该车续航 580 公里。"
    assert [(claim["entity_id"], claim["value"]) for claim in claims_of(text)] == [
        (S7, 21.98),
        (K5, 580),
    ]


def test_comparison_sentence_assigns_each_value_to_its_own_entity() -> None:
    assert brief("澜序 S7 续航 620 公里，岑野 K5 为 580 公里") == [
        (S7, "续航里程", 620, "km", {}, "numeric", "澜序 S7 续航 620 公里"),
        (K5, "续航里程", 580, "km", {}, "numeric", "岑野 K5 为 580 公里"),
    ]
    assert [
        (claim["entity_id"], claim["value"])
        for claim in claims_of("澜序 S7 的续航（620 公里）高于岑野 K5（580 公里）")
    ] == [(S7, 620), (K5, 580)]


def test_respectively() -> None:
    assert [
        (claim["entity_id"], claim["value"])
        for claim in claims_of("澜序 S7 和岑野 K5 的续航分别为 620 公里和 580 公里")
    ] == [(S7, 620), (K5, 580)]
    assert [
        (claim["conditions"], claim["value"])
        for claim in claims_of(CONTEXT + "CLTC 和 WLTC 续航分别为 620 公里和 540 公里")
    ] == [(CLTC, 620), (WLTC, 540)]


def test_elliptical_clause_inherits_the_attribute() -> None:
    assert [
        (claim["conditions"], claim["value"], claim["text"])
        for claim in claims_of(CONTEXT + "续航方面，CLTC 为 620 公里，WLTC 为 540 公里")
    ] == [(CLTC, 620, "CLTC 为 620 公里"), (WLTC, 540, "WLTC 为 540 公里")]
    (claim,) = claims_of(CONTEXT + "续航并不是 700 公里，而是 620 公里")
    assert (claim["value"], claim["attribute"], claim["text"]) == (620, "续航里程", "620 公里")


def test_change_amounts_and_unrelated_numbers_are_not_claims() -> None:
    assert claims_of("澜序 S7 比岑野 K5 便宜 2 万元") == []
    assert claims_of(CONTEXT + "续航比上一代提升 50 公里") == []
    texts = [
        claim["text"] for claim in claims_of(CONTEXT + "续航 620 公里，充电 30 分钟可补能 200 公里")
    ]
    assert texts == ["续航 620 公里"]
    assert claims_of(CONTEXT + "电池 8 年质保") == []
    assert claims_of("澜序 S7 是 5 座车，第 3 代平台") == []


# ------------------------------------------------------------------ 否定与疑问


@pytest.mark.parametrize(
    "text",
    [
        CONTEXT + "续航并不是 700 公里",
        CONTEXT + "续航不是 700 公里",
        CONTEXT + "售价并非 19.98 万元",
        CONTEXT + "续航有 700 公里吗？",
        CONTEXT + "续航能到 700 公里吗",
        CONTEXT + "续航是否达到 700 公里？",
        CONTEXT + "如果续航能达到 700 公里就好了",
        CONTEXT + "网传售价 19.98 万元",
    ],
)
def test_negations_questions_and_hearsay_are_not_affirmative_claims(text: str) -> None:
    assert claims_of(text) == []


def test_limits_are_not_negations() -> None:
    (claim,) = claims_of(CONTEXT + "续航不低于 600 公里")
    assert claim["ext"]["quantity"]["qualifier"] == "ge"
    (claim,) = claims_of(CONTEXT + "售价不超过 22 万元")
    assert claim["ext"]["quantity"]["qualifier"] == "le"


# ------------------------------------------------------------------ 型号类陈述


def test_model_code_claims() -> None:
    assert brief("澜序 S7 的电机型号为 LX-M220") == [
        (S7, "电机型号", "LX-M220", None, {}, "model", "澜序 S7 的电机型号为 LX-M220")
    ]
    (claim,) = claims_of("澜序 S7 搭载 ＬＸ－Ｍ２２０ 电机")
    assert (claim["attribute"], claim["value"], claim["claim_type"]) == (
        "电机型号",
        "ＬＸ－Ｍ２２０",
        "model",
    )


# ------------------------------------------------------------------ claim_id 与记录


def test_claim_ids_are_deterministic_and_distinct() -> None:
    text = "澜序 S7 续航 620 公里，售价 21.98 万元。"
    first = claims_of(text)
    second = claims_of(text)
    assert [claim["claim_id"] for claim in first] == [claim["claim_id"] for claim in second]
    assert len({claim["claim_id"] for claim in first}) == 2
    other = extract_claims(
        text, lexicon=LEXICON, source={"type": "observation", "id": "obs-2"}, project_id="auto-demo"
    )
    assert {claim["claim_id"] for claim in other}.isdisjoint({claim["claim_id"] for claim in first})
    claim = first[0]
    assert claim["extractor"] == {"type": "rule", "id": "geo-facts-rules", "version": "1.0"}
    assert claim["source"] == SOURCE
    assert claim["project_id"] == "auto-demo"
    assert claim["claim_id"].startswith("clm-")


def test_empty_and_claimless_texts() -> None:
    assert claims_of("") == []
    assert claims_of("澜序 S7 外观时尚，空间宽敞。") == []


# ------------------------------------------------------------------ 随机输入：原文位置


_NOISE = st.text(
    alphabet=st.sampled_from(
        [
            *"的了，。！？、 \n\t·—",
            chr(0x1F697),
            chr(0x1F600),
            chr(0x200D),
            chr(0x3000),
            chr(0xFF21),
            chr(0xFF11),
            *"abcXYZ0123456789",
            *"澜序岑野续航售价公里万元",
        ]
    ),
    max_size=8,
)
_PIECES = st.sampled_from(
    [
        "澜序 S7 的 CLTC 续航为 620 公里",
        "澜序Ｓ７续航６２０ｋｍ（ＣＬＴＣ工况）",
        "售价 21.98 万元",
        "指导价 219,800 元",
        "它的续航约 540 公里",
        "于 2026 年 3 月上市",
        "整车质保 5 年或 15 万公里",
        "岑野 S7 续航 580 公里",
        "- 百公里加速：5.9 秒",
        "续航有 700 公里吗？",
        chr(0x1F697) + "澜序 S7 卖 21.98 万" + chr(0x1F600),
    ]
)


@settings(max_examples=150, deadline=None)
@given(st.lists(st.tuples(_NOISE, _PIECES), min_size=1, max_size=5), _NOISE)
def test_spans_always_point_back_to_the_original_text(
    parts: list[tuple[str, str]], tail: str
) -> None:
    text = "".join(noise + piece for noise, piece in parts) + tail
    claims = claims_of(text)  # claims_of 断言 text[start:end] == claim.text，且符合契约
    spans = [(claim["span"]["start"], claim["span"]["end"]) for claim in claims]
    assert spans == sorted(spans)
    for start, end in spans:
        assert 0 <= start < end <= len(text)


# ------------------------------------------------------------------ 角标、序号、排除词


def test_citation_markers_and_list_numbers_are_not_values() -> None:
    text = "可以考虑：1. 澜序 S7；2. 岑野 K5。澜序 S7 的 CLTC 续航为 620 公里[1]。"
    assert brief(text) == [
        (S7, "续航里程", 620, "km", CLTC, "numeric", "澜序 S7 的 CLTC 续航为 620 公里")
    ]
    listed = "澜序 S7：\n1. 续航 620 公里\n2、售价 21.98 万元\n3) 百公里加速 5.9 秒"
    assert [(claim["attribute"], claim["value"]) for claim in claims_of(listed)] == [
        ("续航里程", 620),
        ("售价", 21.98),
        ("百公里加速", 5.9),
    ]
    marked = "澜序 S7 续航 620 公里【2】，售价 21.98 万元" + chr(0x00B9) + "。"
    assert [claim["value"] for claim in claims_of(marked)] == [620, 21.98]


def test_bare_number_only_right_after_the_attribute() -> None:
    assert [claim["value"] for claim in claims_of(CONTEXT + "续航 620")] == [620]
    assert [claim["value"] for claim in claims_of(CONTEXT + "续航 620 公里 1 次充满")] == [620]


def test_exclusion_phrase_is_not_a_brand_mention() -> None:
    (claim,) = claims_of("波澜序曲般的设计，续航 620 公里")
    assert claim["entity_id"] is None
    assert claim["ext"]["entity_resolution"] == "none"


# ------------------------------------------------------------------ 词表


def test_lexicon_entity_names() -> None:
    keys = dict(LEXICON.entity_keys)
    assert keys["澜序s7"] == S7
    assert keys["lanxus7"] == S7  # 品牌别名 + 型号（生成）
    assert keys["澜序汽车s7"] == S7
    assert keys["s7"] == S7  # 显式别名
    assert keys["岑野s7"] == CENYE_S7
    assert keys["cenyes7"] == CENYE_S7
    lengths = [len(key) for key, _ in LEXICON.entity_keys]
    assert lengths == sorted(lengths, reverse=True)


def test_ambiguous_and_derived_names() -> None:
    from helpers_store import make_entity

    entities = [
        make_entity("brand-a", "甲牌", kind="brand"),
        make_entity("brand-b", "乙牌", kind="brand", role="competitor"),
        make_entity("product-a-s7", "甲牌 S7", parent="brand-a", aliases=("S7",)),
        make_entity(
            "product-b-s7", "乙牌 S7", parent="brand-b", role="competitor", aliases=("S7",)
        ),
        make_entity("product-a-q3", "甲牌 Q3", parent="brand-a"),
        make_entity("product-b-q3", "乙牌 Q3", parent="brand-b", role="competitor"),
        make_entity("product-a-v9", "甲牌 V9", parent="brand-a"),
    ]
    lexicon = build_lexicon(memory_store([], entities))
    keys = dict(lexicon.entity_keys)
    assert keys["s7"] is None  # 两个显式别名冲突：匹配到也不归属
    assert "q3" not in keys  # 生成的写法冲突：不收录
    assert keys["v9"] == "product-a-v9"  # 生成的型号写法没有冲突时收录
    claims = extract_claims("S7 续航 600 公里", lexicon=lexicon, source=SOURCE)
    assert [claim["entity_id"] for claim in claims] == [None]
    assert claims[0]["ext"]["entity_resolution"] == "unknown"
    claims = extract_claims("V9 售价 30 万元", lexicon=lexicon, source=SOURCE)
    assert [(claim["entity_id"], claim["attribute"]) for claim in claims] == [
        ("product-a-v9", "售价")
    ]


def test_lexicon_concepts() -> None:
    assert LEXICON.concepts["range"].name_for(S7) == "续航里程"
    assert LEXICON.concepts["range"].name_for(K5) == "续航里程"  # K5 没有事实：取全库最常用的名字
    assert LEXICON.concepts["launch"].kind == "date"
    assert LEXICON.concepts["attr:电机型号"].kind == "model"
    assert LEXICON.concepts["warranty"].dimensions == frozenset({"calendar", "length"})
    assert LEXICON.condition_keys == frozenset({"test_cycle", "trim", "soc_range"})
    phrases = {key: concept for key, concept, _ in LEXICON.attribute_keys}
    assert phrases["电机"] == "attr:电机型号"
    assert phrases["续驶里程"] == "range"
    empty = build_lexicon(memory_store([], []))
    assert empty.concepts["price"].default_name == "售价"
    assert empty.concepts["price"].dimensions == frozenset({"currency"})
    assert extract_claims("售价 21.98 万元", lexicon=empty, source=SOURCE)[0]["entity_id"] is None


def test_model_year_condition_when_the_store_has_one() -> None:
    from helpers_store import make_fact

    entities, facts = load_example("auto", "auto-demo")
    facts.append(
        make_fact(
            "F-S7-PRICE-2025",
            attribute="售价",
            value=22.98,
            unit="万元",
            conditions={"model_year": "2025"},
        )
    )
    lexicon = build_lexicon(memory_store(facts, entities))
    (claim,) = extract_claims("2026 款澜序 S7 售价 21.98 万元", lexicon=lexicon, source=SOURCE)
    assert claim["conditions"] == {"model_year": "2026"}
    assert "model_year" not in claim["ext"]
