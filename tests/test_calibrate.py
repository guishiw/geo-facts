"""判定校准（F5）测试：Wilson 区间、混淆矩阵与 P / R / F1、结论与退出码。

期望值来源：GOAL.md 5.4 的 Wilson 黄金值、公开已知的正态分位数、区间端点的闭式解
和手工推算；没有一处取自被测程序的输出。
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest
from conftest import run_cli, run_cli_json
from hypothesis import given
from hypothesis import strategies as st

from geo_facts import jsonl
from geo_facts.errors import InputError, InsufficientData
from geo_facts.judge.calibration import calibrate, index_labels, wilson_interval

# 标准正态分位数（公开已知值）：双侧 95% / 99% / 90% 分别对应 0.975 / 0.995 / 0.95 分位数。
Z95 = 1.959963984540054
Z99 = 2.5758293035489004
Z90 = 1.6448536269514722

S, C, U = "supported", "contradicted", "unverifiable"
INSUFFICIENT_TEXT = "判定精度不足，无法证明达标"

# ------------------------------------------------------------------ Wilson 区间

GOLDEN = [
    (0, 5, 0.0, 0.4345),
    (4, 5, 0.3755, 0.9638),
    (5, 5, 0.5655, 1.0),
    (24, 30, 0.6269, 0.9049),
    (120, 150, 0.7289, 0.8562),
]


@pytest.mark.parametrize(("successes", "total", "low", "high"), GOLDEN)
def test_wilson_matches_goal_golden_values(
    successes: int, total: int, low: float, high: float
) -> None:
    got_low, got_high = wilson_interval(successes, total, 0.95)
    assert got_low == pytest.approx(low, abs=1e-4)
    assert got_high == pytest.approx(high, abs=1e-4)


def test_wilson_default_confidence_is_95_percent() -> None:
    assert wilson_interval(24, 30) == wilson_interval(24, 30, 0.95)


@pytest.mark.parametrize(("confidence", "z"), [(0.95, Z95), (0.99, Z99), (0.90, Z90)])
@pytest.mark.parametrize("total", [1, 10, 50, 2000])
def test_wilson_edges_follow_closed_form(confidence: float, z: float, total: int) -> None:
    # 0/n 的上限是 z² / (n + z²)，n/n 的下限是 n / (n + z²)；另一端恰好是 0 或 1。
    # 分位数若被误取成单侧（如 95% 取 1.645），这里会不相等。
    low, high = wilson_interval(0, total, confidence)
    assert low == 0.0
    assert high == pytest.approx(z * z / (total + z * z), rel=1e-9)
    low, high = wilson_interval(total, total, confidence)
    assert high == 1.0
    assert low == pytest.approx(total / (total + z * z), rel=1e-9)


@pytest.mark.parametrize(
    ("successes", "total", "confidence", "z"),
    [(1, 5, 0.95, Z95), (50, 1000, 0.95, Z95), (7, 40, 0.99, Z99), (333, 1000, 0.90, Z90)],
)
def test_wilson_bounds_solve_the_score_equation(
    successes: int, total: int, confidence: float, z: float
) -> None:
    # Wilson 区间的两个端点是 (p̂ − p)² = z² · p(1 − p) / n 的两个根。
    p_hat = successes / total
    low, high = wilson_interval(successes, total, confidence)
    assert low < p_hat < high
    for bound in (low, high):
        assert (p_hat - bound) ** 2 == pytest.approx(z * z * bound * (1 - bound) / total, rel=1e-9)


def test_wilson_for_five_percent_of_one_thousand() -> None:
    # 手工推算：中心 (0.05 + z²/2000) / (1 + z²/1000) ≈ 0.05172，半宽 ≈ 0.01359。
    low, high = wilson_interval(50, 1000, 0.95)
    assert low == pytest.approx(0.0381, abs=2e-4)
    assert high == pytest.approx(0.0653, abs=2e-4)


@pytest.mark.parametrize(
    ("successes", "total", "confidence"),
    [
        (0, 0, 0.95),
        (-1, 5, 0.95),
        (6, 5, 0.95),
        (1, 5, 0.0),
        (1, 5, 1.0),
        (1, 5, 95.0),
        (1, 5, float("nan")),
        # 浮点下分位数 0.5 + c/2 会落到 1 或 0.5 的极端取值：同样是输入错误，不是内部错误。
        (1, 5, 0.9999999999999999),
        (1, 5, 1e-300),
    ],
)
def test_wilson_rejects_invalid_arguments(successes: int, total: int, confidence: float) -> None:
    with pytest.raises(InputError):
        wilson_interval(successes, total, confidence)


counts = st.integers(1, 100_000).flatmap(lambda n: st.tuples(st.integers(0, n), st.just(n)))
confidences = st.floats(0.5, 0.999)


@given(counts, confidences)
def test_wilson_contains_point_estimate(pair: tuple[int, int], confidence: float) -> None:
    successes, total = pair
    low, high = wilson_interval(successes, total, confidence)
    assert 0.0 <= low <= successes / total <= high <= 1.0


@given(counts, confidences)
def test_wilson_is_monotonic_in_successes(pair: tuple[int, int], confidence: float) -> None:
    successes, total = pair
    successes = min(successes, total - 1)
    low, high = wilson_interval(successes, total, confidence)
    next_low, next_high = wilson_interval(successes + 1, total, confidence)
    assert low <= next_low
    assert high <= next_high


@given(counts, confidences, confidences)
def test_wilson_widens_with_confidence(pair: tuple[int, int], first: float, second: float) -> None:
    successes, total = pair
    narrow = wilson_interval(successes, total, min(first, second))
    wide = wilson_interval(successes, total, max(first, second))
    assert wide[0] <= narrow[0] + 1e-12
    assert narrow[1] <= wide[1] + 1e-12


@given(counts, confidences)
def test_wilson_is_symmetric_under_complement(pair: tuple[int, int], confidence: float) -> None:
    successes, total = pair
    low, high = wilson_interval(successes, total, confidence)
    other_low, other_high = wilson_interval(total - successes, total, confidence)
    assert other_low == pytest.approx(1 - high, abs=1e-12)
    assert other_high == pytest.approx(1 - low, abs=1e-12)


@given(st.integers(1, 100_000), confidences)
def test_zero_error_upper_bound_shrinks_with_more_samples(total: int, confidence: float) -> None:
    assert wilson_interval(0, total + 1, confidence)[1] < wilson_interval(0, total, confidence)[1]


# ------------------------------------------------------------------ 测试数据构造


def as_maps(pairs: list[tuple[str, str]]) -> tuple[dict[str, str], dict[str, str]]:
    """把 (金标标签, 判定标签) 列表变成两个 id → 标签 的映射。"""
    gold = {f"case-{n:05d}": pair[0] for n, pair in enumerate(pairs, start=1)}
    predicted = {f"case-{n:05d}": pair[1] for n, pair in enumerate(pairs, start=1)}
    return gold, predicted


def binary_pairs(total: int, errors: int) -> list[tuple[str, str]]:
    """total 条金标都是 supported，其中 errors 条被判定器判成 contradicted。"""
    return [(S, C if n < errors else S) for n in range(total)]


def write_labels(path: Path, labels: dict[str, str]) -> Path:
    records = [{"id": key, "label": value, "synthetic": True} for key, value in labels.items()]
    jsonl.append_jsonl(path, records)
    return path


def write_case(tmp_path: Path, pairs: list[tuple[str, str]]) -> list[str]:
    """写出一对 gold / predicted 文件，返回 CLI 参数。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    gold, predicted = as_maps(pairs)
    gold_path = write_labels(tmp_path / "gold.jsonl", gold)
    predicted_path = write_labels(tmp_path / "predicted.jsonl", predicted)
    return ["calibrate", "--gold", str(gold_path), "--predicted", str(predicted_path)]


