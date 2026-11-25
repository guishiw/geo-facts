"""数字解析：阿拉伯数字（含千分位、小数）与中文数字。

规则（写进 docs/methodology.md）：
- 中文数字支持 零〇一二两三…九、十百千万亿，以及大写 壹贰…玖拾佰仟；
- 没有单位字的纯数字串按位读：“二〇二六”= 2026；
- “点”表示小数点：“三点五”= 3.5，“二十一点九八万”= 219800；
- 口语省略：“两万五”= 25000，“三千五”= 3500，“一百五”= 150；带“零”时不省略：“一百零五”= 105。
"""

from __future__ import annotations

from decimal import Decimal

_DIGITS: dict[str, int] = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "壹": 1,
    "贰": 2,
    "叁": 3,
    "肆": 4,
    "伍": 5,
    "陆": 6,
    "柒": 7,
    "捌": 8,
    "玖": 9,
}
_SMALL_UNITS: dict[str, int] = {"十": 10, "百": 100, "千": 1000, "拾": 10, "佰": 100, "仟": 1000}
_BIG_UNITS: dict[str, int] = {"万": 10**4, "亿": 10**8, "萬": 10**4, "億": 10**8}

CN_NUMBER_CHARS: frozenset[str] = (
    frozenset(_DIGITS) | frozenset(_SMALL_UNITS) | frozenset(_BIG_UNITS)
)
CN_DIGIT_CHARS: frozenset[str] = frozenset(_DIGITS)
CN_POINT = "点"

_OUT_DIGITS = "零一二三四五六七八九"


