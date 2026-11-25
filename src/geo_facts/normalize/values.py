"""值的归一与比较。

把事实的值和陈述的值都归一成同一组类型，再比较：
- 数量（Measure）：取值区间 + 单位；同量纲换算到基准单位后比较；
- 组合数量（Compound）：如“5 年或 15 万公里”，按量纲逐项比较；
- 日期（DateValue）：按较粗的精度比较；
- 文本（TextValue）：折叠全半角、大小写、空格和连字符后比较；
- 布尔（BoolValue）。

比较结果只有三种：一致（MATCH）、不一致（MISMATCH）、不可比（INCOMPARABLE）。
不可比不是不一致：量纲不同、类型不同、范围只是部分重叠时都算不可比。
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from geo_facts.normalize.dates import DateValue, dates_consistent, parse_date
from geo_facts.normalize.numbers import parse_number
from geo_facts.normalize.quantity import Interval, Quantity, parse_quantities, plain_decimal, point
from geo_facts.normalize.text import compact
from geo_facts.normalize.units import Unit, unit_for


class Outcome(enum.Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    INCOMPARABLE = "incomparable"


@dataclass(frozen=True)
class Comparison:
    outcome: Outcome
    note: str


@dataclass(frozen=True)
class Measure:
    """归一后的数量：区间以书写单位计。"""

    interval: Interval
    unit: Unit | None
    scaled: bool = False
    qualifier: str = "exact"

    @property
    def dimension(self) -> str | None:
        return None if self.unit is None else self.unit.dimension

    def base(self) -> Interval:
        return self.interval if self.unit is None else self.interval.scale(self.unit.factor)

    def describe(self) -> str:
        unit = "" if self.unit is None else f" {self.unit.symbol}"
        if self.interval.is_point:
            assert self.interval.low is not None
            return f"{plain_decimal(self.interval.low)}{unit}"
        return f"{self.interval.describe()}{unit}"


@dataclass(frozen=True)
class Compound:
    parts: tuple[Measure, ...]

    def describe(self) -> str:
        return " / ".join(part.describe() for part in self.parts)


@dataclass(frozen=True)
class TextValue:
    key: str
    raw: str

    def describe(self) -> str:
        return self.raw


@dataclass(frozen=True)
class BoolValue:
    value: bool

    def describe(self) -> str:
        return "是" if self.value else "否"


NormValue = Measure | Compound | DateValue | TextValue | BoolValue


def to_decimal(value: Any) -> Decimal | None:
    """把 JSON 数字或数字字符串转成 Decimal；布尔值和非数字返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        try:
            result = Decimal(repr(value))
        except InvalidOperation:  # pragma: no cover - repr(float) 总能被 Decimal 解析
            return None
        return result if result.is_finite() else None
    if isinstance(value, str):
        return parse_number(value)
    return None


def measure_from_quantity(quantity: Quantity) -> Measure:
    return Measure(
        interval=quantity.interval,
        unit=quantity.unit,
        scaled=quantity.scaled,
        qualifier=quantity.qualifier,
    )


def describe_value(value: NormValue) -> str:
    if isinstance(value, DateValue):
        return value.iso()
    return value.describe()


def normalize_fact_value(value: Any, unit: str | None) -> NormValue | None:
    """归一事实的值；无法归一时返回 None。"""
    resolved = unit_for(unit) if unit is not None and unit.strip() else None
    if isinstance(value, bool):
        return BoolValue(value)
    if isinstance(value, int | float):
        number = to_decimal(value)
        return None if number is None else Measure(interval=point(number), unit=resolved)
    if isinstance(value, Mapping):
        low, high = to_decimal(value.get("min")), to_decimal(value.get("max"))
        if low is None or high is None or low > high:
            return None
        return Measure(interval=Interval(low=low, high=high), unit=resolved, qualifier="range")
    if not isinstance(value, str) or not value.strip():
        return None
    if resolved is not None:
        number = parse_number(value)
        if number is not None:
            return Measure(interval=point(number), unit=resolved)
    parsed_date = parse_date(value)
    if parsed_date is not None:
        return parsed_date
    quantities = parse_quantities(value)
    if quantities is not None and all(not quantity.delta for quantity in quantities):
        measures = tuple(measure_from_quantity(quantity) for quantity in quantities)
        if len(measures) == 1:
            return measures[0]
        return Compound(parts=measures)
    return TextValue(key=compact(value), raw=value)


