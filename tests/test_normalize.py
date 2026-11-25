"""归一模块测试。期望值均为手工推算，写在每个用例旁边。"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from geo_facts.normalize import (
    MASK,
    BoolValue,
    Compound,
    DateValue,
    Interval,
    Measure,
    Outcome,
    TextValue,
    canonical_conditions,
    canonical_value,
    compact,
    compare_conditions,
    compare_values,
    dates_consistent,
    describe_value,
    dimension_of,
    fold,
    is_invisible,
    lookup_unit,
    make_view,
    model_key,
    normalize_claim_value,
    normalize_fact_value,
    parse_arabic,
    parse_cn_integer,
    parse_cn_number,
    parse_date,
    parse_number,
    parse_quantities,
    same_conditions,
    same_model,
    same_value,
    scan_conditions,
    scan_dates,
    scan_quantities,
    split_model_year,
    to_chinese,
    unit_for,
)
from geo_facts.normalize.quantity import approx_interval, qualified_interval
from geo_facts.normalize.values import measure_from_ext, quantity_ext, to_decimal

D = Decimal
ZWSP = chr(0x200B)
ZWJ = chr(0x200D)


def fullwidth(text: str) -> str:
    """把半角 ASCII 换成全角（独立于被测实现的转换：码点 + 0xFEE0）。"""
    return "".join(chr(ord(char) + 0xFEE0) if "!" <= char <= "~" else char for char in text)


# ------------------------------------------------------------------ 文本折叠


@pytest.mark.parametrize("variant", ["S7", "Ｓ７", "s 7", "S-7", "s—7", "S" + ZWSP + "7", " S 7 "])
def test_model_spellings_share_one_key(variant: str) -> None:
    assert model_key(variant) == "s7"
    assert same_model(variant, "S7")


def test_fold_keeps_spaces_but_compact_drops_them() -> None:
    assert fold("ＬＡＮＸＵ Ｓ７") == "lanxu s7"
    assert compact("Lan-Xu  S7") == "lanxus7"
    assert compact("澜序 Ｓ７") == "澜序s7"


def test_invisible_characters() -> None:
    for code in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x202E, 0x2066, 0xE0041):
        assert is_invisible(chr(code))
    for char in ("a", " ", "中", "\n", "-"):
        assert not is_invisible(char)


def test_view_maps_back_to_original_positions() -> None:
    text = "澜序 Ｓ７" + ZWSP + " 续航**620**公里"
    view = make_view(text, drop=frozenset("*"))
    assert view.folded == "澜序 s7 续航620公里"
    start = view.folded.index("620")
    left, right = view.span(start, start + 3)
    assert text[left:right] == "620"
    start = view.folded.index("s7")
    assert view.original_slice(start, start + 2) == "Ｓ７"
    # 跨过被丢弃的字符时，原文区间把它们包含在内
    start = view.folded.index("续航")
    assert view.original_slice(start, start + 5) == "续航**620"
    with pytest.raises(ValueError, match="非法区间"):
        view.span(3, 3)


def test_view_compact_mode_drops_spaces_hyphens_and_newlines() -> None:
    view = make_view("S - 7\nX5", compact_mode=True)
    assert view.folded == "s7x5"
    assert view.span(0, 2) == (0, 5)
    plain = make_view("a\r\nb\tc")
    assert plain.folded == "a\n\nb c"


@given(st.text(max_size=40))
def test_view_positions_always_point_at_source_characters(text: str) -> None:
    view = make_view(text)
    assert len(view.folded) == len(view.starts)
    assert list(view.starts) == sorted(view.starts)
    for index, origin in enumerate(view.starts):
        assert 0 <= origin < len(text)
        assert not is_invisible(text[origin])
        if index + 1 <= len(view.folded):
            left, right = view.span(index, index + 1)
            assert right == left + 1


@given(
    st.text(alphabet="abcXYZ0189澜序岑野", min_size=1, max_size=8),
    st.lists(st.sampled_from([" ", "-", ZWSP, chr(0x3000)]), max_size=8),
    st.randoms(use_true_random=False),
)
def test_compact_ignores_width_case_spaces_and_hyphens(name: str, noise: list[str], rng) -> None:  # type: ignore[no-untyped-def]
    chars = list(fullwidth(name) if rng.random() < 0.5 else name.swapcase())
    for piece in noise:
        chars.insert(rng.randrange(len(chars) + 1), piece)
    assert compact("".join(chars)) == compact(name)


def test_split_model_year() -> None:
    assert split_model_year("2026 款澜序 S7") == ("澜序s7", "2026")
    assert split_model_year("澜序 S7 2025年款") == ("澜序s7", "2025")
    assert split_model_year("澜序 S7") == ("澜序s7", None)


# ------------------------------------------------------------------ 数字


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("二十一万九千八百", 219800),
        ("两万", 20000),
        ("十五", 15),
        ("十", 10),
        ("一百零五", 105),
        ("一千零五十", 1050),
        ("两万五", 25000),  # 口语省略：2 万 + 5 千
        ("三千五", 3500),
        ("一百五", 150),
        ("一万零五", 10005),
        ("二〇二六", 2026),  # 按位读
        ("十万", 100000),
        ("一亿二千万", 120000000),
        ("一亿二", 120000000),
        ("壹万贰仟", 12000),
        ("零", 0),
        ("九", 9),
        ("三百二十一", 321),
    ],
)
def test_chinese_integers(text: str, expected: int) -> None:
    assert parse_cn_integer(text) == expected


@pytest.mark.parametrize(
    "text", ["", "万", "十十", "一百十", "五百零", "一万一万", "三四百", "千万", "abc", "一2"]
)
def test_invalid_chinese_integers(text: str) -> None:
    assert parse_cn_integer(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("三点五", "3.5"),
        ("二十一点九八万", "219800"),  # 21.98 × 10000
        ("零点五", "0.5"),
        ("一点二亿", "120000000"),
        ("五", "5"),
    ],
)
def test_chinese_decimals(text: str, expected: str) -> None:
    assert parse_cn_number(text) == D(expected)


@pytest.mark.parametrize("text", ["", "点五", "三点", "三点五点六", "三点十", "x点五", "三点万"])
def test_invalid_chinese_decimals(text: str) -> None:
    assert parse_cn_number(text) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "零"),
        (10, "十"),
        (15, "十五"),
        (105, "一百零五"),
        (20000, "二万"),
        (100000, "十万"),
        (219800, "二十一万九千八百"),
        (1000010, "一百万零一十"),
        (100000001, "一亿零一"),
        (120000000, "一亿二千万"),
        (2001000, "二百万一千"),
    ],
)
def test_to_chinese_known_values(value: int, expected: str) -> None:
    assert to_chinese(value) == expected


def test_to_chinese_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="只支持"):
        to_chinese(-1)
    with pytest.raises(ValueError, match="只支持"):
        to_chinese(10**12)


@given(st.integers(min_value=0, max_value=10**12 - 1))
def test_chinese_number_roundtrip(value: int) -> None:
    assert parse_cn_integer(to_chinese(value)) == value


@pytest.mark.parametrize(
    ("text", "expected"),
    [("219,800", "219800"), ("21.98", "21.98"), ("1,234,567.5", "1234567.5"), (" 620 ", "620")],
)
def test_arabic_numbers(text: str, expected: str) -> None:
    assert parse_arabic(text) == D(expected)
    assert parse_number(text) == D(expected)


@pytest.mark.parametrize("text", ["", "1,23", "12,3456", "abc", "1.", "1.x", "1,,000", "²"])
def test_invalid_arabic_numbers(text: str) -> None:
    assert parse_arabic(text) is None


@given(st.integers(min_value=0, max_value=10**9))
def test_thousands_separator_roundtrip(value: int) -> None:
    assert parse_arabic(f"{value:,}") == D(value)
    assert parse_number(fold(fullwidth(str(value)))) == D(value)


def test_parse_number_accepts_chinese() -> None:
    assert parse_number("两万") == D(20000)
    assert parse_number("不是数字") is None


# ------------------------------------------------------------------ 单位


def test_unit_aliases_and_dimensions() -> None:
    for name in ("km", "KM", "公里", "千米", "ｋｍ"):
        unit = lookup_unit(name)
        assert unit is not None
        assert (unit.symbol, unit.dimension) == ("km", "length")
    assert lookup_unit("度") == lookup_unit("kWh") == lookup_unit("千瓦时")
    assert lookup_unit("秒") == lookup_unit("s")
    assert lookup_unit(None) is None
    assert lookup_unit("光年") is None
    assert dimension_of(None) is None
    assert dimension_of("  ") is None
    assert dimension_of("万元") == "currency"
    assert dimension_of("光年") == "unit:光年"
    assert unit_for("光年").factor == D(1)


@pytest.mark.parametrize(
    ("value", "unit", "base"),
    [
        ("21.98", "万元", "219800"),  # 21.98 × 10000 元
        ("1", "km", "1000"),
        ("5", "年", "60"),  # 5 × 12 个月
        ("18", "分钟", "1080"),  # 18 × 60 秒
        ("1", "小时", "3600"),
        ("82", "度", "82"),
        ("500", "Wh", "0.5"),
        ("2", "吨", "2000"),
        ("1", "亿元", "100000000"),
    ],
)
def test_unit_conversion_to_base(value: str, unit: str, base: str) -> None:
    assert unit_for(unit).to_base(D(value)) == D(base)


@given(st.decimals(min_value=0, max_value=10**6, places=2))
def test_unit_conversion_is_consistent_across_spellings(value: Decimal) -> None:
    # 同一个量用不同单位写出来，归一后必须相等
    yuan = normalize_fact_value(float(value * 10000), "元")
    wan = normalize_fact_value(float(value), "万元")
    assert yuan is not None
    assert wan is not None
    assert same_value(yuan, wan)
    assert compare_values(yuan, wan).outcome is Outcome.MATCH
    km = normalize_fact_value(float(value), "公里")
    metres = normalize_fact_value(float(value * 1000), "米")
    assert km is not None
    assert metres is not None
    assert same_value(km, metres)


# ------------------------------------------------------------------ 数量扫描


def one(text: str):  # type: ignore[no-untyped-def]
    matches = scan_quantities(make_view(text).folded)
    assert len(matches) == 1, matches
    return matches[0].quantity


@pytest.mark.parametrize(
    ("text", "value", "unit", "base"),
    [
        ("续航 620 公里", "620", "km", "620000"),
        ("620km", "620", "km", "620000"),
        ("６２０千米", "620", "km", "620000"),
        ("售价 21.98 万元起", "21.98", "万元", "219800"),
        ("指导价 219,800 元", "219800", "元", "219800"),
        ("起售价二十一万九千八百元", "219800", "元", "219800"),
        ("加速 5.9s", "5.9", "s", "5.9"),
        ("加速 5.9 秒", "5.9", "s", "5.9"),
        ("电池 82 kWh", "82", "kWh", "82"),
        ("82 度电", "82", "kWh", "82"),
        ("需 18 分钟", "18", "min", "1080"),
        ("质保 15 万公里", "150000", "km", "150000000"),
        ("百分之八十", "80", "%", "80"),
        ("80％", "80", "%", "80"),
        ("三点五秒", "3.5", "s", "3.5"),
        ("质保五年", "5", "年", "60"),
        ("¥219,800", "219800", "元", "219800"),
        ("￥21.98万", "21.98", "万元", "219800"),
        ("2 千万元", "20000000", "元", "20000000"),
        ("3 千米", "3", "km", "3000"),
        ("100 千瓦", "100", "kW", "100"),
    ],
)
def test_exact_quantities(text: str, value: str, unit: str, base: str) -> None:
    quantity = one(text)
    assert quantity.qualifier == "exact"
    assert quantity.value == D(value)
    assert quantity.unit is not None
    assert quantity.unit.symbol == unit
    assert quantity.base_interval() == Interval(low=D(base), high=D(base))
    assert not quantity.delta


@pytest.mark.parametrize(
    ("text", "qualifier", "low", "high", "low_closed", "high_closed"),
    [
        # 约 620：容差 max(10/2, 620×2%) = 12.4
        ("约 620 公里", "approx", "607.6", "632.4", True, True),
        # 约 600：末位有效数字在百位，容差 max(50, 12) = 50
        ("约 600 公里", "approx", "550", "650", True, True),
        ("620 公里左右", "approx", "607.6", "632.4", True, True),
        ("大约 5.9 秒", "approx", "5.782", "6.018", True, True),  # max(0.05, 0.118)
        ("600 多公里", "more", "600", "700", True, False),
        ("六百余公里", "more", "600", "700", True, False),
        ("620 多公里", "more", "620", "630", True, False),
        ("3 年多", "more", "3", "4", True, False),
        ("超过 600 公里", "gt", "600", None, False, True),
        ("600 公里以上", "ge", "600", None, True, True),
        ("至少 600 公里", "ge", "600", None, True, True),
        ("不到 6 秒", "lt", None, "6", True, False),
        ("6 秒以内", "le", None, "6", True, True),
        ("不超过 6 秒", "le", None, "6", True, True),
        ("接近 700 公里", "near", "630", "700", True, True),  # 700 × 0.9
        ("600～650 公里", "range", "600", "650", True, True),
        ("600-650km", "range", "600", "650", True, True),
        ("600 至 650 公里", "range", "600", "650", True, True),
        ("600 公里到 650 公里", "range", "600", "650", True, True),
        ("30% 到 80%", "range", "30", "80", True, True),
        ("20-25 万元", "range", "20", "25", True, True),
    ],
)
def test_qualified_quantities(
    text: str,
    qualifier: str,
    low: str | None,
    high: str | None,
    low_closed: bool,
    high_closed: bool,
) -> None:
    quantity = one(text)
    assert quantity.qualifier == qualifier
    expected = Interval(
        low=None if low is None else D(low),
        high=None if high is None else D(high),
        low_closed=low_closed,
        high_closed=high_closed,
    )
    assert quantity.interval == expected


def test_scaled_unitless_quantities() -> None:
    near = one("近 20 万")
    assert near.unit is None
    assert near.scaled
    assert near.interval == Interval(low=D(180000), high=D(200000))  # 20 万 × 0.9
    more = one("20 多万")
    assert more.interval == Interval(low=D(200000), high=D(300000), high_closed=False)
    more_wan = one("1 万多公里")
    assert more_wan.interval == Interval(low=D(10000), high=D(20000), high_closed=False)
    approx = one("约 22 万元")
    # 22 万：容差 max(1 万 / 2, 22 万 × 2%) = 0.5 万；区间以书写单位“万元”计
    assert approx.interval == Interval(low=D("21.5"), high=D("22.5"))
    assert approx.base_interval() == Interval(low=D(215000), high=D(225000))
    plain = one("两万")
    assert (plain.value, plain.unit, plain.scaled) == (D(20000), None, True)


@pytest.mark.parametrize(
    "text", ["提升了 10%", "降价 2 万元", "比它多 40 公里", "优惠约 5000 元", "便宜了 3000 元"]
)
def test_delta_quantities_are_flagged(text: str) -> None:
    assert one(text).delta


@pytest.mark.parametrize(
    "text", ["最多 40 公里", "至少 5 年", "降至 21.98 万元", "提升到 620 公里"]
)
def test_targets_and_bounds_are_not_deltas(text: str) -> None:
    assert not one(text).delta


@pytest.mark.parametrize(
    "text",
    [
        "岑野 K5",
        "第 3 代",
        "第一代",
        "2.0T 发动机",
        "3 档可调",
        "一度成为",
        "一个",
        "三元锂",
        "三四年",
        "万一",
        "千万别",
        "v2.0",
        "百分之",
        "5G",
    ],
)
def test_non_quantities_are_ignored(text: str) -> None:
    quantities = scan_quantities(make_view(text).folded)
    assert [match.quantity for match in quantities if match.quantity.unit is None] == []
    if text != "5G":
        assert quantities == []


def test_scanner_details() -> None:
    folded = make_view("节约 3 万元，最近 5 年，反超 20 辆").folded
    assert [match.quantity.qualifier for match in scan_quantities(folded)] == ["exact"] * 3
    # “1,2345” 不是千分位写法，按普通数字读出 1 和 2345 之外不应拼成 12345
    assert [m.quantity.value for m in scan_quantities("1,2345")] == [D(1), D(2345)]
    # 被遮蔽的位置不能被当成单位
    masked = "82 " + MASK * 2
    quantity = scan_quantities(masked)[0].quantity
    assert (quantity.value, quantity.unit) == (D(82), None)
    # 范围后一个数写反时不合并
    assert [m.quantity.qualifier for m in scan_quantities("650-600")] == ["exact", "exact"]
    # 位置信息
    match = scan_quantities("续航约 620 公里。")[0]
    assert (match.start, match.end) == (2, 10)
    assert scan_quantities("600 多的")[0].quantity.qualifier == "more"
    assert scan_quantities("600 多功能")[0].quantity.qualifier == "exact"
    assert scan_quantities("600 公里 - 650")[0].quantity.qualifier == "exact"
    assert [m.quantity.value for m in scan_quantities("3-5 档")] == [D(3)]


def test_interval_helpers() -> None:
    closed = Interval(low=D(1), high=D(3))
    assert closed.contains(D(1))
    assert closed.contains(D(3))
    assert not closed.contains(D("3.01"))
    half_open = Interval(low=D(1), high=D(3), high_closed=False)
    assert not half_open.contains(D(3))
    assert not Interval(low=D(1), high=None, low_closed=False).contains(D(1))
    assert Interval(low=None, high=None).contains(D(-5))
    assert closed.contains_interval(Interval(low=D(2), high=D(3)))
    assert not half_open.contains_interval(closed)
    assert not closed.contains_interval(Interval(low=D(0), high=D(2)))
    assert not closed.contains_interval(Interval(low=None, high=D(2)))
    assert not closed.contains_interval(Interval(low=D(2), high=None))
    assert not Interval(low=D(1), high=D(3), low_closed=False).contains_interval(closed)
    assert closed.overlaps(Interval(low=D(3), high=D(5)))
    assert not half_open.overlaps(Interval(low=D(3), high=D(5)))
    assert not closed.overlaps(Interval(low=D(4), high=None))
    assert closed.overlaps(Interval(low=None, high=None))
    assert closed.describe() == "[1, 3]"
    assert Interval(low=None, high=D("2.50"), high_closed=False).describe() == "(−∞, 2.5)"
    assert Interval(low=D(0), high=None, low_closed=False).describe() == "(0, +∞)"
    assert approx_interval(D(620), D(10)) == Interval(low=D("607.6"), high=D("632.4"))
    assert qualified_interval("exact", D(5), D(1)) == Interval(low=D(5), high=D(5))


@given(st.integers(min_value=1, max_value=10**6), st.sampled_from(["公里", "km", "千米", "KM"]))
def test_exact_claims_never_get_tolerance(value: int, unit: str) -> None:
    quantity = one(f"续航 {value} {unit}")
    assert quantity.interval.is_point
    assert quantity.base_interval().low == D(value) * 1000


@given(st.integers(min_value=1, max_value=10**6))
def test_approx_interval_always_contains_the_stated_value(value: int) -> None:
    quantity = one(f"约 {value} 公里")
    assert quantity.interval.contains(D(value))
    assert quantity.interval.low is not None
    assert quantity.interval.high is not None
    assert quantity.interval.high - quantity.interval.low >= D(value) * D("0.04")


def test_parse_quantities_for_whole_strings() -> None:
    parts = parse_quantities("5 年或 15 万公里")
    assert parts is not None
    assert [(part.value, part.unit.symbol if part.unit else None) for part in parts] == [
        (D(5), "年"),
        (D(150000), "km"),
    ]
    single = parse_quantities("21.98 万元起")
    assert single is not None
    assert len(single) == 1
    assert parse_quantities("安静得像一片湖") is None
    assert parse_quantities("续航 620 公里") is None
    assert parse_quantities("620 公里（CLTC）") is None
    assert parse_quantities("  ") is None


# ------------------------------------------------------------------ 日期


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026年3月18日", DateValue(2026, 3, 18)),
        ("2026 年 3 月 18 号", DateValue(2026, 3, 18)),
        ("2026-03-18", DateValue(2026, 3, 18)),
        ("2026/3/18", DateValue(2026, 3, 18)),
        ("2026.3.18", DateValue(2026, 3, 18)),
        ("２０２６－０３－１８", DateValue(2026, 3, 18)),
        ("二〇二六年三月十八日", DateValue(2026, 3, 18)),
        ("2026 年 3 月", DateValue(2026, 3)),
        ("2026-03", DateValue(2026, 3)),
        ("2026年", DateValue(2026)),
    ],
)
def test_date_formats(text: str, expected: DateValue) -> None:
    assert parse_date(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "2026-02-30",
        "2026年13月",
        "2026/3",
        "1800年",
        "3月18日",
        "2026年款",
        "明年",
        "2026-03-18 上市",
    ],
)
def test_invalid_or_partial_dates(text: str) -> None:
    assert parse_date(text) is None


def test_date_precision_and_iso() -> None:
    day, month, year = DateValue(2026, 3, 18), DateValue(2026, 3), DateValue(2026)
    assert (day.precision, month.precision, year.precision) == ("day", "month", "year")
    assert (day.iso(), month.iso(), year.iso()) == ("2026-03-18", "2026-03", "2026")
    assert dates_consistent(day, month)
    assert dates_consistent(month, day)
    assert dates_consistent(day, year)
    assert not dates_consistent(day, DateValue(2026, 4))
    assert not dates_consistent(day, DateValue(2026, 3, 19))
    assert not dates_consistent(day, DateValue(2025))
    assert dates_consistent(day, DateValue(2026, 3, 18))


def test_scan_dates_positions_and_overlap() -> None:
    folded = "于 2026 年 3 月上市，2026-03-18 交付，2026 年款不算"
    matches = scan_dates(folded)
    assert [match.value for match in matches] == [DateValue(2026, 3), DateValue(2026, 3, 18)]
    assert folded[matches[0].start : matches[0].end] == "2026 年 3 月"
    assert folded[matches[1].start : matches[1].end] == "2026-03-18"


@given(
    st.dates(
        min_value=__import__("datetime").date(1900, 1, 1),
        max_value=__import__("datetime").date(2199, 12, 31),
    )
)
def test_all_date_spellings_agree(day) -> None:  # type: ignore[no-untyped-def]
    expected = DateValue(day.year, day.month, day.day)
    spellings = [
        day.isoformat(),
        f"{day.year}/{day.month}/{day.day}",
        f"{day.year}年{day.month}月{day.day}日",
        f"{day.year} 年 {day.month} 月 {day.day} 日",
        f"{to_chinese_year(day.year)}年{to_chinese(day.month)}月{to_chinese(day.day)}日",
    ]
    for spelling in spellings:
        assert parse_date(spelling) == expected, spelling
    month = parse_date(f"{day.year}年{day.month}月")
    assert month is not None
    assert dates_consistent(month, expected)


def to_chinese_year(year: int) -> str:
    return "".join("〇一二三四五六七八九"[int(digit)] for digit in str(year))


# ------------------------------------------------------------------ 适用条件


def test_canonical_condition_values() -> None:
    assert canonical_value("test_cycle", "cltc") == "CLTC"
    assert canonical_value("test_cycle", "CLTC-P") == "CLTC"
    assert canonical_value("test_cycle", "WLTP") == "WLTC"
    assert canonical_value("test_cycle", "自定工况") == "自定工况"
    assert canonical_value("trim", "长续航版") == "长续航"
    assert canonical_value("trim", "长续航版本") == "长续航"
    assert canonical_value("trim", "长续航款") == "长续航"
    assert canonical_value("trim", "Pro 版") == "pro"
    assert canonical_value("trim", "版") == "版"
    assert canonical_value("model_year", "2026 款") == "2026"
    assert canonical_value("model_year", "26款") == "2026"
    assert canonical_value("model_year", "新款") == "新款"
    assert canonical_value("soc_range", "30% 到 80%") == "30%-80%"
    assert canonical_value("soc_range", "快充区间") == "快充区间"
    assert canonical_value("drivetrain", "四 驱") == "四驱"
    assert canonical_conditions({" Test_Cycle ": "cltc", "trim": "长续航版"}) == {
        "test_cycle": "CLTC",
        "trim": "长续航",
    }


def test_condition_comparability() -> None:
    fact = {"test_cycle": "CLTC", "trim": "长续航版"}
    assert compare_conditions({}, fact) == (True, None)
    assert compare_conditions({"test_cycle": "cltc"}, fact) == (True, None)
    comparable, reason = compare_conditions({"test_cycle": "WLTC"}, fact)
    assert not comparable
    assert reason is not None
    assert "条件不同" in reason
    assert "WLTC" in reason
    comparable, reason = compare_conditions({"model_year": "2026"}, fact)
    assert not comparable
    assert reason is not None
    assert "事实没有这一条件" in reason
    # CLTC 与 WLTC、NEDC、EPA 两两不可互换
    cycles = ["CLTC", "WLTC", "NEDC", "EPA"]
    for first in cycles:
        for second in cycles:
            comparable, _ = compare_conditions({"test_cycle": first}, {"test_cycle": second})
            assert comparable == (first == second)
    assert same_conditions({"trim": "长续航版"}, {"trim": "长续航版本"})
    assert not same_conditions({"trim": "长续航版"}, {})
    assert not same_conditions({"test_cycle": "CLTC"}, {"test_cycle": "WLTC"})


def scan(text: str, **kwargs):  # type: ignore[no-untyped-def]
    folded = make_view(text).folded
    return [(m.key, m.value, folded[m.start : m.end]) for m in scan_conditions(folded, **kwargs)]


def test_scan_conditions() -> None:
    assert scan("CLTC 续航 620 公里") == [("test_cycle", "CLTC", "cltc")]
    assert scan("续航620km（WLTP工况）") == [("test_cycle", "WLTC", "wltp")]
    assert scan("CLTC-P 工况") == [("test_cycle", "CLTC", "cltc-p")]
    assert scan("excltcx epat") == []
    assert scan("2026 款澜序 S7") == [("model_year", "2026", "2026 款")]
    assert scan("2026年款") == [("model_year", "2026", "2026年款")]
    assert scan("有 20 款车型") == []
    assert scan("澜序 S7 长续航版") == [("trim", "长续航版", "长续航版")]
    assert scan("它的标准版本售价") == [("trim", "标准版本", "标准版本")]
    assert scan("澜序 S7 Pro 版") == [("trim", "pro 版", "pro 版")]
    assert scan("四驱pro版") == [("trim", "四驱pro版", "四驱pro版")]
    assert scan("版权所有，新版上市，出版，排版面") == []
    assert scan("2026版") == []


def test_scan_conditions_respects_blocked_positions_and_vocabulary() -> None:
    text = "澜序s7长续航版"
    blocked = [index < 4 for index in range(len(text))]
    mentions = scan_conditions(text, blocked=blocked)
    assert [(m.value, m.start, m.end) for m in mentions] == [("长续航版", 4, 8)]
    # 没有遮蔽时，“版”前面 7 个字符都不是停用字，且不超过 8 个字符的上限，
    # 整段都会被并进版本名；所以拆解时必须先遮蔽实体
    assert scan_conditions(text)[0].start == 0
    vocabulary = {"trim": frozenset({"长续航"}), "drivetrain": frozenset({"四驱"})}
    found = scan("长续航款，四驱", vocabulary=vocabulary)
    assert found == [("trim", "长续航款", "长续航款"), ("drivetrain", "四驱", "四驱")]
    assert scan("长续航型号", vocabulary=vocabulary) == [("trim", "长续航型", "长续航型")]
    blocked_all = [True] * len("cltc")
    assert scan_conditions("cltc", blocked=blocked_all) == []


# ------------------------------------------------------------------ 值的归一与比较


def test_normalize_fact_value_kinds() -> None:
    assert normalize_fact_value(True, None) == BoolValue(True)
    number = normalize_fact_value(620, "km")
    assert isinstance(number, Measure)
    assert number.dimension == "length"
    assert describe_value(number) == "620 km"
    span = normalize_fact_value({"min": 600, "max": 650}, "km")
    assert isinstance(span, Measure)
    assert span.interval == Interval(low=D(600), high=D(650))
    assert describe_value(span) == "[600, 650] km"
    assert normalize_fact_value({"min": 5, "max": 1}, "km") is None
    assert normalize_fact_value({"min": "x", "max": 1}, "km") is None
    assert normalize_fact_value("2026-03-18", None) == DateValue(2026, 3, 18)
    assert describe_value(DateValue(2026, 3)) == "2026-03"
    compound = normalize_fact_value("5 年或 15 万公里", None)
    assert isinstance(compound, Compound)
    assert describe_value(compound) == "5 年 / 150000 km"
    text = normalize_fact_value("三元锂", None)
    assert text == TextValue(key="三元锂", raw="三元锂")
    assert normalize_fact_value("LX-220A", None) == TextValue(key="lx220a", raw="LX-220A")
    assert normalize_fact_value("620", "km") == normalize_fact_value(620, "km")
    assert normalize_fact_value("六百二十", "公里") == normalize_fact_value(620, "km")
    assert isinstance(normalize_fact_value("21.98 万元", None), Measure)
    assert normalize_fact_value("", None) is None
    assert normalize_fact_value(None, None) is None
    assert normalize_fact_value(float("nan"), "km") is None
    assert describe_value(BoolValue(False)) == "否"
    assert describe_value(BoolValue(True)) == "是"
    assert to_decimal(True) is None
    assert to_decimal([1]) is None


def compare(claim_value, claim_unit, fact_value, fact_unit):  # type: ignore[no-untyped-def]
    claim = normalize_fact_value(claim_value, claim_unit)
    fact = normalize_fact_value(fact_value, fact_unit)
    assert claim is not None
    assert fact is not None
    return compare_values(claim, fact)


@pytest.mark.parametrize(
    ("claim", "fact", "outcome"),
    [
        ((219800, "元"), (21.98, "万元"), Outcome.MATCH),
        ((21.98, "万元"), (219800, "元"), Outcome.MATCH),
        ((239800, "元"), (21.98, "万元"), Outcome.MISMATCH),
        ((620, "公里"), (620, "km"), Outcome.MATCH),
        ((620000, "m"), (620, "km"), Outcome.MATCH),
        ((385, "英里"), (620, "km"), Outcome.MISMATCH),
        ((60, "个月"), (5, "年"), Outcome.MATCH),
        ((5.9, "s"), (5.90, "秒"), Outcome.MATCH),
        ((82, "度"), (82, "kWh"), Outcome.MATCH),
        ((82, "km"), (82, "kWh"), Outcome.INCOMPARABLE),
        ((620, None), (620, "km"), Outcome.MATCH),
        ((520, None), (620, "km"), Outcome.MISMATCH),
        ((5, "座"), (5, None), Outcome.MATCH),
        ((7, "座"), (5, None), Outcome.MISMATCH),
        ((5, None), (5, None), Outcome.MATCH),
        ((620, "km"), ({"min": 600, "max": 650}, "km"), Outcome.MATCH),
        ((700, "km"), ({"min": 600, "max": 650}, "km"), Outcome.MISMATCH),
        (({"min": 600, "max": 650}, "km"), (620, "km"), Outcome.MATCH),
        (({"min": 600, "max": 610}, "km"), (620, "km"), Outcome.MISMATCH),
        (({"min": 610, "max": 640}, "km"), ({"min": 600, "max": 650}, "km"), Outcome.MATCH),
        (({"min": 600, "max": 650}, "km"), ({"min": 610, "max": 640}, "km"), Outcome.MATCH),
        (({"min": 500, "max": 550}, "km"), ({"min": 600, "max": 650}, "km"), Outcome.MISMATCH),
        (({"min": 590, "max": 620}, "km"), ({"min": 600, "max": 650}, "km"), Outcome.INCOMPARABLE),
        (("2026 年 3 月", None), ("2026-03-18", None), Outcome.MATCH),
        (("2026 年 4 月", None), ("2026-03-18", None), Outcome.MISMATCH),
        (("Ｓ７", None), ("S-7", None), Outcome.MATCH),
        (("磷酸铁锂", None), ("三元锂", None), Outcome.MISMATCH),
        ((True, None), (True, None), Outcome.MATCH),
        ((False, None), (True, None), Outcome.MISMATCH),
        (("三元锂", None), (620, "km"), Outcome.INCOMPARABLE),
        (("2026-03-18", None), ("三元锂", None), Outcome.INCOMPARABLE),
        # 组合数量：逐项按量纲比较
        (("5 年或 15 万公里", None), ("5 年或 15 万公里", None), Outcome.MATCH),
        (("60 个月 / 150000 km", None), ("5 年或 15 万公里", None), Outcome.MATCH),
        (("5 年", None), ("5 年或 15 万公里", None), Outcome.MATCH),
        (("8 年或 15 万公里", None), ("5 年或 15 万公里", None), Outcome.MISMATCH),
        (("5 年或 20 万公里", None), ("5 年或 15 万公里", None), Outcome.MISMATCH),
        (("82 kWh", None), ("5 年或 15 万公里", None), Outcome.INCOMPARABLE),
    ],
)
def test_compare_values(claim, fact, outcome) -> None:  # type: ignore[no-untyped-def]
    result = compare(*claim, *fact)
    assert result.outcome is outcome, result.note
    assert result.note


def test_scaled_unitless_claim_is_read_in_base_unit() -> None:
    # “售价 21.98 万”没写“元”：按货币基准单位理解为 219800 元
    claim = {"claim_type": "numeric", "value": 219800, "unit": None}
    claim["ext"] = {
        "quantity": {"qualifier": "exact", "low": "219800", "high": "219800", "scaled": True}
    }
    normalized = normalize_claim_value(claim)
    fact = normalize_fact_value(21.98, "万元")
    assert normalized is not None
    assert fact is not None
    assert compare_values(normalized, fact).outcome is Outcome.MATCH
    claim["ext"]["quantity"].update(low="239800", high="239800")
    wrong = normalize_claim_value(claim)
    assert wrong is not None
    result = compare_values(wrong, fact)
    assert result.outcome is Outcome.MISMATCH
    assert "未写单位" in result.note


def test_quantity_ext_roundtrip() -> None:
    quantity = one("约 620 公里")
    ext = quantity_ext(quantity)
    assert ext == {
        "qualifier": "approx",
        "low": "607.6",
        "high": "632.4",
        "low_closed": True,
        "high_closed": True,
        "unit": "km",
        "scaled": False,
    }
    measure = measure_from_ext(ext)
    assert measure is not None
    assert measure.interval == quantity.interval
    assert measure.base() == quantity.base_interval()
    unbounded = quantity_ext(one("超过 600 公里"))
    assert (unbounded["low"], unbounded["high"], unbounded["low_closed"]) == ("600", None, False)
    assert measure_from_ext({"low": "x", "high": None}) is None
    assert measure_from_ext({"low": None, "high": "y"}) is None
    bare = measure_from_ext({"low": "1", "high": "1"})
    assert bare is not None
    assert bare.unit is None


def test_normalize_claim_value_by_type() -> None:
    assert normalize_claim_value({"claim_type": "free_text", "value": "很安静"}) is None
    assert normalize_claim_value({"claim_type": "numeric", "value": None}) is None
    assert normalize_claim_value({"claim_type": "date", "value": "2026-03"}) == DateValue(2026, 3)
    assert normalize_claim_value({"claim_type": "date", "value": 2026}) is None
    assert normalize_claim_value({"claim_type": "model", "value": "S-7"}) == TextValue("s7", "S-7")
    assert normalize_claim_value({"claim_type": "model", "value": 7}) is None
    plain = normalize_claim_value({"claim_type": "numeric", "value": 620, "unit": "km"})
    assert plain == normalize_fact_value(620, "km")
    parts = [
        {"qualifier": "exact", "low": "5", "high": "5", "unit": "年"},
        {"qualifier": "exact", "low": "150000", "high": "150000", "unit": "km"},
    ]
    compound = normalize_claim_value(
        {"claim_type": "numeric", "value": "5 年或 15 万公里", "ext": {"components": parts}}
    )
    assert isinstance(compound, Compound)
    assert len(compound.parts) == 2
    broken = normalize_claim_value(
        {"claim_type": "numeric", "value": "x", "ext": {"components": [{"low": "bad"}]}}
    )
    assert broken is None


def test_same_value_is_stricter_than_consistent() -> None:
    def norm(value, unit=None):  # type: ignore[no-untyped-def]
        result = normalize_fact_value(value, unit)
        assert result is not None
        return result

    assert same_value(norm(21.98, "万元"), norm(219800, "元"))
    assert not same_value(norm(620, "km"), norm({"min": 600, "max": 650}, "km"))
    assert not same_value(norm(620, "km"), norm(620, None))
    assert same_value(norm(5, None), norm(5.0, None))
    assert not same_value(norm(82, "km"), norm(82, "kWh"))
    assert same_value(norm("5 年或 15 万公里"), norm("15 万公里 / 60 个月"))
    assert not same_value(norm("5 年或 15 万公里"), norm("5 年或 20 万公里"))
    assert not same_value(norm("5 年或 15 万公里"), norm("5 年、15 万公里、82 kWh"))
    assert not same_value(norm("2026-03"), norm("2026-03-18"))
    assert same_value(norm("2026年3月18日"), norm("2026-03-18"))
    assert same_value(norm("S-7"), norm("Ｓ７"))
    assert same_value(norm(True), norm(True))
    assert not same_value(norm(True), norm(False))
    assert not same_value(norm(True), norm("是"))
