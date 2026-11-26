"""版本与时效：按日期取当时有效的版本，并判断一条事实能否作为确定依据。

规则（写进 docs/methodology.md）：
- 某日有效 = valid_from ≤ 该日 ≤ valid_until（valid_until 为空表示尚未失效）；
- 同一 fact_id 在某日有效的版本可能不止一个，取版本号最高的那个；
- 只有事实区（zone=fact）、状态为 active、带证据的版本才能作为确定依据；
  观点区、营销区、已撤回、有争议、无证据、已过期、尚未生效的都不行。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from geo_facts.errors import InputError
from geo_facts.timeutil import zone

# 不能作为确定依据的原因代码（顺序即输出顺序）。
REASON_CODES: tuple[str, ...] = (
    "expired",
    "not_yet_effective",
    "not_fact_zone",
    "retracted",
    "disputed",
    "no_evidence",
    "conflict",
)

REASON_TEXT: dict[str, str] = {
    "expired": "已过期",
    "not_yet_effective": "尚未生效",
    "not_fact_zone": "不在事实区（观点或营销表达）",
    "retracted": "已撤回",
    "disputed": "有争议",
    "no_evidence": "没有绑定证据",
    "conflict": "事实库对此存在未裁决的冲突",
}


def parse_day(text: str, *, what: str = "日期") -> date:
    """解析 YYYY-MM-DD。"""
    try:
        if len(text) != 10:
            raise ValueError(text)
        return date.fromisoformat(text)
    except (ValueError, TypeError) as exc:
        raise InputError(f"{what}必须是 YYYY-MM-DD：{text!r}") from exc


def today_in(timezone: str) -> date:
    """指定时区的今天。"""
    return datetime.now(UTC).astimezone(zone(timezone)).date()


def valid_on(fact: dict[str, Any], day: date) -> bool:
    """这个版本在某日是否处于有效期内（不看状态和证据）。"""
    if date.fromisoformat(fact["valid_from"]) > day:
        return False
    until = fact["valid_until"]
    return until is None or day <= date.fromisoformat(until)


def effective_version(versions: list[dict[str, Any]], day: date) -> dict[str, Any] | None:
    """某日有效的最高版本；没有则为 None。"""
    candidates = [fact for fact in versions if valid_on(fact, day)]
    if not candidates:
        return None
    return max(candidates, key=lambda fact: int(fact["version"]))


def change_days(versions: list[dict[str, Any]]) -> list[date]:
    """“当日有效版本”可能发生变化的日子：各版本的生效日，以及失效日的次日。"""
    days: set[date] = set()
    for fact in versions:
        days.add(date.fromisoformat(fact["valid_from"]))
        if fact["valid_until"] is not None:
            until = date.fromisoformat(fact["valid_until"])
            if until < date.max:
                days.add(until + timedelta(days=1))
    return sorted(days)


def live_versions(versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """至少在某一天是“当日有效版本”的版本（按版本号升序）。

    被更高版本在整个有效期内取代的版本不在其中——它们只是历史，不再参与任何判定。
    """
    live: dict[int, dict[str, Any]] = {}
    for day in change_days(versions):
        current = effective_version(versions, day)
        if current is not None:
            live[int(current["version"])] = current
    return [live[number] for number in sorted(live)]


@dataclass(frozen=True)
class Resolution:
    """某个 fact_id 在某日的状态。

    state：effective（有当日有效的版本）、expired（曾经有效，已过期）、
    not_yet_effective（尚未生效）、unknown（事实库里没有这个 fact_id）。
    fact：effective 时为当日有效版本；expired 时为最近过期的版本；
    not_yet_effective 时为最早将生效的版本。
    """

    fact_id: str
    state: str
    fact: dict[str, Any] | None


def resolve(fact_id: str, versions: list[dict[str, Any]], day: date) -> Resolution:
    if not versions:
        return Resolution(fact_id=fact_id, state="unknown", fact=None)
    current = effective_version(versions, day)
    if current is not None:
        return Resolution(fact_id=fact_id, state="effective", fact=current)
    past = [
        fact
        for fact in versions
        if fact["valid_until"] is not None and date.fromisoformat(fact["valid_until"]) < day
    ]
    if past:
        latest = max(past, key=lambda fact: (fact["valid_until"], int(fact["version"])))
        return Resolution(fact_id=fact_id, state="expired", fact=latest)
    earliest = min(versions, key=lambda fact: (fact["valid_from"], -int(fact["version"])))
    return Resolution(fact_id=fact_id, state="not_yet_effective", fact=earliest)


def stale_versions(versions: list[dict[str, Any]], day: date) -> list[tuple[dict[str, Any], str]]:
    """某日已经不再生效的旧版本：过期的（expired）和被更高版本取代的（superseded）。

    判定“回答与已过期的版本一致”时用它取历史值。尚未生效的版本不在其中。
    """
    current = effective_version(versions, day)
    result: list[tuple[dict[str, Any], str]] = []
    for fact in sorted(versions, key=lambda item: int(item["version"])):
        if current is not None and fact is current:
            continue
        until = fact["valid_until"]
        if until is not None and date.fromisoformat(until) < day:
            result.append((fact, "expired"))
        elif valid_on(fact, day):
            result.append((fact, "superseded"))
    return result


def basis_blockers(fact: dict[str, Any]) -> list[str]:
    """这个版本本身不能作为确定依据的原因（不含时效和冲突）。空列表表示可以。"""
    blockers: list[str] = []
    if fact["zone"] != "fact":
        blockers.append("not_fact_zone")
    if fact["status"] in ("retracted", "disputed"):
        blockers.append(fact["status"])
    if fact["evidence"] is None:
        blockers.append("no_evidence")
    return blockers


def describe_reasons(reasons: list[str] | tuple[str, ...]) -> str:
    return "、".join(REASON_TEXT[code] for code in reasons)
