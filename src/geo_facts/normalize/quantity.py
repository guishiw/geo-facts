"""数量的识别与取值区间：精确值、范围、约数、上下界。

约数的容差语义（写进 docs/methodology.md）：
- “约 / 大约 / 左右 / 上下” X：区间 [X − t, X + t]，t = max(末位有效数字所在位的一半, |X| 的 2%)。
  例：约 620 → [607.6, 632.4]；约 600 → [550, 650]；约 22 万 → [21.5 万, 22.5 万]。
- “X 多 / X 余”：区间 [X, X + 末位有效数字所在位)。例：600 多 → [600, 700)；20 多万 → [20 万, 30 万)。
- “近 / 将近 / 接近” X：区间 [0.9 X, X]。例：近 20 万 → [18 万, 20 万]。
- “超过 / 高于” X：(X, +∞)；“X 以上 / 至少”：[X, +∞)；
  “不到 / 不足 / 低于” X：(−∞, X)；“X 以内 / 以下 / 最多”：(−∞, X]。
- “A～B / A-B / A 至 B / A 到 B”：闭区间 [A, B]。
- 没有上述词的数值按精确值比较，不加容差。
- 前面带“提升、降价、多、少”等词的数值是变化量，不是属性值，标记为 delta。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from geo_facts.normalize.numbers import (
    CN_DIGIT_CHARS,
    CN_NUMBER_CHARS,
    CN_POINT,
    parse_arabic,
    parse_cn_number,
    place_of_integer,
    written_place,
)
from geo_facts.normalize.text import is_cjk
from geo_facts.normalize.units import SCALED_CURRENCY, UNITS, Unit, match_unit_at

APPROX_RELATIVE = Decimal("0.02")
NEAR_RELATIVE = Decimal("0.1")

# 被遮蔽位置的占位符：陈述拆解会把实体、属性、日期等已识别的片段换成它再扫描数量。
MASK = chr(0)


@dataclass(frozen=True)
class Interval:
    """取值区间；None 表示该侧无界。"""

    low: Decimal | None
    high: Decimal | None
    low_closed: bool = True
    high_closed: bool = True

    @property
    def is_point(self) -> bool:
        return self.low is not None and self.low == self.high

    def contains(self, value: Decimal) -> bool:
        if self.low is not None and (
            value < self.low or (value == self.low and not self.low_closed)
        ):
            return False
        return not (
            self.high is not None
            and (value > self.high or (value == self.high and not self.high_closed))
        )

    def contains_interval(self, other: Interval) -> bool:
        """other 是否整个落在本区间内。"""
        if self.low is not None:
            if other.low is None or other.low < self.low:
                return False
            if other.low == self.low and other.low_closed and not self.low_closed:
                return False
        if self.high is not None:
            if other.high is None or other.high > self.high:
                return False
            if other.high == self.high and other.high_closed and not self.high_closed:
                return False
        return True

    def overlaps(self, other: Interval) -> bool:
        """两个区间是否有公共点。"""
        return _below(self.low, self.low_closed, other.high, other.high_closed) and _below(
            other.low, other.low_closed, self.high, self.high_closed
        )

    def scale(self, factor: Decimal) -> Interval:
        return Interval(
            low=None if self.low is None else self.low * factor,
            high=None if self.high is None else self.high * factor,
            low_closed=self.low_closed,
            high_closed=self.high_closed,
        )

    def describe(self) -> str:
        left = (
            "(−∞"
            if self.low is None
            else f"{'[' if self.low_closed else '('}{plain_decimal(self.low)}"
        )
        right = (
            "+∞)"
            if self.high is None
            else f"{plain_decimal(self.high)}{']' if self.high_closed else ')'}"
        )
        return f"{left}, {right}"


def _below(low: Decimal | None, low_closed: bool, high: Decimal | None, high_closed: bool) -> bool:
    """下界 low 是否不高于上界 high（考虑开闭）。"""
    if low is None or high is None:
        return True
    if low < high:
        return True
    return low == high and low_closed and high_closed


def plain_decimal(value: Decimal) -> str:
    """去掉多余的尾随零，不用科学计数法。"""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def point(value: Decimal) -> Interval:
    return Interval(low=value, high=value)


@dataclass(frozen=True)
class Quantity:
    """一个数量：书写的数值与单位、限定词，以及它表示的取值区间（以书写单位计）。"""

    qualifier: str  # exact / approx / range / more / near / gt / ge / lt / le
    value: Decimal
    upper: Decimal | None
    unit: Unit | None
    interval: Interval
    scaled: bool = False
    delta: bool = False

    @property
    def dimension(self) -> str | None:
        return None if self.unit is None else self.unit.dimension

    def base_interval(self) -> Interval:
        """换算到量纲基准单位后的区间；没有单位时原样返回。"""
        return self.interval if self.unit is None else self.interval.scale(self.unit.factor)


@dataclass(frozen=True)
class QuantityMatch:
    """文本中的一处数量：折叠文本上的区间 [start, end)。"""

    start: int
    end: int
    quantity: Quantity


def approx_interval(value: Decimal, place: Decimal) -> Interval:
    tolerance = max(place / 2, abs(value) * APPROX_RELATIVE)
    return Interval(low=value - tolerance, high=value + tolerance)


def qualified_interval(qualifier: str, value: Decimal, place: Decimal) -> Interval:
    """按限定词给出取值区间。"""
    if qualifier == "approx":
        return approx_interval(value, place)
    if qualifier == "more":
        return Interval(low=value, high=value + place, high_closed=False)
    if qualifier == "near":
        return Interval(low=value - abs(value) * NEAR_RELATIVE, high=value)
    if qualifier == "gt":
        return Interval(low=value, high=None, low_closed=False)
    if qualifier == "ge":
        return Interval(low=value, high=None)
    if qualifier == "lt":
        return Interval(low=None, high=value, high_closed=False)
    if qualifier == "le":
        return Interval(low=None, high=value)
    return point(value)


_ARABIC = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
_ARABIC_PLAIN = re.compile(r"\d+(?:\.\d+)?")
_CN_RUN = re.compile("[" + "".join(sorted(CN_NUMBER_CHARS)) + CN_POINT + "]+")
_MULTIPLIERS: tuple[tuple[str, int], ...] = (
    ("万亿", 10**12),
    ("千万", 10**7),
    ("百万", 10**6),
    ("亿", 10**8),
    ("万", 10**4),
    ("千", 10**3),
    ("百", 10**2),
)
# “千”“百”后面跟这些字时是单位的一部分（千米、千克、千瓦、千伏、百分之），不是数量级。
_NOT_MULTIPLIER_FOLLOW = frozenset("米克瓦伏分")
_PERCENT_PREFIX = "百分之"
_RANGE_SEPARATORS: tuple[str, ...] = ("~", "-", chr(0x2014), chr(0x2013), "至", "到")
# 单个汉字数字只在这些量纲下才当作数量（“五年”“三天”），避免把“一度”“一个”当成数值。
_SINGLE_CN_DIMENSIONS = frozenset({"calendar", "duration"})
# 数字后面紧跟这些字时不算“未识别的单位”。
_PLAIN_FOLLOW = frozenset("的和与或及是为而但且则即也还再起以之到至多余左上下等")

_PREFIX_QUALIFIERS: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            ("最高不超过", "le"),
            ("不超过", "le"),
            ("不高于", "le"),
            ("最多", "le"),
            ("至多", "le"),
            ("不低于", "ge"),
            ("不少于", "ge"),
            ("至少", "ge"),
            ("最少", "ge"),
            ("起码", "ge"),
            ("超过", "gt"),
            ("高于", "gt"),
            ("大于", "gt"),
            ("多于", "gt"),
            ("突破", "gt"),
            ("逾", "gt"),
            ("超", "gt"),
            ("不到", "lt"),
            ("不足", "lt"),
            ("低于", "lt"),
            ("小于", "lt"),
            ("少于", "lt"),
            ("未满", "lt"),
            ("差不多", "approx"),
            ("大约", "approx"),
            ("约为", "approx"),
            ("约合", "approx"),
            ("大概", "approx"),
            ("大致", "approx"),
            ("约", "approx"),
            ("~", "approx"),
            ("≈", "approx"),
            ("将近", "near"),
            ("接近", "near"),
            ("逼近", "near"),
            ("近", "near"),
        ),
        key=lambda item: -len(item[0]),
    )
)
# “近”前面是这些字时不是“将近”的意思（最近、附近、靠近、临近、邻近、亲近）。
_NEAR_BLOCKERS = frozenset("最附靠临邻亲新远")
# “约”前面是这些字时不是约数（节约、预约、签约、合约……）。
_APPROX_BLOCKERS = frozenset("节预签合契违邀相隐制公特条")
_DELTA_WORDS: tuple[str, ...] = tuple(
    sorted(
        (
            "提升",
            "提高",
            "增加",
            "增长",
            "增幅",
            "上涨",
            "上调",
            "下降",
            "降低",
            "减少",
            "下调",
            "降幅",
            "降价",
            "直降",
            "立减",
            "优惠",
            "便宜",
            "补贴",
            "缩短",
            "延长",
            "高出",
            "多出",
            "低出",
            "相差",
            "差距",
            "涨幅",
            "涨",
            "降",
            "多",
            "少",
            "贵",
            "快",
            "慢",
        ),
        key=lambda word: -len(word),
    )
)
# 这些词以“多/少/降/涨”结尾，但不是变化量（最多、至少、差不多、下降至……由更长的词先匹配）。
_DELTA_BLOCKERS: tuple[str, ...] = (
    "最多",
    "至多",
    "差不多",
    "至少",
    "最少",
    "不少",
    "许多",
    "很多",
)
_SUFFIX_QUALIFIERS: tuple[tuple[str, str], ...] = (
    ("左右", "approx"),
    ("上下", "approx"),
    ("以上", "ge"),
    ("以内", "le"),
    ("以下", "le"),
)


@dataclass(frozen=True)
class _Number:
    end: int
    value: Decimal
    place: Decimal
    scaled: bool
    single_cn: bool


def _skip_spaces(folded: str, position: int) -> int:
    while position < len(folded) and folded[position] == " ":
        position += 1
    return position


def _arabic_at(folded: str, position: int) -> _Number | None:
    match = _ARABIC.match(folded, position)
    if match is None:
        return None
    if match.end() < len(folded) and folded[match.end()].isdigit():
        # 千分位写法后面还紧跟数字（如 1,2345）：退回按普通数字读。
        match = _ARABIC_PLAIN.match(folded, position)
        assert match is not None
    text = match.group()
    value = parse_arabic(text)
    assert value is not None
    return _Number(
        end=match.end(), value=value, place=written_place(text), scaled=False, single_cn=False
    )


def _cn_at(folded: str, position: int) -> _Number | None:
    match = _CN_RUN.match(folded, position)
    if match is None:
        return None
    text = match.group()
    while text:
        following = folded[position + len(text)] if position + len(text) < len(folded) else ""
        blocked_tail = (
            text[-1] == CN_POINT
            or (text[-1] in "千百" and following in _NOT_MULTIPLIER_FOLLOW)
            or (text[-1] in "千百万亿" and len(text) == 1)
        )
        value = None if blocked_tail else parse_cn_number(text)
        if value is not None:
            break
        text = text[:-1]
    if not text or value is None:
        return None
    digits_only = all(char in CN_DIGIT_CHARS for char in text)
    if digits_only and len(text) == 2:
        # “三四年”“七八成”是约数说法，不能按位读成 34、78。
        return None
    if CN_POINT in text:
        fraction = text.partition(CN_POINT)[2]
        big = 10**4 if fraction[-1] in "万萬" else 10**8 if fraction[-1] in "亿億" else 1
        digits = len(fraction) - (1 if big != 1 else 0)
        place = Decimal(big) / (Decimal(10) ** digits)
    else:
        place = place_of_integer(int(value))
    scaled = any(char in "万亿萬億" for char in text)
    return _Number(
        end=position + len(text),
        value=value,
        place=place,
        scaled=scaled,
        single_cn=len(text) == 1,
    )


def _number_at(folded: str, position: int) -> _Number | None:
    if folded[position].isdigit() and folded[position].isascii():
        return _arabic_at(folded, position)
    if folded[position] in CN_NUMBER_CHARS:
        return _cn_at(folded, position)
    return None


def _multiplier_at(folded: str, position: int) -> tuple[int, int] | None:
    for word, factor in _MULTIPLIERS:
        if not folded.startswith(word, position):
            continue
        end = position + len(word)
        following = folded[end] if end < len(folded) else ""
        if word[-1] in "千百" and following in _NOT_MULTIPLIER_FOLLOW:
            continue
        return factor, end
    return None


def _prefix_qualifier(folded: str, start: int) -> tuple[str | None, int]:
    """向前查看限定词，返回 (限定词, 限定词起点)。"""
    position = start
    while position > 0 and folded[position - 1] == " ":
        position -= 1
    head = folded[:position]
    for word, qualifier in _PREFIX_QUALIFIERS:
        if not head.endswith(word):
            continue
        before = head[: -len(word)]
        if word == "近" and before and before[-1] in _NEAR_BLOCKERS:
            continue
        if word == "超" and before and before[-1] in "反赶":
            continue
        if word == "约" and before and before[-1] in _APPROX_BLOCKERS:
            continue
        return qualifier, position - len(word)
    return None, start


def _is_delta(folded: str, start: int) -> bool:
    """数字前面（跳过空格、“了”和约数词之后）是否是变化量词。"""
    position = start
    while position > 0 and folded[position - 1] in " 了":
        position -= 1
    head = folded[:position]
    if any(head.endswith(word) for word in _DELTA_BLOCKERS):
        return False
    return any(head.endswith(word) for word in _DELTA_WORDS)


@dataclass(frozen=True)
class _Token:
    start: int
    end: int
    value: Decimal
    place: Decimal
    unit: Unit | None
    written_value: Decimal
    written_unit: Unit | None
    scaled: bool
    multiplier: int
    has_more: bool
    suffix: str | None
    bare: bool  # 数字后面没有数量级词、单位和后缀


def _token_at(folded: str, position: int) -> _Token | None:
    """从 position 起解析“数字 [多] [数量级] [多] [单位] [后缀]”。"""
    if folded.startswith(_PERCENT_PREFIX, position):
        after = _skip_spaces(folded, position + len(_PERCENT_PREFIX))
        number = _number_at(folded, after) if after < len(folded) else None
        if number is None:
            return None
        percent = UNITS["%"]
        return _Token(
            start=position,
            end=number.end,
            value=number.value,
            place=number.place,
            unit=percent,
            written_value=number.value,
            written_unit=percent,
            scaled=False,
            multiplier=1,
            has_more=False,
            suffix=None,
            bare=False,
        )
    number = _number_at(folded, position)
    if number is None:
        return None
    cursor = number.end
    has_more = False
    multiplier = 1
    probe = _skip_spaces(folded, cursor)
    if probe < len(folded) and folded[probe] in "多余":
        after_more = _skip_spaces(folded, probe + 1)
        # “多”后面必须还是这个数量的一部分（数量级词或单位），或者数量到此结束。
        if (
            _multiplier_at(folded, after_more) is not None
            or match_unit_at(folded, after_more) is not None
            or after_more >= len(folded)
            or folded[after_more] in _PLAIN_FOLLOW
            or not (is_cjk(folded[after_more]) or folded[after_more].isalnum())
        ):
            has_more = True
            cursor = probe + 1
            probe = after_more
    found = _multiplier_at(folded, probe)
    if found is not None:
        multiplier, cursor = found
        probe = _skip_spaces(folded, cursor)
        if not has_more and probe < len(folded) and folded[probe] in "多余":
            has_more = True
            cursor = probe + 1
            probe = _skip_spaces(folded, cursor)
    unit: Unit | None = None
    unit_match = match_unit_at(folded, probe)
    if unit_match is not None:
        unit, cursor = unit_match
    suffix: str | None = None
    probe = _skip_spaces(folded, cursor)
    if unit is not None and not has_more and probe < len(folded) and folded[probe] in "多余":
        has_more = True
        cursor = probe + 1
        probe = _skip_spaces(folded, cursor)
    for word, qualifier in _SUFFIX_QUALIFIERS:
        if folded.startswith(word, probe):
            suffix = qualifier
            cursor = probe + len(word)
            break
    value = number.value * multiplier
    place = number.place * multiplier
    written_value, written_unit = value, unit
    if unit is not None and unit.dimension == "currency" and unit.factor == 1:
        scaled_symbol = SCALED_CURRENCY.get(multiplier)
        if scaled_symbol is not None:
            written_value, written_unit = number.value, UNITS[scaled_symbol]
    lone_digit_allowed = unit is not None and unit.dimension in _SINGLE_CN_DIMENSIONS
    if number.single_cn and multiplier == 1 and not lone_digit_allowed:
        return None
    return _Token(
        start=position,
        end=cursor,
        value=value,
        place=place,
        unit=unit,
        written_value=written_value,
        written_unit=written_unit,
        scaled=number.scaled or multiplier != 1,
        multiplier=multiplier,
        has_more=has_more,
        suffix=suffix,
        bare=multiplier == 1 and unit is None and suffix is None and not has_more,
    )


def _unknown_unit_follows(folded: str, token: _Token) -> bool:
    """数字后面是否紧跟未收录的单位（“3 档”“5G”），此时不当作数量。"""
    if token.unit is not None or token.suffix is not None or token.has_more:
        return False
    if token.end < len(folded):
        attached = folded[token.end]
        if attached.isascii() and attached.isalpha():
            return True
    probe = _skip_spaces(folded, token.end)
    if probe >= len(folded):
        return False
    char = folded[probe]
    return is_cjk(char) and char not in _PLAIN_FOLLOW


def _attached_before(folded: str, position: int) -> bool:
    """数字前面是否紧贴字母、数字或小数点（“K5”“v2.0”），此时它是型号的一部分。"""
    if position == 0:
        return False
    previous = folded[position - 1]
    return previous.isascii() and (previous.isalnum() or previous in "._")


def _is_ordinal(folded: str, position: int) -> bool:
    """数字前面是否是“第”（“第一代”“第 3 名”是序数，不是数量）。"""
    while position > 0 and folded[position - 1] == " ":
        position -= 1
    return position > 0 and folded[position - 1] == "第"


def _build(token: _Token, qualifier: str | None, delta: bool) -> Quantity:
    final = "more" if token.has_more else (token.suffix or qualifier or "exact")
    factor = Decimal(1)
    if token.written_unit is not None and token.unit is not None:
        factor = token.unit.factor / token.written_unit.factor
    interval = qualified_interval(final, token.value, token.place).scale(factor)
    return Quantity(
        qualifier=final,
        value=token.written_value,
        upper=None,
        unit=token.written_unit,
        interval=interval,
        scaled=token.scaled,
        delta=delta,
    )


def _try_range(folded: str, first: _Token) -> tuple[_Token, int] | None:
    """first 后面若是“分隔符 + 第二个数量”，返回第二个数量。"""
    if first.has_more or first.suffix is not None:
        return None
    probe = _skip_spaces(folded, first.end)
    for separator in _RANGE_SEPARATORS:
        if not folded.startswith(separator, probe):
            continue
        after = probe + len(separator)
        while folded.startswith(separator, after):
            after += len(separator)
        after = _skip_spaces(folded, after)
        if after >= len(folded):
            return None
        second = _token_at(folded, after)
        if second is None or second.has_more:
            return None
        if first.unit is not None and (
            second.unit is None or second.unit.dimension != first.unit.dimension
        ):
            return None
        return second, after
    return None


def _range_quantity(first: _Token, second: _Token, delta: bool) -> Quantity | None:
    low = first.value
    if first.unit is None and first.multiplier == 1 and second.multiplier != 1:
        # “20-25 万”：前一个数共享后一个数的数量级词。
        shared = first.value * second.multiplier
        if shared < second.value:
            low = shared
    unit = second.written_unit
    high_written = second.written_value
    if second.unit is None:
        low_written = low
    else:
        assert unit is not None
        source = first.unit if first.unit is not None else second.unit
        low_written = low * source.factor / unit.factor
    if low_written > high_written:
        return None
    return Quantity(
        qualifier="range",
        value=low_written,
        upper=high_written,
        unit=unit,
        interval=Interval(low=low_written, high=high_written),
        scaled=first.scaled or second.scaled,
        delta=delta,
    )


def scan_quantities(folded: str) -> list[QuantityMatch]:
    """在折叠文本中找出全部数量。被遮蔽的位置请先替换成 MASK。"""
    matches: list[QuantityMatch] = []
    position = 0
    length = len(folded)
    while position < length:
        char = folded[position]
        starts_number = (char.isdigit() and char.isascii()) or char in CN_NUMBER_CHARS
        if not starts_number:
            position += 1
            continue
        if _is_ordinal(folded, position) or (char.isdigit() and _attached_before(folded, position)):
            position = _skip_run(folded, position)
            continue
        token = _token_at(folded, position)
        if token is None:
            position = _skip_run(folded, position)
            continue
        qualifier, qualifier_start = _prefix_qualifier(folded, token.start)
        delta = _is_delta(folded, qualifier_start)
        found = _try_range(folded, token)
        if found is not None:
            second, _ = found
            quantity = _range_quantity(token, second, delta)
            if quantity is not None and not _unknown_unit_follows(folded, second):
                matches.append(QuantityMatch(start=token.start, end=second.end, quantity=quantity))
                position = second.end
                continue
        if _unknown_unit_follows(folded, token):
            position = token.end
            continue
        start = qualifier_start if qualifier is not None else token.start
        if token.start >= 1 and folded[token.start - 1] == chr(0xA5) and token.unit is None:
            # 前置的货币符号 ¥。
            token = _with_unit(token, UNITS["元"])
            start = min(start, token.start - 1)
        matches.append(
            QuantityMatch(start=start, end=token.end, quantity=_build(token, qualifier, delta))
        )
        position = token.end
    return matches


def _with_unit(token: _Token, unit: Unit) -> _Token:
    written_value, written_unit = token.value, unit
    scaled_symbol = SCALED_CURRENCY.get(token.multiplier)
    if scaled_symbol is not None:
        written_value, written_unit = token.value / token.multiplier, UNITS[scaled_symbol]
    return _Token(
        start=token.start,
        end=token.end,
        value=token.value,
        place=token.place,
        unit=unit,
        written_value=written_value,
        written_unit=written_unit,
        scaled=token.scaled,
        multiplier=token.multiplier,
        has_more=token.has_more,
        suffix=token.suffix,
        bare=False,
    )


def _skip_run(folded: str, position: int) -> int:
    """跳过一段连续的数字字符（阿拉伯数字、小数点、千分位、中文数字），至少前进一位。"""
    start = position
    while position < len(folded) and (
        (folded[position].isdigit() and folded[position].isascii())
        or folded[position] in ".,"
        or folded[position] in CN_NUMBER_CHARS
        or folded[position] == CN_POINT
    ):
        position += 1
    return max(position, start + 1)


_CONNECTORS = re.compile(r"(?:或者|以及|或|和|及|、|/|,|，|;|；| )*")


def parse_quantities(text: str) -> list[Quantity] | None:
    """把一个完整的字符串解析成一个或多个数量（如“5 年或 15 万公里”）。

    字符串里除了数量和“或、和、/”等连接词之外还有别的内容时返回 None。
    """
    from geo_facts.normalize.text import make_view

    folded = make_view(text).folded.strip()
    if not folded:
        return None
    matches = scan_quantities(folded)
    if not matches:
        return None
    cursor = 0
    for match in matches:
        gap = folded[cursor : match.start]
        if _CONNECTORS.fullmatch(gap) is None:
            return None
        cursor = match.end
    tail = folded[cursor:]
    if _CONNECTORS.fullmatch(tail) is None and tail != "起":
        return None
    return [match.quantity for match in matches]
