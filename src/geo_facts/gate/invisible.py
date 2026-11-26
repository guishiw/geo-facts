"""不可见字符：零宽字符、双向控制字符、Unicode 标签字符，以及它们的正常用法。

正常用法（不算隐藏文本）：
- 文件开头的 BOM（U+FEFF）；
- emoji 连接序列里的 ZWJ（U+200D）：前一个字符（跳过变体选择符与肤色修饰符）和后一个字符都是 emoji；
- 阿拉伯文、印度诸文字等连写文字中的 ZWJ / ZWNJ：前后都是这些文字的字母；
- 旗帜 emoji 的标签序列：紧跟在 U+1F3F4 之后、以 U+E007F 结束的一串标签字符；
- 双向控制字符紧挨着希伯来文、阿拉伯文等从右到左的文字；
- 软连字符（U+00AD）夹在两个非中日韩的字母之间（西文断词提示）。
"""

from __future__ import annotations

ZERO_WIDTH = "zero_width"
BIDI_CONTROL = "bidi_control"
TAG_CHARACTER = "tag_character"

_ZERO_WIDTH_NAMES: dict[int, str] = {
    0x00AD: "软连字符",
    0x034F: "组合用字形连接符",
    0x115F: "韩文初声填充符",
    0x1160: "韩文中声填充符",
    0x17B4: "高棉文元音占位符",
    0x17B5: "高棉文元音占位符",
    0x180E: "蒙古文元音分隔符",
    0x200B: "零宽空格",
    0x200C: "零宽不连字",
    0x200D: "零宽连字",
    0x2060: "词连接符",
    0x2061: "不可见函数应用符",
    0x2062: "不可见乘号",
    0x2063: "不可见分隔符",
    0x2064: "不可见加号",
    0x206A: "已废弃的格式控制符",
    0x206B: "已废弃的格式控制符",
    0x206C: "已废弃的格式控制符",
    0x206D: "已废弃的格式控制符",
    0x206E: "已废弃的格式控制符",
    0x206F: "已废弃的格式控制符",
    0x3164: "韩文填充符",
    0xFEFF: "零宽不换行空格（BOM）",
    0xFFA0: "半角韩文填充符",
}
_BIDI_NAMES: dict[int, str] = {
    0x061C: "阿拉伯文字母标记",
    0x200E: "从左到右标记",
    0x200F: "从右到左标记",
    0x202A: "从左到右嵌入",
    0x202B: "从右到左嵌入",
    0x202C: "方向格式结束",
    0x202D: "从左到右覆盖",
    0x202E: "从右到左覆盖",
    0x2066: "从左到右隔离",
    0x2067: "从右到左隔离",
    0x2068: "首字符方向隔离",
    0x2069: "方向隔离结束",
}
_TAG_RANGE = (0xE0000, 0xE007F)
_TAG_CANCEL = 0xE007F
_BLACK_FLAG = 0x1F3F4

# 近似的 emoji 码位范围（标准库没有 Extended_Pictographic 属性）。
_EMOJI_RANGES: tuple[tuple[int, int], ...] = (
    (0x00A9, 0x00A9),
    (0x00AE, 0x00AE),
    (0x203C, 0x203C),
    (0x2049, 0x2049),
    (0x2122, 0x2122),
    (0x2139, 0x2139),
    (0x2194, 0x21AA),
    (0x231A, 0x23FF),
    (0x24C2, 0x24C2),
    (0x25AA, 0x25FE),
    (0x2600, 0x27BF),
    (0x2934, 0x2935),
    (0x2B05, 0x2B55),
    (0x3030, 0x3030),
    (0x303D, 0x303D),
    (0x3297, 0x3299),
    (0x1F000, 0x1FAFF),
)
# emoji 序列中位于 ZWJ 左侧、可以跳过的修饰字符：变体选择符、肤色修饰符、键帽组合符。
_EMOJI_MODIFIERS: frozenset[int] = frozenset({0xFE0E, 0xFE0F, 0x20E3, *range(0x1F3FB, 0x1F400)})
# 使用 ZWJ / ZWNJ 控制连写的文字。
_JOINING_SCRIPTS: tuple[tuple[int, int], ...] = (
    (0x0600, 0x06FF),
    (0x0700, 0x074F),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0x0900, 0x0DFF),
    (0x1000, 0x109F),
    (0x1780, 0x17FF),
    (0x1800, 0x18AF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFE),
)
# 从右到左的文字。
_RTL_SCRIPTS: tuple[tuple[int, int], ...] = (
    (0x0590, 0x05FF),
    (0x0600, 0x06FF),
    (0x0700, 0x074F),
    (0x0750, 0x077F),
    (0x0780, 0x07BF),
    (0x07C0, 0x07FF),
    (0x08A0, 0x08FF),
    (0xFB1D, 0xFDFF),
    (0xFE70, 0xFEFE),
)


