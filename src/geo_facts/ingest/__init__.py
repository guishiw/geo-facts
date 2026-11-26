"""事实导入：xlsx / csv / json / md 四种格式，逐行报错，同 fact_id 内容变化时追加新版本。

```python
from geo_facts.ingest import import_file, render_template

result = import_file("STORE", "facts.csv", fmt=None, reviewer="审核人", now="2026-10-08T02:00:00Z")
result["facts"]    # {"created", "new_versions", "unchanged", "invalid"}
result["errors"]   # [{"row", "kind", "column", "message", "fact_id"?}]
result["notices"]  # 来源等级按自有域名默认 C 级的提示
```
"""

from geo_facts.ingest.columns import COLUMN_DOCS, KNOWN_COLUMNS, TEMPLATE_COLUMNS, VERSION_COLUMN
from geo_facts.ingest.convert import Context, Converted, convert_entity_row, convert_fact_row
from geo_facts.ingest.importer import import_file, import_source
from geo_facts.ingest.readers import FORMATS, RawRow, RawSource, detect_format, read_source
from geo_facts.ingest.templates import TEMPLATE_FORMATS, column_docs, render_template

__all__ = [
    "COLUMN_DOCS",
    "FORMATS",
    "KNOWN_COLUMNS",
    "TEMPLATE_COLUMNS",
    "TEMPLATE_FORMATS",
    "VERSION_COLUMN",
    "Context",
    "Converted",
    "RawRow",
    "RawSource",
    "column_docs",
    "convert_entity_row",
    "convert_fact_row",
    "detect_format",
    "import_file",
    "import_source",
    "read_source",
    "render_template",
]
