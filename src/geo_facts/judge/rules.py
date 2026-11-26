"""规则判定：两个方向、两个分母，不得混用。

回答 → 事实库（answer_to_facts，用于准确率），对每条陈述：
1. 自由文本陈述一律不可核验（“自由文本判定未验证”）；没有实体或属性的同样不可核验。
2. 候选事实 = 同实体、同属性概念（属性名、别名或常见说法同义表归到同一概念）的全部事实。
   没有候选 → 不可核验（不可核验不等于错误，也不等于缺失）。
3. 条件：陈述写明的每个条件，事实都必须有同名同值的条件才可比（normalize.compare_conditions）。
   一个可比的事实都没有（如陈述写 WLTC 而事实库只有 CLTC）→ 不可核验，条件不同不得判为矛盾。
   陈述没写条件时，事实库里该属性所有条件的值都可比：与任一有效值一致 → 支持；
   都不一致 → 矛盾，理由里列出全部比对对象（只有一个带条件的值时同样如此）。这是取舍：
   回答不写工况就报一个与所有工况都对不上的数，按错误处理。
4. 可比的事实里没有一条可作为确定依据（过期、未生效、撤回、有争议、无证据、冲突、非事实区）
   → 不可核验，理由写明原因。
5. 与可用事实比较值（单位换算、约数容差、范围包含、日期精度、型号全半角，见 normalize）：
   有一条一致 → 支持；否则有不一致的 →
   a. 陈述的值与该事实已过期或已被更正的旧版本一致（或与另一条已整段过期的同类事实一致）
      → 矛盾，理由注明“与已过期的版本一致（过期信息）”；
   b. 陈述的值与另一条“不可用”的可比事实一致（如无证据的 WLTC 值）→ 不可核验，不下矛盾结论；
   c. 否则 → 矛盾。
   全部不可比（量纲不同、范围只部分重叠）→ 不可核验。

约数与范围的容差（常量定义在 normalize.quantity，这里引用同一组常量）：
- “约 / 大约 / 左右 X”：[X − t, X + t]，t = max(末位有效数字所在位的一半, |X| × APPROX_RELATIVE)，
  APPROX_RELATIVE = 0.02；例：约 620 → [607.6, 632.4]，约 600 → [550, 650]；
- “X 多 / X 余”：[X, X + 末位有效数字所在位)；例：600 多 → [600, 700)；
- “近 / 将近 / 接近 X”：[X × (1 − NEAR_RELATIVE), X]，NEAR_RELATIVE = 0.1；例：近 650 → [585, 650]；
- “A～B”：闭区间 [A, B]；“超过 X”：(X, +∞)，“不低于 X”：[X, +∞)，“不到 X”：(−∞, X)；
- 没有这些词的数按精确值比较，不加容差。

事实库 → 回答（facts_to_answer，用于呈现率）：只对题库里配置了 core_fact_ids 的题，
对该题每条核心事实给一个标签：回答里有同实体、同属性概念、条件可比的陈述且值一致 → present_correct；
有这样的陈述但值不一致（且它也不是在说同实体同属性的另一条事实，如 WLTC 值不算 CLTC 呈现错误）
→ present_wrong；没有 → missing。核心事实在查询日不可用（过期、撤回、无证据等）时不判定、
不进呈现率分母，在摘要里列出；核心事实编号不在事实库中是输入错误。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from geo_facts.claims import Lexicon, attribute_concept, build_lexicon
from geo_facts.errors import InputError
from geo_facts.normalize import (
    Comparison,
    NormValue,
    Outcome,
    compact,
    compare_conditions,
    compare_values,
    normalize_claim_value,
    normalize_fact_value,
)
from geo_facts.normalize.quantity import APPROX_RELATIVE, NEAR_RELATIVE
from geo_facts.store import BasisView, FactStatus, FactStore, basis_view, describe_reasons

__all__ = [
    "APPROX_RELATIVE",
    "JUDGE",
    "NEAR_RELATIVE",
    "Exclusion",
    "JudgeContext",
    "PresenceVerdict",
    "Verdict",
    "judge_claim",
    "judge_claims",
    "presence_for",
]

JUDGE: dict[str, str] = {"type": "rule", "id": "geo-facts-judge-rules", "version": "1.0"}

SUPPORTED = "supported"
CONTRADICTED = "contradicted"
UNVERIFIABLE = "unverifiable"
PRESENT_CORRECT = "present_correct"
PRESENT_WRONG = "present_wrong"
MISSING = "missing"

FREE_TEXT_REASON = "自由文本判定未验证：自由文本陈述没有规则判定，一律标为不可核验"
NOT_ERROR = "不可核验不等于错误，也不等于缺失"


@dataclass(frozen=True)
class Verdict:
    """一条陈述的判定（answer_to_facts）。

    reason_code：match（一致）、mismatch（不一致）、stale_match（与过期或已更正的旧版本一致）、
    free_text、no_entity、no_attribute、no_fact、value_unparsed、condition_differs、
    fact_unusable、matches_unusable、incomparable。
    fact：判定依据的事实版本（支持、矛盾时必有；不可核验时为比对过的事实或 None）。
    compared：比对过的事实版本；stale：一致的旧版本 {fact_id, version, kind, valid_from, valid_until}；
    unusable：可比但不可用的事实状态（reason_code = fact_unusable 时）。
    """

    claim: Mapping[str, Any]
    label: str
    reason_code: str
    reason: str
    fact: dict[str, Any] | None = None
    compared: tuple[dict[str, Any], ...] = ()
    stale: dict[str, Any] | None = None
    unusable: tuple[FactStatus, ...] = ()


@dataclass(frozen=True)
class PresenceVerdict:
    """一条核心事实在一个回答里的呈现情况（facts_to_answer）。claims：一致或不一致的陈述。"""

    fact: dict[str, Any]
    label: str
    reason: str
    claims: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class Exclusion:
    """查询日不可用、因而不进呈现率分母的核心事实。"""

    fact_id: str
    reasons: tuple[str, ...]
    fact: dict[str, Any]


class JudgeContext:
    """某个查询日的判定环境：事实库视图、词表与“实体 + 属性概念”索引。"""

    def __init__(self, store: FactStore, as_of: date, lexicon: Lexicon | None = None) -> None:
        self.store = store
        self.as_of = as_of
        self.view: BasisView = basis_view(store, as_of)
        self.lexicon = lexicon if lexicon is not None else build_lexicon(store)
        self._phrases = {key: concept for key, concept, _ in self.lexicon.attribute_keys}
        self._index: dict[tuple[str, str], list[FactStatus]] = {}
        for status in self.view.statuses.values():
            key = (status.fact["entity_id"], self.fact_concept(status.fact))
            self._index.setdefault(key, []).append(status)

    @classmethod
    def from_store(cls, store: FactStore, as_of: date) -> JudgeContext:
        return cls(store, as_of)

    def claim_concept(self, attribute: str) -> str:
        return self._phrases.get(compact(attribute)) or attribute_concept(attribute)

    @staticmethod
    def fact_concept(fact: Mapping[str, Any]) -> str:
        return attribute_concept(fact["attribute"], list(fact.get("aliases", [])))

    def candidates(self, entity_id: str, concept: str) -> list[FactStatus]:
        """同实体、同属性概念的全部事实（按 fact_id 升序）。"""
        return list(self._index.get((entity_id, concept), []))


def _compare(claim_value: NormValue, fact: Mapping[str, Any]) -> Comparison:
    fact_value = normalize_fact_value(fact["value"], fact["unit"])
    if fact_value is None:
        return Comparison(Outcome.INCOMPARABLE, f"事实 {fact['fact_id']} 的值无法归一，不可比")
    return compare_values(claim_value, fact_value)


def format_conditions(conditions: Mapping[str, str]) -> str:
    return "、".join(f"{key}={value}" for key, value in sorted(conditions.items())) or "无"


def _label_fact(fact: Mapping[str, Any]) -> str:
    return (
        f"{fact['fact_id']}（版本 {fact['version']}，条件 {format_conditions(fact['conditions'])}）"
    )


def _fact_value_text(fact: Mapping[str, Any]) -> str:
    value = fact["value"]
    if isinstance(value, dict):
        value = f"{value['min']}~{value['max']}"
    unit = "" if fact["unit"] is None else f" {fact['unit']}"
    return f"{value}{unit}"


def _condition_note(claim: Mapping[str, Any], fact: Mapping[str, Any]) -> str:
    claimed = {key.strip().lower() for key in (claim.get("conditions") or {})}
    extra = {
        key: value
        for key, value in fact["conditions"].items()
        if key.strip().lower() not in claimed
    }
    if not extra:
        return ""
    return f"（陈述未写明条件 {format_conditions(extra)}，按事实的条件比对）"


def _unverifiable(claim: Mapping[str, Any], code: str, reason: str, **extra: Any) -> Verdict:
    return Verdict(claim=claim, label=UNVERIFIABLE, reason_code=code, reason=reason, **extra)


def _stale_dict(fact: Mapping[str, Any], kind: str) -> dict[str, Any]:
    return {
        "fact_id": fact["fact_id"],
        "version": fact["version"],
        "kind": kind,
        "valid_from": fact["valid_from"],
        "valid_until": fact["valid_until"],
    }


def _find_stale(
    claim_value: NormValue,
    conditions: Mapping[str, str],
    mismatched: Sequence[FactStatus],
    comparable: Sequence[FactStatus],
    ctx: JudgeContext,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """（一致的旧版本, 判定依据的当前版本）；没有则 None。"""
    candidates: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
    for status in mismatched:
        for old, kind in ctx.view.stale(status.fact_id):
            candidates.append((old, kind, status.fact))
    for status in comparable:
        if not status.usable and status.reasons == ("expired",):
            candidates.append((status.fact, "expired", mismatched[0].fact))
    candidates.sort(key=lambda item: (item[0]["fact_id"], -int(item[0]["version"])))
    for old, kind, current in candidates:
        if not compare_conditions(conditions, old["conditions"])[0]:
            continue
        if _compare(claim_value, old).outcome is Outcome.MATCH:
            return _stale_dict(old, kind), current
    return None


def judge_claim(claim: Mapping[str, Any], ctx: JudgeContext) -> Verdict:
    """判定一条陈述（answer_to_facts）。"""
    if claim.get("claim_type") == "free_text":
        return _unverifiable(claim, "free_text", FREE_TEXT_REASON)
    entity_id = claim.get("entity_id")
    if entity_id is None:
        return _unverifiable(
            claim,
            "no_entity",
            f"未能确定陈述所指的实体（未点名、有歧义或是事实库没有的车型），{NOT_ERROR}",
        )
    attribute = claim.get("attribute")
    if attribute is None:
        return _unverifiable(claim, "no_attribute", f"陈述没有可识别的属性，{NOT_ERROR}")
    statuses = ctx.candidates(entity_id, ctx.claim_concept(attribute))
    if not statuses:
        return _unverifiable(
            claim,
            "no_fact",
            f"事实库中没有实体 {entity_id} 的“{attribute}”事实，{NOT_ERROR}",
        )
    claim_value = normalize_claim_value(claim)
    if claim_value is None:
        return _unverifiable(claim, "value_unparsed", f"陈述的值无法归一，{NOT_ERROR}")
    conditions: Mapping[str, str] = claim.get("conditions") or {}
    comparable: list[FactStatus] = []
    differing: list[tuple[FactStatus, str]] = []
    for status in statuses:
        ok, why = compare_conditions(conditions, status.fact["conditions"])
        if ok:
            comparable.append(status)
        else:
            differing.append((status, why or "条件不同"))
    if not comparable:
        status, why = differing[0]
        return _unverifiable(
            claim,
            "condition_differs",
            f"{why}；事实库没有与陈述条件相同的值，条件不同不得判为矛盾",
            fact=status.fact,
            compared=tuple(item.fact for item, _ in differing),
        )
    usable = [status for status in comparable if status.usable]
    if not usable:
        reason = "；".join(
            f"可比的事实 {status.fact_id}（版本 {status.version}）在 {ctx.as_of.isoformat()} "
            f"不得作为确定依据：{describe_reasons(status.reasons)}"
            for status in comparable
        )
        return _unverifiable(
            claim,
            "fact_unusable",
            f"{reason}；{NOT_ERROR}",
            fact=comparable[0].fact,
            compared=tuple(status.fact for status in comparable),
            unusable=tuple(comparable),
        )
    results = [(status, _compare(claim_value, status.fact)) for status in usable]
    compared = tuple(status.fact for status in usable)
    for status, comparison in results:
        if comparison.outcome is Outcome.MATCH:
            return Verdict(
                claim=claim,
                label=SUPPORTED,
                reason_code="match",
                reason=f"与事实 {status.fact_id}（版本 {status.version}）一致：{comparison.note}"
                + _condition_note(claim, status.fact),
                fact=status.fact,
                compared=compared,
            )
    mismatched = [
        status for status, comparison in results if comparison.outcome is Outcome.MISMATCH
    ]
    if not mismatched:
        status, comparison = results[0]
        return _unverifiable(
            claim,
            "incomparable",
            f"值不可比：{comparison.note}；{NOT_ERROR}",
            fact=status.fact,
            compared=compared,
        )
    stale = _find_stale(claim_value, conditions, mismatched, comparable, ctx)
    if stale is not None:
        old, current = stale
        kind_text = (
            "与已过期的版本一致（过期信息）"
            if old["kind"] == "expired"
            else "与已被更正的旧版本一致"
        )
        return Verdict(
            claim=claim,
            label=CONTRADICTED,
            reason_code="stale_match",
            reason=f"{kind_text}：陈述的值等于 {old['fact_id']} 版本 {old['version']}"
            f"（有效期 {old['valid_from']} 至 {old['valid_until'] or '未定'}），"
            f"{ctx.as_of.isoformat()} 当日有效的 {current['fact_id']} 版本 {current['version']} "
            f"的值为 {_fact_value_text(current)}",
            fact=current,
            compared=compared,
            stale=old,
        )
    for status in comparable:
        if status.usable or status.reasons == ("expired",):
            continue
        if _compare(claim_value, status.fact).outcome is Outcome.MATCH:
            return _unverifiable(
                claim,
                "matches_unusable",
                f"陈述的值与 {_label_fact(status.fact)} 一致，但该事实不得作为确定依据"
                f"（{describe_reasons(status.reasons)}）；与可用事实 "
                + "、".join(_label_fact(item.fact) for item in mismatched)
                + f" 不一致，不下矛盾结论；{NOT_ERROR}",
                fact=status.fact,
                compared=compared,
            )
    primary = mismatched[0]
    notes = {status.fact_id: comparison.note for status, comparison in results}
    if len(mismatched) == 1:
        reason = (
            f"与事实 {_label_fact(primary.fact)}不一致：{notes[primary.fact_id]}"
            + _condition_note(claim, primary.fact)
        )
    else:
        reason = "陈述未写明全部条件，与事实库中该属性可比的有效值都不一致：" + "；".join(
            f"{_label_fact(status.fact)} {_fact_value_text(status.fact)}" for status in mismatched
        )
    return Verdict(
        claim=claim,
        label=CONTRADICTED,
        reason_code="mismatch",
        reason=reason,
        fact=primary.fact,
        compared=compared,
    )


def judge_claims(claims: Iterable[Mapping[str, Any]], ctx: JudgeContext) -> list[Verdict]:
    """判定入口：给定陈述列表与某日的判定环境，返回判定列表（与输入同序）。"""
    return [judge_claim(claim, ctx) for claim in claims]


def presence_for(
    core_fact_ids: Sequence[str],
    claims: Sequence[Mapping[str, Any]],
    ctx: JudgeContext,
) -> tuple[list[PresenceVerdict], list[Exclusion]]:
    """事实库 → 回答：一题的核心事实在一个回答（其陈述列表）里的呈现情况。"""
    verdicts: list[PresenceVerdict] = []
    excluded: list[Exclusion] = []
    for fact_id in dict.fromkeys(core_fact_ids):
        status = ctx.view.status(fact_id)
        if status is None:
            raise InputError(f"核心事实 {fact_id} 不在事实库中（请检查题库的 core_fact_ids）")
        if not status.usable:
            excluded.append(Exclusion(fact_id=fact_id, reasons=status.reasons, fact=status.fact))
            continue
        verdicts.append(_presence(status, claims, ctx))
    return verdicts, excluded


def _presence(
    status: FactStatus, claims: Sequence[Mapping[str, Any]], ctx: JudgeContext
) -> PresenceVerdict:
    fact = status.fact
    concept = ctx.fact_concept(fact)
    siblings = [
        item
        for item in ctx.candidates(fact["entity_id"], concept)
        if item.fact_id != status.fact_id
    ]
    correct: list[Mapping[str, Any]] = []
    wrong: list[tuple[Mapping[str, Any], str]] = []
    for claim in claims:
        attribute = claim.get("attribute")
        if (
            claim.get("claim_type") == "free_text"
            or claim.get("entity_id") != fact["entity_id"]
            or attribute is None
            or ctx.claim_concept(attribute) != concept
        ):
            continue
        conditions: Mapping[str, str] = claim.get("conditions") or {}
        if not compare_conditions(conditions, fact["conditions"])[0]:
            continue
        claim_value = normalize_claim_value(claim)
        if claim_value is None:
            continue
        comparison = _compare(claim_value, fact)
        if comparison.outcome is Outcome.MATCH:
            correct.append(claim)
        elif comparison.outcome is Outcome.MISMATCH and not any(
            compare_conditions(conditions, sibling.fact["conditions"])[0]
            and _compare(claim_value, sibling.fact).outcome is Outcome.MATCH
            for sibling in siblings
        ):
            wrong.append((claim, comparison.note))
    label_fact = f"核心事实 {status.fact_id}（版本 {status.version}）"
    if correct:
        texts = "；".join(f"“{claim['text']}”" for claim in correct)
        return PresenceVerdict(
            fact, PRESENT_CORRECT, f"回答中有陈述与{label_fact}一致：{texts}", tuple(correct)
        )
    if wrong:
        texts = "；".join(f"“{claim['text']}”（{note}）" for claim, note in wrong)
        return PresenceVerdict(
            fact,
            PRESENT_WRONG,
            f"回答提到了{label_fact}对应的内容，但与之不一致：{texts}",
            tuple(claim for claim, _ in wrong),
        )
    return PresenceVerdict(fact, MISSING, f"回答中没有与{label_fact}对应的陈述")