def write_raw(tmp_path: Path, name: str, records: list[dict[str, Any]]) -> str:
    path = tmp_path / name
    lines = [json.dumps({**record, "synthetic": True}) + "\n" for record in records]
    path.write_text("".join(lines), encoding="utf-8")
    return str(path)


# 手工算例（12 条）：
#   金标 supported    5 条 → 判定 supported 4、contradicted 1
#   金标 contradicted 5 条 → 判定 supported 2、contradicted 1、unverifiable 2
#   金标 unverifiable 2 条 → 判定 supported 1、unverifiable 1
HAND_PAIRS = [(S, S)] * 4 + [(S, C)] + [(C, S)] * 2 + [(C, C)] + [(C, U)] * 2 + [(U, S)] + [(U, U)]


# ------------------------------------------------------------------ 混淆矩阵与 P / R / F1


def test_confusion_matrix_and_per_label_metrics_hand_example() -> None:
    report = calibrate(*as_maps(HAND_PAIRS))
    assert report["labels"] == [C, S, U]
    # 行是金标，列是判定器。
    assert report["confusion_matrix"] == {
        S: {S: 4, C: 1, U: 0},
        C: {S: 2, C: 1, U: 2},
        U: {S: 1, C: 0, U: 1},
    }
    per_label = report["per_label"]
    # supported：判定器给出 7 条，其中 4 条对；金标 5 条，找回 4 条。
    assert per_label[S]["support"] == 5
    assert per_label[S]["predicted"] == 7
    assert per_label[S]["true_positive"] == 4
    assert per_label[S]["precision"] == pytest.approx(4 / 7)
    assert per_label[S]["recall"] == pytest.approx(4 / 5)
    assert per_label[S]["f1"] == pytest.approx(2 / 3)
    # contradicted：精确率 1/2，召回率 1/5，F1 = 2·(1/2)(1/5) / (1/2 + 1/5) = 2/7。
    assert per_label[C]["support"] == 5
    assert per_label[C]["predicted"] == 2
    assert per_label[C]["precision"] == pytest.approx(1 / 2)
    assert per_label[C]["recall"] == pytest.approx(1 / 5)
    assert per_label[C]["f1"] == pytest.approx(2 / 7)
    # unverifiable：精确率 1/3，召回率 1/2，F1 = 2/5。
    assert per_label[U]["support"] == 2
    assert per_label[U]["predicted"] == 3
    assert per_label[U]["precision"] == pytest.approx(1 / 3)
    assert per_label[U]["recall"] == pytest.approx(1 / 2)
    assert per_label[U]["f1"] == pytest.approx(2 / 5)
    for label in (S, C, U):
        assert per_label[label]["null_reasons"] == {}

    counts = report["counts"]
    assert counts == {
        "gold": 12,
        "predicted": 12,
        "aligned": 12,
        "correct": 6,
        "errors": 6,
        "only_in_gold": 0,
        "only_in_predicted": 0,
    }
    assert report["accuracy"] == pytest.approx(0.5)
    assert report["error_rate"] == pytest.approx(0.5)


