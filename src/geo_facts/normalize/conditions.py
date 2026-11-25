"""适用条件的识别与比较。

条件不同的两个值不可比较，也就不能判为矛盾：
- 测试工况 CLTC / WLTC / NEDC / EPA 互不可换（WLTP 视为 WLTC 的写法，CLTC-P 视为 CLTC）；
- 版本配置（“长续航版”“标准版”）按去掉“版 / 版本 / 款 / 型”后的名称比较；
- 年款（“2026 款”“26 款”）按四位年份比较；
- 其他条件键按折叠后的字面比较。

可比规则（写进 docs/methodology.md）：陈述写明的每一个条件，事实都必须有同名且同值的条件，
两者才可比；事实多出的条件视为陈述没有限定，不影响可比。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from geo_facts.normalize.text import compact, is_cjk

TEST_CYCLE = "test_cycle"
TRIM = "trim"
MODEL_YEAR = "model_year"
SOC_RANGE = "soc_range"

_CYCLE_ALIASES: dict[str, str] = {
    "cltc": "CLTC",
    "cltcp": "CLTC",
    "wltc": "WLTC",
    "wltp": "WLTC",
    "nedc": "NEDC",
    "epa": "EPA",
}
_CYCLE_PATTERN = re.compile(r"(?<![a-z])(cltc-p|cltc|wltc|wltp|nedc|epa)(?![a-z])")
_YEAR4_PATTERN = re.compile(r"(?<![\d.])((?:19|20|21)\d{2}) ?年? ?款")
_TRIM_SUFFIXES: tuple[str, ...] = ("版本", "版", "款", "型")
# “版”后面跟这些字时是别的词（版权、版面、版图、版式、版块、版税、版画）。
_TRIM_NOT_FOLLOW = frozenset("权面图式块税画")
# 往前取版本名时遇到这些字就停。
_TRIM_STOP = frozenset(
    "的了是为和与及或在有对比而且款个其该此这那各每等把被将从向给让于以之也都又还就才只很更最太较"
)
_TRIM_MAX = 8


@dataclass(frozen=True)
class ConditionMention:
    """文本中的一处条件：折叠文本上的区间、条件键与规范值。"""

    start: int
    end: int
    key: str
    value: str


def canonical_value(key: str, value: str) -> str:
    """把条件值归一成可比较的写法。"""
    folded = compact(value)
    if key == TEST_CYCLE:
        return _CYCLE_ALIASES.get(folded, folded.upper())
    if key == TRIM:
        for suffix in _TRIM_SUFFIXES:
            if folded.endswith(suffix) and len(folded) > len(suffix):
                return folded[: -len(suffix)]
        return folded
    if key == MODEL_YEAR:
        digits = "".join(char for char in folded if char.isdigit())
        if len(digits) == 2:
            return "20" + digits
        return digits or folded
    if key == SOC_RANGE:
        numbers = re.findall(r"\d+", folded)
        if len(numbers) == 2:
            return f"{int(numbers[0])}%-{int(numbers[1])}%"
        return folded
    return folded


def canonical_conditions(conditions: Mapping[str, str]) -> dict[str, str]:
    """整组条件归一：键去掉首尾空白并转小写，值按各自规则归一。"""
    return {
        key.strip().lower(): canonical_value(key.strip().lower(), value)
        for key, value in sorted(conditions.items())
    }


def compare_conditions(
    claim: Mapping[str, str], fact: Mapping[str, str]
) -> tuple[bool, str | None]:
    """陈述的条件与事实的条件是否可比。不可比时返回原因。"""
    claim_norm = canonical_conditions(claim)
    fact_norm = canonical_conditions(fact)
    for key, value in claim_norm.items():
        if key not in fact_norm:
            return (
                False,
                f"陈述写明了条件 {key}={claim[_original_key(claim, key)]}，事实没有这一条件",
            )
        if fact_norm[key] != value:
            return (
                False,
                f"条件不同：陈述为 {key}={claim[_original_key(claim, key)]}，"
                f"事实为 {key}={fact[_original_key(fact, key)]}",
            )
    return True, None


def _original_key(conditions: Mapping[str, str], normalized: str) -> str:
    for key in conditions:
        if key.strip().lower() == normalized:
            return key
    raise KeyError(normalized)


def same_conditions(first: Mapping[str, str], second: Mapping[str, str]) -> bool:
    """两组条件归一后是否完全相同（用于冲突检测）。"""
    return canonical_conditions(first) == canonical_conditions(second)


def _trim_mentions(folded: str, blocked: list[bool]) -> list[ConditionMention]:
    mentions: list[ConditionMention] = []
    position = folded.find("版")
    while position != -1:
        end = position + 1
        if folded.startswith("版本", position):
            end = position + 2
        following = folded[end] if end < len(folded) else ""
        start = position
        while (
            start > 0
            and position - start < _TRIM_MAX
            and not blocked[start - 1]
            and folded[start - 1] not in _TRIM_STOP
            and (
                is_cjk(folded[start - 1])
                or (folded[start - 1].isascii() and folded[start - 1].isalnum())
                or folded[start - 1] in "+."
            )
        ):
            start -= 1
        if start == position and position >= 2 and folded[position - 1] == " ":
            # “Pro 版”：英文名与“版”之间允许一个空格。
            probe = position - 1
            while (
                probe > 0
                and position - probe <= _TRIM_MAX
                and not blocked[probe - 1]
                and folded[probe - 1].isascii()
                and (folded[probe - 1].isalnum() or folded[probe - 1] == "+")
            ):
                probe -= 1
            if probe < position - 1:
                start = probe
        name = folded[start:position].strip()
        cjk_only = bool(name) and all(is_cjk(char) for char in name)
        valid = bool(name) and not (cjk_only and len(name) < 2) and not name.isdigit()
        if valid and following not in _TRIM_NOT_FOLLOW and not blocked[position]:
            mentions.append(
                ConditionMention(start=start, end=end, key=TRIM, value=folded[start:end].strip())
            )
        position = folded.find("版", end)
    return mentions


def scan_conditions(
    folded: str,
    *,
    blocked: list[bool] | None = None,
    vocabulary: Mapping[str, frozenset[str]] | None = None,
) -> list[ConditionMention]:
    """在折叠文本中找出条件：测试工况、年款、版本配置，以及事实库里出现过的其他条件值。

    `blocked[i]` 为真表示该位置已被实体等占用。`vocabulary` 是事实库中“条件键 → 取值集合”：
    trim 的取值是去掉后缀的版本名，用来识别“长续航款 / 长续航型”这类不带“版”字的写法；
    其余键的取值是折叠后的字面，按字面匹配。年款只识别四位年份（“2026 款”）。
    """
    mask = blocked if blocked is not None else [False] * len(folded)
    mentions: list[ConditionMention] = []
    for match in _CYCLE_PATTERN.finditer(folded):
        value = _CYCLE_ALIASES[match.group(1).replace("-", "")]
        mentions.append(ConditionMention(match.start(), match.end(), TEST_CYCLE, value))
    for match in _YEAR4_PATTERN.finditer(folded):
        mentions.append(ConditionMention(match.start(), match.end(), MODEL_YEAR, match.group(1)))
    mentions.extend(_trim_mentions(folded, mask))
    for key, values in sorted((vocabulary or {}).items()):
        if key in (TEST_CYCLE, MODEL_YEAR, SOC_RANGE):
            continue
        for value in sorted(values, key=lambda item: (-len(item), item)):
            needles = [value + suffix for suffix in ("款", "型")] if key == TRIM else [value]
            for needle in needles:
                position = folded.find(needle)
                while needle and position != -1:
                    mentions.append(ConditionMention(position, position + len(needle), key, needle))
                    position = folded.find(needle, position + len(needle))
    usable = [
        mention
        for mention in mentions
        if not any(mask[index] for index in range(mention.start, mention.end))
    ]
    usable.sort(key=lambda item: (item.start, -(item.end - item.start), item.key))
    result: list[ConditionMention] = []
    for mention in usable:
        if result and mention.start < result[-1].end:
            continue
        result.append(mention)
    return result