def quantity_ext(quantity: Quantity) -> dict[str, Any]:
    """把数量的限定词和区间写成可放进 claim.ext 的结构（数值用字符串，保证精确）。"""
    interval = quantity.interval
    return {
        "qualifier": quantity.qualifier,
        "low": None if interval.low is None else plain_decimal(interval.low),
        "high": None if interval.high is None else plain_decimal(interval.high),
        "low_closed": interval.low_closed,
        "high_closed": interval.high_closed,
        "unit": None if quantity.unit is None else quantity.unit.symbol,
        "scaled": quantity.scaled,
    }


def measure_from_ext(data: Mapping[str, Any]) -> Measure | None:
    """从 claim.ext 里的数量结构还原 Measure。"""
    low_raw, high_raw = data.get("low"), data.get("high")
    low = None if low_raw is None else to_decimal(low_raw)
    high = None if high_raw is None else to_decimal(high_raw)
    if (low_raw is not None and low is None) or (high_raw is not None and high is None):
        return None
    unit_text = data.get("unit")
    unit = unit_for(unit_text) if isinstance(unit_text, str) and unit_text.strip() else None
    return Measure(
        interval=Interval(
            low=low,
            high=high,
            low_closed=bool(data.get("low_closed", True)),
            high_closed=bool(data.get("high_closed", True)),
        ),
        unit=unit,
        scaled=bool(data.get("scaled", False)),
        qualifier=str(data.get("qualifier", "exact")),
    )


def normalize_claim_value(claim: Mapping[str, Any]) -> NormValue | None:
    """归一陈述的值。数值陈述优先使用 ext.quantity / ext.components 里记录的区间。"""
    value = claim.get("value")
    unit = claim.get("unit")
    claim_type = claim.get("claim_type")
    ext = claim.get("ext")
    ext_map: Mapping[str, Any] = ext if isinstance(ext, Mapping) else {}
    if claim_type == "free_text" or value is None:
        return None
    if claim_type == "date":
        return parse_date(value) if isinstance(value, str) else None
    if claim_type == "model":
        return TextValue(key=compact(value), raw=value) if isinstance(value, str) else None
    components = ext_map.get("components")
    if isinstance(components, list) and components:
        parts = [measure_from_ext(item) for item in components if isinstance(item, Mapping)]
        if len(parts) == len(components) and all(part is not None for part in parts):
            return Compound(parts=tuple(part for part in parts if part is not None))
        return None
    quantity = ext_map.get("quantity")
    if isinstance(quantity, Mapping):
        return measure_from_ext(quantity)
    return normalize_fact_value(value, unit if isinstance(unit, str) else None)


def _compare_intervals(claim: Interval, fact: Interval) -> Outcome:
    if fact.is_point:
        assert fact.low is not None
        return Outcome.MATCH if claim.contains(fact.low) else Outcome.MISMATCH
    if claim.is_point:
        assert claim.low is not None
        return Outcome.MATCH if fact.contains(claim.low) else Outcome.MISMATCH
    if fact.contains_interval(claim) or claim.contains_interval(fact):
        return Outcome.MATCH
    if not fact.overlaps(claim):
        return Outcome.MISMATCH
    return Outcome.INCOMPARABLE


def _combine(outcomes: list[Outcome]) -> Outcome:
    if Outcome.MATCH in outcomes:
        return Outcome.MATCH
    if Outcome.MISMATCH in outcomes:
        return Outcome.MISMATCH
    return Outcome.INCOMPARABLE


def _compare_measures(claim: Measure, fact: Measure) -> Comparison:
    if claim.unit is not None and fact.unit is not None:
        if claim.dimension != fact.dimension:
            return Comparison(
                Outcome.INCOMPARABLE,
                f"量纲不同（陈述单位 {claim.unit.symbol}，事实单位 {fact.unit.symbol}），不可比",
            )
        outcome = _compare_intervals(claim.base(), fact.base())
        return Comparison(outcome, _note(outcome, claim, fact))
    outcomes = [_compare_intervals(claim.interval, fact.interval)]
    note_suffix = ""
    if claim.unit is None and fact.unit is not None:
        note_suffix = f"；陈述未写单位，按事实的单位 {fact.unit.symbol} 理解"
        if claim.scaled:
            # 带“万 / 亿”的无单位数：再按该量纲的基准单位理解一次（“21.98 万”即 219800 元）。
            outcomes.append(_compare_intervals(claim.interval, fact.base()))
    elif claim.unit is not None and fact.unit is None:
        note_suffix = "；事实未写单位，按陈述的数值直接比较"
    outcome = _combine(outcomes)
    return Comparison(outcome, _note(outcome, claim, fact) + note_suffix)


