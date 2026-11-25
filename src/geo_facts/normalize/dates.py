"""日期归一：识别不同写法，并保留精度（年、月、日）。

精度不同的两个日期按较粗的精度比较：“2026 年 3 月”与“2026-03-18”一致，
“2026 年 4 月”与“2026-03-18”不一致。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from geo_facts.normalize.numbers import parse_cn_integer

_CN = "零〇一二两三四五六七八九十"
_YEAR = rf"(?P<y>\d{{4}}|[{_CN}]{{4}})"
_MONTH = rf"(?P<m>\d{{1,2}}|[{_CN}]{{1,3}})"
_DAY = rf"(?P<d>\d{{1,2}}|[{_CN}]{{1,3}})"

_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 2026年3月18日 / 2026 年 3 月 18 号 / 二〇二六年三月十八日
    re.compile(rf"{_YEAR} ?年 ?{_MONTH} ?月 ?{_DAY} ?[日号]"),
    # 2026年3月
    re.compile(rf"{_YEAR} ?年 ?{_MONTH} ?月(?!\d)"),
    # 2026-03-18 / 2026/3/18 / 2026.3.18
    re.compile(r"(?<![\d.])(?P<y>\d{4})(?P<sep>[-/.])(?P<m>\d{1,2})(?P=sep)(?P<d>\d{1,2})(?![\d])"),
    # 2026-03（只接受 ISO 写法，避免把“2026/3”这类分数读成日期）
    re.compile(r"(?<![\d.-])(?P<y>\d{4})-(?P<m>\d{2})(?![\d-])"),
    # 2026年（后面不是“款”“代”等）
    re.compile(rf"(?<![\d.]){_YEAR} ?年(?![款代型前后来内度以之间份])"),
)


@dataclass(frozen=True)
class DateValue:
    """带精度的日期：月、日可以缺省。"""

    year: int
    month: int | None = None
    day: int | None = None

    @property
    def precision(self) -> str:
        if self.month is None:
            return "year"
        return "month" if self.day is None else "day"

    def iso(self) -> str:
        """规范写法：2026、2026-03、2026-03-18。"""
        if self.month is None:
            return f"{self.year:04d}"
        if self.day is None:
            return f"{self.year:04d}-{self.month:02d}"
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"


@dataclass(frozen=True)
class DateMatch:
    start: int
    end: int
    value: DateValue


def _number(text: str | None) -> int | None:
    if text is None:
        return None
    if text.isascii() and text.isdigit():
        return int(text)
    return parse_cn_integer(text)


def _build(year_text: str, month_text: str | None, day_text: str | None) -> DateValue | None:
    year = _number(year_text)
    month = _number(month_text)
    day = _number(day_text)
    if year is None or not 1900 <= year <= 2199:
        return None
    if month_text is not None and (month is None or not 1 <= month <= 12):
        return None
    if day_text is not None:
        if day is None or month is None:
            return None
        try:
            date(year, month, day)
        except ValueError:
            return None
    return DateValue(year=year, month=month, day=day)


def scan_dates(folded: str) -> list[DateMatch]:
    """在折叠文本中找出全部日期；重叠时取先出现、较长的一个。"""
    found: list[DateMatch] = []
    for pattern in _PATTERNS:
        for match in pattern.finditer(folded):
            groups = match.groupdict()
            value = _build(groups["y"], groups.get("m"), groups.get("d"))
            if value is None:
                continue
            found.append(DateMatch(start=match.start(), end=match.end(), value=value))
    found.sort(key=lambda item: (item.start, -(item.end - item.start)))
    result: list[DateMatch] = []
    for item in found:
        if result and item.start < result[-1].end:
            continue
        result.append(item)
    return result


def parse_date(text: str) -> DateValue | None:
    """把一个完整的字符串解析成日期；字符串里还有别的内容时返回 None。"""
    from geo_facts.normalize.text import make_view

    folded = make_view(text).folded.strip()
    matches = scan_dates(folded)
    if len(matches) != 1 or matches[0].start != 0 or matches[0].end != len(folded):
        return None
    return matches[0].value


def dates_consistent(first: DateValue, second: DateValue) -> bool:
    """按两者中较粗的精度比较是否一致。"""
    if first.year != second.year:
        return False
    if first.month is None or second.month is None:
        return True
    if first.month != second.month:
        return False
    if first.day is None or second.day is None:
        return True
    return first.day == second.day
