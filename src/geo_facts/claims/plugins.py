"""拆解器插件接口。

默认只注册规则拆解器 `rules`（离线、不联网）。**本仓库未提供模型实现**：模型拆解只保留这个接口，
默认不启用；第三方实现需自行注册，且不得在测试或默认运行中访问任何真实 AI 平台（真实调用须显式
`--live`，密钥只来自环境变量）。插件输出的每条陈述都会再按 claim 契约校验。

```python
class MyExtractor:
    name = "my-model"
    def __init__(self, lexicon: Lexicon) -> None: ...
    def extract(self, text, *, source, project_id, synthetic) -> list[dict]: ...

register_extractor("my-model", MyExtractor)
```
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from geo_facts.claims.lexicon import Lexicon
from geo_facts.claims.rules import extract_claims
from geo_facts.errors import InputError

DEFAULT_EXTRACTOR = "rules"
MODEL_NOTICE = "未提供模型实现：模型拆解只保留插件接口，默认不启用、不联网。"


class ClaimExtractor(Protocol):
    """陈述拆解器：给定原文，返回 claim 契约记录列表。"""

    name: str

    def extract(
        self,
        text: str,
        *,
        source: Mapping[str, str],
        project_id: str | None,
        synthetic: bool,
    ) -> list[dict[str, Any]]: ...


ExtractorFactory = Callable[[Lexicon], ClaimExtractor]


class RuleExtractor:
    """默认的规则拆解器。"""

    name = DEFAULT_EXTRACTOR

    def __init__(self, lexicon: Lexicon) -> None:
        self.lexicon = lexicon

    def extract(
        self,
        text: str,
        *,
        source: Mapping[str, str],
        project_id: str | None,
        synthetic: bool,
    ) -> list[dict[str, Any]]:
        return extract_claims(
            text, lexicon=self.lexicon, source=source, project_id=project_id, synthetic=synthetic
        )


_REGISTRY: dict[str, ExtractorFactory] = {DEFAULT_EXTRACTOR: RuleExtractor}


def register_extractor(name: str, factory: ExtractorFactory) -> None:
    """注册一个拆解器。不能覆盖已注册的名字（包括默认的 rules）。"""
    if not name.strip():
        raise ValueError("拆解器名称不能为空")
    if name in _REGISTRY:
        raise ValueError(f"拆解器 {name} 已注册")
    _REGISTRY[name] = factory


def unregister_extractor(name: str) -> None:
    if name == DEFAULT_EXTRACTOR:
        raise ValueError("默认的规则拆解器不能注销")
    _REGISTRY.pop(name, None)


def available_extractors() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def get_extractor(name: str, lexicon: Lexicon) -> ClaimExtractor:
    factory = _REGISTRY.get(name)
    if factory is None:
        raise InputError(
            f"未知拆解器：{name}；可选：{', '.join(available_extractors())}。{MODEL_NOTICE}"
        )
    return factory(lexicon)