def test_hand_example_intervals_use_wilson() -> None:
    report = calibrate(*as_maps(HAND_PAIRS))
    per_label = report["per_label"]
    # supported 的召回率是 4/5：GOAL 黄金值 [0.3755, 0.9638]。
    recall = per_label[S]["recall_interval"]
    assert recall["method"] == "wilson"
    assert recall["level"] == 0.95
    assert recall["low"] == pytest.approx(0.3755, abs=1e-4)
    assert recall["high"] == pytest.approx(0.9638, abs=1e-4)
    # contradicted 的召回率是 1/5：由 4/5 的黄金值按对称性得到 [1 − 0.9638, 1 − 0.3755]。
    recall = per_label[C]["recall_interval"]
    assert recall["low"] == pytest.approx(0.0362, abs=1e-4)
    assert recall["high"] == pytest.approx(0.6245, abs=1e-4)
    # 精确率的分母是“判定器给出的条数”：supported 为 4/7，中心与半宽按公式手算。
    precision = per_label[S]["precision_interval"]
    center = (4 / 7 + Z95**2 / 14) / (1 + Z95**2 / 7)
    half = Z95 * ((4 / 7) * (3 / 7) / 7 + Z95**2 / 196) ** 0.5 / (1 + Z95**2 / 7)
    assert precision["low"] == pytest.approx(center - half, abs=1e-9)
    assert precision["high"] == pytest.approx(center + half, abs=1e-9)
    # 准确率 6/12：p = 0.5 时区间关于 0.5 对称，半宽 z / (2·sqrt(n + z²)) ≈ 0.2462。
    assert report["accuracy_interval"]["low"] == pytest.approx(0.2538, abs=1e-4)
    assert report["accuracy_interval"]["high"] == pytest.approx(0.7462, abs=1e-4)
    assert report["error_rate_interval"]["low"] == pytest.approx(0.2538, abs=1e-4)
    assert report["error_rate_interval"]["high"] == pytest.approx(0.7462, abs=1e-4)


