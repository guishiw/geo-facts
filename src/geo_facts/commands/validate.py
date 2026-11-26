"""`geo-facts validate`：事实库整体检查，或按数据契约校验 JSON / JSONL 文件。

- `validate STORE [--as-of DATE]`：位置参数是事实库目录时做整体检查；
- `validate PATH --type CONTRACT`：带 `--type` 时按契约校验文件。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from geo_facts import contracts as spec
from geo_facts.cli import CommandResult, SubParsers, add_command
from geo_facts.commands._common import add_as_of_argument, parse_as_of
from geo_facts.errors import EXIT_CHECK_FAILED, EXIT_OK, InputError
from geo_facts.jsonl import iter_jsonl, read_json
from geo_facts.store import check_store, load_store, today_in

MAX_REPORTED = 50


def _records(path: Path) -> list[tuple[str, Any]]:
    if path.suffix == ".jsonl":
        return [(f"第 {number} 行", record) for number, record in iter_jsonl(path)]
    value = read_json(path)
    if isinstance(value, list):
        return [(f"第 {index + 1} 项", item) for index, item in enumerate(value)]
    return [("文件", value)]


def _run_contract(args: argparse.Namespace) -> CommandResult:
    path = Path(args.path)
    contract: str = args.type
    if contract not in spec.CONTRACTS:
        raise InputError(f"未知契约：{contract}；可选：{', '.join(spec.CONTRACTS)}")
    if args.as_of is not None:
        raise InputError("--as-of 只用于事实库检查，不能与 --type 同时使用")
    if path.is_dir():
        raise InputError(f"{path} 是目录；--type 只用于校验单个文件，事实库检查请去掉 --type")
    records = _records(path)
    problems: list[dict[str, Any]] = []
    invalid = 0
    for where, record in records:
        issues = spec.iter_issues(contract, record)
        if not issues:
            continue
        invalid += 1
        if len(problems) < MAX_REPORTED:
            problems.append({"where": where, "issues": [issue.to_dict() for issue in issues]})
    data = {
        "contract": contract,
        "path": str(path),
        "records": len(records),
        "invalid": invalid,
        "problems": problems,
        "problems_truncated": invalid > len(problems),
    }
    if invalid == 0:
        return CommandResult(
            data=data, text=f"通过：{len(records)} 条记录全部符合契约 {contract}。"
        )
    lines = [f"未通过：{len(records)} 条记录中有 {invalid} 条不符合契约 {contract}。"]
    for problem in problems:
        for issue in problem["issues"]:
            lines.append(f"  {problem['where']} {issue['path'] or '/'}：{issue['message']}")
    if invalid > len(problems):
        lines.append(f"  ……仅列出前 {MAX_REPORTED} 条。")
    return CommandResult(data=data, text="\n".join(lines), exit_code=EXIT_CHECK_FAILED)


def _run_store(args: argparse.Namespace) -> CommandResult:
    as_of = parse_as_of(args.as_of)
    store = load_store(args.path, strict=False)
    day = as_of if as_of is not None else today_in(store.timezone)
    data = check_store(store, as_of=day, as_of_given=as_of is not None)
    counts = data["as_of_counts"]
    zones = data["zones"]
    lines = [
        ("通过" if data["ok"] else "未通过")
        + f"：事实库 {data['store_id']}（项目 {data['project_id']}），"
        f"{data['entities']} 个实体，{data['fact_ids']} 条事实（共 {data['fact_versions']} 个版本）。",
        f"  三区：事实 {zones['fact']}，观点 {zones['opinion']}，营销表达 {zones['marketing']}"
        "（只有事实区能作为判定依据）",
        f"  {data['as_of']} 可作为确定依据 {counts['usable']} 条，不可用 {counts['unusable']} 条；"
        f"冲突 {len(data['conflicts'])} 处；没有证据的核心事实 {len(data['core_without_evidence'])} 条",
    ]
    for label, key in (("错误", "errors"), ("提示", "warnings")):
        if data[key]:
            lines.append(f"{label}（{len(data[key])}）：")
            lines.extend(f"  [{item['code']}] {item['message']}" for item in data[key])
    return CommandResult(
        data=data,
        text="\n".join(lines),
        exit_code=EXIT_OK if data["ok"] else EXIT_CHECK_FAILED,
    )


def run(args: argparse.Namespace) -> CommandResult:
    if args.type is not None:
        return _run_contract(args)
    if Path(args.path).is_dir():
        return _run_store(args)
    raise InputError(
        f"{args.path} 不是事实库目录；校验单个文件请用 --type 指定契约（如 --type fact）"
    )


def register(subparsers: SubParsers) -> None:
    parser = add_command(
        subparsers,
        "validate",
        run,
        help="事实库整体检查（给目录），或按数据契约校验 JSON / JSONL 文件（给文件和 --type）",
    )
    parser.add_argument(
        "path", metavar="STORE|FILE", help="事实库目录，或待校验文件（.json / .jsonl）"
    )
    parser.add_argument("--type", help="契约名，如 fact、entity；给出后按契约校验文件")
    add_as_of_argument(parser, help="事实库检查的查询日（YYYY-MM-DD），默认取事实库时区的今天")
