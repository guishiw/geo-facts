"""`geo-facts claims extract`：从回答（observations.jsonl）或稿件文本拆出陈述，写成 claims.jsonl。

- `claims extract --store STORE --observations FILE --out FILE`
- `claims extract --store STORE --text FILE --source-id ID [--source-type draft|observation] --out FILE`

默认用规则拆解器；`--extractor` 只能选已注册的拆解器（本仓库未提供模型实现）。
`--out` 指向的文件已存在时拒绝，不覆盖。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from geo_facts.claims import build_lexicon
from geo_facts.claims.batch import (
    extract_observations,
    extract_text,
    load_observations,
    summarize,
    usable_observations,
)
from geo_facts.claims.plugins import (
    DEFAULT_EXTRACTOR,
    MODEL_NOTICE,
    available_extractors,
    get_extractor,
)
from geo_facts.cli import CommandResult, SubParsers, add_command, add_group
from geo_facts.errors import InputError
from geo_facts.jsonl import append_jsonl
from geo_facts.store import is_valid_id, load_store

SOURCE_TYPES: tuple[str, ...] = ("draft", "observation")


def prepare_out(path_text: str) -> Path:
    """输出文件：已存在时拒绝（结果文件只新建，不覆盖、不追加）。"""
    out = Path(path_text)
    if out.exists():
        raise InputError(f"输出文件已存在，拒绝覆盖：{out}（请换一个文件名或先移走旧文件）")
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def run_extract(args: argparse.Namespace) -> CommandResult:
    if (args.observations is None) == (args.text is None):
        raise InputError("--observations 与 --text 必须且只能给一个")
    if args.text is not None and args.source_id is None:
        raise InputError("--text 需要同时给出 --source-id")
    if args.observations is not None and (
        args.source_id is not None or args.source_type is not None
    ):
        raise InputError("--source-id、--source-type 只用于 --text")
    if args.source_id is not None and not is_valid_id(args.source_id):
        raise InputError(f"--source-id 不合法：{args.source_id!r}（字母或数字开头，可含 . _ : -）")
    store = load_store(args.store)
    extractor = get_extractor(args.extractor, build_lexicon(store))
    observations: dict[str, int] | None = None
    if args.observations is not None:
        usable = usable_observations(load_observations(Path(args.observations)))
        out = prepare_out(args.out)
        claims = extract_observations(
            extractor, usable.observations, project_id=store.project_id, synthetic=store.synthetic
        )
        observations = usable.stats.to_dict()
        source_label = "observations"
    else:
        path = Path(args.text)
        if not path.is_file():
            raise InputError(f"文本文件不存在：{path}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise InputError(f"{path.name} 不是 UTF-8 编码") from exc
        out = prepare_out(args.out)
        source = {"type": args.source_type or "draft", "id": args.source_id}
        claims = extract_text(
            extractor, text, source=source, project_id=store.project_id, synthetic=store.synthetic
        )
        source_label = "text"
    out.touch()
    append_jsonl(out, claims)
    data: dict[str, Any] = {
        "store": str(args.store),
        "input": args.observations or args.text,
        "source": source_label,
        "observations": observations,
        **summarize(claims),
        "out": str(out),
        "extractor": {"name": extractor.name},
        "notice": MODEL_NOTICE,
    }
    lines = [f"已拆出 {data['claims']} 条陈述，写入 {out}"]
    if observations is not None:
        lines.append(
            f"  观测 {observations['total']} 条：使用 {observations['used']} 条，"
            f"无效跳过 {observations['skipped_invalid']} 条，空回答跳过 {observations['skipped_empty']} 条"
        )
    by_type = data["by_type"]
    lines.append(
        f"  数值 {by_type['numeric']}、型号 {by_type['model']}、日期 {by_type['date']}、"
        f"自由文本 {by_type['free_text']}；未能归属实体 {data['without_entity']} 条"
    )
    lines.append(f"  拆解器 {extractor.name}。{MODEL_NOTICE}")
    return CommandResult(data=data, text="\n".join(lines))


def register(subparsers: SubParsers) -> None:
    group = add_group(subparsers, "claims", help="陈述拆解")
    parser = add_command(
        group,
        "extract",
        run_extract,
        help="从回答或稿件拆出陈述，写成 claims.jsonl（逐条符合 claim 契约）",
    )
    parser.add_argument(
        "--store", required=True, metavar="STORE", help="事实库目录（提供实体与属性词）"
    )
    parser.add_argument(
        "--observations", metavar="FILE", help="observations.jsonl（observation 契约）"
    )
    parser.add_argument("--text", metavar="FILE", help="一份稿件或回答的纯文本文件（UTF-8）")
    parser.add_argument("--source-id", dest="source_id", metavar="ID", help="--text 的来源编号")
    parser.add_argument(
        "--source-type",
        dest="source_type",
        choices=SOURCE_TYPES,
        help="--text 的来源类型，默认 draft",
    )
    parser.add_argument(
        "--out", required=True, metavar="FILE", help="输出的 claims.jsonl（已存在则拒绝）"
    )
    parser.add_argument(
        "--extractor",
        default=DEFAULT_EXTRACTOR,
        help=f"拆解器，默认 {DEFAULT_EXTRACTOR}；已注册：{', '.join(available_extractors())}",
    )