def test_zero_denominators_give_null_with_reason() -> None:
    # 金标只有 supported / contradicted；判定器从不给 contradicted，却给了金标没有的 unverifiable。
    pairs = [(S, S)] * 3 + [(C, S)] * 2 + [(S, U)]
    report = calibrate(*as_maps(pairs))
    assert report["labels"] == [C, S, U]
    never_predicted = report["per_label"][C]
    assert never_predicted["predicted"] == 0
    assert never_predicted["precision"] is None
    assert never_predicted["precision_interval"] is None
    assert never_predicted["recall"] == 0.0
    assert never_predicted["f1"] is None
    assert set(never_predicted["null_reasons"]) == {"precision", "f1"}
    not_in_gold = report["per_label"][U]
    assert not_in_gold["support"] == 0
    assert not_in_gold["recall"] is None
    assert not_in_gold["recall_interval"] is None
    assert not_in_gold["precision"] == 0.0
    assert not_in_gold["f1"] is None
    assert set(not_in_gold["null_reasons"]) == {"recall", "f1"}
    # 金标没有的标签不会被丢掉：计数、报告，并计为错误。
    assert report["unknown_predicted_labels"] == {"count": 1, "labels": {U: 1}}
    assert report["counts"]["errors"] == 3
    assert any(U in note for note in report["notes"])


# ------------------------------------------------------------------ 结论与退出码


def test_cli_output_has_the_contract_fields(tmp_path: Path) -> None:
    code, data = run_cli_json(*write_case(tmp_path, HAND_PAIRS))
    assert code == 3
    for key in (
        "labels",
        "confusion_matrix",
        "per_label",
        "accuracy",
        "accuracy_interval",
        "error_rate",
        "error_rate_interval",
        "target",
        "conclusion",
        "method",
    ):
        assert key in data
    assert data["target"] == 0.98
    assert data["confidence"] == 0.95
    assert data["confusion_matrix"][C] == {S: 2, C: 1, U: 2}
    assert data["per_label"][S]["precision"] == pytest.approx(4 / 7)
    assert data["method"]["interval"] == "wilson"
    assert data["method"]["sides"] == "two_sided"
    assert data["method"]["confidence"] == 0.95
    assert "Wilson 双侧区间" in data["method"]["description"]


def s13_pairs() -> list[tuple[str, str]]:
    """金标 1000 条（990 条 supported、10 条 contradicted，即回答准确率真值 99%），
    判定器每 20 条错 1 条，共错 50 条（错误率 5%）。"""
    pairs: list[tuple[str, str]] = []
    for n in range(1000):
        gold = S if n < 990 else C
        if n % 20 == 0:
            pairs.append((gold, U if gold == S else S))
        else:
            pairs.append((gold, gold))
    return pairs


def test_s13_judge_error_rate_5_percent_cannot_prove_98(tmp_path: Path) -> None:
    argv = write_case(tmp_path, s13_pairs())
    code, data = run_cli_json(*argv)
    assert code == 3
    assert data["counts"]["aligned"] == 1000
    assert data["counts"]["errors"] == 50
    assert data["error_rate"] == pytest.approx(0.05)
    assert data["error_rate_interval"]["low"] == pytest.approx(0.0381, abs=2e-4)
    assert data["error_rate_interval"]["high"] == pytest.approx(0.0653, abs=2e-4)
    assert data["allowed_error_rate"] == pytest.approx(0.02)
    conclusion = data["conclusion"]
    assert conclusion["code"] == "insufficient_precision"
    assert conclusion["text"] == INSUFFICIENT_TEXT
    assert conclusion["error_rate_upper"] == data["error_rate_interval"]["high"]
    assert conclusion["allowed_error_rate"] == pytest.approx(0.02)

    code, out, _ = run_cli(*argv)
    assert code == 3
    assert INSUFFICIENT_TEXT in out


