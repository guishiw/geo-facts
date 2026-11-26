#!/usr/bin/env python3
"""合成标注集基准：只通过 CLI 子进程（`python -m geo_facts ...`）驱动被测实现，按门槛判定。

用法：

    python benchmarks/run_benchmark.py --split dev|holdout [--workdir DIR] [--json]
        [--seed N] [--now T] [--only judgment|injection] [--show-failures] [--jobs N]

流程：
1. 用 benchmarks/generate.py 在工作目录下现场生成数据（data/）。
2. 判定类：`init` → 把 store-src/entities.jsonl 追加进事实库的 entities.jsonl → `import`（facts.csv）
   → `judge`（输出 judgments.jsonl 与 claims.jsonl）；按“claim 的 span 落在 gold 的 span 内”对齐到金标，
   计算矛盾召回率、矛盾精确率、条件不同被误判为矛盾的比例。
3. 注入类：对 injection/ 下每份稿件运行 `gate`，计算攻击检出率与正常样本误拦率。
4. 按门槛判定（不达标退出 1），并在 benchmarks/runs.jsonl 追加一行运行记录。

对齐规则（F-CONTRACT）：同一金标对应多条判定时，有 contradicted 取 contradicted，否则有 supported 取
supported，否则 unverifiable；没有对应判定视为 unverifiable。补充：一句里有多条金标（一句多陈述）时，
落进该句的判定只归给实体与属性都相同的金标；找不到实体属性都相同的金标时才归给该句全部金标。

保留集默认只输出汇总数值，不输出逐样本明细（防止针对保留集调参）；需要明细必须显式加
`--show-failures`，输出与运行记录里会标明“已查看保留集明细”。调参只用开发集。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent
RUNS_FILE = BENCH_DIR / "runs.jsonl"
DEFAULT_WORKDIR = BENCH_DIR / "out"
MARKER = ".bench-workdir"
PROJECT_ID = "bench-auto"
CATEGORIES = ("numeric", "model", "date")
BLOCKING_RULES = ("G-HIDDEN-TEXT", "G-PROMPT-INJECTION")
GATE_EXIT_CODES = (0, 1, 5)
MAX_LISTED = 50
NOTICE = (
    "合成集结果只能说明实现符合预期，不代表真实业务准确率；自由文本判定没有真实金标，标为“未验证”。"
    "调参只用开发集，保留集只在里程碑验收时运行并记录。"
)

SUPPORTED, CONTRADICTED, UNVERIFIABLE = "supported", "contradicted", "unverifiable"


@dataclass(frozen=True)
class Threshold:
    metric: str
    part: str
    op: str  # ">=" 或 "<="
    value: float
    label: str


THRESHOLDS: tuple[Threshold, ...] = (
    Threshold("contradiction_recall", "judgment", ">=", 0.95, "矛盾检出召回率"),
    Threshold("contradiction_precision", "judgment", ">=", 0.95, "矛盾判定精确率"),
    Threshold(
        "condition_false_contradiction_rate", "judgment", "<=", 0.02, "条件不同被误判为矛盾的比例"
    ),
    Threshold("attack_detection_rate", "injection", ">=", 1.0, "攻击样本检出率（须全部检出）"),
    Threshold("benign_false_block_rate", "injection", "<=", 0.02, "正常样本误拦率"),
)


# ---------------------------------------------------------------------------- 子进程


@dataclass(frozen=True)
class Outcome:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str]], Outcome]


def cli_runner(args: Sequence[str]) -> Outcome:
    """以子进程运行 `python -m geo_facts ARGS`。"""
    completed = subprocess.run(
        [sys.executable, "-m", "geo_facts", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
    return Outcome(completed.returncode, completed.stdout, completed.stderr)


def tail(text: str, lines: int = 8) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


class StepFailed(Exception):
    def __init__(self, step: str, outcome: Outcome) -> None:
        super().__init__(step)
        self.step = step
        self.outcome = outcome

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "exit_code": self.outcome.returncode,
            "stderr_tail": tail(self.outcome.stderr),
            "stdout_tail": tail(self.outcome.stdout),
        }


def run_step(runner: Runner, step: str, args: Sequence[str], ok: Sequence[int] = (0,)) -> Outcome:
    outcome = runner(args)
    if outcome.returncode not in ok:
        raise StepFailed(step, outcome)
    return outcome


# ---------------------------------------------------------------------------- 读文件


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


# ---------------------------------------------------------------------------- 对齐与指标


def aggregate(labels: Iterable[str]) -> str:
    """同一金标对应多条判定：有 contradicted 取 contradicted，否则有 supported 取 supported，否则 unverifiable。"""
    found = set(labels)
    if CONTRADICTED in found:
        return CONTRADICTED
    if SUPPORTED in found:
        return SUPPORTED
    return UNVERIFIABLE


def _span(value: Any) -> tuple[int, int] | None:
    if (
        isinstance(value, dict)
        and isinstance(value.get("start"), int)
        and isinstance(value.get("end"), int)
    ):
        return value["start"], value["end"]
    if isinstance(value, list) and len(value) == 2 and all(isinstance(item, int) for item in value):
        return value[0], value[1]
    return None


def align(
    golds: Sequence[dict[str, Any]],
    claims: Sequence[dict[str, Any]],
    judgments: Sequence[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """把 answer_to_facts 方向的判定对齐到金标。返回 {gold_id: {predicted, judgment_ids}}。"""
    claims_by_id = {claim.get("claim_id"): claim for claim in claims}
    golds_by_obs: dict[str, list[dict[str, Any]]] = {}
    for item in golds:
        golds_by_obs.setdefault(item["observation_id"], []).append(item)
    matched: dict[str, list[tuple[str, str]]] = {item["id"]: [] for item in golds}
    for judgment in judgments:
        if judgment.get("direction") != "answer_to_facts":
            continue
        claim = claims_by_id.get(judgment.get("claim_id"))
        if claim is None:
            continue
        source = judgment.get("source") or claim.get("source") or {}
        span = _span(claim.get("span"))
        if span is None:
            continue
        candidates = [
            item
            for item in golds_by_obs.get(source.get("id"), [])
            if item["span"][0] <= span[0] and span[1] <= item["span"][1]
        ]
        if len(candidates) > 1:
            same = [
                item
                for item in candidates
                if item["entity_id"] == claim.get("entity_id")
                and item["attribute"] == claim.get("attribute")
            ]
            candidates = same or candidates
        for item in candidates:
            matched[item["id"]].append((judgment.get("judgment_id", ""), judgment.get("label", "")))
    return {
        gold_id: {
            "predicted": aggregate(label for _, label in pairs),
            "judgment_ids": sorted(judgment_id for judgment_id, _ in pairs),
        }
        for gold_id, pairs in matched.items()
    }


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def judgment_metrics(
    golds: Sequence[dict[str, Any]], aligned: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    scored = [item for item in golds if item["category"] in CATEGORIES]
    predicted = {
        item["id"]: aligned.get(item["id"], {}).get("predicted", UNVERIFIABLE) for item in scored
    }
    expected_c = [item for item in scored if item["expected"] == CONTRADICTED]
    predicted_c = [item for item in scored if predicted[item["id"]] == CONTRADICTED]
    true_c = [item for item in expected_c if predicted[item["id"]] == CONTRADICTED]
    condition = [item for item in scored if item["condition_differs"]]
    condition_c = [item for item in condition if predicted[item["id"]] == CONTRADICTED]
    confusion: dict[str, dict[str, int]] = {
        label: dict.fromkeys((SUPPORTED, CONTRADICTED, UNVERIFIABLE), 0)
        for label in (SUPPORTED, CONTRADICTED, UNVERIFIABLE)
    }
    for item in scored:
        confusion[item["expected"]][predicted[item["id"]]] += 1
    by_kind: dict[str, dict[str, int]] = {}
    for item in scored:
        kind = item["hard_kind"] or "plain"
        entry = by_kind.setdefault(kind, {"total": 0, "correct": 0})
        entry["total"] += 1
        entry["correct"] += int(predicted[item["id"]] == item["expected"])
    correct = sum(1 for item in scored if predicted[item["id"]] == item["expected"])
    return {
        "golds": len(scored),
        "excluded_categories": len(golds) - len(scored),
        "contradicted_expected": len(expected_c),
        "contradicted_predicted": len(predicted_c),
        "true_contradictions": len(true_c),
        "contradiction_recall": ratio(len(true_c), len(expected_c)),
        "contradiction_precision": ratio(len(true_c), len(predicted_c)),
        "condition_differs": len(condition),
        "condition_false_contradictions": len(condition_c),
        "condition_false_contradiction_rate": ratio(len(condition_c), len(condition)),
        "agreement": ratio(correct, len(scored)),
        "confusion": confusion,
        "by_kind": dict(sorted(by_kind.items())),
        "failures": {
            "missed_contradictions": sorted(
                item["id"] for item in expected_c if predicted[item["id"]] != CONTRADICTED
            ),
            "false_contradictions": sorted(
                item["id"] for item in predicted_c if item["expected"] != CONTRADICTED
            ),
            "condition_false_contradictions": sorted(item["id"] for item in condition_c),
            "other_disagreements": sorted(
                item["id"]
                for item in scored
                if predicted[item["id"]] != item["expected"]
                and CONTRADICTED not in (predicted[item["id"]], item["expected"])
            ),
        },
    }


def gate_outcome(item: dict[str, Any], outcome: Outcome) -> dict[str, Any]:
    result: dict[str, Any] = {"file": item["file"], "label": item["label"], "kind": item["kind"]}
    result["exit_code"] = outcome.returncode
    if outcome.returncode not in GATE_EXIT_CODES:
        result["error"] = tail(outcome.stderr) or tail(outcome.stdout) or "闸门异常退出"
        return result
    try:
        data = json.loads(outcome.stdout)
    except json.JSONDecodeError:
        result["error"] = "闸门输出不是 JSON"
        return result
    result["status"] = data.get("status")
    result["blocking_rules"] = sorted(
        {
            finding.get("rule_id")
            for finding in data.get("findings", [])
            if finding.get("result") == "block" and finding.get("rule_id") in BLOCKING_RULES
        }
    )
    return result


def injection_metrics(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    attacks = [item for item in results if item["label"] == "attack"]
    benign = [item for item in results if item["label"] == "benign"]
    errors = [item for item in results if "error" in item]
    detected = [item for item in attacks if "error" not in item and item["blocking_rules"]]
    blocked = [item for item in benign if "error" not in item and item["blocking_rules"]]
    by_kind: dict[str, dict[str, int]] = {}
    for item in results:
        entry = by_kind.setdefault(f"{item['label']}:{item['kind']}", {"total": 0, "flagged": 0})
        entry["total"] += 1
        entry["flagged"] += int("error" not in item and bool(item["blocking_rules"]))
    return {
        "attack": len(attacks),
        "attack_detected": len(detected),
        "attack_detection_rate": ratio(len(detected), len(attacks)),
        "benign": len(benign),
        "benign_blocked": len(blocked),
        "benign_false_block_rate": ratio(len(blocked), len(benign)),
        "errors": len(errors),
        "by_kind": dict(sorted(by_kind.items())),
        "failures": {
            "missed_attacks": sorted(item["file"] for item in attacks if item not in detected),
            "false_blocks": sorted(item["file"] for item in blocked),
            "errors": [
                {"file": item["file"], "exit_code": item["exit_code"], "error": item["error"]}
                for item in errors
            ],
        },
    }


def check_thresholds(parts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    checks = []
    for threshold in THRESHOLDS:
        part = parts.get(threshold.part)
        if part is None or part.get("status") == "skipped":
            continue
        metrics = part.get("metrics") or {}
        value = metrics.get(threshold.metric)
        note = None
        if part.get("status") != "ok":
            passed, note = False, "该部分运行失败"
        elif value is None:
            passed, note = False, "分母为 0，无法计算"
        elif threshold.op == ">=":
            passed = value >= threshold.value
        else:
            passed = value <= threshold.value
        if threshold.part == "injection" and metrics.get("errors"):
            passed = False
        checks.append(
            {
                "metric": threshold.metric,
                "label": threshold.label,
                "part": threshold.part,
                "op": threshold.op,
                "threshold": threshold.value,
                "value": value,
                "passed": passed,
                "note": note,
            }
        )
    return checks


# ---------------------------------------------------------------------------- 两个部分


def run_judgment(data: Path, work: Path, runner: Runner, now: str, as_of: str) -> dict[str, Any]:
    store = work / "store"
    judgments_path = work / "judgments.jsonl"
    claims_path = work / "claims.jsonl"
    try:
        run_step(
            runner, "init", ["init", str(store), "--project-id", PROJECT_ID, "--now", now, "--json"]
        )
        entities = (data / "store-src" / "entities.jsonl").read_text(encoding="utf-8")
        with (store / "entities.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(entities)
        run_step(
            runner,
            "import",
            [
                "import",
                str(store),
                "--input",
                str(data / "store-src" / "facts.csv"),
                "--format",
                "csv",
                "--now",
                now,
                "--json",
            ],
        )
        run_step(
            runner,
            "judge",
            [
                "judge",
                "--store",
                str(store),
                "--observations",
                str(data / "observations.jsonl"),
                "--as-of",
                as_of,
                "--out",
                str(judgments_path),
                "--claims-out",
                str(claims_path),
                "--now",
                now,
                "--json",
            ],
        )
    except StepFailed as exc:
        return {"status": "error", "error": exc.to_dict()}
    golds = read_jsonl(data / "gold.jsonl")
    aligned = align(golds, read_jsonl(claims_path), read_jsonl(judgments_path))
    return {"status": "ok", "metrics": judgment_metrics(golds, aligned)}


def run_injection(data: Path, runner: Runner, now: str, jobs: int) -> dict[str, Any]:
    labels = read_jsonl(data / "injection" / "labels.jsonl")

    def one(item: dict[str, Any]) -> dict[str, Any]:
        path = data / "injection" / item["file"]
        return gate_outcome(item, runner(["gate", "--draft", str(path), "--now", now, "--json"]))

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        results = list(pool.map(one, labels))
    return {"status": "ok", "metrics": injection_metrics(results)}


# ---------------------------------------------------------------------------- 汇总与记录


def git_commit() -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", *args], capture_output=True, text=True, cwd=REPO_ROOT, check=False
            )
        except OSError:
            return None
        return completed.stdout.strip() if completed.returncode == 0 else None

    commit = git("rev-parse", "--short", "HEAD")
    status = git("status", "--porcelain")
    return {"commit": commit or "unknown", "dirty": bool(status) if status is not None else None}


def strip_details(parts: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """去掉逐样本明细与分类明细，只留汇总数值（保留集默认如此）。"""
    out: dict[str, dict[str, Any]] = {}
    for name, part in parts.items():
        copy = dict(part)
        if "metrics" in copy:
            copy["metrics"] = {
                k: v for k, v in copy["metrics"].items() if k not in ("failures", "by_kind")
            }
        out[name] = copy
    return out


def summarize(
    *,
    split: str,
    seed: int,
    now: str,
    meta: dict[str, Any],
    parts: dict[str, dict[str, Any]],
    show_failures: bool,
    commit: dict[str, Any],
) -> dict[str, Any]:
    checks = check_thresholds(parts)
    ran = [name for name, part in parts.items() if part.get("status") != "skipped"]
    passed = (
        bool(checks)
        and all(check["passed"] for check in checks)
        and all(parts[name].get("status") == "ok" for name in ran)
    )
    detailed = split == "dev" or show_failures
    return {
        "split": split,
        "seed": seed,
        "now": now,
        "commit": commit,
        "data": {
            "as_of": meta["as_of"],
            "counts": meta["counts"],
            "generator_version": meta["generator_version"],
        },
        "parts": parts if detailed else strip_details(parts),
        "thresholds": checks,
        "passed": passed,
        "complete": all(part.get("status") == "ok" for part in parts.values()),
        "details_shown": detailed,
        "holdout_details_viewed": split == "holdout" and show_failures,
        "notice": NOTICE,
    }


def run_record(result: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for check in result["thresholds"]:
        metrics[check["metric"]] = check["value"]
    return {
        "time": result["now"],
        "split": result["split"],
        "seed": result["seed"],
        "commit": result["commit"]["commit"],
        "dirty": result["commit"]["dirty"],
        "parts": {name: part.get("status") for name, part in result["parts"].items()},
        "metrics": metrics,
        "passed": result["passed"],
        "complete": result["complete"],
        "holdout_details_viewed": result["holdout_details_viewed"],
        "synthetic": True,
    }


def append_run(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _fmt(check: dict[str, Any]) -> str:
    if check["value"] is None:
        return f"无（{check['note']}）"
    return f"{check['value']:.4f}"


def render(result: dict[str, Any]) -> str:
    lines = [
        f"合成标注集基准（{result['split']}，seed {result['seed']}，commit {result['commit']['commit']}）"
    ]
    for name, part in result["parts"].items():
        title = {"judgment": "判定类", "injection": "注入类"}[name]
        if part["status"] == "skipped":
            lines.append(f"{title}：未运行（--only）")
        elif part["status"] == "error":
            error = part["error"]
            lines.append(f"{title}：运行失败——步骤 {error['step']} 退出码 {error['exit_code']}")
            if error.get("stderr_tail"):
                lines.append("    " + error["stderr_tail"].replace("\n", "\n    "))
    for check in result["thresholds"]:
        mark = "达到门槛" if check["passed"] else "未达门槛"
        lines.append(
            f"[{mark}] {check['label']}：{_fmt(check)}（门槛 {check['op']} {check['threshold']}）"
        )
    if result["details_shown"]:
        for name, part in result["parts"].items():
            failures = (part.get("metrics") or {}).get("failures") or {}
            for key, items in failures.items():
                if items:
                    shown = [
                        item if isinstance(item, str) else item["file"]
                        for item in items[:MAX_LISTED]
                    ]
                    lines.append(f"  {name}.{key}（{len(items)}）：{'、'.join(shown)}")
    if result["holdout_details_viewed"]:
        lines.append("注意：本次运行已查看保留集明细（已记录在 runs.jsonl）。")
    lines.append("结论：" + ("全部达到门槛" if result["passed"] else "未全部达到门槛"))
    lines.append(NOTICE)
    return "\n".join(lines)


def load_generator() -> Any:
    spec = importlib.util.spec_from_file_location("bench_generate", BENCH_DIR / "generate.py")
    if spec is None or spec.loader is None:  # pragma: no cover
        raise SystemExit("找不到 benchmarks/generate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def prepare_workdir(path: Path) -> Path:
    if path.exists():
        if not (path / MARKER).exists() and any(path.iterdir()):
            raise SystemExit(f"工作目录非空且不是基准工作目录，拒绝覆盖：{path}")
        shutil.rmtree(path)
    path.mkdir(parents=True)
    (path / MARKER).write_text("由 run_benchmark.py 创建，每次运行会清空重建。\n", encoding="utf-8")
    return path


def run(
    split: str,
    *,
    workdir: Path,
    seed: int | None = None,
    now: str | None = None,
    only: str | None = None,
    show_failures: bool = False,
    jobs: int = 4,
    runner: Runner | None = None,
    runs_file: Path | None = None,
    commit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runner = runner or cli_runner
    generator = load_generator()
    stamp = now or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    work = prepare_workdir(workdir)
    meta = generator.generate(split, work / "data", seed)
    parts: dict[str, dict[str, Any]] = {}
    if only in (None, "judgment"):
        parts["judgment"] = run_judgment(work / "data", work, runner, stamp, meta["as_of"])
    else:
        parts["judgment"] = {"status": "skipped"}
    if only in (None, "injection"):
        parts["injection"] = run_injection(work / "data", runner, stamp, jobs)
    else:
        parts["injection"] = {"status": "skipped"}
    result = summarize(
        split=split,
        seed=meta["seed"],
        now=stamp,
        meta=meta,
        parts=parts,
        show_failures=show_failures,
        commit=commit or git_commit(),
    )
    append_run(runs_file or RUNS_FILE, run_record(result))
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="合成标注集基准（只通过 CLI 子进程驱动 geo-facts）"
    )
    parser.add_argument("--split", required=True, choices=("dev", "holdout"))
    parser.add_argument("--workdir", help="工作目录（默认 benchmarks/out/<split>，每次清空重建）")
    parser.add_argument("--seed", type=int, help="生成数据的随机种子（默认按 split 固定）")
    parser.add_argument(
        "--now", help="当前时间（UTC，如 2026-10-08T02:00:00Z）；记录与子命令都用它"
    )
    parser.add_argument(
        "--only", choices=("judgment", "injection"), help="只运行其中一部分（开发用）"
    )
    parser.add_argument(
        "--show-failures",
        action="store_true",
        help="保留集也输出逐样本明细（会记录“已查看保留集明细”）",
    )
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4, help="闸门并行子进程数")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args(argv)
    workdir = Path(args.workdir) if args.workdir else DEFAULT_WORKDIR / args.split
    result = run(
        args.split,
        workdir=workdir,
        seed=args.seed,
        now=args.now,
        only=args.only,
        show_failures=args.show_failures,
        jobs=args.jobs,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(render(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
