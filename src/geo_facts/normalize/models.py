"""型号名称与年款的归一。"""

from __future__ import annotations

import re

from geo_facts.normalize.text import compact, fold

_MODEL_YEAR = re.compile(r"(?<![\d.])((?:19|20|21)\d{2}) ?年? ?款")


def model_key(name: str) -> str:
    """型号比较用的键：“S7”“Ｓ７”“s 7”“S-7”得到同一个键 `s7`。"""
    return compact(name)


def same_model(first: str, second: str) -> bool:
    return model_key(first) == model_key(second)


def split_model_year(name: str) -> tuple[str, str | None]:
    """把“2026 款澜序 S7”拆成（型号键, 年款）；没有年款时年款为 None。"""
    folded = fold(name)
    match = _MODEL_YEAR.search(folded)
    if match is None:
        return model_key(name), None
    rest = folded[: match.start()] + folded[match.end() :]
    return compact(rest), match.group(1)
