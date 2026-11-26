"""稿件解析：原文，以及“可见文本 / 隐藏文本 / 代码 / 注释 / 脚本 / 属性”的划分。

所有位置都是**原文**（文件按 UTF-8 解码后的字符串，不做换行转换）的 Unicode 码点下标。

- `.txt`：整篇是可见文本。
- `.html`：用标准库 html.parser 逐个事件解析（不转换字符引用，自己记录每个字符对应的原文区间），
  按元素的隐藏属性、行内 style、`<style>` 中的简单规则判断隐藏；`<pre>` / `<code>` / `<kbd>` /
  `<samp>` 中的是代码；`<script>` / `<style>` 中的是脚本；`<!-- -->` 是注释；alt、title、
  aria-label、content、placeholder、value、data-* 等属性值单独成一类。
- `.md`：Markdown 代码围栏（``` / ~~~）与行内代码是代码，引用行（`>`）标为引用；
  其余部分按 HTML 规则再解析一遍（Markdown 渲染时会原样输出内嵌 HTML，注释和隐藏元素同样生效）。

划分结果是一串 `Piece`（按原文顺序）。`Draft.index(...)` 把选定类别的片段拼成一段折叠文本
（NFKC + 小写，去掉不可见字符），并能把折叠文本上的任意区间映射回原文区间。
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from itertools import pairwise
from pathlib import Path

from geo_facts.errors import InputError
from geo_facts.gate import css
from geo_facts.gate.invisible import is_hidden_char
from geo_facts.normalize.text import HYPHENS, fold_char

VISIBLE = "visible"
HIDDEN = "hidden"
CODE = "code"
COMMENT = "comment"
SCRIPT = "script"
ATTRIBUTE = "attribute"
MARKUP = "markup"  # 解析器没有消费的残余原文（如文末未闭合的标签）

FORMATS: dict[str, str] = {
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
    ".html": "html",
    ".htm": "html",
}

VOID_TAGS = frozenset(
    [
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "keygen",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    ]
)
BLOCK_TAGS = frozenset(
    [
        "address",
        "article",
        "aside",
        "blockquote",
        "body",
        "br",
        "caption",
        "dd",
        "details",
        "dialog",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "head",
        "header",
        "hgroup",
        "hr",
        "html",
        "legend",
        "li",
        "main",
        "menu",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "select",
        "summary",
        "table",
        "tbody",
        "td",
        "textarea",
        "tfoot",
        "th",
        "thead",
        "title",
        "tr",
        "ul",
        "script",
        "style",
        "template",
        "option",
    ]
)
# 导航 / 交互类元素：里面的短文本（菜单项、按钮文字）不算“实质性文本”。
NAV_TAGS = frozenset(
    ["a", "button", "label", "menu", "menuitem", "nav", "option", "select", "summary"]
)
CODE_TAGS = frozenset(["pre", "code", "kbd", "samp", "xmp", "listing", "plaintext"])
SCRIPT_TAGS = frozenset({"script", "style"})
QUOTE_TAGS = frozenset({"blockquote", "q"})
FOREIGN_TAGS = frozenset({"svg", "math"})
TEXT_ATTRIBUTES = frozenset(
    [
        "alt",
        "title",
        "aria-label",
        "aria-description",
        "aria-roledescription",
        "placeholder",
        "content",
        "value",
        "label",
        "summary",
        "abbr",
    ]
)
# p 被这些块级元素的开始标签隐式关闭。
_CLOSES_P = frozenset(
    [
        "address",
        "article",
        "aside",
        "blockquote",
        "details",
        "div",
        "dl",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hgroup",
        "hr",
        "main",
        "menu",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "ul",
    ]
)
_P_SCOPE = frozenset(
    [
        "div",
        "section",
        "article",
        "aside",
        "main",
        "header",
        "footer",
        "nav",
        "li",
        "td",
        "th",
        "blockquote",
        "body",
        "html",
    ]
)


@dataclass(frozen=True)
class Piece:
    """一段解码后的文本及其每个字符对应的原文区间。"""

    text: str
    starts: tuple[int, ...]
    ends: tuple[int, ...]
    channel: str
    block: int
    run: int
    group: int = 0
    hidden_by: str = ""
    aria_group: int = 0
    quoted: bool = False
    nav: bool = False
    tag: str = ""

    @property
    def start(self) -> int:
        return self.starts[0]

    @property
    def end(self) -> int:
        return self.ends[-1]


@dataclass(frozen=True)
class TextIndex:
    """折叠后的可检索文本。owners[i] 是该字符所属片段在 Draft.pieces 中的下标，-1 表示分隔符。"""

    text: str
    raw_starts: tuple[int, ...]
    raw_ends: tuple[int, ...]
    owners: tuple[int, ...]

    def raw_span(self, start: int, end: int) -> tuple[int, int] | None:
        """折叠文本区间 [start, end) → 原文区间；区间内只有分隔符时返回 None。"""
        first = next((i for i in range(start, end) if self.owners[i] >= 0), None)
        if first is None:
            return None
        last = next(i for i in range(end - 1, start - 1, -1) if self.owners[i] >= 0)
        return self.raw_starts[first], self.raw_ends[last]


@dataclass(frozen=True)
class Draft:
    raw: str
    fmt: str
    pieces: tuple[Piece, ...]
    name: str = ""
    group_spans: dict[int, tuple[int, int]] = field(default_factory=dict)

    def select(self, channels: Iterable[str]) -> list[tuple[int, Piece]]:
        wanted = set(channels)
        return [(i, piece) for i, piece in enumerate(self.pieces) if piece.channel in wanted]

    def _breaks(self, selected: Sequence[tuple[int, Piece]]) -> list[bool]:
        """selected[k] 之前是否要断开：块、类别、分组不同，或中间夹着被跳过的可见内容。"""
        flags: list[bool] = []
        for position, (owner, piece) in enumerate(selected):
            if position == 0:
                flags.append(False)
                continue
            previous_owner, previous = selected[position - 1]
            skipped = self.pieces[previous_owner + 1 : owner]
            flags.append(
                _boundary(previous, piece)
                or any(item.channel in (VISIBLE, CODE) for item in skipped)
            )
        return flags

    def text_of(self, *channels: str) -> str:
        """选定类别的解码文本；不同块之间用换行分开。"""
        selected = self.select(channels)
        out: list[str] = []
        for (_, piece), cut in zip(selected, self._breaks(selected), strict=True):
            if cut:
                out.append("\n")
            out.append(piece.text)
        return "".join(out)

    def index(self, *channels: str, compact: bool = False) -> TextIndex:
        selected = self.select(channels)
        return build_index(selected, compact=compact, breaks=self._breaks(selected))


def _boundary(previous: Piece, piece: Piece) -> bool:
    return (
        piece.block != previous.block
        or piece.channel != previous.channel
        or piece.group != previous.group
    )


def build_index(
    pieces: Sequence[tuple[int, Piece]],
    *,
    compact: bool = False,
    breaks: Sequence[bool] | None = None,
) -> TextIndex:
    """把片段拼成折叠文本：NFKC + 小写；去掉不可见字符；普通模式下空白统一成空格、
    换行保留；紧凑模式下去掉空白与连字符。不同块之间插入一个不属于任何片段的换行
    （breaks 未给出时按块、类别、分组判断）。"""
    chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    owners: list[int] = []
    previous: Piece | None = None
    for position, (owner, piece) in enumerate(pieces):
        cut = (
            breaks[position]
            if breaks is not None
            else (previous is not None and _boundary(previous, piece))
        )
        if cut:
            chars.append("\n")
            starts.append(piece.start)
            ends.append(piece.start)
            owners.append(-1)
        previous = piece
        for char, start, end in zip(piece.text, piece.starts, piece.ends, strict=True):
            if is_hidden_char(char):
                continue
            if char in "\r\n":
                if not compact:
                    chars.append("\n")
                    starts.append(start)
                    ends.append(end)
                    owners.append(owner)
                continue
            for folded in fold_char(char):
                if folded.isspace():
                    if compact:
                        continue
                    folded = " "
                elif compact and folded in HYPHENS:
                    continue
                chars.append(folded)
                starts.append(start)
                ends.append(end)
                owners.append(owner)
    return TextIndex("".join(chars), tuple(starts), tuple(ends), tuple(owners))


# ------------------------------------------------------------------ 读取


def format_of(path: Path) -> str:
    fmt = FORMATS.get(path.suffix.lower())
    if fmt is None:
        raise InputError(
            f"不支持的稿件格式：{path.name}；只支持 .md / .txt / .html",
            details={"file": path.name},
        )
    return fmt


def read_text(path: Path, what: str = "稿件") -> str:
    """按 UTF-8 读取原文，不做换行转换（span 以此为准）。"""
    if not path.is_file():
        raise InputError(f"{what}不存在：{path.name}", details={"file": path.name})
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError(f"{what}不是合法的 UTF-8 文本：{path.name}") from exc


def load_draft(path: Path) -> Draft:
    fmt = format_of(path)
    return replace(parse_draft(read_text(path), fmt), name=path.name)


def parse_draft(raw: str, fmt: str) -> Draft:
    if fmt == "txt":
        pieces: tuple[Piece, ...] = ()
        if raw:
            pieces = (
                Piece(raw, tuple(range(len(raw))), tuple(range(1, len(raw) + 1)), VISIBLE, 0, 0),
            )
        return Draft(raw=raw, fmt=fmt, pieces=pieces)
    if fmt == "html":
        return _parse_markup(raw, fmt, code_ranges=(), quote_ranges=())
    if fmt == "md":
        code_ranges = markdown_code_ranges(raw)
        return _parse_markup(raw, fmt, code_ranges, markdown_quote_ranges(raw, code_ranges))
    raise InputError(f"未知稿件格式：{fmt}")


# ------------------------------------------------------------------ Markdown

_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _lines(raw: str) -> list[tuple[int, int]]:
    """每行的 [起点, 终点)（终点含换行符）。"""
    out: list[tuple[int, int]] = []
    position = 0
    while position < len(raw):
        newline = raw.find("\n", position)
        end = len(raw) if newline == -1 else newline + 1
        out.append((position, end))
        position = end
    return out


def _inline_code(raw: str, start: int, end: int) -> list[tuple[int, int]]:
    """[start, end) 内的行内代码：等长反引号串包围，且不跨空行。"""
    out: list[tuple[int, int]] = []
    position = start
    while position < end:
        tick = raw.find("`", position, end)
        if tick == -1:
            break
        run_end = tick
        while run_end < end and raw[run_end] == "`":
            run_end += 1
        length = run_end - tick
        search = run_end
        closing = -1
        while search < end:
            candidate = raw.find("`" * length, search, end)
            if candidate == -1:
                break
            after = candidate + length
            if raw[candidate - 1] != "`" and (after >= end or raw[after] != "`"):
                closing = candidate
                break
            while candidate < end and raw[candidate] == "`":
                candidate += 1
            search = candidate
        if closing == -1 or "\n\n" in raw[run_end:closing]:
            position = run_end
            continue
        out.append((tick, closing + length))
        position = closing + length
    return out


def markdown_code_ranges(raw: str) -> tuple[tuple[int, int], ...]:
    """代码围栏（含围栏行本身；未闭合则到文末）与行内代码的原文区间。"""
    ranges: list[tuple[int, int]] = []
    fence: str | None = None
    fence_start = 0
    text_start = 0
    for line_start, line_end in _lines(raw):
        line = raw[line_start:line_end].rstrip("\r\n")
        if fence is None:
            match = _FENCE_OPEN.match(line)
            if match:
                ranges.extend(_inline_code(raw, text_start, line_start))
                fence = match.group(1)
                fence_start = line_start
        else:
            stripped = line.strip()
            if (
                len(line) - len(line.lstrip(" ")) <= 3
                and stripped
                and set(stripped) == {fence[0]}
                and len(stripped) >= len(fence)
            ):
                ranges.append((fence_start, line_end))
                fence = None
                text_start = line_end
    if fence is not None:
        ranges.append((fence_start, len(raw)))
    else:
        ranges.extend(_inline_code(raw, text_start, len(raw)))
    return tuple(sorted(ranges))


def markdown_quote_ranges(
    raw: str, code_ranges: Sequence[tuple[int, int]]
) -> tuple[tuple[int, int], ...]:
    """引用行（行首 0～3 个空格后是 `>`）的原文区间；代码围栏里的不算。"""
    out: list[tuple[int, int]] = []
    for line_start, line_end in _lines(raw):
        if any(start <= line_start < end for start, end in code_ranges):
            continue
        if re.match(r"^ {0,3}>", raw[line_start:line_end]):
            out.append((line_start, line_end))
    return tuple(out)


# ------------------------------------------------------------------ HTML


@dataclass(frozen=True)
class _State:
    tag: str
    start: int = 0
    hidden_by: str = ""
    group: int = 0
    aria_group: int = 0
    code: bool = False
    script: bool = False
    quoted: bool = False
    nav: bool = False
    foreign: bool = False
    color: css.Color | None = None
    background: css.Color | None = None


_ROOT = _State(tag="#root")


class _StyleCollector(HTMLParser):
    """第一遍：收集所有 `<style>` 的内容（样式表可能写在被作用的元素之后）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.in_style = False
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.in_style = tag == "style"

    def handle_endtag(self, tag: str) -> None:
        self.in_style = False

    def handle_data(self, data: str) -> None:
        if self.in_style:
            self.chunks.append(data)


