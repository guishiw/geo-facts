"""导入模板：csv、json、md 三种写法，附列说明。示例行全部虚构。"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from geo_facts.errors import InputError
from geo_facts.ingest.columns import COLUMN_DOCS, TEMPLATE_COLUMNS

TEMPLATE_FORMATS: tuple[str, ...] = ("csv", "json", "md")

EXAMPLE_ROWS: tuple[dict[str, str], ...] = (
    {
        "fact_id": "F-S7-RANGE-CLTC",
        "entity_id": "product-lanxu-s7",
        "attribute": "续航里程",
        "value": "620",
        "value_type": "number",
        "unit": "km",
        "conditions": "test_cycle=CLTC; trim=长续航版",
        "zone": "fact",
        "source_tier": "C",
        "source_type": "企业官网",
        "content_use": "specification",
        "is_core": "true",
        "evidence_id": "EV-S7-SPEC",
        "source_name": "澜序汽车官网 S7 参数页（虚构）",
        "source_ref": "https://lanxu-auto.example/s7/spec",
        "collected_at": "2026-09-20T02:00:00Z",
        "last_verified_at": "2026-10-01T02:00:00Z",
        "reviewer": "事实库审核人（虚构）",
        "valid_from": "2026-03-18",
        "valid_until": "",
        "status": "active",
        "aliases": "续航; 续航能力",
        "note": "",
        "synthetic": "true",
        "ext": "",
    },
    {
        "fact_id": "M-S7-SLOGAN",
        "entity_id": "product-lanxu-s7",
        "attribute": "宣传语",
        "value": "安静得像一片湖",
        "value_type": "text",
        "unit": "",
        "conditions": "",
        "zone": "marketing",
        "source_tier": "",
        "source_type": "",
        "content_use": "promotion",
        "is_core": "false",
        "evidence_id": "",
        "source_name": "",
        "source_ref": "",
        "collected_at": "",
        "last_verified_at": "",
        "reviewer": "",
        "valid_from": "2026-03-18",
        "valid_until": "",
        "status": "active",
        "aliases": "",
        "note": "营销表达不作为判定依据",
        "synthetic": "true",
        "ext": "",
    },
)

# json 模板里的示例行用原生类型。
EXAMPLE_JSON_ROWS: tuple[dict[str, Any], ...] = (
    {
        "fact_id": "F-S7-RANGE-CLTC",
        "entity_id": "product-lanxu-s7",
        "attribute": "续航里程",
        "value": 620,
        "unit": "km",
        "conditions": {"test_cycle": "CLTC", "trim": "长续航版"},
        "zone": "fact",
        "source_tier": "C",
        "source_type": "企业官网",
        "content_use": "specification",
        "is_core": True,
        "evidence_id": "EV-S7-SPEC",
        "source_name": "澜序汽车官网 S7 参数页（虚构）",
        "source_ref": "https://lanxu-auto.example/s7/spec",
        "collected_at": "2026-09-20T02:00:00Z",
        "last_verified_at": "2026-10-01T02:00:00Z",
        "reviewer": "事实库审核人（虚构）",
        "valid_from": "2026-03-18",
        "valid_until": None,
        "status": "active",
        "aliases": ["续航", "续航能力"],
        "synthetic": True,
    },
    {
        "fact_id": "M-S7-SLOGAN",
        "entity_id": "product-lanxu-s7",
        "attribute": "宣传语",
        "value": "安静得像一片湖",
        "value_type": "text",
        "zone": "marketing",
        "content_use": "promotion",
        "valid_from": "2026-03-18",
        "note": "营销表达不作为判定依据",
        "synthetic": True,
    },
)


def column_docs() -> list[dict[str, str]]:
    return [
        {"name": name, "required": required, "description": description}
        for name, required, description in COLUMN_DOCS
    ]


def render_template(fmt: str) -> str:
    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(TEMPLATE_COLUMNS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(EXAMPLE_ROWS)
        return buffer.getvalue()
    if fmt == "json":
        payload = {"columns": column_docs(), "facts": list(EXAMPLE_JSON_ROWS)}
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if fmt == "md":
        lines = [
            "# 事实导入模板（geo-facts import --format md）",
            "",
            "示例行全部虚构；导入时读取第一张含 fact_id 列的表格。",
            "",
            "| " + " | ".join(TEMPLATE_COLUMNS) + " |",
            "|" + "|".join("---" for _ in TEMPLATE_COLUMNS) + "|",
        ]
        for row in EXAMPLE_ROWS:
            lines.append("| " + " | ".join(row[name] for name in TEMPLATE_COLUMNS) + " |")
        lines.extend(["", "## 列说明", ""])
        lines.extend(
            f"- `{name}`（{required}）：{description}"
            for name, required, description in COLUMN_DOCS
        )
        return "\n".join(lines) + "\n"
    raise InputError(f"未知模板格式：{fmt}；可选：{', '.join(TEMPLATE_FORMATS)}")
