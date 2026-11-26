"""`geo-facts import`：把 xlsx / csv / json / md 文件导入事实库，或输出导入模板。

- `import STORE --input FILE [--format xlsx|csv|json|md] [--reviewer NAME] [--now T]`
- `import --template csv|json|md [--out FILE]`

合法行写入、非法行不写入并逐行报告（行号、列、原因）；有非法行时退出 1。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from geo_facts.cli import CommandResult, SubParsers, add_command
from geo_facts.commands._common import NOW_HELP
from geo_facts.errors import EXIT_CHECK_FAILED, InputError
from geo_facts.ingest import FORMATS, TEMPLATE_FORMATS, column_docs, import_file, render_template
from geo_facts.store import resolve_now
from geo_facts.store.export import UTF8_BOM

MAX_LISTED = 50


def _run_template(args: argparse.Namespace) -> CommandResult:
    if args.store is not None or args.input is not None:
        raise InputError("--template 只输出模板，不能同时给出 STORE 或 --input")
    content = render_template(args.template)
    columns = column_docs()
    data: dict[str, Any] = {"template": args.template, "out": args.out, "columns": columns}
    if args.out is None:
        data["content"] = content
        return CommandResult(data=data, text=content.rstrip("\n"))
    out = Path(args.out)
    if out.exists():
        raise InputError(f"文件已存在，拒绝覆盖：{out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    # csv 模板加 UTF-8 BOM，表格软件才能正确识别中文。
    text = UTF8_BOM + content if args.template == "csv" else content
    out.write_text(text, encoding="utf-8", newline="")
    lines = [f"已写出 {args.template} 模板：{out}", "列说明："]
    lines.extend(
        f"  {item['name']}（{item['required']}）：{item['description']}" for item in columns
    )
    return CommandResult(data=data, text="\n".join(lines))


def _where(item: dict[str, Any]) -> str:
    where = f"第 {item['row']} 条实体记录" if item["kind"] == "entity" else f"第 {item['row']} 行"
    return where + (f" [{item['column']}]" if item["column"] else "")


def _run_import(args: argparse.Namespace) -> CommandResult:
    if args.store is None or args.input is None:
        raise InputError("导入需要 STORE 和 --input FILE；只要模板请用 --template csv|json|md")
    if args.out is not None:
        raise InputError("--out 只用于 --template")
    result = import_file(
        args.store,
        args.input,
        fmt=args.format,
        reviewer=args.reviewer,
        now=resolve_now(args.now),
    )
    data = {"store": str(args.store), **result}
    facts, entities = result["facts"], result["entities"]
    head = "导入完成" if result["ok"] else "导入未完全通过"
    lines = [
        f"{head}：{result['input']}（{result['format']}，{result['rows']} 行）",
        f"  事实：新增 {facts['created']} 条，新版本 {facts['new_versions']} 个，"
        f"内容未变跳过 {facts['unchanged']} 条，非法未写入 {facts['invalid']} 行",
    ]
    if any(entities.values()):
        lines.append(
            f"  实体：新增 {entities['created']} 个，更新 {entities['updated']} 个，"
            f"未变 {entities['unchanged']} 个，非法 {entities['invalid']} 个"
        )
    if result["notices"]:
        lines.append(f"提示（{len(result['notices'])}）：")
        lines.extend(
            f"  第 {item['row']} 行 {item['fact_id']}：{item['message']}"
            for item in result["notices"][:MAX_LISTED]
        )
    if result["errors"]:
        lines.append(f"错误（{len(result['errors'])}）：")
        lines.extend(
            f"  {_where(item)}：{item['message']}" for item in result["errors"][:MAX_LISTED]
        )
        if len(result["errors"]) > MAX_LISTED:
            lines.append(f"  ……仅列出前 {MAX_LISTED} 条，完整清单见 --json 输出。")
    lines.append(f"  审计事件 {result['audit_event_id']} 已记录。")
    return CommandResult(
        data=data, text="\n".join(lines), exit_code=0 if result["ok"] else EXIT_CHECK_FAILED
    )


def run(args: argparse.Namespace) -> CommandResult:
    if args.template is not None:
        return _run_template(args)
    return _run_import(args)


def register(subparsers: SubParsers) -> None:
    parser = add_command(
        subparsers,
        "import",
        run,
        help="导入事实（xlsx / csv / json / md），或用 --template 输出导入模板",
        description="导入事实：合法行写入、非法行逐行报错（有非法行退出 1）；"
        "同 fact_id 内容变化时追加新版本，内容不变则跳过。",
    )
    parser.add_argument("store", metavar="STORE", nargs="?", help="事实库目录")
    parser.add_argument("--input", metavar="FILE", help="要导入的文件")
    parser.add_argument("--format", choices=FORMATS, help="文件格式；默认按扩展名判断")
    parser.add_argument(
        "--reviewer",
        metavar="NAME",
        help="审核责任人：补进未填 reviewer 的证据，并记为审计事件的变更人",
    )
    parser.add_argument("--now", metavar="T", help=NOW_HELP)
    parser.add_argument("--template", choices=TEMPLATE_FORMATS, help="输出导入模板与列说明")
    parser.add_argument("--out", metavar="FILE", help="模板写入的文件（与 --template 一起用）")
