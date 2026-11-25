"""单位表与换算。

每个单位属于一个量纲，并给出换算到该量纲基准单位的系数。量纲不同的量不可比较；
没有固定换算关系的单位（如“座”“马力”）各自成一个量纲。未收录的单位按字面比较。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from geo_facts.normalize.text import compact


@dataclass(frozen=True)
class Unit:
    """一个单位：规范写法、量纲、换算到基准单位的系数。"""

    symbol: str
    dimension: str
    factor: Decimal

    def to_base(self, value: Decimal) -> Decimal:
        return value * self.factor


# (规范写法, 量纲, 系数, 其他写法)
_TABLE: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    # 长度，基准：米
    ("mm", "length", "0.001", ("毫米",)),
    ("cm", "length", "0.01", ("厘米",)),
    ("m", "length", "1", ("米",)),
    ("km", "length", "1000", ("公里", "千米")),
    ("英里", "length", "1609.344", ("mile", "miles")),
    ("英寸", "length", "0.0254", ("inch", "吋")),
    # 货币，基准：元
    ("元", "currency", "1", ("块", "块钱", "元人民币", "rmb", "cny")),
    ("千元", "currency", "1000", ()),
    ("万元", "currency", "10000", ("万块",)),
    ("亿元", "currency", "100000000", ()),
    # 电量，基准：千瓦时
    ("kWh", "energy", "1", ("千瓦时", "度", "度电", "kw·h", "kw.h")),
    ("Wh", "energy", "0.001", ("瓦时",)),
    # 功率，基准：千瓦
    ("kW", "power", "1", ("千瓦",)),
    ("W", "power", "0.001", ("瓦",)),
    ("马力", "horsepower", "1", ("匹", "ps")),
    # 扭矩
    ("N·m", "torque", "1", ("nm", "n.m", "牛米", "牛·米", "牛・米")),
    # 速度
    ("km/h", "speed", "1", ("公里/小时", "千米/小时", "公里每小时", "千米每小时", "kph")),
    # 时长，基准：秒
    ("ms", "duration", "0.001", ("毫秒",)),
    ("s", "duration", "1", ("秒", "秒钟", "sec")),
    ("min", "duration", "60", ("分钟",)),
    ("h", "duration", "3600", ("小时", "个小时", "hr")),
    ("天", "duration", "86400", ()),
    # 年月，基准：月（年与月之间按 1 年 = 12 个月换算，不与“天”互换）
    ("个月", "calendar", "1", ()),
    ("年", "calendar", "12", ()),
    # 百分比
    ("%", "percent", "1", ("percent",)),
    # 质量，基准：千克
    ("mg", "mass", "0.000001", ("毫克",)),
    ("g", "mass", "0.001", ("克",)),
    ("kg", "mass", "1", ("千克", "公斤")),
    ("吨", "mass", "1000", ()),
    ("斤", "mass", "0.5", ()),
    # 容积，基准：升
    ("mL", "volume", "0.001", ("毫升",)),
    ("L", "volume", "1", ("升",)),
    # 电压，基准：伏
    ("V", "voltage", "1", ("伏", "伏特")),
    ("kV", "voltage", "1000", ("千伏",)),
    # 温度
    ("℃", "temperature", "1", ("°c", "摄氏度")),
    # 计数单位：互不换算
    ("座", "count:座", "1", ()),
    ("个", "count:个", "1", ()),
    ("辆", "count:辆", "1", ()),
    ("台", "count:台", "1", ()),
    ("人", "count:人", "1", ()),
    ("次", "count:次", "1", ()),
    ("家", "count:家", "1", ()),
    ("项", "count:项", "1", ()),
)


def _build() -> dict[str, Unit]:
    table: dict[str, Unit] = {}
    for symbol, dimension, factor, aliases in _TABLE:
        unit = Unit(symbol=symbol, dimension=dimension, factor=Decimal(factor))
        for name in (symbol, *aliases):
            key = compact(name)
            if key in table and table[key] != unit:
                raise RuntimeError(f"单位写法重复：{name}")
            table[key] = unit
    return table


UNITS: dict[str, Unit] = _build()

# 按长度从长到短排列的写法，用于在文本中做最长匹配。
UNIT_KEYS: tuple[str, ...] = tuple(sorted(UNITS, key=lambda key: (-len(key), key)))

# 数量级词与货币单位合写时保留原写法（“21.98 万元”记为 21.98、万元）。
SCALED_CURRENCY: dict[int, str] = {10**3: "千元", 10**4: "万元", 10**8: "亿元"}


def lookup_unit(text: str | None) -> Unit | None:
    """按写法查单位；未收录返回 None。"""
    if text is None:
        return None
    return UNITS.get(compact(text))


def unit_for(text: str) -> Unit:
    """查单位；未收录的单位自成量纲，只能与同样写法的单位比较。"""
    known = lookup_unit(text)
    if known is not None:
        return known
    key = compact(text)
    return Unit(symbol=text.strip(), dimension=f"unit:{key}", factor=Decimal(1))


def dimension_of(text: str | None) -> str | None:
    """单位的量纲；没有单位时为 None。"""
    if text is None or not text.strip():
        return None
    return unit_for(text).dimension


def match_unit_at(folded: str, position: int) -> tuple[Unit, int] | None:
    """在折叠文本的指定位置做最长单位匹配，返回 (单位, 结束位置)。

    以英文字母结尾的单位后面不能紧跟英文字母（“5 sets”里的 s 不是秒）。
    """
    for key in UNIT_KEYS:
        if not folded.startswith(key, position):
            continue
        end = position + len(key)
        if key[-1].isascii() and key[-1].isalpha() and end < len(folded):
            following = folded[end]
            if following.isascii() and following.isalpha():
                continue
        return UNITS[key], end
    return None
