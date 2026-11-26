"""规则陈述拆解（默认实现，离线可用）。

处理顺序（全部在折叠文本上进行，最后映射回原文位置，左闭右开、按码点计）：
1. 实体：事实库实体名与别名，跨实体最长优先；英文数字写法要求两侧不紧贴英文字母或数字；
   落在排除词里的不算。品牌后面紧跟一个车型：车型属于该品牌 → 合并为该车型；车型属于
   别的品牌（“朔风 S7”）或是事实库里没有的型号（“澜序 S9”）→ 记为“未知实体”，不归属任何车型。
2. 条件：测试工况（CLTC / WLTC / NEDC / EPA）、年款、版本配置、事实库里出现过的其他条件值、电量区间。
3. 日期、属性词（事实库属性名与别名 + 常见说法同义表）、数量（数值、范围、约数、上下界）、型号写法。
   变化量（“提升 50 公里”“便宜 2 万元”）不是属性值，不拆。
4. 分句：句号、问号、感叹号、分号、换行分句；逗号、顿号、表格竖线分小句。
5. 每个值配一个属性：同一小句里最近的前置属性词；没有前置属性词时取最近的后置属性词
   （“2026 年 3 月上市”）；表格的数据单元格取表头同列的属性；小句除实体、条件和“为、是、约、而是”
   等虚词外没有别的内容时（“岑野 K5 为 580 公里”），沿用同一句前面小句的属性。
   值与属性的取值类型、量纲不相容时不拆（“电池 8 年质保”不拆成电池容量 8 年）。
6. 实体归属：同一小句里值前面最近的实体；没有则取值后面的；再没有则取前文最近提及的车型
   （“它的……”“该车……”“这款车……”及省略主语都按此处理；前文最近的是未知实体时不归属）。
   “分别”句按顺序一一对应。
7. 条件归属：小句里该键只有一处条件时适用于小句里所有的值；有多处时归最近的值；
   同一句前面不含值的小句里的条件（“在 WLTC 工况下，……”）和表头里的条件也适用。
   年款只有在事实库按年款区分事实时才作为条件，否则记在 ext.model_year。
8. 不拆：问句（以问号结尾、小句以“吗 / 么”结尾、含“是否 / 是不是 / 有没有 / 能不能 / 会不会”）；
   否定（值前面、上一个值之后出现“不是 / 并非 / 不为 / 没有 / 并没有 / 并无 / 未达 / 达不到 /
   不会 / 不可能 / 不止 / 绝非”）；假设与传闻（句首到值之间出现“如果 / 假如 / 假设 / 倘若 /
   要是 / 若是 / 传闻 / 网传 / 据传 / 谣传 / 据说 / 有传言 / 有人说”）。
   “不低于、不超过”等是上下界，不是否定。
陈述的原文片段从（同一小句里的）实体或属性词开始，到值、属性词或条件结束，括号补齐。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from geo_facts.canon import hash_obj
from geo_facts.claims.lexicon import Concept, Lexicon, looks_like_model_code
from geo_facts.normalize import (
    MASK,
    DateValue,
    Quantity,
    compact,
    lookup_unit,
    make_view,
    scan_conditions,
    scan_dates,
    scan_quantities,
)
from geo_facts.normalize.conditions import MODEL_YEAR, SOC_RANGE
from geo_facts.normalize.text import fold_char
from geo_facts.normalize.values import quantity_ext

SCHEMA_VERSION = "1.0"
EXTRACTOR: dict[str, str] = {"type": "rule", "id": "geo-facts-rules", "version": "1.0"}

SENTENCE_ENDS = frozenset("。!?;\n")
CLAUSE_SEPARATORS = frozenset({chr(0xFF0C), "、", "|"})
NEGATIONS: tuple[str, ...] = (
    "不是",
    "并非",
    "不为",
    "没有",
    "并无",
    "未达",
    "达不到",
    "不会",
    "不可能",
    "不止",
    "绝非",
)
HYPOTHETICALS: tuple[str, ...] = (
    "如果",
    "假如",
    "假设",
    "倘若",
    "要是",
    "若是",
    "传闻",
    "网传",
    "据传",
    "谣传",
    "据说",
    "有传言",
    "有人说",
)
QUESTION_WORDS: tuple[str, ...] = ("是否", "是不是", "有没有", "能不能", "会不会")
QUESTION_ENDINGS = frozenset("吗么")
# 小句里除实体、条件外只剩这些虚词时，算“省略了属性”的小句（沿用前面小句的属性）。
FILLERS: tuple[str, ...] = tuple(
    sorted(
        (
            "而是",
            "则为",
            "则是",
            "约为",
            "分别为",
            "分别是",
            "大约",
            "约",
            "可达",
            "达到",
            "达",
            "只有",
            "仅有",
            "仅",
            "也有",
            "也是",
            "也",
            "则",
            "而",
            "为",
            "是",
            "有",
            "的",
            "了",
            "在",
            "下",
            "工况",
            "不过",
            "但是",
            "但",
            "和",
            "与",
            "及",
            "就",
            "都",
            "还",
            "最高",
            "最低",
            "版本",
            "方面",
        ),
        key=lambda word: -len(word),
    )
)
COMPOUND_CONNECTORS = frozenset({"或", "或者", "和", "及", "以及", "/"})
_MODEL_TOKEN = re.compile(r"[a-z]{1,4}-?\d{1,4}[a-z0-9]*(?![a-z0-9])")
# 型号写法：字母数字段之间可以有连字符、下划线、点或一个空格（LX-M220、LX M220）。
_CODE_TOKEN = re.compile(r"(?<![a-z0-9])[a-z0-9]+(?:[-_. ][a-z0-9]+)*(?![a-z0-9])")
_SOC = re.compile(r"(?<![\d.])(\d{1,3}) ?% ?[-~至到] ?(\d{1,3}) ?%")
# 引用角标与列表序号：[1]、[^2]、【3】、〔4〕，行首的“1.”“2、”“3)”。上标数字另行处理。
_NOISE = re.compile(
    r"\[\^?\d{1,3}(?:[,，、-]\d{1,3})*\]|【\d{1,3}】|〔\d{1,3}〕|(?m:^[ \t]*(?:[-*] )?\d{1,2}[.、)](?!\d))"
)
_TABLE_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")


@dataclass(frozen=True)
class _Span:
    start: int
    end: int


@dataclass(frozen=True)
class _Entity:
    start: int
    end: int
    entity_id: str | None
    resolution: str  # explicit / unknown


@dataclass(frozen=True)
class _Attribute:
    start: int
    end: int
    concept: str
    tight: bool
    unit_like: bool


@dataclass(frozen=True)
class _Condition:
    start: int
    end: int
    key: str
    value: str


@dataclass(frozen=True)
class _Value:
    start: int
    end: int
    kind: str  # quantity / compound / date / model
    quantities: tuple[Quantity, ...] = ()
    date: DateValue | None = None


@dataclass
class _Clause:
    start: int
    end: int
    column: int | None = None


@dataclass
class _Sentence:
    start: int
    end: int
    clauses: list[_Clause]
    question: bool
    header: list[tuple[_Attribute | None, list[_Condition]]] | None = None


@dataclass
class _Pairing:
    value: _Value
    attribute: _Attribute
    concept: Concept
    source: str  # clause / header / inherited
    in_clause: bool
    conditions: dict[str, _Condition] = field(default_factory=dict)
    extra_conditions: list[_Condition] = field(default_factory=list)
    entity: _Entity | None = None  # “分别”句按顺序指定的实体


def json_number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def _is_ascii_alnum(char: str) -> bool:
    folded = fold_char(char)
    return bool(folded) and folded[0].isascii() and folded[0].isalnum()


class _Document:
    def __init__(self, text: str, lexicon: Lexicon) -> None:
        self.text = text
        self.lex = lexicon
        self.view = make_view(text)
        self.folded = self.view.folded
        self.cview = make_view(text, compact_mode=True)
        self.o2f = self._inverse()
        self.blocked = [False] * len(self.folded)
        self._block(self._scan_noise())
        self.entities = self._scan_entities()
        self._block(self.entities)
        self.conditions = self._scan_conditions()
        self._block(self.conditions)
        self.dates = self._scan_dates()
        self._block(self.dates)
        self.attributes = self._scan_attributes()
        self._block([item for item in self.attributes if not item.unit_like])
        self.masked = "".join(
            MASK if blocked else char
            for char, blocked in zip(self.folded, self.blocked, strict=True)
        )
        self.quantities = [
            match for match in scan_quantities(self.masked) if not match.quantity.delta
        ]
        self.codes = self._scan_codes()
        self.sentences = self._segment()

    # ------------------------------------------------------------ 位置换算

    def _inverse(self) -> list[int]:
        starts = self.view.starts
        result: list[int] = []
        index = 0
        for original in range(len(self.text) + 1):
            while index < len(starts) and starts[index] < original:
                index += 1
            result.append(index)
        return result

    def _from_compact(self, start: int, end: int) -> tuple[int, int, int, int]:
        """紧凑文本区间 → (原文起, 原文止, 折叠起, 折叠止)。"""
        original_start = self.cview.starts[start]
        original_end = self.cview.starts[end - 1] + 1
        return original_start, original_end, self.o2f[original_start], self.o2f[original_end]

    def _block(self, items: list[Any]) -> None:
        for item in items:
            for index in range(item.start, item.end):
                self.blocked[index] = True

    def _free(self, start: int, end: int) -> bool:
        return not any(self.blocked[start:end])

    def _boundary_ok(self, key: str, original_start: int, original_end: int) -> bool:
        head = key[0].isascii() and key[0].isalnum() and original_start > 0
        if head and _is_ascii_alnum(self.text[original_start - 1]):
            return False
        tail = key[-1].isascii() and key[-1].isalnum() and original_end < len(self.text)
        return not (tail and _is_ascii_alnum(self.text[original_end]))

    def _compact_matches(
        self, keys: list[tuple[str, Any]], reject: Any = None
    ) -> list[tuple[int, int, str, Any]]:
        """在紧凑文本里找全部写法，最长优先、互不重叠。返回 (折叠起, 折叠止, 写法, 附带值)。

        reject(紧凑起, 紧凑止, 附带值) 为真的候选不收录（如落在排除词里的实体名）。
        """
        haystack = self.cview.folded
        candidates: list[tuple[int, int, str, Any]] = []
        for key, payload in keys:
            position = haystack.find(key)
            while position != -1:
                candidates.append((position, position + len(key), key, payload))
                position = haystack.find(key, position + 1)
        candidates.sort(key=lambda item: (-(item[1] - item[0]), item[0]))
        taken: list[tuple[int, int]] = []
        found: list[tuple[int, int, str, Any]] = []
        for start, end, key, payload in candidates:
            if any(start < other_end and other_start < end for other_start, other_end in taken):
                continue
            original_start, original_end, folded_start, folded_end = self._from_compact(start, end)
            if not self._boundary_ok(key, original_start, original_end):
                continue
            if reject is not None and reject(start, end, payload):
                continue
            taken.append((start, end))
            found.append((folded_start, folded_end, key, payload))
        found.sort(key=lambda item: item[0])
        return found

    # ------------------------------------------------------------ 扫描

    def _scan_noise(self) -> list[_Span]:
        """不参与拆解的片段：引用角标、列表序号、上标数字。"""
        spans = [_Span(*match.span()) for match in _NOISE.finditer(self.folded)]
        for index, char in enumerate(self.text):
            if unicodedata.decomposition(char).startswith("<super>"):
                spans.append(_Span(self.o2f[index], self.o2f[index + 1]))
        return spans

    def _excluded(self, start: int, end: int, entity_id: str | None) -> bool:
        """实体名落在该实体的排除词里（如“波澜序曲”里的“澜序”）。"""
        if entity_id is None:
            return False
        haystack = self.cview.folded
        for phrase in self.lex.exclusions.get(entity_id, ()):
            position = haystack.find(phrase)
            while position != -1:
                if position <= start and end <= position + len(phrase):
                    return True
                position = haystack.find(phrase, position + 1)
        return False

    def _scan_entities(self) -> list[_Entity]:
        raw: list[_Entity] = []
        for folded_start, folded_end, _, entity_id in self._compact_matches(
            list(self.lex.entity_keys), reject=self._excluded
        ):
            resolution = "explicit" if entity_id is not None else "unknown"
            raw.append(_Entity(folded_start, folded_end, entity_id, resolution))
        merged: list[_Entity] = []
        index = 0
        while index < len(raw):
            current = raw[index]
            if (
                current.entity_id is not None
                and self.lex.entities[current.entity_id]["kind"] == "brand"
            ):
                following = raw[index + 1] if index + 1 < len(raw) else None
                if (
                    following is not None
                    and self.folded[current.end : following.start].strip(" -·") == ""
                ):
                    target = following.entity_id
                    if target is not None and self.lex.is_product(target):
                        same_brand = self.lex.parent(target) == current.entity_id
                        merged.append(
                            _Entity(
                                current.start,
                                following.end,
                                target if same_brand else None,
                                "explicit" if same_brand else "unknown",
                            )
                        )
                        index += 2
                        continue
                probe = current.end
                while probe < len(self.folded) and self.folded[probe] == " ":
                    probe += 1
                token = _MODEL_TOKEN.match(self.folded, probe)
                if token is not None and (following is None or following.start >= token.end()):
                    merged.append(_Entity(current.start, token.end(), None, "unknown"))
                    index += 1
                    continue
            merged.append(current)
            index += 1
        return merged

    def _scan_conditions(self) -> list[_Condition]:
        found = [
            _Condition(item.start, item.end, item.key, item.value)
            for item in scan_conditions(
                self.folded, blocked=self.blocked, vocabulary=self.lex.vocabulary
            )
        ]
        if SOC_RANGE in self.lex.condition_keys:
            for match in _SOC.finditer(self.folded):
                if self._free(match.start(), match.end()) and not any(
                    match.start() < item.end and item.start < match.end() for item in found
                ):
                    value = f"{int(match.group(1))}%-{int(match.group(2))}%"
                    found.append(_Condition(match.start(), match.end(), SOC_RANGE, value))
        return sorted(found, key=lambda item: item.start)

    def _masked_text(self) -> str:
        return "".join(
            MASK if blocked else char
            for char, blocked in zip(self.folded, self.blocked, strict=True)
        )

    def _scan_dates(self) -> list[_Value]:
        return [
            _Value(match.start, match.end, "date", date=match.value)
            for match in scan_dates(self._masked_text())
        ]

    def _scan_attributes(self) -> list[_Attribute]:
        keys = [(key, (concept, tight)) for key, concept, tight in self.lex.attribute_keys]
        found: list[_Attribute] = []
        for start, end, key, payload in self._compact_matches(keys):
            if not self._free(start, end):
                continue
            concept, tight = payload
            found.append(_Attribute(start, end, concept, tight, lookup_unit(key) is not None))
        return found

    def _scan_codes(self) -> list[_Value]:
        found: list[_Value] = []
        for match in _CODE_TOKEN.finditer(self.masked):
            start, end = match.span()
            if end - start < 3 or not looks_like_model_code(match.group()):
                continue
            if any(start < item.end and item.start < end for item in self.quantities):
                continue
            found.append(_Value(start, end, "model"))
        return found

    # ------------------------------------------------------------ 分句

    def _split_clauses(self, start: int, end: int, *, table: bool) -> list[_Clause]:
        clauses: list[_Clause] = []
        cursor = start
        column = 0
        if table and self.folded[start:end].lstrip().startswith("|"):
            cursor = self.folded.index("|", start) + 1
        for index in range(cursor, end):
            char = self.folded[index]
            separator = char in CLAUSE_SEPARATORS and (table or char != "|")
            if char == "," and not (
                0 < index < len(self.folded) - 1
                and self.folded[index - 1].isdigit()
                and self.folded[index + 1 : index + 4].isdigit()
            ):
                separator = True
            if separator:
                if table and char != "|":
                    continue
                clauses.append(_Clause(cursor, index, column if table else None))
                cursor = index + 1
                column += 1
        clauses.append(_Clause(cursor, end, column if table else None))
        return [clause for clause in clauses if self.folded[clause.start : clause.end].strip()]

    def _segment(self) -> list[_Sentence]:
        sentences: list[_Sentence] = []
        lines: list[tuple[int, int]] = []
        cursor = 0
        for index, char in enumerate(self.folded):
            if char == "\n":
                lines.append((cursor, index))
                cursor = index + 1
        lines.append((cursor, len(self.folded)))
        header: list[tuple[_Attribute | None, list[_Condition]]] | None = None
        previous_table_line: tuple[int, int] | None = None
        for line_start, line_end in lines:
            stripped = self.folded[line_start:line_end].strip()
            if stripped.startswith("|"):
                if _TABLE_SEPARATOR.match(stripped):
                    if previous_table_line is not None:
                        header = self._header(*previous_table_line)
                    previous_table_line = None
                    continue
                clauses = self._split_clauses(line_start, line_end, table=True)
                sentences.append(_Sentence(line_start, line_end, clauses, False, header))
                previous_table_line = (line_start, line_end)
                continue
            header = None
            previous_table_line = None
            sentence_start = line_start
            for index in range(line_start, line_end + 1):
                if index < line_end and self.folded[index] not in SENTENCE_ENDS:
                    continue
                end = index
                if self.folded[sentence_start:end].strip():
                    question = index < line_end and self.folded[index] == "?"
                    clauses = self._split_clauses(sentence_start, end, table=False)
                    sentences.append(_Sentence(sentence_start, end, clauses, question))
                sentence_start = index + 1
        return sentences

    def _header(self, start: int, end: int) -> list[tuple[_Attribute | None, list[_Condition]]]:
        columns: list[tuple[_Attribute | None, list[_Condition]]] = []
        for clause in self._split_clauses(start, end, table=True):
            attributes = self._within(self.attributes, clause.start, clause.end)
            conditions = self._within(self.conditions, clause.start, clause.end)
            while len(columns) < (clause.column or 0):
                columns.append((None, []))
            columns.append((attributes[-1] if attributes else None, conditions))
        return columns

    @staticmethod
    def _within(items: list[Any], start: int, end: int) -> list[Any]:
        return [item for item in items if start <= item.start and item.end <= end]

    # ------------------------------------------------------------ 值、属性、实体、条件

    def _values(self, clause: _Clause) -> list[_Value]:
        quantities = [
            match
            for match in self.quantities
            if clause.start <= match.start and match.end <= clause.end
        ]
        values: list[_Value] = []
        index = 0
        while index < len(quantities):
            group = [quantities[index]]
            while index + 1 < len(quantities):
                gap = self.folded[group[-1].end : quantities[index + 1].start].strip()
                dimensions = {item.quantity.dimension for item in group}
                nxt = quantities[index + 1].quantity
                if (
                    gap in COMPOUND_CONNECTORS
                    and nxt.dimension is not None
                    and None not in dimensions
                    and nxt.dimension not in dimensions
                ):
                    group.append(quantities[index + 1])
                    index += 1
                    continue
                break
            values.append(
                _Value(
                    group[0].start,
                    group[-1].end,
                    "compound" if len(group) > 1 else "quantity",
                    tuple(item.quantity for item in group),
                )
            )
            index += 1
        values.extend(self._within(self.dates, clause.start, clause.end))
        values.extend(self._within(self.codes, clause.start, clause.end))
        return sorted(values, key=lambda item: item.start)

    def _value_between(self, start: int, end: int, clause: _Clause) -> bool:
        return any(start <= item.start and item.end <= end for item in self._values(clause))

    def _compatible(self, concept: Concept, value: _Value) -> bool:
        if value.kind == "date":
            return concept.kind == "date"
        if value.kind == "model":
            return concept.kind == "model"
        if concept.kind != "numeric":
            return False
        if None in concept.dimensions or not concept.dimensions:
            return True
        return all(
            quantity.dimension is None or quantity.dimension in concept.dimensions
            for quantity in value.quantities
        )

    def _elliptical(self, clause: _Clause, value: _Value) -> bool:
        """小句在值之前除实体、条件、虚词和标点外没有别的内容。"""
        chars = list(self.folded[clause.start : value.start])
        spans = [(item.start, item.end) for item in self.entities]
        spans.extend((item.start, item.end) for item in self.conditions)
        for item_start, item_end in spans:
            for index in range(max(item_start, clause.start), min(item_end, value.start)):
                chars[index - clause.start] = " "
        rest = "".join(chars)
        for word in FILLERS:
            rest = rest.replace(word, " ")
        return not any(char.isalnum() for char in rest)

    def _negated(self, clause: _Clause, value: _Value, previous_end: int) -> bool:
        window = self.folded[max(clause.start, previous_end) : value.start]
        return any(word in window for word in NEGATIONS)

    def _hypothetical(self, sentence: _Sentence, value: _Value) -> bool:
        window = self.folded[sentence.start : value.start]
        return any(word in window for word in HYPOTHETICALS)

    def _question_clause(self, clause: _Clause) -> bool:
        body = self.folded[clause.start : clause.end].strip(" .。!?)）")
        return bool(body) and (
            body[-1] in QUESTION_ENDINGS or any(word in body for word in QUESTION_WORDS)
        )

    def _entity_for(self, clause: _Clause, value: _Value) -> tuple[str | None, str, _Entity | None]:
        inside = self._within(self.entities, clause.start, clause.end)
        before = [item for item in inside if item.end <= value.start]
        if before:
            item = before[-1]
            return item.entity_id, item.resolution, item
        after = [item for item in inside if item.start >= value.end]
        if after:
            return after[0].entity_id, after[0].resolution, None
        brand: _Entity | None = None
        for item in reversed([entity for entity in self.entities if entity.end <= clause.start]):
            if item.entity_id is None:
                return None, "unknown", None
            if self.lex.is_product(item.entity_id):
                return item.entity_id, "context", None
            brand = brand or item
        if brand is not None:
            return brand.entity_id, "context", None
        return None, "none", None

    # ------------------------------------------------------------ 主流程

    def pairings(self) -> list[tuple[_Sentence, _Clause, _Pairing]]:
        result: list[tuple[_Sentence, _Clause, _Pairing]] = []
        for sentence in self.sentences:
            if sentence.question:
                continue
            carried: _Attribute | None = None
            carried_conditions: dict[str, _Condition] = {}
            for clause in sentence.clauses:
                attributes = self._within(self.attributes, clause.start, clause.end)
                values = self._values(clause)
                conditions = self._within(self.conditions, clause.start, clause.end)
                found = (
                    []
                    if self._question_clause(clause)
                    else self._pair_clause(sentence, clause, values, attributes, carried)
                )
                self._assign_conditions(clause, found, conditions, carried_conditions, sentence)
                result.extend((sentence, clause, pairing) for pairing in found)
                if attributes:
                    carried = attributes[-1]
                if not values:
                    for condition in conditions:
                        carried_conditions[condition.key] = condition
        return result

    def _pair_clause(
        self,
        sentence: _Sentence,
        clause: _Clause,
        values: list[_Value],
        attributes: list[_Attribute],
        carried: _Attribute | None,
    ) -> list[_Pairing]:
        found: list[_Pairing] = []
        closed: set[int] = set()
        previous_end = clause.start
        for value in values:
            pairing = self._pair_value(sentence, clause, value, attributes, carried, closed)
            negated = self._negated(clause, value, previous_end)
            previous_end = value.end
            if pairing is None or negated or self._hypothetical(sentence, value):
                continue
            found.append(pairing)
        return found

    def _pair_value(
        self,
        sentence: _Sentence,
        clause: _Clause,
        value: _Value,
        attributes: list[_Attribute],
        carried: _Attribute | None,
        closed: set[int],
    ) -> _Pairing | None:
        def tight_ok(attribute: _Attribute) -> bool:
            if not attribute.tight:
                return True
            gap = self.folded[attribute.end : value.start]
            return attribute.end <= value.start and len(gap.strip()) == 0

        bare = value.kind == "quantity" and all(
            quantity.unit is None and not quantity.scaled for quantity in value.quantities
        )
        preceding = [
            item
            for item in attributes
            if item.end <= value.start and item.start not in closed and tight_ok(item)
        ]
        if bare and preceding and self._value_between(preceding[-1].end, value.start, clause):
            # 不带单位的裸数字只认紧跟在属性词后面的那一个（“续航 620，1 次充电”里的 1 不算）。
            return None
        following = [
            item
            for item in attributes
            if item.start >= value.start and item not in preceding and not item.tight and not bare
        ]
        choice: _Attribute | None = None
        source = "clause"
        if preceding:
            choice = preceding[-1]
        elif following:
            choice = following[0]
            closed.add(choice.start)
        elif (
            sentence.header is not None
            and clause.column is not None
            and clause.column < len(sentence.header)
        ):
            choice = sentence.header[clause.column][0]
            source = "header"
        if choice is None and carried is not None and self._elliptical(clause, value):
            choice = carried
            source = "inherited"
        if choice is None:
            return None
        concept = self.lex.concepts[choice.concept]
        if not self._compatible(concept, value):
            return None
        extra: list[_Condition] = []
        if source == "header" and sentence.header is not None and clause.column is not None:
            extra = list(sentence.header[clause.column][1])
        return _Pairing(value, choice, concept, source, source == "clause", extra_conditions=extra)

    def _assign_conditions(
        self,
        clause: _Clause,
        found: list[_Pairing],
        conditions: list[_Condition],
        carried: dict[str, _Condition],
        sentence: _Sentence,
    ) -> None:
        if not found:
            return
        respectively = "分别" in self.folded[clause.start : clause.end] and len(found) >= 2
        if respectively:
            first = found[0].value.start
            named = self._within(self.entities, clause.start, first)
            if len(named) == len(found):
                for pairing, entity in zip(found, named, strict=True):
                    pairing.entity = entity
        keys = sorted({item.key for item in conditions})
        for key in keys:
            mentions = [item for item in conditions if item.key == key]
            if respectively and len(mentions) == len(found):
                for pairing, mention in zip(found, mentions, strict=True):
                    pairing.conditions[key] = mention
                continue
            if len(mentions) == 1:
                for pairing in found:
                    pairing.conditions[key] = mentions[0]
                continue
            for mention in mentions:
                nearest = min(found, key=lambda item: _gap(item.value, mention))
                nearest.conditions.setdefault(key, mention)
        for pairing in found:
            for mention in pairing.extra_conditions:
                pairing.conditions.setdefault(mention.key, mention)
            for key, mention in carried.items():
                if key not in keys:
                    pairing.conditions.setdefault(key, mention)


def _gap(value: _Value, condition: _Condition) -> int:
    if condition.end <= value.start:
        return value.start - condition.end
    if condition.start >= value.end:
        return condition.start - value.end
    return 0


def _value_fields(
    document: _Document, value: _Value
) -> tuple[Any, str | None, str, dict[str, Any]]:
    """（value, unit, claim_type, ext 片段）。"""
    if value.kind == "date":
        assert value.date is not None
        date = value.date
        text = date.iso() if date.month is not None else f"{date.year:04d}年"
        return text, None, "date", {"date": {"iso": date.iso(), "precision": date.precision}}
    original = document.text[slice(*document.view.span(value.start, value.end))]
    if value.kind == "model":
        return original, None, "model", {"model_key": compact(original)}
    if value.kind == "compound":
        return (
            original,
            None,
            "numeric",
            {"components": [quantity_ext(item) for item in value.quantities]},
        )
    quantity = value.quantities[0]
    unit = None if quantity.unit is None else quantity.unit.symbol
    if quantity.qualifier == "range":
        assert quantity.upper is not None
        number: Any = {"min": json_number(quantity.value), "max": json_number(quantity.upper)}
    else:
        number = json_number(quantity.value)
    return number, unit, "numeric", {"quantity": quantity_ext(quantity)}


def extract_claims(
    text: str,
    *,
    lexicon: Lexicon,
    source: Mapping[str, str],
    project_id: str | None = None,
    synthetic: bool = False,
) -> list[dict[str, Any]]:
    """从一段回答或稿件里拆出陈述（claim 契约记录），按原文位置排序。"""
    if not text.strip():
        return []
    document = _Document(text, lexicon)
    claims: list[dict[str, Any]] = []
    for sentence, clause, pairing in document.pairings():
        claims.append(_record(document, sentence, clause, pairing, source, project_id, synthetic))
    claims.sort(key=lambda item: (item["span"]["start"], item["span"]["end"], item["claim_id"]))
    return claims


def _record(
    document: _Document,
    sentence: _Sentence,
    clause: _Clause,
    pairing: _Pairing,
    source: Mapping[str, str],
    project_id: str | None,
    synthetic: bool,
) -> dict[str, Any]:
    value = pairing.value
    mention: _Entity | None
    if pairing.entity is not None:
        mention = pairing.entity
        entity_id, resolution = mention.entity_id, mention.resolution
    else:
        entity_id, resolution, mention = document._entity_for(clause, value)
    start, end = value.start, value.end
    if pairing.in_clause:
        start = min(start, pairing.attribute.start)
        end = max(end, pairing.attribute.end)
    if mention is not None:
        start = min(start, mention.start)
    conditions: dict[str, str] = {}
    ext: dict[str, Any] = {}
    for key, condition in sorted(pairing.conditions.items()):
        if key == MODEL_YEAR and MODEL_YEAR not in document.lex.condition_keys:
            ext["model_year"] = condition.value
        else:
            conditions[key] = condition.value
        if clause.start <= condition.start and condition.end <= clause.end:
            start = min(start, condition.start)
            end = max(end, condition.end)
    segment = document.folded[start:end]
    if segment.count("(") > segment.count(")"):
        closing = document.folded.find(")", end, clause.end)
        if closing != -1:
            end = closing + 1
    original_start, original_end = document.view.span(start, end)
    number, unit, claim_type, value_ext = _value_fields(document, value)
    ext.update(value_ext)
    ext["entity_resolution"] = resolution
    ext["attribute_source"] = pairing.source
    attribute_start, attribute_end = document.view.span(
        pairing.attribute.start, pairing.attribute.end
    )
    ext["attribute_mention"] = document.text[attribute_start:attribute_end]
    sentence_start, sentence_end = document.view.span(
        sentence.start, max(sentence.start + 1, sentence.end)
    )
    ext["sentence"] = {"start": sentence_start, "end": sentence_end}
    attribute = pairing.concept.name_for(entity_id)
    claim_text = document.text[original_start:original_end]
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "claim_id": "",
        "source": {"type": source["type"], "id": source["id"]},
        "text": claim_text,
        "span": {"start": original_start, "end": original_end},
        "entity_id": entity_id,
        "attribute": attribute,
        "value": number,
        "unit": unit,
        "conditions": conditions,
        "claim_type": claim_type,
        "extractor": dict(EXTRACTOR),
        "ext": ext,
    }
    if project_id is not None:
        record["project_id"] = project_id
    if synthetic:
        record["synthetic"] = True
    record["claim_id"] = (
        "clm-"
        + hash_obj(
            {
                "source": record["source"],
                "span": record["span"],
                "entity_id": entity_id,
                "attribute": attribute,
                "value": number,
                "unit": unit,
                "conditions": conditions,
                "claim_type": claim_type,
                "extractor": EXTRACTOR,
            }
        )[:32]
    )
    return record
