"""陈述拆解：从回答或稿件中拆出可核验的陈述，保留原文位置。

默认用规则实现（离线、不联网）；模型实现只留插件接口（见 plugins），本仓库未提供模型实现。

```python
from geo_facts.claims import build_lexicon, extract_claims

lexicon = build_lexicon(load_store("STORE"))
claims = extract_claims(text, lexicon=lexicon, source={"type": "observation", "id": "obs-1"},
                        project_id="auto-demo")
# 每条是 claim 契约记录；claim["text"] == text[claim["span"]["start"]:claim["span"]["end"]]
```
"""

from geo_facts.claims.attributes import CONCEPT_PHRASES, attribute_concept, concept_of
from geo_facts.claims.lexicon import Concept, Lexicon, build_lexicon
from geo_facts.claims.rules import EXTRACTOR, extract_claims

__all__ = [
    "CONCEPT_PHRASES",
    "EXTRACTOR",
    "Concept",
    "Lexicon",
    "attribute_concept",
    "build_lexicon",
    "concept_of",
    "extract_claims",
]
