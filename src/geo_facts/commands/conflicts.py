"""`geo-facts conflicts`：列出事实库里的冲突。只陈列，不裁决。"""

from __future__ import annotations

import argparse
from typing import Any

from geo_facts.cli import CommandResult, SubParsers, add_command
from geo_facts.commands._common import add_as_of_argument, add_store_argument, parse_as_of
from geo_facts.errors import EXIT_CHECK_FAILED
from geo_facts.store import detect_conflicts, load_store


def _value_text(item: dict[str, Any]) -> str:
    value = item["value"]
    if isinstance(value, dict):
        value = f"{value['min']}~{value['max']}"
    unit = "" if item["unit"] is None else f" {item['unit']}"
    return f"{value}{unit}"


def _conflict_lines(conflict: dict[str, Any]) -> list[str]:
    conditions = "、".join(f"{key}={value}" for key, value in conflict["conditions"].items())
    periods = "；".join(
        f"{period['from']} 至 {period['until'] or '未定'}" for period in conflict["periods"]
    )
    lines = [
        f"- 实体 {conflict['entity_id']}，属性 {conflict['attribute']}，"
        f"条件 {conditions or '无'}，重叠时段 {periods}"
    ]
    for item in conflict["facts"]:
        source = "无证据" if item["evidence_id"] is None else f"证据 {item['evidence_id']}"
        lines.append(
            f"    {item['fact_id']}（版本 {item['version']}）：{_value_text(item)}，"
            f"来源等级 {item['source_tier'] or '未定'}，{source}，状态 {item['status']}"
        )
    return lines


def run(args: argparse.Namespace) -> CommandResult:
    as_of = parse_as_of(args.as_of)
    store = load_store(args.store)
    conflicts = detect_conflicts(store, as_of=as_of)
    data = {
        "store": str(store.paths.root),
        "as_of": None if as_of is None else as_of.isoformat(),
        "count": len(conflicts),
        "conflicts": conflicts,
        "notice": "只列出冲突，不自动裁决；请人工核对后撤回或更正其中一条。",
    }
    scope = "任意一天" if as_of is None else as_of.isoformat()
    if not conflicts:
        return CommandResult(data=data, text=f"未发现冲突（检查范围：{scope}）。")
    lines = [f"发现 {len(conflicts)} 处冲突（检查范围：{scope}）。{data['notice']}"]
    for conflict in conflicts:
        lines.extend(_conflict_lines(conflict))
    return CommandResult(data=data, text="\n".join(lines), exit_code=EXIT_CHECK_FAILED)


def register(subparsers: SubParsers) -> None:
    parser = add_command(
        subparsers,
        "conflicts",
        run,
        help="列出冲突：同实体、同属性、同条件、有效期重叠但值不同（有冲突退出 1）",
    )
    add_store_argument(parser)
    add_as_of_argument(
        parser, help="只看该日（YYYY-MM-DD）仍然存在的冲突；不给则任何一天有重叠都列出"
    )
