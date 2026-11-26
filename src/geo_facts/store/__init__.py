"""事实库：事实、观点、营销表达三区分治；版本、时效与冲突。

只有事实区（zone=fact）的条目能作为判定依据，而且必须当日有效、未撤回、无争议、
绑定了证据、不处于未裁决的冲突中。来源等级（source_tier）与内容用途（content_use）
是两个独立字段，互不推导。规则说明见 docs/methodology.md。

后续模块（陈述拆解、判定、闸门）使用的查询接口：

```python
from datetime import date
from geo_facts.store import basis_view, load_store

store = load_store("STORE")
view = basis_view(store, date(2026, 10, 8))
view.usable                  # list[dict]：可作为确定依据的事实（fact 契约记录）
view.unusable                # list[FactStatus]：不可用的事实及原因代码
view.status("F-S7-PRICE")    # FactStatus | None
view.history("F-S7-PRICE")   # 全部历史版本，版本号升序
view.stale("F-S7-PRICE")     # [(旧版本, "expired" | "superseded")]
```
"""

from geo_facts.store.audit import resolve_now, state_hash, verify_chain
from geo_facts.store.checks import check_store
from geo_facts.store.conflicts import (
    conflicting_fact_ids,
    detect_conflicts,
    same_attribute,
    values_differ,
)
from geo_facts.store.export import (
    EXPORT_COLUMNS,
    FACT_COLUMNS,
    select_facts,
    to_csv,
    to_jsonl,
)
from geo_facts.store.model import (
    FactStore,
    InvalidRecord,
    StorePaths,
    is_valid_id,
    load_meta,
    load_store,
)
from geo_facts.store.query import BasisView, FactStatus, basis_view
from geo_facts.store.timeline import (
    REASON_CODES,
    REASON_TEXT,
    Resolution,
    basis_blockers,
    describe_reasons,
    effective_version,
    live_versions,
    parse_day,
    resolve,
    stale_versions,
    today_in,
    valid_on,
)
from geo_facts.store.writer import commit_records, init_store, load_example

__all__ = [
    "EXPORT_COLUMNS",
    "FACT_COLUMNS",
    "REASON_CODES",
    "REASON_TEXT",
    "BasisView",
    "FactStatus",
    "FactStore",
    "InvalidRecord",
    "Resolution",
    "StorePaths",
    "basis_blockers",
    "basis_view",
    "check_store",
    "commit_records",
    "conflicting_fact_ids",
    "describe_reasons",
    "detect_conflicts",
    "effective_version",
    "init_store",
    "is_valid_id",
    "live_versions",
    "load_example",
    "load_meta",
    "load_store",
    "parse_day",
    "resolve",
    "resolve_now",
    "same_attribute",
    "select_facts",
    "stale_versions",
    "state_hash",
    "to_csv",
    "to_jsonl",
    "today_in",
    "valid_on",
    "values_differ",
    "verify_chain",
]
