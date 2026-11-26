"""命令之间共用的参数与小工具。文件名以下划线开头，cli.py 不会把它当成命令模块。"""

from __future__ import annotations

import argparse
from datetime import date

from geo_facts.store import parse_day

NOW_HELP = "写入记录用的 UTC 时间，如 2026-10-08T02:00:00Z；传入后输出可逐字节复现"


def add_store_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("store", metavar="STORE", help="事实库目录")


def add_as_of_argument(parser: argparse.ArgumentParser, *, help: str) -> None:
    parser.add_argument("--as-of", dest="as_of", metavar="DATE", help=help)


def parse_as_of(value: str | None) -> date | None:
    """解析 --as-of（YYYY-MM-DD）；未给时返回 None。"""
    return None if value is None else parse_day(value, what="--as-of ")
