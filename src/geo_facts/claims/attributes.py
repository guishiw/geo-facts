"""属性的常见说法（同义表）。

陈述里的属性词先按事实库的属性名和 fact.aliases 识别；此外按下表把常见说法归到同一个“概念”，
事实库里属于同一概念的属性名（如“续航里程”“续驶里程”）在判定时视为同一属性。
同义表只收通用说法，不针对任何数据源的模板。
"""

from __future__ import annotations

from geo_facts.normalize import compact

# 概念 → 常见说法。“卖”只在后面紧跟数量时才构成陈述（拆解规则保证）。
CONCEPT_PHRASES: dict[str, tuple[str, ...]] = {
    "range": (
        "续航里程",
        "续航",
        "续驶里程",
        "续驶",
        "续航能力",
        "纯电续航",
        "纯电续航里程",
        "综合续航",
        "能跑",
        "可跑",
    ),
    "price": (
        "售价",
        "价格",
        "指导价",
        "厂商指导价",
        "起售价",
        "起价",
        "起步价",
        "入门价",
        "上市价",
        "上市售价",
        "报价",
        "定价",
        "售价区间",
        "卖",
    ),
    "acceleration": (
        "百公里加速",
        "百公里加速时间",
        "零百加速",
        "零百加速时间",
        "0-100km/h加速",
        "0-100km/h加速时间",
        "0-100公里加速",
        "0-100加速",
        "加速时间",
        "加速",
        "破百",
    ),
    "battery": ("电池容量", "电池", "电池包", "电池组", "电池电量", "带电量", "电量"),
    "launch": ("上市时间", "上市日期", "上市", "正式上市", "开售"),
    "warranty": ("整车质保", "质保", "保修", "整车保修", "质保期", "质保政策"),
    "fast_charge": ("快充时间", "快充", "快充用时"),
}

PHRASE_CONCEPT: dict[str, str] = {
    compact(phrase): concept for concept, phrases in CONCEPT_PHRASES.items() for phrase in phrases
}

# 这些概念的值是日期。
DATE_CONCEPTS: frozenset[str] = frozenset({"launch"})


def concept_of(name: str) -> str | None:
    """常见说法所属的概念；不在同义表里返回 None。"""
    return PHRASE_CONCEPT.get(compact(name))


def attribute_concept(attribute: str, aliases: list[str] | tuple[str, ...] = ()) -> str:
    """事实属性所属的概念：属性名或任一别名在同义表里即取该概念，否则以属性名自成一个概念。"""
    for name in (attribute, *aliases):
        found = concept_of(name)
        if found is not None:
            return found
    return "attr:" + compact(attribute)