def _parse_section(text: str) -> int | None:
    """解析万以内的一节（只含数字和 十百千）。"""
    total = 0
    current: int | None = None
    last_unit: int | None = None
    zero_seen = False
    for char in text:
        if char in _SMALL_UNITS:
            unit = _SMALL_UNITS[char]
            if current is None:
                # 只有开头的“十”可以省略前面的“一”（“十五”= 15）。
                if unit != 10 or total != 0 or zero_seen:
                    return None
                current = 1
            if last_unit is not None and unit >= last_unit:
                return None
            total += current * unit
            last_unit = unit
            current = None
            zero_seen = False
            continue
        digit = _DIGITS[char]
        if current is not None:
            return None
        if digit == 0:
            zero_seen = True
        else:
            current = digit
    if current is None:
        # 以“零”结尾（“五百零”）不是合法写法。
        return None if zero_seen else total
    if last_unit is not None and last_unit >= 100 and not zero_seen:
        # 口语省略：“三千五”= 3500，“一百五”= 150。
        return total + current * (last_unit // 10)
    return total + current


def _parse_with_big_unit(text: str, big_units: tuple[int, ...]) -> int | None:
    """按 亿、万 逐级拆分。big_units 从大到小，如 (10**8, 10**4)。"""
    if not big_units:
        return _parse_section(text)
    unit = big_units[0]
    chars = [char for char, value in _BIG_UNITS.items() if value == unit]
    positions = [index for index, char in enumerate(text) if char in chars]
    if not positions:
        return _parse_with_big_unit(text, big_units[1:])
    if len(positions) > 1:
        return None
    left_text, right_text = text[: positions[0]], text[positions[0] + 1 :]
    if not left_text:
        return None
    left = _parse_with_big_unit(left_text, big_units[1:])
    if left is None or left == 0:
        return None
    if not right_text:
        return left * unit
    # 口语省略：“两万五”= 25000；右侧是单独一个非零数字且不带“零”。
    if len(right_text) == 1 and right_text in _DIGITS and _DIGITS[right_text] != 0:
        return left * unit + _DIGITS[right_text] * (unit // 10)
    right = _parse_with_big_unit(right_text, big_units[1:])
    if right is None or right >= unit:
        return None
    return left * unit + right


def parse_cn_integer(text: str) -> int | None:
    """解析中文整数；不是合法写法时返回 None。"""
    if not text or any(char not in CN_NUMBER_CHARS for char in text):
        return None
    if all(char in _DIGITS for char in text):
        # 没有任何单位字：按位读（“二〇二六”= 2026）。
        value = 0
        for char in text:
            value = value * 10 + _DIGITS[char]
        return value
    return _parse_with_big_unit(text, (10**8, 10**4))


def parse_cn_number(text: str) -> Decimal | None:
    """解析中文数字（可带“点”小数，以及小数后的“万/亿”）。"""
    if not text:
        return None
    if CN_POINT not in text:
        integer = parse_cn_integer(text)
        return None if integer is None else Decimal(integer)
    head, _, tail = text.partition(CN_POINT)
    if CN_POINT in tail or not head or not tail:
        return None
    multiplier = 1
    if tail[-1] in _BIG_UNITS:
        multiplier = _BIG_UNITS[tail[-1]]
        tail = tail[:-1]
    if not tail or any(char not in _DIGITS for char in tail):
        return None
    integer = parse_cn_integer(head)
    if integer is None:
        return None
    fraction = Decimal(0)
    for position, char in enumerate(tail, start=1):
        fraction += Decimal(_DIGITS[char]) / (Decimal(10) ** position)
    return (Decimal(integer) + fraction) * multiplier


def parse_arabic(text: str) -> Decimal | None:
    """解析阿拉伯数字：可带千分位逗号和小数点。全角数字请先折叠。"""
    body = text.strip()
    if not body:
        return None
    integer_part, dot, fraction_part = body.partition(".")
    if dot and (not fraction_part or not fraction_part.isdigit() or not fraction_part.isascii()):
        return None
    if "," in integer_part:
        groups = integer_part.split(",")
        if not 1 <= len(groups[0]) <= 3 or any(len(group) != 3 for group in groups[1:]):
            return None
        integer_part = "".join(groups)
    if not integer_part.isdigit() or not integer_part.isascii():
        return None
    return Decimal(integer_part + (dot + fraction_part if dot else ""))


def parse_number(text: str) -> Decimal | None:
    """解析一个完整的数字字符串（阿拉伯或中文），失败返回 None。"""
    value = parse_arabic(text)
    if value is not None:
        return value
    return parse_cn_number(text.strip())


def _section_to_chinese(value: int) -> str:
    """把 1～9999 写成中文，内部补“零”。"""
    out = ""
    pending_zero = False
    for unit, name in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        digit = value // unit % 10
        if digit == 0:
            pending_zero = bool(out)
            continue
        if pending_zero:
            out += "零"
            pending_zero = False
        out += _OUT_DIGITS[digit] + name
    return out


def to_chinese(value: int) -> str:
    """把非负整数写成规范的中文数字（小于 10**12）。

    例：219800 → “二十一万九千八百”，20000 → “二万”，105 → “一百零五”，10 → “十”。
    """
    if value < 0 or value >= 10**12:
        raise ValueError("只支持 0 到 10**12 - 1 的整数")
    if value == 0:
        return "零"
    sections = [(value // 10**8, "亿"), (value // 10**4 % 10**4, "万"), (value % 10**4, "")]
    out = ""
    need_zero = False
    for section, name in sections:
        if section == 0:
            need_zero = bool(out)
            continue
        if out and (need_zero or section < 1000):
            out += "零"
        out += _section_to_chinese(section) + name
        need_zero = False
    if out.startswith("一十"):
        out = out[1:]
    return out


def written_place(text: str) -> Decimal:
    """一个阿拉伯数字写法的“末位有效数字所在的位”。

    “620”→ 10，“600”→ 100，“5.9”→ 0.1，“21.98”→ 0.01，“0”→ 1。用于约数容差。
    """
    body = text.replace(",", "").strip()
    _, dot, fraction = body.partition(".")
    if dot:
        return Decimal(1) / (Decimal(10) ** len(fraction))
    return place_of_integer(int(body))


def place_of_integer(value: int) -> Decimal:
    """整数末位非零数字所在的位：620 → 10，600 → 100，219800 → 100，0 → 1。"""
    if value == 0:
        return Decimal(1)
    place = 1
    while value % (place * 10) == 0:
        place *= 10
    return Decimal(place)
