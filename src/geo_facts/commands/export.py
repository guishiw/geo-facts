"""`geo-facts export`：导出事实库（JSONL 或 CSV）。"""

from __future__ import annotations

import argparse
from pathlib import Path

from geo_facts.cli import CommandResult, SubParsers, add_command
from geo_facts.commands._common import add_as_of_argument, add_store_argument, parse_as_of
from geo_facts.store import load_store, select_facts, to_csv, to_jsonl
from geo_facts.store.export import UTF8_BOM

FORMATS: tuple[str, ...] = ("jsonl", "csv")


def run(args: argparse.Namespace) -> CommandResult:
    as_of = parse_as_of(args.as_of)
    store = load_store(args.store)
    facts = select_facts(store, as_of)
    content = to_csv(facts) if args.format == "csv" else to_jsonl(facts)
    data = {
        "store": str(store.paths.root),
        "as_of": None if as_of is None else as_of.isoformat(),
        "format": args.format,
        "count": len(facts),
        "fact_ids": len({fact["fact_id"] for fact in facts}),
        "out": args.out,
    }
    if args.out is None:
        data["records"] = facts
        return CommandResult(data=data, text=content.rstrip("\n"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # CSV 加 UTF-8 BOM，表格软件才能正确识别中文；JSONL 不加。
    text = UTF8_BOM + content if args.format == "csv" else content
    out.write_text(text, encoding="utf-8", newline="")
    scope = "全部版本" if as_of is None else f"{as_of.isoformat()} 当日有效的版本"
    return CommandResult(data=data, text=f"已导出 {len(facts)} 条（{scope}，{args.format}）：{out}")


def register(subparsers: SubParsers) -> None:
    parser = add_command(subparsers, "export", run, help="导出事实库（JSONL 或 CSV）")
    add_store_argument(parser)
    add_as_of_argument(parser, help="只导出该日（YYYY-MM-DD）有效的版本；不给则导出全部版本")
    parser.add_argument(
        "--format", choices=FORMATS, default="jsonl", help="导出格式，默认 jsonl；csv 可再导入"
    )
    parser.add_argument("--out", metavar="FILE", help="输出文件；不给则打印到标准输出")
