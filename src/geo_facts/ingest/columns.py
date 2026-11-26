"""导入模板的列与列说明。

导入模板与 CSV 导出共用同一组列（`geo_facts.store.FACT_COLUMNS`）；导出文件多一列 version，
导入时可选（见 `VERSION_COLUMN`）。列名不区分大小写、忽略首尾空白；出现未知列时整份文件按
输入错误拒绝，避免拼错的列名把数据悄悄丢掉。
"""

from __future__ import annotations

from geo_facts.store import FACT_COLUMNS

VERSION_COLUMN = "version"
TEMPLATE_COLUMNS: tuple[str, ...] = FACT_COLUMNS
KNOWN_COLUMNS: frozenset[str] = frozenset((*FACT_COLUMNS, VERSION_COLUMN))
REQUIRED_HEADERS: tuple[str, ...] = (
    "fact_id",
    "entity_id",
    "attribute",
    "value",
    "zone",
    "content_use",
    "valid_from",
)
EVIDENCE_COLUMNS: tuple[str, ...] = (
    "evidence_id",
    "source_name",
    "source_ref",
    "collected_at",
    "last_verified_at",
    "reviewer",
)

# (列名, 是否必填, 说明)。顺序即模板列顺序，最后一项 version 只在导出文件里出现。
COLUMN_DOCS: tuple[tuple[str, str, str], ...] = (
    (
        "fact_id",
        "必填",
        "事实编号：字母或数字开头，可含 . _ : -。同一 fact_id 再次导入且内容有变化时自动取"
        "“最新版本号 + 1”，旧版本保留；内容不变则跳过并计数。",
    ),
    (
        "entity_id",
        "必填",
        "实体编号，必须已在事实库 entities.jsonl 中（或在同一份文件里一并导入）。",
    ),
    ("attribute", "必填", "属性名，如 续航里程、售价、上市时间。"),
    ("value", "必填", "值：数字、范围（600~650）、布尔（true / false）或文本（如 2026-03-18）。"),
    (
        "value_type",
        "可选",
        "number / range / boolean / text；留空时自动识别：纯数字为 number，A~B 为 range，其余为 text。",
    ),
    ("unit", "可选", "单位，如 km、万元、kWh、s；没有单位留空。"),
    (
        "conditions",
        "可选",
        "适用条件，写成 键=值，多个用分号分隔，如 test_cycle=CLTC; trim=长续航版。"
        "条件不同的事实不会被判为矛盾。",
    ),
    (
        "zone",
        "必填",
        "分区：fact（事实）/ opinion（观点）/ marketing（营销表达）；只有事实区能作为判定依据。",
    ),
    (
        "source_tier",
        "事实区必填",
        "来源等级 A～D，与 content_use 是两个独立字段，互不推导。留空时：来源链接属于该实体"
        "（或其上级品牌）的自有域名 → 按 026 默认 C 级，并在结果里提示这是默认值；"
        "其他来源 → 事实区的行报错（观点区、营销区允许为空）。",
    ),
    (
        "source_type",
        "可选",
        "来源类型的文字说明（如 企业官网、评测媒体），只作记录，不影响来源等级。",
    ),
    (
        "content_use",
        "必填",
        "内容用途：specification / pricing / policy / test_result / review / promotion / other。",
    ),
    (
        "is_core",
        "可选",
        "是否核心事实：true / false，默认 false。核心事实必须绑定证据才能作为确定依据（026 §6.2）。",
    ),
    (
        "evidence_id",
        "有证据时必填",
        "证据编号。下列六个证据列要么全部留空（没有证据），要么全部填写。",
    ),
    ("source_name", "有证据时必填", "来源名称。"),
    ("source_ref", "有证据时必填", "原始链接或文件。"),
    ("collected_at", "有证据时必填", "采集时间，UTC，如 2026-09-20T02:00:00Z。"),
    ("last_verified_at", "有证据时必填", "最近核验时间，UTC，不得早于采集时间。"),
    ("reviewer", "有证据时必填", "审核责任人；留空时取命令行的 --reviewer。"),
    ("valid_from", "必填", "生效日期 YYYY-MM-DD（含当天）。"),
    ("valid_until", "可选", "失效日期 YYYY-MM-DD（含当天）；留空表示尚未失效。"),
    ("status", "可选", "active（默认）/ retracted（撤回）/ disputed（有争议）。"),
    ("aliases", "可选", "属性的其他说法，分号分隔，如 续航; 续航能力。"),
    ("note", "可选", "备注。"),
    ("synthetic", "可选", "是否合成数据 true / false；合成事实库里的记录一律记为 true。"),
    ("ext", "可选", "扩展字段，JSON 对象。"),
    (
        VERSION_COLUMN,
        "可选",
        "版本号（导出文件带这一列）。留空时按上面的规则自动编号；填写时：该版本已存在且内容相同 → "
        "跳过，内容不同 → 该行报错（已有版本只追加、不改写）；不存在时必须等于“最新版本号 + 1”。",
    ),
)


def normalize_header(name: str) -> str:
    return name.strip().lower()