def _in(ranges: tuple[tuple[int, int], ...], code: int) -> bool:
    return any(low <= code <= high for low, high in ranges)


def kind_of(char: str) -> str | None:
    """字符属于哪一类不可见字符；普通字符返回 None。"""
    code = ord(char)
    if code in _ZERO_WIDTH_NAMES:
        return ZERO_WIDTH
    if code in _BIDI_NAMES:
        return BIDI_CONTROL
    if _TAG_RANGE[0] <= code <= _TAG_RANGE[1]:
        return TAG_CHARACTER
    return None


def is_hidden_char(char: str) -> bool:
    return kind_of(char) is not None


def describe(char: str) -> str:
    """形如“U+200B（零宽空格）”的说明。"""
    code = ord(char)
    name = _ZERO_WIDTH_NAMES.get(code) or _BIDI_NAMES.get(code) or "Unicode 标签字符"
    return f"U+{code:04X}（{name}）"


def is_emoji(char: str) -> bool:
    return _in(_EMOJI_RANGES, ord(char))


def _is_cjk_like(char: str) -> bool:
    code = ord(char)
    return (
        0x2E80 <= code <= 0x9FFF
        or 0xAC00 <= code <= 0xD7AF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x3FFFF
    )


def _emoji_before(text: str, index: int) -> bool:
    position = index - 1
    while position >= 0 and ord(text[position]) in _EMOJI_MODIFIERS:
        position -= 1
    return position >= 0 and is_emoji(text[position])


def _neighbors(text: str, index: int) -> tuple[str, str]:
    before = text[index - 1] if index > 0 else ""
    after = text[index + 1] if index + 1 < len(text) else ""
    return before, after


def _tag_sequence_ok(text: str, index: int) -> bool:
    """标签字符是否属于“U+1F3F4 + 标签字符… + U+E007F”的旗帜序列。"""
    start = index
    while start > 0 and kind_of(text[start - 1]) == TAG_CHARACTER:
        start -= 1
    if start == 0 or ord(text[start - 1]) != _BLACK_FLAG:
        return False
    end = index
    while end < len(text) and kind_of(text[end]) == TAG_CHARACTER:
        if ord(text[end]) == _TAG_CANCEL:
            return True
        end += 1
    return False


def is_legitimate(text: str, index: int) -> bool:
    """text[index] 是不可见字符时，它是否属于正常用法。"""
    char = text[index]
    code = ord(char)
    before, after = _neighbors(text, index)
    if code == 0xFEFF:
        return index == 0
    if code == 0x200D and after and is_emoji(after) and _emoji_before(text, index):
        return True
    if code in (0x200C, 0x200D):
        return bool(before and after) and (
            _in(_JOINING_SCRIPTS, ord(before)) and _in(_JOINING_SCRIPTS, ord(after))
        )
    if code == 0x00AD:
        return all(
            neighbor.isalpha() and not _is_cjk_like(neighbor) for neighbor in (before, after)
        ) and bool(before and after)
    kind = kind_of(char)
    if kind == TAG_CHARACTER:
        return _tag_sequence_ok(text, index)
    if kind == BIDI_CONTROL:
        return any(neighbor and _in(_RTL_SCRIPTS, ord(neighbor)) for neighbor in (before, after))
    return False


def decode_tags(chars: str) -> str:
    """把 Unicode 标签字符还原成对应的 ASCII 文本（用于说明夹带内容）。"""
    out: list[str] = []
    for char in chars:
        code = ord(char)
        if 0xE0020 <= code <= 0xE007E:
            out.append(chr(code - 0xE0000))
    return "".join(out)
