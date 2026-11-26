"""`geo-facts judge`：双向判定，写出 judgments.jsonl（逐条符合 judgment 契约）。

`judge --store STORE --observations FILE [--prompt-set FILE] --as-of DATE --out FILE
       [--claims-out FILE] [--now T]`

- 回答 → 事实库：对回答里拆出的每条陈述判 supported / contradicted / unverifiable（准确率）；
- 事实库 → 回答：对题库里配置了 core_fact_ids 的题，判每条核心事实 present_correct /
  present_wrong / missing（呈现率）。没有 --prompt-set 时不做这一方向。
- `--out`、`--claims-out` 指向的文件已存在时拒绝（不覆盖、不追加）；需要保留多次结果请换文件名。
"""

from __future__ import annotations

import argparse
from typing import Any

from geo_facts.cli import CommandResult, SubParsers, add_command
from geo_facts.commands._common import NOW_HELP, parse_as_of
from geo_facts.commands.claims import prepare_out
from geo_facts.errors import InputError
from geo_facts.jsonl import append_jsonl
from geo_facts.judge.run import run_judge
from geo_facts.store import resolve_now


def _rate(value: float | None, numerator: int, denominator: int) -> str:
    if value is None:
        return "无（分母为 0）"
    return f"{round(value, 4)}（{numerator} / {denominator}）"


def run(args: argparse.Namespace) -> CommandResult:
    as_of = parse_as_of(args.as_of)
    assert as_of is not None  # argparse 已要求必填
    if args.claims_out is not None and args.claims_out == args.out:
        raise InputError("--out 与 --claims-out 不能是同一个文件")
    out = prepare_out(args.out)
    claims_out = prepare_out(args.claims_out) if args.claims_out is not None else None
    result = run_judge(
        args.store,
        args.observations,
        as_of=as_of,
        created_at=resolve_now(args.now),
        prompt_set_path=args.prompt_set,
    )
    out.touch()
    append_jsonl(out, result.judgments)
    if claims_out is not None:
        claims_out.touch()
        append_jsonl(claims_out, result.claims)
    data: dict[str, Any] = {
        **result.summary,
        "outputs": {
            "judgments": str(out),
            "claims": None if claims_out is None else str(claims_out),
        },
    }
    answer = data["answer_to_facts"]
    presence = data["facts_to_answer"]
    verifiable = answer["supported"] + answer["contradicted"]
    lines = [
        f"判定完成（查询日 {data['as_of']}）：{data['observations']} 条观测，{data['claims']} 条陈述，"
        f"写入 {out}",
        f"  回答 → 事实库：支持 {answer['supported']}，矛盾 {answer['contradicted']}"
        f"（其中过期信息 {answer['stale_contradictions']}），不可核验 {answer['unverifiable']}",
        f"  准确率：可核验口径 {_rate(answer['accuracy_verifiable'], answer['supported'], verifiable)}；"
        f"严格口径 {_rate(answer['accuracy_strict'], answer['supported'], answer['total'])}；"
        f"不可核验率 {_rate(answer['unverifiable_rate'], answer['unverifiable'], answer['total'])}",
        f"  事实库 → 回答：呈现正确 {presence['present_correct']}，呈现错误 {presence['present_wrong']}，"
        f"缺失 {presence['missing']}；排除不可用的核心事实 {presence['excluded']} 条",
        f"  呈现率 {_rate(presence['presence_rate'], presence['present_correct'], presence['total'])}"
        + (f"（{presence['null_reason']}）" if presence["null_reason"] else ""),
        f"  {data['basis_note']}",
    ]
    return CommandResult(data=data, text="\n".join(lines))


def register(subparsers: SubParsers) -> None:
    parser = add_command(
        subparsers,
        "judge",
        run,
        help="双向判定：回答 → 事实库（准确率）与事实库 → 回答（呈现率），写出 judgments.jsonl",
    )
    parser.add_argument("--store", required=True, metavar="STORE", help="事实库目录")
    parser.add_argument("--observations", required=True, metavar="FILE", help="observations.jsonl")
    parser.add_argument(
        "--prompt-set",
        dest="prompt_set",
        metavar="FILE",
        help="封存题库（含按题配置的 core_fact_ids）",
    )
    parser.add_argument(
        "--as-of",
        dest="as_of",
        required=True,
        metavar="DATE",
        help="按该日（YYYY-MM-DD）取有效的事实版本",
    )
    parser.add_argument(
        "--out", required=True, metavar="FILE", help="judgments.jsonl（已存在则拒绝）"
    )
    parser.add_argument(
        "--claims-out", dest="claims_out", metavar="FILE", help="同时写出拆解出的 claims.jsonl"
    )
    parser.add_argument("--now", metavar="T", help=NOW_HELP)
