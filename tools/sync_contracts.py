"""契约副本同步与自检。

用法：
    python tools/sync_contracts.py --check                 只校验 contracts/ 与 contracts.lock 是否一致
    python tools/sync_contracts.py --from ../geo-verify/spec  从唯一源复制契约并重写 contracts.lock

契约唯一源在 geo-verify 的 spec/。本仓库只保存副本，并用 contracts.lock
记录版本和每个文件的 SHA-256。副本被改动而锁未更新时，--check 退出 1。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "contracts"
LOCK = CONTRACTS / "contracts.lock"
VERSION = "v1"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_files() -> dict[str, str]:
    base = CONTRACTS / VERSION
    return {
        path.relative_to(base).as_posix(): digest(path)
        for path in sorted(base.rglob("*.json"))
        if path.is_file()
    }


def write_lock() -> None:
    lock = {
        "source": "geo-verify/spec",
        "version": VERSION,
        "algorithm": "sha256",
        "files": current_files(),
    }
    LOCK.write_text(
        json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def check() -> list[str]:
    if not LOCK.exists():
        return ["缺少 contracts/contracts.lock"]
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    problems: list[str] = []
    if lock.get("version") != VERSION:
        problems.append(f"锁文件版本为 {lock.get('version')!r}，期望 {VERSION!r}")
    locked: dict[str, str] = lock.get("files", {})
    actual = current_files()
    for name in sorted(set(locked) | set(actual)):
        if name not in actual:
            problems.append(f"锁中登记的文件缺失：{name}")
        elif name not in locked:
            problems.append(f"文件未登记到锁：{name}")
        elif locked[name] != actual[name]:
            problems.append(f"文件与锁不一致：{name}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="契约副本同步与自检")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="校验副本与锁一致")
    group.add_argument("--from", dest="source", help="geo-verify 的 spec 目录")
    args = parser.parse_args()
    if args.source:
        source = Path(args.source) / VERSION
        if not source.is_dir():
            print(f"找不到契约源目录：{source}", file=sys.stderr)
            return 2
        target = CONTRACTS / VERSION
        shutil.copytree(source, target, dirs_exist_ok=True)
        source_names = {path.relative_to(source).as_posix() for path in source.rglob("*.json")}
        stale = [
            path
            for path in target.rglob("*.json")
            if path.relative_to(target).as_posix() not in source_names
        ]
        for path in stale:
            path.unlink()
        write_lock()
        print(f"已同步 {len(source_names)} 个契约文件并更新 contracts.lock")
        return 0
    problems = check()
    for problem in problems:
        print(f"[失败] {problem}")
    if not problems:
        print(f"[通过] contracts/{VERSION} 与 contracts.lock 一致（{len(current_files())} 个文件）")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