def test_no_errors_in_2000_samples_is_adequate(tmp_path: Path) -> None:
    argv = write_case(tmp_path, binary_pairs(2000, 0))
    code, data = run_cli_json(*argv)
    assert code == 0
    assert data["error_rate"] == 0.0
    assert data["error_rate_interval"]["low"] == 0.0
    # 0/2000 的上限 = z² / (2000 + z²) ≈ 0.001917。
    assert data["error_rate_interval"]["high"] == pytest.approx(0.001917, abs=1e-6)
    assert data["conclusion"]["code"] == "adequate"
    assert data["conclusion"]["text"] == "在本金标集上，判定器错误率的区间上限不超过 0.02"
    code, out, _ = run_cli(*argv)
    assert code == 0
    assert "在本金标集上，判定器错误率的区间上限不超过 0.02" in out
    assert INSUFFICIENT_TEXT not in out


def test_no_errors_in_50_samples_is_still_insufficient(tmp_path: Path) -> None:
    # 点估计是 0，但 0/50 的上限 = z² / (50 + z²) ≈ 0.0713 > 0.02：样本太少，证明不了。
    code, data = run_cli_json(*write_case(tmp_path, binary_pairs(50, 0)))
    assert code == 3
    assert data["error_rate"] == 0.0
    assert data["accuracy"] == 1.0
    assert data["error_rate_interval"]["high"] == pytest.approx(0.0713, abs=2e-4)
    assert data["conclusion"]["code"] == "insufficient_precision"
    assert data["conclusion"]["text"] == INSUFFICIENT_TEXT


def test_zero_error_sample_size_boundary() -> None:
    # 0 错时上限 z² / (n + z²) ≤ 0.02 ⇔ n ≥ 49·z² ≈ 188.2：188 条不够，189 条才够。
    assert calibrate(*as_maps(binary_pairs(188, 0)))["conclusion"]["code"] == (
        "insufficient_precision"
    )
    assert calibrate(*as_maps(binary_pairs(189, 0)))["conclusion"]["code"] == "adequate"


def test_target_and_confidence_change_the_conclusion(tmp_path: Path) -> None:
    # 50/1000 的上限约 0.0653：目标 0.98 不够，目标 0.9（允许 0.1）就够了。
    argv = write_case(tmp_path / "a", s13_pairs())
    code, data = run_cli_json(*argv, "--target", "0.9")
    assert code == 0
    assert data["target"] == 0.9
    assert data["allowed_error_rate"] == pytest.approx(0.1)
    assert data["conclusion"]["code"] == "adequate"
    assert data["conclusion"]["text"] == "在本金标集上，判定器错误率的区间上限不超过 0.1"

    # 0/150：95% 时上限 z² / (150 + z²) ≈ 0.0250 > 0.02；90% 时 ≈ 0.0177 ≤ 0.02。
    argv = write_case(tmp_path / "b", binary_pairs(150, 0))
    code, data = run_cli_json(*argv)
    assert code == 3
    assert data["error_rate_interval"]["high"] == pytest.approx(0.02497, abs=1e-5)
    code, data = run_cli_json(*argv, "--confidence", "0.9")
    assert code == 0
    assert data["confidence"] == 0.9
    assert data["method"]["confidence"] == 0.9
    assert data["error_rate_interval"]["level"] == 0.9
    assert data["error_rate_interval"]["high"] == pytest.approx(0.01772, abs=1e-5)
    # 降低置信水平会让区间变窄，必须在输出里说出来。
    assert any("0.95" in note for note in data["notes"])
    code, out, _ = run_cli(*argv, "--confidence", "0.9")
    assert "置信水平 0.9" in out


def test_conclusion_states_the_allowed_error_rate_without_rounding_it_down() -> None:
    # 1 − 0.98765436 = 0.01234564；写成 0.0123456 就把门槛说小了。
    report = calibrate(*as_maps(binary_pairs(5000, 0)), target=0.98765436)
    assert report["allowed_error_rate"] == pytest.approx(0.01234564, abs=1e-12)
    assert report["conclusion"]["code"] == "adequate"
    assert report["conclusion"]["text"] == "在本金标集上，判定器错误率的区间上限不超过 0.01234564"
    assert "1 − 目标准确率 = 0.01234564" in report["conclusion"]["detail"]


