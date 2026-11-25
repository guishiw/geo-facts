"""文本折叠：全半角、大小写、空格、连字符与不可见字符。

所有“把原文变形后再匹配”的地方都必须能映射回原文位置，因此这里提供 `View`：
折叠后的字符串加上每个折叠字符对应的原文下标（按 Unicode 码点计）。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

# 连字符与各种横线：型号别名比较时忽略（“S-7”与“S7”等价）。
HYPHENS: frozenset[str] = frozenset(
    chr(code)
    for code in (0x2D, 0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xFE63, 0xFF0D)
)

# 不可见字符：零宽空格与连接符、方向控制符、词连接符、BOM、蒙古文元音分隔符。
_INVISIBLE_SINGLE: frozenset[int] = frozenset(
    {0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0xFEFF, 0x180E}
)
_INVISIBLE_RANGES: tuple[tuple[int, int], ...] = (
    (0x202A, 0x202E),  # 方向嵌入与覆盖
    (0x2066, 0x2069),  # 方向隔离
    (0xE0000, 0xE007F),  # Unicode 标签字符（可用来夹带肉眼不可见的文本）
)

# 折叠时保持原样的字符：中文逗号必须与千分位的半角逗号区分开。
_KEEP: frozenset[str] = frozenset({chr(0xFF0C)})


def is_invisible(char: str) -> bool:
    """是否为零宽或方向控制等肉眼不可见的字符。"""
    code = ord(char)
    if code in _INVISIBLE_SINGLE:
        return True
    return any(low <= code <= high for low, high in _INVISIBLE_RANGES)


def fold_char(char: str) -> str:
    """单个字符的折叠：NFKC（全角转半角等）后转小写。结果可能是多个字符。"""
    if char in _KEEP:
        return char
    return unicodedata.normalize("NFKC", char).casefold()


def fold(text: str) -> str:
    """折叠全半角与大小写，保留空格和连字符。"""
    return "".join(fold_char(char) for char in text)


def compact(text: str) -> str:
    """名称比较用的键：折叠全半角与大小写，并去掉空白、连字符和不可见字符。

    “S7”“Ｓ７”“s 7”“S-7”得到同一个键。
    """
    out: list[str] = []
    for char in text:
        if is_invisible(char):
            continue
        for piece in fold_char(char):
            if piece.isspace() or piece in HYPHENS:
                continue
            out.append(piece)
    return "".join(out)


@dataclass(frozen=True)
class View:
    """折叠后的文本及其到原文的位置映射。"""

    original: str
    folded: str
    starts: tuple[int, ...]

    def span(self, start: int, end: int) -> tuple[int, int]:
        """把折叠文本上的区间 [start, end) 映射成原文区间（左闭右开，按码点计）。"""
        if not 0 <= start < end <= len(self.folded):
            raise ValueError(f"非法区间：[{start}, {end})")
        return self.starts[start], self.starts[end - 1] + 1

    def original_slice(self, start: int, end: int) -> str:
        left, right = self.span(start, end)
        return self.original[left:right]


def make_view(text: str, *, compact_mode: bool = False, drop: frozenset[str] = frozenset()) -> View:
    """构造折叠视图。

    - 不可见字符和 `drop` 中的字符被丢弃；
    - 普通模式下各种空白统一成一个空格（换行保留为换行）；
    - 紧凑模式（`compact_mode`）下空白和连字符也被丢弃，用于名称匹配。
    """
    pieces: list[str] = []
    starts: list[int] = []
    for index, char in enumerate(text):
        if is_invisible(char) or char in drop:
            continue
        if char in "\n\r":
            if not compact_mode:
                pieces.append("\n")
                starts.append(index)
            continue
        for piece in fold_char(char):
            if piece.isspace():
                if compact_mode:
                    continue
                piece = " "
            elif compact_mode and piece in HYPHENS:
                continue
            pieces.append(piece)
            starts.append(index)
    return View(original=text, folded="".join(pieces), starts=tuple(starts))


def is_ascii_alnum(char: str) -> bool:
    return char.isascii() and char.isalnum()


def is_cjk(char: str) -> bool:
    """是否为中日韩统一表意文字（含扩展 A 区）。"""
    code = ord(char)
    return 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF
