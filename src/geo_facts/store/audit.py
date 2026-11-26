"""事实库的变更记录：只追加的哈希链，格式遵循 audit_event 契约。

初始化、导入（含撤回、更正产生的新版本）都会追加一个事件。任何对历史事件的修改、
删除或重排都会让链校验失败；事件里的 after_hash 记录了当时 entities.jsonl 与
facts.jsonl 的内容哈希，绕过本工具改动这两个文件也能被发现。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from geo_facts import contracts
from geo_facts.canon import hash_obj, hash_without, sha256_file
from geo_facts.errors import InputError
from geo_facts.jsonl import append_jsonl, read_jsonl
from geo_facts.store.model import ENTITIES_FILE, FACTS_FILE, StorePaths
from geo_facts.timeutil import format_utc, parse_utc

SCHEMA_VERSION = "1.0"
TARGET_TYPE = "fact_store"


def resolve_now(value: str | None) -> str:
    """命令的时间戳：传入 --now 时使用它（便于复算），否则取当前 UTC 时间。"""
    if value is None:
        return format_utc(datetime.now(UTC))
    parse_utc(value)
    return value


def event_hash(event: dict[str, Any]) -> str:
    return hash_without(event, "event_hash")


def file_hash(path: Path) -> str | None:
    """文件内容的 SHA-256；文件不存在或为空时为 None。"""
    if not path.exists() or path.stat().st_size == 0:
        return None
    return sha256_file(path)


def state_hash(paths: StorePaths) -> str:
    """事实库数据的内容哈希：entities.jsonl 与 facts.jsonl 各自的 SHA-256 再做规范化哈希。"""
    return hash_obj({ENTITIES_FILE: file_hash(paths.entities), FACTS_FILE: file_hash(paths.facts)})


def append_event(
    audit_path: Path,
    *,
    project_id: str,
    actor: str,
    action: str,
    target_id: str,
    reason: str,
    occurred_at: str,
    before_hash: str | None,
    after_hash: str | None,
    details: dict[str, Any] | None = None,
    synthetic: bool = False,
) -> dict[str, Any]:
    """追加一个审计事件并返回它。"""
    existing = read_jsonl(audit_path, missing_ok=True)
    event: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_id": f"evt-{len(existing) + 1:06d}",
        "project_id": project_id,
        "actor": actor,
        "occurred_at": occurred_at,
        "action": action,
        "target": {"type": TARGET_TYPE, "id": target_id},
        "reason": reason,
        "before_hash": before_hash,
        "after_hash": after_hash,
        "previous_event_hash": existing[-1].get("event_hash") if existing else None,
    }
    if details is not None:
        event["details"] = details
    if synthetic:
        event["synthetic"] = True
    event["event_hash"] = event_hash(event)
    issues = contracts.iter_issues("audit_event", event)
    if issues:
        raise InputError(
            f"审计事件不符合契约（{issues[0].path or '/'}：{issues[0].message}）",
            details=[issue.to_dict() for issue in issues],
        )
    append_jsonl(audit_path, [event])
    return event


def verify_chain(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """校验审计链，返回问题列表（空列表表示完整）。"""
    problems: list[dict[str, Any]] = []
    previous: str | None = None
    for index, event in enumerate(events, start=1):
        if not contracts.is_valid("audit_event", event):
            problems.append(
                {"index": index, "kind": "invalid", "message": "不符合 audit_event 契约"}
            )
            stored = event.get("event_hash")
            previous = stored if isinstance(stored, str) else None
            continue
        if event_hash(event) != event["event_hash"]:
            problems.append(
                {
                    "index": index,
                    "kind": "modified",
                    "message": f"事件 {event['event_id']} 内容被改动",
                }
            )
        if event["previous_event_hash"] != previous:
            problems.append(
                {
                    "index": index,
                    "kind": "chain_broken",
                    "message": f"事件 {event['event_id']} 与前一事件的链接断裂（可能有删除、插入或重排）",
                }
            )
        previous = event["event_hash"]
    return problems