def test_conclusion_shows_enough_digits_when_the_bound_is_close_to_the_budget() -> None:
    # 0/189 的上限 z² / (189 + z²) ≈ 0.01992030，略大于 1 − 0.98008 = 0.01992。
    # 只写 6 位小数会变成“0.019920 大于 0.01992”，读不懂；此时要写出足够的位数。
    report = calibrate(*as_maps(binary_pairs(189, 0)), target=0.98008)
    conclusion = report["conclusion"]
    assert conclusion["code"] == "insufficient_precision"
    assert "0.0199202" in conclusion["detail"]
    assert "0.019920（" not in conclusion["detail"]
    assert "大于 1 − 目标准确率 = 0.01992。" in conclusion["detail"]


def affirmative_claims(output: str) -> list[str]:
    """去掉唯一允许的否定句式后，输出里还剩下哪些“达标”类措辞。"""
    remaining = output.replace("无法证明达标", "")
    return [word for word in ("已达", "达标") if word in remaining]


@pytest.mark.parametrize(
    ("total", "errors"), [(1000, 50), (50, 0), (188, 0), (12, 6), (2000, 30), (5, 5)]
)
def test_insufficient_output_never_claims_the_target_is_met(
    tmp_path: Path, total: int, errors: int
) -> None:
    argv = write_case(tmp_path, binary_pairs(total, errors))
    code, text_out, _ = run_cli(*argv)
    assert code == 3
    code, json_out, _ = run_cli(*argv, "--json")
    assert code == 3
    for output in (text_out, json_out):
        assert INSUFFICIENT_TEXT in output
        assert affirmative_claims(output) == []
        assert "在本金标集上" not in output


@pytest.mark.parametrize(("total", "errors"), [(2000, 0), (189, 0), (5000, 60)])
def test_adequate_output_only_states_the_interval_bound(
    tmp_path: Path, total: int, errors: int
) -> None:
    argv = write_case(tmp_path, binary_pairs(total, errors))
    code, text_out, _ = run_cli(*argv)
    assert code == 0
    code, json_out, _ = run_cli(*argv, "--json")
    assert code == 0
    for output in (text_out, json_out):
        assert "在本金标集上，判定器错误率的区间上限不超过 0.02" in output
        assert "已达" not in output
        assert "达标" not in output
        assert "精度不足" not in output


def test_help_text_has_no_affirmative_claim() -> None:
    code, out, _ = run_cli("calibrate", "--help")
    assert code == 0
    assert "--gold" in out
    assert "--predicted" in out
    # 帮助文本会按终端宽度折行，所以这里要求两个词都完全不出现。
    assert "已达" not in out
    assert "达标" not in out


@given(st.integers(1, 400), st.sampled_from([0.9, 0.95, 0.98, 0.99]), st.data())
def test_conclusion_is_conservative_and_monotonic(total: int, target: float, data: Any) -> None:
    errors = data.draw(st.integers(0, total))
    report = calibrate(*as_maps(binary_pairs(total, errors)), target=target)
    code = report["conclusion"]["code"]
    allowed = report["allowed_error_rate"]
    assert allowed == pytest.approx(1 - target, abs=1e-12)
    high = report["error_rate_interval"]["high"]
    assert (code == "insufficient_precision") == (high > allowed)
    # 点估计已经超出允许的错误率时，绝不可能是 adequate。
    if errors / total > 1 - target:
        assert code == "insufficient_precision"
    # 错得更多不会让结论变好。
    if errors > 0 and code == "adequate":
        fewer = calibrate(*as_maps(binary_pairs(total, errors - 1)), target=target)
        assert fewer["conclusion"]["code"] == "adequate"


# ------------------------------------------------------------------ 对齐、异常输入


