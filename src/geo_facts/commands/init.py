"""`geo-facts init`：新建事实库目录。"""

from __future__ import annotations

import argparse

from geo_facts.cli import CommandResult, SubParsers, add_command
from geo_facts.commands._common import NOW_HELP
from geo_facts.store import init_store, resolve_now
from geo_facts.store.writer import EXAMPLE_PROJECT_ID, EXAMPLES
from geo_facts.timeutil import DEFAULT_TIMEZONE


def run(args: argparse.Namespace) -> CommandResult:
    data = init_store(
        args.store,
        project_id=args.project_id,
        now=resolve_now(args.now),
        example=args.example,
        timezone=args.timezone,
        store_id=args.store_id,
        synthetic=args.synthetic,
    )
    lines = [
        f"已创建事实库：{data['store']}",
        f"  事实库标识 {data['store_id']}，项目 {data['project_id']}，时区 {data['timezone']}",
    ]
    if data["example"] is not None:
        lines.append(
            f"  已写入样例 {data['example']}：{data['entities']} 个实体，"
            f"{data['fact_ids']} 条事实（共 {data['fact_versions']} 个版本）"
        )
    if data["synthetic"]:
        lines.append("  全部为合成数据，不代表任何真实品牌、产品或效果。")
    return CommandResult(data=data, text="\n".join(lines))


def register(subparsers: SubParsers) -> None:
    parser = add_command(subparsers, "init", run, help="新建事实库目录（目录非空时拒绝）")
    parser.add_argument("store", metavar="STORE", help="要创建的事实库目录")
    parser.add_argument(
        "--example",
        choices=EXAMPLES,
        help="写入内置的虚构样例（auto：虚构汽车品牌，全部为合成数据）",
    )
    parser.add_argument(
        "--project-id",
        dest="project_id",
        metavar="ID",
        help=f"项目标识；使用 --example 时可省略（默认 {EXAMPLE_PROJECT_ID}）",
    )
    parser.add_argument(
        "--store-id", dest="store_id", metavar="ID", help="事实库标识，默认 store-<项目标识>"
    )
    parser.add_argument(
        "--timezone", default=DEFAULT_TIMEZONE, help=f"按日统计用的时区，默认 {DEFAULT_TIMEZONE}"
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="标记为合成数据（导入的事实都会带 synthetic: true）",
    )
    parser.add_argument("--now", metavar="T", help=NOW_HELP)
