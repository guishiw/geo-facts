"""文档完整性、红线措辞、条款号白名单、CLI 命令一致性与仓库隔离测试。"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

from geo_facts.cli import build_parser
from geo_facts.gate.model import ALLOWED_CLAUSES

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES: tuple[str, ...] = (
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    ".github/workflows/ci.yml",
    "contracts/contracts.lock",
    "docs/cli.md",
    "docs/quickstart.md",
    "docs/methodology.md",
    "docs/gate-rules.md",
    "docs/clause-map.md",
    "examples/auto/README.md",
    "examples/auto/run.sh",
)

# 禁止在公开仓库的文档、源码、样例和基准中出现的措辞（GOAL R4 / R9）。
FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("禁用词：官方", re.compile(r"官方")),
    ("禁用词：认证", re.compile(r"认证")),
    ("禁用词：对标", re.compile(r"对标")),
    (
        "禁用保证性表述",
        re.compile(
            r"保证首推|必然收录|保证收录|保证推荐|必然推荐|包收录|保证排名|确保首推|稳居第一"
        ),
    ),
)

TEXT_SUFFIXES = frozenset({".md", ".py", ".json", ".jsonl", ".sh", ".yml", ".yaml", ".txt", ".csv"})
SCAN_ROOTS: tuple[str, ...] = (
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "docs",
    "src",
    "examples",
    "benchmarks",
)

# 匹配任何含 § 的条款引用，确保每个 § 引用都带标准编号且落在 ALLOWED_CLAUSES 白名单内。
CLAUSE_REF_RE = re.compile(r"(?:(?:T/(?:CAAAD|CAPT)\s*)?(\d{3})\s*)?§\s*(\d+(?:\.\d+)*)")


def _iter_repo_text_files() -> list[Path]:
    files: list[Path] = []
    for entry in SCAN_ROOTS:
        target = REPO_ROOT / entry
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            for path in sorted(target.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(REPO_ROOT)
                if "__pycache__" in rel.parts or "out" in rel.parts:
                    continue
                if path.suffix.lower() in TEXT_SUFFIXES:
                    files.append(path)
    return files


def _cli_command_paths(parser: argparse.ArgumentParser) -> set[tuple[str, ...]]:
    """递归提取 geo-facts 命令行解析器的全部叶子命令路径。"""
    paths: set[tuple[str, ...]] = set()

    def walk(current: argparse.ArgumentParser, prefix: tuple[str, ...]) -> None:
        sub_action = next(
            (
                action
                for action in current._actions
                if isinstance(action, argparse._SubParsersAction)
            ),
            None,
        )
        if sub_action is None:
            if prefix:
                paths.add(prefix)
            return
        for name, subparser in sub_action.choices.items():
            walk(subparser, (*prefix, name))

    walk(parser, ())
    return paths


def _extract_cli_doc_commands(markdown: str) -> set[tuple[str, ...]]:
    """从 docs/cli.md 中提取所有 `geo-facts <命令...>` 的命令路径。"""
    commands: set[tuple[str, ...]] = set()
    # 匹配标题（### `geo-facts ...`）和代码块中的 `geo-facts <cmd> [subcmd]`。
    pattern = re.compile(r"\bgeo-facts\s+([a-z][a-z0-9-]*)(?:\s+([a-z][a-z0-9-]*))?")
    for line in markdown.splitlines():
        for match in pattern.finditer(line):
            first = match.group(1)
            second = match.group(2)
            # 若第二个词是子命令（小写标识符而非参数/占位符/选项），先记录两级，后续按命令树核对。
            if first == "claims" and second is not None:
                commands.add((first, second))
            else:
                commands.add((first,))
    return commands


def test_required_docs_and_repo_files_exist() -> None:
    """公开仓库必有文档、治理文件与 CI 配置全部存在且非空。"""
    for rel in REQUIRED_FILES:
        path = REPO_ROOT / rel
        assert path.is_file(), f"缺少必有文件：{rel}"
        assert path.read_text(encoding="utf-8").strip(), f"文件内容为空：{rel}"


def test_no_forbidden_wording_in_repository() -> None:
    """全仓库不出现禁用措辞及对第三方平台结果的保证性表述。"""
    hits: list[str] = []
    for path in _iter_repo_text_files():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO_ROOT)
        for lineno, line in enumerate(text.splitlines(), start=1):
            for label, pattern in FORBIDDEN_PATTERNS:
                if pattern.search(line):
                    hits.append(f"{rel}:{lineno} [{label}] {line.strip()}")
    assert not hits, "发现禁用措辞：\n" + "\n".join(hits)


def test_only_whitelisted_clause_numbers_appear() -> None:
    """条款对照表、闸门规则、方法学、接口文档与源码中只出现白名单条款号。"""
    targets = [
        REPO_ROOT / "docs" / "clause-map.md",
        REPO_ROOT / "docs" / "gate-rules.md",
        REPO_ROOT / "docs" / "methodology.md",
        REPO_ROOT / "docs" / "cli.md",
        REPO_ROOT / "README.md",
        *sorted((REPO_ROOT / "src" / "geo_facts").rglob("*.py")),
        *sorted((REPO_ROOT / "src" / "geo_facts" / "lexicons").glob("*.json")),
    ]
    bad: list[str] = []
    found_in_clause_map: set[str] = set()
    for path in targets:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO_ROOT)
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in CLAUSE_REF_RE.finditer(line):
                std_code, sec = match.group(1), match.group(2)
                if std_code is None:
                    bad.append(f"{rel}:{lineno} 缺少标准编号：{match.group(0)!r}")
                    continue
                canonical = f"{std_code} §{sec}"
                if canonical not in ALLOWED_CLAUSES:
                    bad.append(f"{rel}:{lineno} 条款号不在白名单：{canonical!r}")
                elif path.name == "clause-map.md":
                    found_in_clause_map.add(canonical)
    assert not bad, "发现非法条款号引用：\n" + "\n".join(bad)
    assert found_in_clause_map, "docs/clause-map.md 应包含白名单内的条款号对照"
    assert found_in_clause_map <= ALLOWED_CLAUSES


def test_cli_md_commands_match_help_tree() -> None:
    """docs/cli.md 列出的每个命令都在 geo-facts --help 命令树里，且所有叶子命令均有文档。"""
    cli_md = (REPO_ROOT / "docs" / "cli.md").read_text(encoding="utf-8")
    documented = _extract_cli_doc_commands(cli_md)
    actual = _cli_command_paths(build_parser())
    assert documented, "未能从 docs/cli.md 解析出任何命令"
    unknown = documented - actual
    missing = actual - documented
    assert not unknown, f"docs/cli.md 列出了命令行不存在的命令：{sorted(unknown)}"
    assert not missing, f"docs/cli.md 漏掉了命令行已存在的命令：{sorted(missing)}"


def test_src_has_no_geo_verify_or_geo_private_imports() -> None:
    """src 下不得存在对 geo_verify 或 geo_private 的 import（两个公开仓库只通过文件交换）。"""
    violations: list[str] = []
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(REPO_ROOT)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".", 1)[0]
                    if top in {"geo_verify", "geo_private"}:
                        violations.append(f"{rel}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".", 1)[0]
                if top in {"geo_verify", "geo_private"}:
                    violations.append(f"{rel}:{node.lineno} from {node.module} import ...")
    assert not violations, "发现跨仓库非法依赖：\n" + "\n".join(violations)


def test_honesty_and_boundary_disclosures_present() -> None:
    """关键诚实声明与边界提示必须写在对应文档中。"""
    methodology = (REPO_ROOT / "docs" / "methodology.md").read_text(encoding="utf-8")
    assert "合成标注集" in methodology
    assert "不代表真实业务准确率" in methodology
    assert "未验证" in methodology
    assert "事前登记" in methodology

    clause_map = (REPO_ROOT / "docs" / "clause-map.md").read_text(encoding="utf-8")
    assert "开发者自行整理" in clause_map
    assert "未经标准制定方确认" in clause_map
    assert "人工复核" in clause_map

    gate_rules = (REPO_ROOT / "docs" / "gate-rules.md").read_text(encoding="utf-8")
    assert "需复核" in gate_rules
    assert "不负责发布" in gate_rules
    assert "未经法务审阅" in gate_rules

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for link in (
        "docs/cli.md",
        "docs/quickstart.md",
        "docs/methodology.md",
        "docs/gate-rules.md",
        "docs/clause-map.md",
        "LICENSE",
    ):
        assert link in readme, f"README.md 缺少文档链接：{link}"