def test_ids_on_one_side_are_counted_reported_and_excluded(tmp_path: Path) -> None:
    gold = {f"case-{n:03d}": S for n in range(1, 11)}  # case-001 … case-010
    predicted = {f"case-{n:03d}": S for n in range(4, 13)}  # case-004 … case-012
    predicted["case-004"] = C
    argv = [
        "calibrate",
        "--gold",
        str(write_labels(tmp_path / "gold.jsonl", gold)),
        "--predicted",
        str(write_labels(tmp_path / "predicted.jsonl", predicted)),
    ]
    code, data = run_cli_json(*argv)
    assert code == 3
    assert data["counts"] == {
        "gold": 10,
        "predicted": 9,
        "aligned": 7,
        "correct": 6,
        "errors": 1,
        "only_in_gold": 3,
        "only_in_predicted": 2,
    }
    assert data["unmatched"]["only_in_gold"] == {
        "count": 3,
        "ids": ["case-001", "case-002", "case-003"],
        "truncated": False,
    }
    assert data["unmatched"]["only_in_predicted"] == {
        "count": 2,
        "ids": ["case-011", "case-012"],
        "truncated": False,
    }
    assert data["error_rate"] == pytest.approx(1 / 7)
    code, out, _ = run_cli(*argv)
    assert "只在金标中出现 3 条" in out
    assert "只在判定结果中出现 2 条" in out


def test_long_unmatched_id_lists_are_truncated_but_fully_counted() -> None:
    gold = {f"case-{n:03d}": S for n in range(1, 31)}
    predicted = {f"case-{n:03d}": S for n in range(26, 31)}
    report = calibrate(gold, predicted)
    only_in_gold = report["unmatched"]["only_in_gold"]
    assert only_in_gold["count"] == 25
    assert only_in_gold["ids"] == [f"case-{n:03d}" for n in range(1, 21)]
    assert only_in_gold["truncated"] is True
    assert report["counts"]["aligned"] == 5
    assert any("25" in note for note in report["notes"])


def test_unmatched_ids_are_disclosed_next_to_an_adequate_conclusion(tmp_path: Path) -> None:
    gold = {f"case-{n:04d}": S for n in range(2100)}
    predicted = {f"case-{n:04d}": S for n in range(2000)}
    argv = [
        "calibrate",
        "--gold",
        str(write_labels(tmp_path / "gold.jsonl", gold)),
        "--predicted",
        str(write_labels(tmp_path / "predicted.jsonl", predicted)),
    ]
    code, data = run_cli_json(*argv)
    assert code == 0
    assert data["counts"]["aligned"] == 2000
    assert data["counts"]["only_in_gold"] == 100
    code, out, _ = run_cli(*argv)
    assert code == 0
    assert "只在金标中出现 100 条" in out
    assert "注意" in out


def test_duplicate_ids_are_input_errors(tmp_path: Path) -> None:
    good = write_raw(tmp_path, "good.jsonl", [{"id": "a", "label": S}, {"id": "b", "label": S}])
    duplicated = write_raw(
        tmp_path,
        "dup.jsonl",
        [
            {"id": "a", "label": S},
            {"id": "b", "label": S},
            {"id": "a", "label": C},
            {"id": "b", "label": S},
            {"id": "a", "label": S},
        ],
    )
    code, data = run_cli_json("calibrate", "--gold", duplicated, "--predicted", good)
    assert code == 2
    assert data["error"] == "input_error"
    assert data["details"]["source"] == "gold"
    assert data["details"]["duplicate_ids"] == {"count": 2, "ids": ["a", "b"], "truncated": False}
    code, data = run_cli_json("calibrate", "--gold", good, "--predicted", duplicated)
    assert code == 2
    assert data["details"]["source"] == "predicted"
    code, _, err = run_cli("calibrate", "--gold", good, "--predicted", duplicated)
    assert code == 2
    assert "重复" in err


@pytest.mark.parametrize(
    "bad",
    [
        {"label": S},
        {"id": "", "label": S},
        {"id": 7, "label": S},
        {"id": "a"},
        {"id": "a", "label": ""},
        {"id": "a", "label": "  "},
        {"id": "a", "label": None},
        {"id": "a", "label": 1},
    ],
)
def test_malformed_records_are_input_errors(tmp_path: Path, bad: dict[str, Any]) -> None:
    good = write_raw(tmp_path, "good.jsonl", [{"id": "a", "label": S}])
    broken = write_raw(tmp_path, "broken.jsonl", [{"id": "z", "label": S}, bad])
    code, data = run_cli_json("calibrate", "--gold", broken, "--predicted", good)
    assert code == 2
    assert data["error"] == "input_error"
    assert "第 2 行" in data["message"]
    code, data = run_cli_json("calibrate", "--gold", good, "--predicted", broken)
    assert code == 2
    assert "第 2 行" in data["message"]


