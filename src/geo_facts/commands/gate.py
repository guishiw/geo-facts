"""`geo-facts gate`：发布前闸门。只给建议和状态，不负责发布。

退出码：有 block → 1；只有 review → 5；无发现 → 0；输入错误 → 2。
"""

from __future__ import annotations

import argparse
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from geo_facts.canon import sha256_bytes
from geo_facts.cli import PRECHECK_NOTICE, CommandResult, SubParsers, add_command
from geo_facts.errors import EXIT_CHECK_FAILED, EXIT_OK, InputError
from geo_facts.gate import engine
from geo_facts.gate.document import load_draft
from geo_facts.gate.entities import store_timezone
from geo_facts.gate.invisible import is_hidden_char
from geo_facts.gate.meta import load_meta
from geo_facts.gate.model import (
    EXIT_REVIEW,
    NOTICE,
    RESULT_BLOCK,
    STATUS_BLOCK,
    STATUS_REVIEW,
    GateContext,
)
from geo_facts.jsonl import write_json
from geo_facts.timeutil import DEFAULT_TIMEZONE, parse_utc, zone

EXCERPT_PREVIEW = 60


def _parse_as_of(text: str) -> date:
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        raise InputError(f"--as-of 必须是 YYYY-MM-DD：{text!r}")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise InputError(f"--as-of 不是合法日期：{text!r}") from exc


def _preview(text: str) -> str:
    """给人看的摘录：不可见字符写成 U+XXXX，换行写成 ⏎，过长截断。"""
    out: list[str] = []
    for char in text:
        if is_hidden_char(char):
            out.append(f"<U+{ord(char):04X}>")
        elif char in "\r\n":
            out.append("⏎")
        else:
            out.append(char)
    shown = "".join(out)
    return shown if len(shown) <= EXCERPT_PREVIEW else shown[:EXCERPT_PREVIEW] + "…"


def _render(data: dict[str, Any]) -> str:
    counts = data["counts"]
    draft = data["draft"]
    lines = [
        f"发布前闸门（预检）：{data['status_label']}",
        f"稿件：{draft['file']}（{draft['format']}，{draft['chars']} 个字符）",
        f"发现：拦截 {counts['block']} 条，需复核 {counts['review']} 条",
    ]
    for finding in data["findings"]:
        label = "拦截" if finding["result"] == RESULT_BLOCK else "需复核"
        span = finding["span"]
        where = f"[{span[0]}, {span[1]})" if span is not None else "整篇"
        lines.append(f"[{label}] {finding['rule_id']} {where}：{finding['reason']}")
        if finding["excerpt"]:
            lines.append(f"    原文：{_preview(finding['excerpt'])}")
        extra = f"；风险级别 {finding['risk_level']}" if finding["risk_level"] else ""
        if finding["requires"]:
            extra += f"；需要 {'、'.join(finding['requires'])}"
        lines.append(f"    建议：{finding['suggestion']}（依据：{finding['clause']}{extra}）")
    for skipped in data["skipped_checks"]:
        lines.append(
            f"未执行：{skipped['check']}（{'、'.join(skipped['rule_ids'])}）——{skipped['reason']}"
        )
    lines.append(f"{NOTICE}。")
    lines.append(PRECHECK_NOTICE)
    return "\n".join(lines)


def run(args: argparse.Namespace) -> CommandResult:
    draft_path = Path(args.draft)
    draft = load_draft(draft_path)
    now = parse_utc(args.now) if args.now else datetime.now(UTC)
    store = Path(args.store) if args.store else None
    timezone = store_timezone(store) if store is not None else DEFAULT_TIMEZONE
    as_of = _parse_as_of(args.as_of) if args.as_of else now.astimezone(zone(timezone)).date()
    meta = load_meta(Path(args.meta), timezone) if args.meta else None
    ctx = GateContext(
        now=now,
        as_of=as_of,
        timezone=timezone,
        store=store,
        draft_path=draft_path,
    )
    report = engine.evaluate(draft, meta, ctx)
    data = report.to_dict()
    data["draft"] = {
        "file": draft.name,
        "format": draft.fmt,
        "chars": len(draft.raw),
        "sha256": sha256_bytes(draft.raw.encode("utf-8")),
    }
    data["meta_provided"] = meta is not None
    data["as_of"] = as_of.isoformat()
    data["timezone"] = timezone
    data["precheck_notice"] = PRECHECK_NOTICE
    if args.out:
        write_json(Path(args.out), data)
    status = data["status"]
    exit_code = (
        EXIT_CHECK_FAILED
        if status == STATUS_BLOCK
        else EXIT_REVIEW
        if status == STATUS_REVIEW
        else EXIT_OK
    )
    return CommandResult(data=data, text=_render(data), exit_code=exit_code)


def register(subparsers: SubParsers) -> None:
    parser = add_command(
        subparsers,
        "gate",
        run,
        help="发布前闸门：检查稿件的隐藏文本、提示词注入、时间、词库、竞品比较、重复与 AI 标识",
        description=(
            "发布前闸门：对稿件做发布前检查，给出 pass / review / block 与逐条建议。"
            "有 block 退出 1，只有 review 退出 5，无发现退出 0。闸门只给建议和状态，不负责发布。"
        ),
    )
    parser.add_argument("--draft", required=True, help="稿件文件（.md / .txt / .html）")
    parser.add_argument("--meta", help="稿件 meta（JSON），可缺省")
    parser.add_argument("--store", help="事实库目录（读取 store.json 的时区与 entities.jsonl）")
    parser.add_argument("--as-of", dest="as_of", help="按该日取有效事实（YYYY-MM-DD）")
    parser.add_argument("--out", help="把结果 JSON 写入该文件")
    parser.add_argument("--now", help="当前时间（UTC，如 2026-10-08T02:00:00Z），传入后输出可复现")
