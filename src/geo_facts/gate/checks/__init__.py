"""闸门检查项的注册表。每个检查项一个模块，模块里定义 `CHECK`；在这里按顺序登记。

新增检查项：新建 `checks/<名>.py`，实现 `run(draft, meta, ctx) -> list[Finding]`（需要时再实现
`unavailable(meta, ctx) -> str | None`），定义 `CHECK = Check(...)`，然后加入下面的 ALL_CHECKS。
"""

from __future__ import annotations

from geo_facts.gate.checks import facts, hidden_text, injection, time_forged
from geo_facts.gate.model import Check

ALL_CHECKS: tuple[Check, ...] = (
    facts.CHECK,
    hidden_text.CHECK,
    injection.CHECK,
    time_forged.CHECK,
)
