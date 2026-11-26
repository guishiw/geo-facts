"""判定校准：对照人工金标集给出混淆矩阵、各类 P / R / F1、区间与结论。

核心规则：判定器错误率的区间上限大于 `1 − 目标准确率` 时，只能给出
“判定精度不足，无法证明达标”；其余情况也只陈述“区间上限不超过多少”，
不对目标准确率下肯定结论。

Wilson 区间在本仓库内独立实现（分位数取自 statistics.NormalDist），不依赖其他仓库。
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from statistics import NormalDist
from typing import Any

from geo_facts.errors import InputError, InsufficientData

DEFAULT_TARGET = 0.98
DEFAULT_CONFIDENCE = 0.95
MAX_REPORTED_IDS = 20
INTERVAL_METHOD = "wilson"

CODE_INSUFFICIENT = "insufficient_precision"
CODE_ADEQUATE = "adequate"
TEXT_INSUFFICIENT = "判定精度不足，无法证明达标"

_SOURCE_NAMES = {"gold": "金标文件", "predicted": "判定结果文件"}


def format_number(value: float) -> str:
    """把目标、置信水平这类参数原样写进文字：最多 12 位有效数字，不额外舍入。"""
    return format(value, ".12g")


def two_sided_z(confidence: float) -> float:
    """双侧区间用的标准正态分位数：置信水平 0.95 对应 0.975 分位数。"""
    quantile = 0.5 + confidence / 2.0
    # 直接检查分位数：置信水平贴近 0 或 1 时，浮点下它会落到 0.5 或 1，同样不可用。
    if not 0.5 < quantile < 1.0:
        raise InputError("置信水平必须是 0 与 1 之间的小数（不含端点），如 0.95")
    return NormalDist().inv_cdf(quantile)


def wilson_interval(
    successes: int, total: int, confidence: float = DEFAULT_CONFIDENCE
) -> tuple[float, float]:
    """二项比例 successes / total 的 Wilson 双侧区间 (下限, 上限)。"""
    if total <= 0:
        raise InputError("计算区间至少需要 1 个样本")
    if not 0 <= successes <= total:
        raise InputError("区间的分子必须在 0 与样本数之间")
    z = two_sided_z(confidence)
    n = float(total)
    p = successes / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denominator
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denominator
    # 0 / n 的下限与 n / n 的上限在数学上恰为 0 和 1，直接取端点，避免浮点误差。
    low = 0.0 if successes == 0 else max(0.0, center - half)
    high = 1.0 if successes == total else min(1.0, center + half)
    return low, high


def index_labels(records: Iterable[tuple[int, Mapping[str, Any]]], source: str) -> dict[str, str]:
    """把 (行号, 记录) 序列整理成 id → label；格式不对或 id 重复都是输入错误。

    source 取 "gold" 或 "predicted"。同一个 id 出现多次时无法确定用哪一条，
    不做静默取舍，直接报错并给出重复的数量和清单。
    """
    name = _SOURCE_NAMES.get(source, source)
    labels: dict[str, str] = {}
    duplicates: set[str] = set()
    for number, record in records:
        identifier = record.get("id")
        label = record.get("label")
        if not isinstance(identifier, str) or not identifier.strip():
            raise InputError(f"{name}第 {number} 行缺少 id，或 id 不是非空字符串")
        if not isinstance(label, str) or not label.strip():
            raise InputError(f"{name}第 {number} 行缺少 label，或 label 不是非空字符串")
        if identifier in labels:
            duplicates.add(identifier)
            continue
        labels[identifier] = label
    if duplicates:
        raise InputError(
            f"{name}中有 {len(duplicates)} 个 id 重复出现，无法按 id 对齐",
            details={"source": source, "duplicate_ids": _id_report(sorted(duplicates))},
        )
    return labels


def _id_report(ids: list[str]) -> dict[str, Any]:
    return {
        "count": len(ids),
        "ids": ids[:MAX_REPORTED_IDS],
        "truncated": len(ids) > MAX_REPORTED_IDS,
    }


def _wilson(successes: int, total: int, confidence: float) -> dict[str, Any]:
    low, high = wilson_interval(successes, total, confidence)
    return {"method": INTERVAL_METHOD, "level": confidence, "low": low, "high": high}


def _interval(successes: int, total: int, confidence: float) -> dict[str, Any] | None:
    """分母为 0 时没有区间，返回 None（原因写在同级的 null_reasons 里）。"""
    return _wilson(successes, total, confidence) if total else None


def _per_label(
    label: str, labels: list[str], matrix: dict[str, dict[str, int]], confidence: float
) -> dict[str, Any]:
    true_positive = matrix[label][label]
    support = sum(matrix[label].values())
    predicted = sum(matrix[gold_label][label] for gold_label in labels)
    null_reasons: dict[str, str] = {}
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    if predicted:
        precision = true_positive / predicted
    else:
        null_reasons["precision"] = "判定器没有给出过该标签，精确率的分母为 0"
    if support:
        recall = true_positive / support
    else:
        null_reasons["recall"] = "对齐后的金标中没有该标签，召回率的分母为 0"
    if predicted and support:
        # F1 = 2PR / (P + R) = 2·TP / (判定器给出的条数 + 金标条数)
        f1 = 2.0 * true_positive / (predicted + support)
    else:
        null_reasons["f1"] = "精确率或召回率无法计算"
    return {
        "support": support,
        "predicted": predicted,
        "true_positive": true_positive,
        "precision": precision,
        "precision_interval": _interval(true_positive, predicted, confidence),
        "recall": recall,
        "recall_interval": _interval(true_positive, support, confidence),
        "f1": f1,
        "null_reasons": null_reasons,
    }


def _conclusion(
    *, errors: int, total: int, upper: float, allowed: float, confidence: float
) -> dict[str, Any]:
    budget = format_number(allowed)
    # 通常写 6 位小数；舍入后与门槛看不出差别时改写 12 位有效数字，免得出现“0.019920 大于 0.01992”。
    shown = f"{upper:.6f}"
    if float(shown) == allowed:
        shown = format_number(upper)
    basis = (
        f"对齐样本 {total} 条，错误 {errors} 条；错误率的 Wilson 双侧区间上限为 "
        f"{shown}（置信水平 {format_number(confidence)}），"
    )
    if upper > allowed:
        code = CODE_INSUFFICIENT
        text = TEXT_INSUFFICIENT
        detail = (
            f"{basis}大于 1 − 目标准确率 = {budget}。"
            "需要扩大金标集或改进判定器后重新校准；在此之前，"
            "不得依据本判定器的结果对目标准确率下肯定结论。"
        )
    else:
        code = CODE_ADEQUATE
        text = f"在本金标集上，判定器错误率的区间上限不超过 {budget}"
        detail = (
            f"{basis}不超过 1 − 目标准确率 = {budget}。"
            "该陈述只对本金标集和被校准的这一版判定器成立；金标集是否有代表性须人工确认。"
        )
    return {
        "code": code,
        "text": text,
        "detail": detail,
        "error_rate_upper": upper,
        "allowed_error_rate": allowed,
    }


def calibrate(
    gold: Mapping[str, str],
    predicted: Mapping[str, str],
    *,
    target: float = DEFAULT_TARGET,
    confidence: float = DEFAULT_CONFIDENCE,
) -> dict[str, Any]:
    """按 id 对齐金标与判定结果，返回校准报告。

    只在一边出现的 id 计数并报告，不参与计算；判定器给出的、金标里没有的标签
    不会被丢弃：照常进入混淆矩阵并计为错误，同时单独计数报告。
    对齐后没有样本时抛出 InsufficientData。
    """
    if not 0.0 < target < 1.0:
        raise InputError("目标准确率必须是 0 与 1 之间的小数（不含端点），如 0.98")
    two_sided_z(confidence)

    pairs = [(label, predicted[key]) for key, label in gold.items() if key in predicted]
    only_in_gold = sorted(key for key in gold if key not in predicted)
    only_in_predicted = sorted(key for key in predicted if key not in gold)
    total = len(pairs)
    if total == 0:
        raise InsufficientData(
            "金标与判定结果按 id 对齐后没有任何样本，无法校准",
            details={"gold": len(gold), "predicted": len(predicted), "aligned": 0},
        )

    gold_labels = set(gold.values())
    unknown = Counter(label for _, label in pairs if label not in gold_labels)
    labels = sorted(gold_labels | set(unknown))
    matrix = {row: dict.fromkeys(labels, 0) for row in labels}
    for gold_label, predicted_label in pairs:
        matrix[gold_label][predicted_label] += 1
    correct = sum(matrix[label][label] for label in labels)
    errors = total - correct

    error_interval = _wilson(errors, total, confidence)
    # 允许的错误率 = 1 − 目标准确率；舍入到 12 位只为消除 1 − 0.98 的浮点尾数，判断与展示用同一个值。
    allowed = round(1.0 - target, 12)
    conclusion = _conclusion(
        errors=errors,
        total=total,
        upper=error_interval["high"],
        allowed=allowed,
        confidence=confidence,
    )

    notes: list[str] = []
    if only_in_gold:
        notes.append(
            f"金标中有 {len(only_in_gold)} 条没有对应的判定结果，未参与计算；"
            "以上数字只覆盖已对齐的样本。"
        )
    if only_in_predicted:
        notes.append(f"判定结果中有 {len(only_in_predicted)} 条在金标中找不到，未参与计算。")
    if unknown:
        listed = "、".join(f"{label}（{count} 条）" for label, count in sorted(unknown.items()))
        notes.append(f"判定器给出了金标中没有的标签：{listed}，均计为错误。")
    if confidence < DEFAULT_CONFIDENCE:
        notes.append(
            f"置信水平 {format_number(confidence)} 低于默认值 "
            f"{format_number(DEFAULT_CONFIDENCE)}，区间更窄，结论相应更弱。"
        )

    return {
        "counts": {
            "gold": len(gold),
            "predicted": len(predicted),
            "aligned": total,
            "correct": correct,
            "errors": errors,
            "only_in_gold": len(only_in_gold),
            "only_in_predicted": len(only_in_predicted),
        },
        "unmatched": {
            "only_in_gold": _id_report(only_in_gold),
            "only_in_predicted": _id_report(only_in_predicted),
        },
        "unknown_predicted_labels": {
            "count": sum(unknown.values()),
            "labels": dict(sorted(unknown.items())),
        },
        "labels": labels,
        "confusion_matrix": matrix,
        "per_label": {label: _per_label(label, labels, matrix, confidence) for label in labels},
        "accuracy": correct / total,
        "accuracy_interval": _wilson(correct, total, confidence),
        "error_rate": errors / total,
        "error_rate_interval": error_interval,
        "target": target,
        "allowed_error_rate": allowed,
        "confidence": confidence,
        "method": {
            "interval": INTERVAL_METHOD,
            "sides": "two_sided",
            "confidence": confidence,
            "decision_rule": (
                f"error_rate_interval.high > 1 - target → {CODE_INSUFFICIENT}；否则 {CODE_ADEQUATE}"
            ),
            "description": (
                f"准确率、错误率和各类精确率、召回率的区间均为 Wilson 双侧区间，"
                f"置信水平 {format_number(confidence)}，分位数取自标准正态分布；"
                "F1 不是二项比例，只给点估计。混淆矩阵的行是金标、列是判定器。"
            ),
        },
        "conclusion": conclusion,
        "notes": notes,
    }
