"""事实库目录的只读访问（不依赖 store 模块，直接按契约读文件）。

- `store.json` 的 timezone：把 UTC 时间换算成自然日；没有该文件或该字段时用 Asia/Shanghai。
"""

from __future__ import annotations

from pathlib import Path

from geo_facts.errors import InputError
from geo_facts.jsonl import read_json
from geo_facts.timeutil import DEFAULT_TIMEZONE, zone


def require_store(store: Path) -> Path:
    if not store.is_dir():
        raise InputError(f"事实库目录不存在：{store.name}", details={"store": store.name})
    return store


def store_timezone(store: Path) -> str:
    path = require_store(store) / "store.json"
    if not path.is_file():
        return DEFAULT_TIMEZONE
    data = read_json(path)
    if not isinstance(data, dict):
        raise InputError("store.json 必须是 JSON 对象")
    name = data.get("timezone")
    if name is None:
        return DEFAULT_TIMEZONE
    if not isinstance(name, str):
        raise InputError("store.json 的 timezone 必须是字符串")
    zone(name)
    return name
