"""G-HIDDEN-TEXT：隐藏文本、零宽字符、CSS 隐藏、HTML 注释里的文本（block）。

判定规则（控制误伤）：
1. 不可见字符：零宽字符、双向控制字符、Unicode 标签字符（含 HTML 字符引用写出的），
   连续的算一处；正常用法（见 invisible 模块：文件头 BOM、emoji ZWJ 序列、连写文字、
   旗帜标签序列、从右到左文字旁的方向符、西文单词里的软连字符）不算。
2. 隐藏容器（hidden 属性、template、display:none 等 CSS 隐藏，见 css 模块）只有含“实质性文本”
   才算：容器内某一段连续文字的内容量 ≥ HIDDEN_MIN_UNITS；位于链接、按钮、菜单等导航元素里的
   文字要 ≥ NAV_MIN_UNITS 才算（移动端折叠菜单、关闭按钮之类不拦）；只有图片、图标、空白的
   装饰元素不算。隐藏容器里出现针对模型的指令时，不论长短都算。
   内容量 = 汉字（及假名、谚文）个数 + 2 × 拉丁字母 / 数字串个数。
3. aria-hidden 只对读屏软件隐藏、肉眼可见，只在其中含针对模型的指令时才算。
4. HTML 注释（含 Markdown 里的）：内容量 ≥ COMMENT_MIN_UNITS，或含针对模型的指令，才算；
   `<!--[if IE]>` 这类条件注释不算。
5. 代码块（`<pre>` / `<code>` / Markdown 代码围栏与行内代码）里的文字是展示用，不参与 2～4；
   但代码块本身被隐藏时按隐藏容器处理。

span：不可见字符为该串字符；隐藏容器为其中文字的首尾范围；注释为整段注释。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence

from geo_facts.gate.document import COMMENT, HIDDEN, VISIBLE, Draft, Piece, build_index
from geo_facts.gate.instructions import contains_instruction
from geo_facts.gate.invisible import (
    TAG_CHARACTER,
    decode_tags,
    describe,
    is_legitimate,
    kind_of,
)
from geo_facts.gate.meta import Meta
from geo_facts.gate.model import (
    CLAUSE_SELF_DEFINED,
    RULE_HIDDEN_TEXT,
    Check,
    Finding,
    GateContext,
)

HIDDEN_MIN_UNITS = 8
NAV_MIN_UNITS = 16
COMMENT_MIN_UNITS = 20

SUGGESTION = "删除隐藏内容，或改为对读者可见的正文"

_KIND_LABELS = {
    "zero_width": "零宽字符",
    "bidi_control": "双向控制字符",
    "tag_character": "Unicode 标签字符",
}
_LATIN_RUN = re.compile(
    "[A-Za-z0-9"
    + "".join(
        f"{chr(low)}-{chr(high)}" for low, high in ((0xC0, 0x24F), (0x370, 0x3FF), (0x400, 0x4FF))
    )
    + "]+"
)


def _is_cjk_unit(char: str) -> bool:
    code = ord(char)
    return (
        0x3040 <= code <= 0x30FF
        or 0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xAC00 <= code <= 0xD7AF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x3FFFF
    )


def content_units(text: str) -> int:
    """文字内容量：汉字等表意 / 音节文字每字 1，拉丁等字母数字串每串 2。"""
    return sum(1 for char in text if _is_cjk_unit(char)) + 2 * len(_LATIN_RUN.findall(text))


# ------------------------------------------------------------------ 不可见字符


def _invisible_positions(draft: Draft) -> list[tuple[int, int, str]]:
    """(原文起点, 原文终点, 字符)：原文中直接出现的，以及字符引用解码出来的。"""
    raw = draft.raw
    found: dict[tuple[int, int], str] = {}
    for index, char in enumerate(raw):
        if kind_of(char) is not None and not is_legitimate(raw, index):
            found[(index, index + 1)] = char
    for piece in draft.pieces:
        for offset, (char, start, end) in enumerate(
            zip(piece.text, piece.starts, piece.ends, strict=True)
        ):
            if (
                end - start > 1
                and kind_of(char) is not None
                and not is_legitimate(piece.text, offset)
            ):
                found.setdefault((start, end), char)
    return sorted((start, end, char) for (start, end), char in found.items())


def _invisible_findings(draft: Draft) -> list[Finding]:
    runs: list[list[tuple[int, int, str]]] = []
    for item in _invisible_positions(draft):
        if runs and runs[-1][-1][1] >= item[0]:
            runs[-1].append(item)
        else:
            runs.append([item])
    findings: list[Finding] = []
    for run in runs:
        chars = "".join(char for _, _, char in run)
        kinds = sorted({kind_of(char) or "" for char in chars})
        counts = Counter(describe(char) for char in chars)
        listing = "、".join(f"{name}×{count}" for name, count in sorted(counts.items()))
        labels = "、".join(_KIND_LABELS[kind] for kind in kinds)
        details: dict[str, object] = {
            "kind": "invisible_characters",
            "categories": kinds,
            "codepoints": sorted({f"U+{ord(char):04X}" for char in chars}),
            "count": len(chars),
        }
        if TAG_CHARACTER in kinds:
            details["decoded_tag_text"] = decode_tags(chars)
        findings.append(
            Finding.at(
                draft.raw,
                RULE_HIDDEN_TEXT,
                (run[0][0], run[-1][1]),
                reason=f"含肉眼不可见的{labels}：{listing}",
                suggestion="删除不可见字符",
                clause=CLAUSE_SELF_DEFINED,
                details=details,
            )
        )
    return findings


# ------------------------------------------------------------------ 隐藏容器与注释


def _text_span(pieces: Sequence[Piece]) -> tuple[int, int] | None:
    starts: list[int] = []
    ends: list[int] = []
    for piece in pieces:
        for char, start, end in zip(piece.text, piece.starts, piece.ends, strict=True):
            if not char.isspace():
                starts.append(start)
                ends.append(end)
    if not starts:
        return None
    return min(starts), max(ends)


def _max_run_units(pieces: Sequence[Piece]) -> tuple[int, bool]:
    """同一隐藏容器内各段连续文字的最大内容量，以及它是否达到门槛。"""
    runs: dict[tuple[int, bool], list[str]] = defaultdict(list)
    for piece in pieces:
        runs[(piece.run, piece.nav)].append(piece.text)
    best, substantive = 0, False
    for (_, nav), texts in runs.items():
        units = content_units("".join(texts))
        best = max(best, units)
        if units >= (NAV_MIN_UNITS if nav else HIDDEN_MIN_UNITS):
            substantive = True
    return best, substantive


def _group_findings(draft: Draft) -> list[Finding]:
    findings: list[Finding] = []
    groups: dict[int, list[tuple[int, Piece]]] = defaultdict(list)
    for owner, piece in draft.select([HIDDEN]):
        groups[piece.group].append((owner, piece))
    for group, members in sorted(groups.items()):
        pieces = [piece for _, piece in members]
        span = _text_span(pieces)
        if span is None:
            continue
        instruction = contains_instruction(build_index(members), draft)
        units, substantive = _max_run_units(pieces)
        if not (substantive or instruction):
            continue
        hidden_by = pieces[0].hidden_by
        what = "针对模型的指令" if instruction else "实质性文本"
        findings.append(
            Finding.at(
                draft.raw,
                RULE_HIDDEN_TEXT,
                span,
                reason=f"隐藏容器（{hidden_by}）内含{what}，读者看不到而模型可能读到",
                suggestion=SUGGESTION,
                clause=CLAUSE_SELF_DEFINED,
                details={
                    "kind": "hidden_container",
                    "hidden_by": hidden_by,
                    "tag": pieces[0].tag,
                    "element_span": list(draft.group_spans.get(group, span)),
                    "content_units": units,
                    "has_instruction": instruction,
                },
            )
        )
    aria: dict[int, list[tuple[int, Piece]]] = defaultdict(list)
    for owner, piece in draft.select([VISIBLE]):
        if piece.aria_group:
            aria[piece.aria_group].append((owner, piece))
    for group, members in sorted(aria.items()):
        span = _text_span([piece for _, piece in members])
        if span is None or not contains_instruction(build_index(members), draft):
            continue
        findings.append(
            Finding.at(
                draft.raw,
                RULE_HIDDEN_TEXT,
                span,
                reason="aria-hidden 区域内含针对模型的指令性文本",
                suggestion=SUGGESTION,
                clause=CLAUSE_SELF_DEFINED,
                details={
                    "kind": "hidden_container",
                    "hidden_by": "aria-hidden",
                    "tag": members[0][1].tag,
                    "element_span": list(draft.group_spans.get(group, span)),
                    "content_units": content_units("".join(p.text for _, p in members)),
                    "has_instruction": True,
                },
            )
        )
    for owner, piece in draft.select([COMMENT]):
        body = piece.text.strip()
        if not body or body.lower().startswith(("[if", "<![endif")):
            continue
        units = content_units(body)
        instruction = contains_instruction(build_index([(owner, piece)]), draft)
        if units < COMMENT_MIN_UNITS and not instruction:
            continue
        what = "针对模型的指令" if instruction else "实质性文本"
        span = draft.group_spans[piece.group]
        findings.append(
            Finding.at(
                draft.raw,
                RULE_HIDDEN_TEXT,
                span,
                reason=f"HTML 注释内含{what}，读者看不到而模型可能读到",
                suggestion=SUGGESTION,
                clause=CLAUSE_SELF_DEFINED,
                details={
                    "kind": "comment",
                    "hidden_by": "HTML 注释",
                    "content_units": units,
                    "has_instruction": instruction,
                },
            )
        )
    return findings


def run(draft: Draft, meta: Meta | None, ctx: GateContext) -> list[Finding]:
    return _invisible_findings(draft) + _group_findings(draft)


CHECK = Check(
    name="hidden_text",
    rule_ids=(RULE_HIDDEN_TEXT,),
    summary="隐藏文本、零宽字符、CSS 隐藏、HTML 注释里的文本",
    run=run,
)
