"""稿件 meta（JSON，可缺省）的读取与校验。

字段：title、brand_entity_id、topic、author、audience、product_type（字符串）；
created_at、updated_at、published_at（`YYYY-MM-DD`，或带时区的 ISO 8601 时间，如
`2026-10-08T02:00:00Z` / `2026-10-08T10:00:00+08:00`；不带时区的时间拒绝猜测，按输入错误处理）；
ai_generated（布尔）；ai_label（布尔或非空字符串）。其他字段忽略。类型不对 → 输入错误（退出 2）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from geo_facts.errors import InputError
from geo_facts.jsonl import read_json
from geo_facts.timeutil import zone

STRING_FIELDS = ("title", "brand_entity_id", "topic", "author", "audience", "product_type")
TIME_FIELDS = ("created_at", "updated_at", "published_at")

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:?\d{2})$"
)


@dataclass(frozen=True)
class TimeValue:
    """meta 里的一个时间：原文、时刻（只有日期时为 None）、在项目时区下的自然日。"""

    text: str
    moment: datetime | None
    day: date

    def earlier_than(self, other: TimeValue) -> bool:
        if self.moment is not None and other.moment is not None:
            return self.moment < other.moment
        return self.day < other.day

    def after(self, now: datetime, timezone: str) -> bool:
        if self.moment is not None:
            return self.moment > now
        return self.day > now.astimezone(zone(timezone)).date()


def parse_time(text: Any, field: str, timezone: str) -> TimeValue:
    if not isinstance(text, str):
        raise InputError(f"meta.{field} 必须是字符串时间", details={"field": field})
    value = text.strip()
    try:
        if _DATE.match(value):
            day = date.fromisoformat(value)
            return TimeValue(text=value, moment=None, day=day)
        if _DATETIME.match(value):
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return TimeValue(
                text=value, moment=moment, day=moment.astimezone(zone(timezone)).date()
            )
    except ValueError as exc:
        raise InputError(f"meta.{field} 不是合法时间：{value!r}", details={"field": field}) from exc
    raise InputError(
        f"meta.{field} 不是合法时间：{value!r}（只接受 YYYY-MM-DD 或带时区的 ISO 8601 时间）",
        details={"field": field},
    )


@dataclass(frozen=True)
class Meta:
    title: str | None = None
    brand_entity_id: str | None = None
    topic: str | None = None
    author: str | None = None
    audience: str | None = None
    product_type: str | None = None
    created_at: TimeValue | None = None
    updated_at: TimeValue | None = None
    published_at: TimeValue | None = None
    has_ai_generated: bool = False
    ai_generated: bool | None = None
    ai_label: bool | str | None = None

    @property
    def ai_labeled(self) -> bool:
        if isinstance(self.ai_label, str):
            return bool(self.ai_label.strip())
        return self.ai_label is True


def parse_meta(value: Any, timezone: str, *, where: str = "meta") -> Meta:
    if not isinstance(value, dict):
        raise InputError(f"{where} 必须是 JSON 对象")
    fields: dict[str, Any] = {}
    for name in STRING_FIELDS:
        item = value.get(name)
        if item is None:
            continue
        if not isinstance(item, str):
            raise InputError(f"{where}.{name} 必须是字符串", details={"field": name})
        fields[name] = item.strip() or None
    for name in TIME_FIELDS:
        if value.get(name) is not None:
            fields[name] = parse_time(value[name], name, timezone)
    ai_generated = value.get("ai_generated")
    if ai_generated is not None and not isinstance(ai_generated, bool):
        raise InputError(
            f"{where}.ai_generated 必须是 true 或 false", details={"field": "ai_generated"}
        )
    ai_label = value.get("ai_label")
    if ai_label is not None and not isinstance(ai_label, (bool, str)):
        raise InputError(f"{where}.ai_label 必须是布尔值或字符串", details={"field": "ai_label"})
    return Meta(
        **fields,
        has_ai_generated=ai_generated is not None,
        ai_generated=ai_generated,
        ai_label=ai_label,
    )


def load_meta(path: Path, timezone: str) -> Meta:
    return parse_meta(read_json(path), timezone, where=f"meta 文件 {path.name}")