def test_missing_or_broken_files_are_input_errors(tmp_path: Path) -> None:
    good = write_raw(tmp_path, "good.jsonl", [{"id": "a", "label": S}])
    code, data = run_cli_json(
        "calibrate", "--gold", str(tmp_path / "none.jsonl"), "--predicted", good
    )
    assert code == 2
    assert data["error"] == "input_error"
    not_json = tmp_path / "bad.jsonl"
    not_json.write_text("not json\n", encoding="utf-8")
    code, data = run_cli_json("calibrate", "--gold", good, "--predicted", str(not_json))
    assert code == 2
    code, _, _ = run_cli("calibrate", "--gold", good)
    assert code == 2


def test_no_aligned_samples_is_insufficient_data(tmp_path: Path) -> None:
    gold = write_raw(tmp_path, "gold.jsonl", [{"id": "a", "label": S}, {"id": "b", "label": C}])
    predicted = write_raw(tmp_path, "predicted.jsonl", [{"id": "x", "label": S}])
    code, data = run_cli_json("calibrate", "--gold", gold, "--predicted", predicted)
    assert code == 3
    assert data["error"] == "insufficient_data"
    assert data["details"] == {"gold": 2, "predicted": 1, "aligned": 0}
    assert "conclusion" not in data
    code, out, err = run_cli("calibrate", "--gold", gold, "--predicted", predicted)
    assert code == 3
    assert affirmative_claims(out + err) == []

    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    code, data = run_cli_json("calibrate", "--gold", str(empty), "--predicted", str(empty))
    assert code == 3
    assert data["error"] == "insufficient_data"
    with pytest.raises(InsufficientData):
        calibrate({}, {"a": S})


@pytest.mark.parametrize(
    "option",
    [
        ["--target", "98"],
        ["--target", "0"],
        ["--target", "1"],
        ["--target", "-0.5"],
        ["--target", "nan"],
        ["--target", "abc"],
        ["--confidence", "95"],
        ["--confidence", "0"],
        ["--confidence", "1"],
        ["--confidence", "nan"],
    ],
)
def test_invalid_target_or_confidence_is_input_error(tmp_path: Path, option: list[str]) -> None:
    code, _, err = run_cli(*write_case(tmp_path, binary_pairs(20, 1)), *option)
    assert code == 2
    assert "输入错误" in err or "错误：" in err


def test_index_labels_reports_line_numbers_and_duplicates() -> None:
    records = [(1, {"id": "a", "label": S}), (2, {"id": "b", "label": C, "note": "忽略多余字段"})]
    assert index_labels(records, "gold") == {"a": S, "b": C}
    with pytest.raises(InputError) as caught:
        index_labels([(1, {"id": "a", "label": S}), (2, {"id": "a", "label": S})], "predicted")
    assert caught.value.details["duplicate_ids"]["ids"] == ["a"]
    many = [(n, {"id": f"dup-{n % 30:02d}", "label": S}) for n in range(1, 61)]
    with pytest.raises(InputError) as caught:
        index_labels(many, "gold")
    assert caught.value.details["duplicate_ids"]["count"] == 30
    assert len(caught.value.details["duplicate_ids"]["ids"]) == 20
    assert caught.value.details["duplicate_ids"]["truncated"] is True


# ------------------------------------------------------------------ 可复现


def test_output_is_reproducible_and_independent_of_line_order(tmp_path: Path) -> None:
    pairs = s13_pairs()
    argv = write_case(tmp_path / "ordered", pairs)
    first = run_cli(*argv, "--json")
    second = run_cli(*argv, "--json")
    assert first == second

    gold, predicted = as_maps(pairs)
    shuffled_gold = list(gold.items())
    shuffled_predicted = list(predicted.items())
    random.Random(20261008).shuffle(shuffled_gold)
    random.Random(7).shuffle(shuffled_predicted)
    (tmp_path / "shuffled").mkdir()
    shuffled = [
        "calibrate",
        "--gold",
        str(write_labels(tmp_path / "shuffled" / "gold.jsonl", dict(shuffled_gold))),
        "--predicted",
        str(write_labels(tmp_path / "shuffled" / "predicted.jsonl", dict(shuffled_predicted))),
    ]
    assert run_cli(*shuffled, "--json") == first
    assert str(tmp_path) not in first[1]
