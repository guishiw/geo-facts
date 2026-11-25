"""归一：数值、范围、约数、百分比、单位、中文数字、日期、型号、年款与适用条件。

规则说明见 docs/methodology.md。
"""

from geo_facts.normalize.conditions import (
    ConditionMention,
    canonical_conditions,
    canonical_value,
    compare_conditions,
    same_conditions,
    scan_conditions,
)
from geo_facts.normalize.dates import DateValue, dates_consistent, parse_date, scan_dates
from geo_facts.normalize.models import model_key, same_model, split_model_year
from geo_facts.normalize.numbers import (
    parse_arabic,
    parse_cn_integer,
    parse_cn_number,
    parse_number,
    to_chinese,
)
from geo_facts.normalize.quantity import (
    MASK,
    Interval,
    Quantity,
    QuantityMatch,
    parse_quantities,
    scan_quantities,
)
from geo_facts.normalize.text import View, compact, fold, is_invisible, make_view
from geo_facts.normalize.units import Unit, dimension_of, lookup_unit, unit_for
from geo_facts.normalize.values import (
    BoolValue,
    Comparison,
    Compound,
    Measure,
    NormValue,
    Outcome,
    TextValue,
    compare_values,
    describe_value,
    normalize_claim_value,
    normalize_fact_value,
    same_value,
)

__all__ = [
    "MASK",
    "BoolValue",
    "Comparison",
    "Compound",
    "ConditionMention",
    "DateValue",
    "Interval",
    "Measure",
    "NormValue",
    "Outcome",
    "Quantity",
    "QuantityMatch",
    "TextValue",
    "Unit",
    "View",
    "canonical_conditions",
    "canonical_value",
    "compact",
    "compare_conditions",
    "compare_values",
    "dates_consistent",
    "describe_value",
    "dimension_of",
    "fold",
    "is_invisible",
    "lookup_unit",
    "make_view",
    "model_key",
    "normalize_claim_value",
    "normalize_fact_value",
    "parse_arabic",
    "parse_cn_integer",
    "parse_cn_number",
    "parse_date",
    "parse_number",
    "parse_quantities",
    "same_conditions",
    "same_model",
    "same_value",
    "scan_conditions",
    "scan_dates",
    "scan_quantities",
    "split_model_year",
    "to_chinese",
    "unit_for",
]
