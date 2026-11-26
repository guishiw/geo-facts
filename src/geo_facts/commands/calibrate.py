"""`geo-facts calibrate`：对照人工金标集校准判定器。

错误率的区间上限大于 `1 − 目标准确率` 时退出 3，结论只能是“判定精度不足”。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from geo_facts.cli import PRECHECK_NOTICE, CommandResult, SubParsers, add_command
from geo_facts.errors import EXIT_INSUFFICIENT, EXIT_OK
from geo_facts.jsonl import iter_jsonl
from geo_facts.judge import calibration


def _ratio(value: float | None, interval: dict[str, Any] | None, reason: str | None) -> str:
    if value is None or interval is None:
        return f"无法计算（{reason}）"
    return f"{value:.4f} [{interval['low']:.4f}, {interval['high']:.4f}]"


def _render(report: dict[str, Any]) -> str:
    counts = report["counts"]
    labels: list[str] = report["labels"]
    lines = [
        "判定校准（预检）",
        f"样本：金标 {counts['gold']} 条，判定结果 {counts['predicted']} 条，"
        f"按 id 对齐 {counts['aligned']} 条；只在金标中出现 {counts['only_in_gold']} 条，"
        f"只在判定结果中出现 {counts['only_in_predicted']} 条（均不参与计算）。",
        "混淆矩阵（每行一个金标标签，行内是判定器给出的各标签条数）：",
    ]
    for gold_label in labels:
        row = report["confusion_matrix"][gold_label]
        cells = "，".join(f"{label} {row[label]}" for label in labels)
        lines.append(f"  金标 {gold_label}：{cells}")
    level = calibration.format_number(report["confidence"])
    lines.append(f"各类指标（Wilson 双侧区间，置信水平 {level}）：")
    for label in labels:
        entry = report["per_label"][label]
        reasons: dict[str, str] = entry["null_reasons"]
        precision = _ratio(
            entry["precision"], entry["precision_interval"], reasons.get("precision")
        )
        recall = _ratio(entry["recall"], entry["recall_interval"], reasons.get("recall"))
        f1 = f"无法计算（{reasons['f1']}）" if entry["f1"] is None else f"{entry['f1']:.4f}"
        lines.append(
            f"  {label}：精确率 {precision}，召回率 {recall}，F1 {f1}"
            f"（金标 {entry['support']} 条，判定器给出 {entry['predicted']} 条）"
        )
    lines.append(
        f"准确率 {_ratio(report['accuracy'], report['accuracy_interval'], None)}；"
        f"错误率 {_ratio(report['error_rate'], report['error_rate_interval'], None)}"
        f"（错误 {counts['errors']} 条）"
    )
    lines.append(
        f"目标准确率 {calibration.format_number(report['target'])}，"
        f"对应的错误率上限 {calibration.format_number(report['allowed_error_rate'])}。"
    )
    conclusion = report["conclusion"]
    lines.append(f"结论：{conclusion['text']}。{conclusion['detail']}")
    lines.extend(f"注意：{note}" for note in report["notes"])
    lines.append(PRECHECK_NOTICE)
    return "\n".join(lines)


def run(args: argparse.Namespace) -> CommandResult:
    gold = calibration.index_labels(iter_jsonl(Path(args.gold)), "gold")
    predicted = calibration.index_labels(iter_jsonl(Path(args.predicted)), "predicted")
    report = calibration.calibrate(gold, predicted, target=args.target, confidence=args.confidence)
    report["notice"] = PRECHECK_NOTICE
    adequate = report["conclusion"]["code"] == calibration.CODE_ADEQUATE
    return CommandResult(
        data=report,
        text=_render(report),
        exit_code=EXIT_OK if adequate else EXIT_INSUFFICIENT,
    )


def register(subparsers: SubParsers) -> None:
    parser = add_command(
        subparsers,
        "calibrate",
        run,
        help="判定校准：对照人工金标集给出混淆矩阵、各类指标与区间",
        description=(
            "判定校准：对照人工金标集给出混淆矩阵、各类精确率 / 召回率 / F1、"
            "准确率与错误率及其 Wilson 双侧区间。"
            "错误率的区间上限大于 1 − 目标准确率时，结论为“判定精度不足”，退出码 3。"
        ),
    )
    parser.add_argument("--gold", required=True, help="人工金标 JSONL，每行 {id, label}")
    parser.add_argument("--predicted", required=True, help="判定器输出 JSONL，每行 {id, label}")
    parser.add_argument(
        "--target",
        type=float,
        default=calibration.DEFAULT_TARGET,
        help="目标准确率，0 与 1 之间的小数（默认 0.98）",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=calibration.DEFAULT_CONFIDENCE,
        help="Wilson 双侧区间的置信水平（默认 0.95）",
    )