class _Builder(HTMLParser):
    def __init__(
        self,
        source: str,
        masked: str,
        rules: Sequence[css.Rule],
        code_ranges: Sequence[tuple[int, int]],
        quote_ranges: Sequence[tuple[int, int]],
    ) -> None:
        super().__init__(convert_charrefs=False)
        self.source = source
        self.masked = masked
        self.rules = rules
        self.code_ranges = code_ranges
        self.quote_ranges = quote_ranges
        self.line_starts = [0] + [i + 1 for i, char in enumerate(masked) if char == "\n"]
        self.stack: list[_State] = []
        self.pieces: list[Piece] = []
        self.group_spans: dict[int, tuple[int, int]] = {}
        self.block = 0
        self.run = 0
        self.next_group = 1
        self.cursor = 0
        self.pending_ref: tuple[int, _State] | None = None
        boundaries = {0, len(source)}
        for start, end in (*code_ranges, *quote_ranges):
            boundaries.update((start, end))
        self.boundaries = sorted(boundaries)

    # ---------------------------------------------------------- 位置
    def position(self) -> int:
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def top(self) -> _State:
        return self.stack[-1] if self.stack else _ROOT

    def new_group(self) -> int:
        group = self.next_group
        self.next_group += 1
        return group

    def advance(self, end: int) -> None:
        self.cursor = max(self.cursor, end)

    def flush(self, here: int) -> None:
        """把上一个字符引用按 [起点, 当前事件起点) 落成片段。"""
        if self.pending_ref is None:
            return
        start, state = self.pending_ref
        self.pending_ref = None
        raw = self.source[start:here]
        decoded = html.unescape(raw)
        if decoded == raw:
            self.add_text(start, raw, state)
            return
        self.emit(decoded, [start] * len(decoded), [here] * len(decoded), state, start)
        self.advance(here)

    # ---------------------------------------------------------- 片段
    def channel_for(self, state: _State, position: int) -> str:
        if state.script:
            return SCRIPT
        if state.hidden_by:
            return HIDDEN
        if state.code or any(start <= position < end for start, end in self.code_ranges):
            return CODE
        return VISIBLE

    def emit(
        self,
        text: str,
        starts: list[int],
        ends: list[int],
        state: _State,
        position: int,
        *,
        channel: str | None = None,
        group: int | None = None,
    ) -> None:
        if not text:
            return
        chosen = channel or self.channel_for(state, position)
        quoted = state.quoted or any(start <= position < end for start, end in self.quote_ranges)
        self.pieces.append(
            Piece(
                text=text,
                starts=tuple(starts),
                ends=tuple(ends),
                channel=chosen,
                block=self.block,
                run=self.run,
                group=state.group if group is None else group,
                hidden_by=state.hidden_by,
                aria_group=state.aria_group,
                quoted=quoted,
                nav=state.nav,
                tag=state.tag,
            )
        )

    def add_text(self, start: int, text: str, state: _State) -> None:
        """一段与原文逐字对应的文本；按代码区间、引用区间的边界切开。"""
        end = start + len(text)
        cuts = [point for point in self.boundaries if start < point < end]
        points = [start, *cuts, end]
        for left, right in pairwise(points):
            self.emit(
                self.source[left:right],
                list(range(left, right)),
                list(range(left + 1, right + 1)),
                state,
                left,
            )
        self.advance(end)

    # ---------------------------------------------------------- 元素
    def implicit_close(self, tag: str) -> None:
        def pop_to(name: str, scope: frozenset[str]) -> None:
            for depth in range(len(self.stack) - 1, -1, -1):
                current = self.stack[depth].tag
                if current == name:
                    self.close_to(depth)
                    return
                if current in scope:
                    return

        if tag in _CLOSES_P:
            pop_to("p", _P_SCOPE)
        if tag == "li":
            pop_to("li", frozenset({"ul", "ol", "menu"}))
        elif tag in ("dt", "dd"):
            pop_to("dt", frozenset({"dl"}))
            pop_to("dd", frozenset({"dl"}))
        elif tag == "option":
            pop_to("option", frozenset({"select", "datalist"}))
        elif tag == "tr":
            pop_to("tr", frozenset({"table", "tbody", "thead", "tfoot"}))
        elif tag in ("td", "th"):
            pop_to("td", frozenset({"tr", "table"}))
            pop_to("th", frozenset({"tr", "table"}))

    def close_to(self, depth: int, end: int | None = None) -> None:
        """弹出 stack[depth:]，记录各隐藏根元素的原文区间。"""
        finish = self.position() if end is None else end
        while len(self.stack) > depth:
            state = self.stack.pop()
            parent = self.top()
            if state.group and state.group != parent.group:
                self.group_spans[state.group] = (state.start, finish)
            if state.aria_group and state.aria_group != parent.aria_group:
                self.group_spans[state.aria_group] = (state.start, finish)

    def derive(self, parent: _State, tag: str, attrs: dict[str, str | None], start: int) -> _State:
        classes = frozenset((attrs.get("class") or "").split())
        decls = css.computed(self.rules, tag, classes, attrs.get("id"), attrs.get("style"))
        own_color = css.text_color(decls)
        if own_color is None and tag == "font":
            own_color = css.parse_color(attrs.get("color"))
        own_background = css.background_color(decls) or css.parse_color(attrs.get("bgcolor"))
        color = own_color or parent.color
        background = own_background or parent.background
        reason = ""
        if "hidden" in attrs:
            reason = "hidden 属性"
        elif tag == "template":
            reason = "template 元素"
        else:
            reason = css.hiding_reason(decls)
        if (
            not reason
            and (own_color is not None or own_background is not None)
            and color is not None
            and background is not None
            and css.same_color(color, background)
        ):
            reason = "文字与背景同色"
        hidden_by, group = parent.hidden_by, parent.group
        if not hidden_by and reason:
            hidden_by, group = reason, self.new_group()
        aria_group = parent.aria_group
        if not aria_group and (attrs.get("aria-hidden") or "").strip().lower() == "true":
            aria_group = self.new_group()
        return _State(
            tag=tag,
            start=start,
            hidden_by=hidden_by,
            group=group,
            aria_group=aria_group,
            code=parent.code or tag in CODE_TAGS,
            script=tag in SCRIPT_TAGS,
            quoted=parent.quoted or tag in QUOTE_TAGS,
            nav=parent.nav or tag in NAV_TAGS,
            foreign=parent.foreign or tag in FOREIGN_TAGS,
            color=color,
            background=background,
        )

    def attribute_pieces(
        self, start: int, text: str, attrs: list[tuple[str, str | None]], state: _State
    ) -> None:
        seen: set[str] = set()
        for name, value in attrs:
            if not value or name in seen:
                continue
            seen.add(name)
            if name not in TEXT_ATTRIBUTES and not name.startswith("data-"):
                continue
            pattern = re.compile(
                r"(?<![\w-])"
                + re.escape(name)
                + r"""\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))""",
                re.I,
            )
            match = pattern.search(text)
            if match is None:
                raw_start, raw_end, raw_value = start, start + len(text), None
            else:
                group = next(index for index in (1, 2, 3) if match.group(index) is not None)
                raw_start = start + match.start(group)
                raw_end = start + match.end(group)
                raw_value = match.group(group)
            if raw_value is not None and raw_value == value:
                starts = list(range(raw_start, raw_end))
                ends = list(range(raw_start + 1, raw_end + 1))
            else:
                starts = [raw_start] * len(value)
                ends = [raw_end] * len(value)
            group_id = self.new_group()
            self.group_spans[group_id] = (raw_start, raw_end)
            self.emit(value, starts, ends, state, raw_start, channel=ATTRIBUTE, group=group_id)

    def start_element(
        self, tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool
    ) -> None:
        here = self.position()
        self.flush(here)
        text = self.get_starttag_text() or ""
        self.implicit_close(tag)
        parent = self.top()
        self.attribute_pieces(here, text, attrs, parent)
        first: dict[str, str | None] = {}
        for name, value in attrs:
            first.setdefault(name, value)
        if tag in BLOCK_TAGS:
            self.block += 1
            self.run += 1
        elif tag in NAV_TAGS:
            self.run += 1
        if tag not in VOID_TAGS and not (self_closing and parent.foreign):
            self.stack.append(self.derive(parent, tag, first, here))
        self.advance(here + len(text))

    # ---------------------------------------------------------- 事件
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.start_element(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.start_element(tag, attrs, self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        here = self.position()
        self.flush(here)
        close = self.masked.find(">", here)
        end = len(self.masked) if close == -1 else close + 1
        for depth in range(len(self.stack) - 1, -1, -1):
            if self.stack[depth].tag == tag:
                self.close_to(depth, end)
                break
        if tag in BLOCK_TAGS:
            self.block += 1
            self.run += 1
        elif tag in NAV_TAGS:
            self.run += 1
        self.advance(end)

    def handle_data(self, data: str) -> None:
        here = self.position()
        self.flush(here)
        self.add_text(here, self.source[here : here + len(data)], self.top())

    def handle_entityref(self, name: str) -> None:
        here = self.position()
        self.flush(here)
        self.pending_ref = (here, self.top())

    def handle_charref(self, name: str) -> None:
        self.handle_entityref(name)

    def handle_comment(self, data: str) -> None:
        here = self.position()
        self.flush(here)
        content = here + 4
        if self.masked[content : content + len(data)] == data and self.masked.startswith(
            "<!--", here
        ):
            close = self.masked.find("-->", content + len(data))
            end = len(self.masked) if close == -1 else close + 3
            starts = list(range(content, content + len(data)))
            ends = list(range(content + 1, content + len(data) + 1))
        else:
            close = self.masked.find(">", here)
            end = len(self.masked) if close == -1 else close + 1
            starts, ends = [here] * len(data), [end] * len(data)
        group = self.new_group()
        self.group_spans[group] = (here, end)
        self.emit(
            self.source[content : content + len(data)] if starts and starts[0] == content else data,
            starts,
            ends,
            self.top(),
            here,
            channel=COMMENT,
            group=group,
        )
        self.advance(end)

    def _skip(self, terminator: str) -> None:
        here = self.position()
        self.flush(here)
        close = self.masked.find(terminator, here)
        self.advance(len(self.masked) if close == -1 else close + len(terminator))

    def handle_decl(self, decl: str) -> None:
        self._skip(">")

    def handle_pi(self, data: str) -> None:
        self._skip(">")

    def unknown_decl(self, data: str) -> None:
        self._skip("]]>" if data.upper().startswith("CDATA[") else ">")

    def finish(self) -> None:
        self.close()
        self.flush(len(self.source))
        self.close_to(0, len(self.source))
        if self.cursor < len(self.source) and self.source[self.cursor :].strip():
            tail = self.cursor
            self.block += 1
            group = self.new_group()
            self.group_spans[group] = (tail, len(self.source))
            self.emit(
                self.source[tail:],
                list(range(tail, len(self.source))),
                list(range(tail + 1, len(self.source) + 1)),
                _ROOT,
                tail,
                channel=MARKUP,
                group=group,
            )


def _mask(raw: str, ranges: Sequence[tuple[int, int]]) -> str:
    """把代码区间替换成等长空白（保留换行），让 HTML 解析器跳过代码里的尖括号。"""
    if not ranges:
        return raw
    chars = list(raw)
    for start, end in ranges:
        for index in range(start, end):
            if chars[index] != "\n":
                chars[index] = " "
    return "".join(chars)


def _parse_markup(
    raw: str,
    fmt: str,
    code_ranges: Sequence[tuple[int, int]],
    quote_ranges: Sequence[tuple[int, int]],
) -> Draft:
    masked = _mask(raw, code_ranges)
    collector = _StyleCollector()
    collector.feed(masked)
    collector.close()
    rules = css.parse_stylesheet("\n".join(collector.chunks))
    builder = _Builder(raw, masked, rules, code_ranges, quote_ranges)
    builder.feed(masked)
    builder.finish()
    pieces = sorted(builder.pieces, key=lambda piece: (piece.start, piece.end))
    return Draft(raw=raw, fmt=fmt, pieces=tuple(pieces), group_spans=builder.group_spans)