def _note(outcome: Outcome, claim: NormValue, fact: NormValue) -> str:
    left, right = describe_value(claim), describe_value(fact)
    if outcome is Outcome.MATCH:
        return f"陈述的值 {left} 与事实的值 {right} 一致"
    if outcome is Outcome.MISMATCH:
        return f"陈述的值 {left} 与事实的值 {right} 不一致"
    return f"陈述的值 {left} 与事实的值 {right} 不可比"


def _parts(value: Measure | Compound) -> tuple[Measure, ...]:
    return value.parts if isinstance(value, Compound) else (value,)


def _compare_compound(claim: Measure | Compound, fact: Measure | Compound) -> Comparison:
    outcomes: list[Outcome] = []
    for part in _parts(claim):
        related = [
            other
            for other in _parts(fact)
            if part.dimension is not None and other.dimension == part.dimension
        ]
        if not related:
            outcomes.append(Outcome.INCOMPARABLE)
            continue
        outcomes.append(_combine([_compare_measures(part, other).outcome for other in related]))
    if Outcome.MISMATCH in outcomes:
        outcome = Outcome.MISMATCH
    elif Outcome.MATCH in outcomes:
        outcome = Outcome.MATCH
    else:
        outcome = Outcome.INCOMPARABLE
    return Comparison(outcome, _note(outcome, claim, fact))


def compare_values(claim: NormValue, fact: NormValue) -> Comparison:
    """比较陈述的值与事实的值。"""
    if isinstance(claim, Measure) and isinstance(fact, Measure):
        return _compare_measures(claim, fact)
    if isinstance(claim, Measure | Compound) and isinstance(fact, Measure | Compound):
        return _compare_compound(claim, fact)
    if isinstance(claim, DateValue) and isinstance(fact, DateValue):
        outcome = Outcome.MATCH if dates_consistent(claim, fact) else Outcome.MISMATCH
        note = _note(outcome, claim, fact)
        if outcome is Outcome.MATCH and claim.precision != fact.precision:
            note += "（两者精度不同，按较粗的精度比较）"
        return Comparison(outcome, note)
    if isinstance(claim, TextValue) and isinstance(fact, TextValue):
        outcome = Outcome.MATCH if claim.key == fact.key else Outcome.MISMATCH
        return Comparison(outcome, _note(outcome, claim, fact))
    if isinstance(claim, BoolValue) and isinstance(fact, BoolValue):
        outcome = Outcome.MATCH if claim.value == fact.value else Outcome.MISMATCH
        return Comparison(outcome, _note(outcome, claim, fact))
    return Comparison(
        Outcome.INCOMPARABLE,
        f"值的类型不同（陈述 {describe_value(claim)}，事实 {describe_value(fact)}），不可比",
    )


def same_value(first: NormValue, second: NormValue) -> bool:
    """两个归一后的值是否完全相同（用于冲突检测，比“一致”更严格）。"""
    if isinstance(first, Measure) and isinstance(second, Measure):
        if (first.unit is None) != (second.unit is None):
            return False
        if first.unit is None:
            return first.interval == second.interval
        return first.dimension == second.dimension and first.base() == second.base()
    if isinstance(first, Compound) and isinstance(second, Compound):
        if len(first.parts) != len(second.parts):
            return False
        remaining = list(second.parts)
        for part in first.parts:
            for other in remaining:
                if same_value(part, other):
                    remaining.remove(other)
                    break
            else:
                return False
        return True
    if isinstance(first, DateValue) and isinstance(second, DateValue):
        return first == second
    if isinstance(first, TextValue) and isinstance(second, TextValue):
        return first.key == second.key
    if isinstance(first, BoolValue) and isinstance(second, BoolValue):
        return first.value == second.value
    return False
