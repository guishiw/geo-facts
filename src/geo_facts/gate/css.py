"""CSS 隐藏判断：行内 style、`<style>` 中的简单选择器、颜色比较。

只看得到稿件本身：外部样式表、脚本动态改样式都看不到（文档写明）。
判为“隐藏”的声明：
- display:none；visibility:hidden / collapse；
- opacity ≤ OPACITY_MAX；font-size ≤ FONT_SIZE_MAX_PX（0 或接近 0）；
- 文字颜色透明，或与（自身或祖先元素显式设置的）背景色相同（各通道差 ≤ COLOR_TOLERANCE）；
  没有显式背景时不假定白底，避免把按钮里的白字误判为隐藏；
- position:absolute / fixed 且 left / top / right / bottom ≤ −OFFSCREEN_PX；text-indent ≤ −OFFSCREEN_PX；
- clip: rect(…) 各值都 ≤ 1px；clip-path: inset(≥ 50%) / circle(0)；
- width 或 height ≤ 1px 且 overflow:hidden；transform: scale(0)。
`<style>` 只解析不带组合符、伪类、属性选择器的简单选择器（tag、.class、#id 及其组合），
@media / @supports 等条件块里的规则不参与判断（响应式布局里的隐藏属于正常用法）。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

OPACITY_MAX = 0.05
FONT_SIZE_MAX_PX = 1.0
OFFSCREEN_PX = 1000.0
COLOR_TOLERANCE = 8
_EM_PX = 16.0

Color = tuple[int, int, int, float]

_NAMED: Mapping[str, tuple[int, int, int]] = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "red": (255, 0, 0),
    "green": (0, 128, 0),
    "lime": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "orange": (255, 165, 0),
    "purple": (128, 0, 128),
    "navy": (0, 0, 128),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "silver": (192, 192, 192),
    "lightgray": (211, 211, 211),
    "lightgrey": (211, 211, 211),
    "darkgray": (169, 169, 169),
    "darkgrey": (169, 169, 169),
    "whitesmoke": (245, 245, 245),
    "snow": (255, 250, 250),
    "ivory": (255, 255, 240),
    "beige": (245, 245, 220),
}

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LENGTH = re.compile(r"^(-?(?:\d+\.?\d*|\.\d+))\s*(px|pt|em|rem|%|vw|vh)?$")
_HEX = re.compile(r"^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$")
_RGB = re.compile(
    r"^rgba?\(\s*([\d.]+%?)\s*[,\s]\s*([\d.]+%?)\s*[,\s]\s*([\d.]+%?)\s*(?:[,/]\s*([\d.]+%?)\s*)?\)$"
)
_COLOR_TOKEN = re.compile(r"#[0-9a-f]{3,8}\b|rgba?\([^)]*\)|\b[a-z]+\b")
_SCALE_ZERO = re.compile(r"scale[xy]?\(\s*0*(?:\.0*)?\s*(?:[,)]|$)")
_CLIP_RECT = re.compile(r"rect\(([^)]*)\)")
_INSET = re.compile(r"inset\(\s*(\d+(?:\.\d+)?)%")
_SIMPLE_SELECTOR = re.compile(r"^([a-z][a-z0-9-]*|\*)?((?:[.#][A-Za-z_][\w-]*)*)$")


def declarations(style: str | None) -> dict[str, str]:
    """解析一段声明（行内 style 或规则体）：属性名小写，值小写并去掉 !important。"""
    result: dict[str, str] = {}
    if not style:
        return result
    for chunk in _COMMENT.sub(" ", style).split(";"):
        name, sep, value = chunk.partition(":")
        if not sep:
            continue
        name = name.strip().lower()
        value = value.strip().lower().replace("!important", "").strip()
        if name and value:
            result[name] = value
    return result


def _number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def length_px(value: str) -> float | None:
    """长度换算成像素；百分比、视口单位等无法换算的返回 None（0 例外）。"""
    match = _LENGTH.match(value.strip())
    if match is None:
        return None
    number = float(match.group(1))
    unit = match.group(2) or "px"
    if number == 0:
        return 0.0
    if unit == "px":
        return number
    if unit == "pt":
        return number * 4 / 3
    if unit in ("em", "rem"):
        return number * _EM_PX
    return None


def _font_px(value: str) -> float | None:
    match = _LENGTH.match(value.strip())
    if match is not None and match.group(2) == "%":
        return float(match.group(1)) / 100 * _EM_PX
    return length_px(value)


def _channel(text: str) -> int | None:
    if text.endswith("%"):
        number = _number(text[:-1])
        return None if number is None else round(min(number, 100.0) * 255 / 100)
    number = _number(text)
    return None if number is None else min(round(number), 255)


def _alpha(text: str | None) -> float | None:
    if text is None:
        return 1.0
    if text.endswith("%"):
        number = _number(text[:-1])
        return None if number is None else number / 100
    return _number(text)


def parse_color(value: str | None) -> Color | None:
    """解析颜色：命名色（常用子集）、#rgb / #rgba / #rrggbb / #rrggbbaa、rgb() / rgba()、transparent。"""
    if not value:
        return None
    text = value.strip().lower()
    if text == "transparent":
        return (0, 0, 0, 0.0)
    if text in _NAMED:
        red, green, blue = _NAMED[text]
        return (red, green, blue, 1.0)
    hex_match = _HEX.match(text)
    if hex_match:
        digits = hex_match.group(1)
        if len(digits) in (3, 4):
            digits = "".join(char * 2 for char in digits)
        alpha = int(digits[6:8], 16) / 255 if len(digits) == 8 else 1.0
        return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16), alpha)
    rgb = _RGB.match(text)
    if rgb:
        channels = [_channel(rgb.group(index)) for index in (1, 2, 3)]
        opacity = _alpha(rgb.group(4))
        if any(item is None for item in channels) or opacity is None:
            return None
        red, green, blue = (int(item) for item in channels if item is not None)
        return (red, green, blue, opacity)
    return None


def first_color(value: str | None) -> Color | None:
    """background 简写里的第一个颜色。"""
    if not value:
        return None
    for token in _COLOR_TOKEN.findall(value.lower()):
        color = parse_color(token)
        if color is not None:
            return color
    return None


def text_color(decls: Mapping[str, str]) -> Color | None:
    return parse_color(decls.get("color"))


def background_color(decls: Mapping[str, str]) -> Color | None:
    if "background-color" in decls:
        return parse_color(decls["background-color"])
    return first_color(decls.get("background"))


def same_color(first: Color, second: Color) -> bool:
    """两个不透明颜色在容差内相同。"""
    if first[3] < 1 or second[3] < 1:
        return False
    return all(abs(first[index] - second[index]) <= COLOR_TOLERANCE for index in range(3))


def _clip_hidden(decls: Mapping[str, str]) -> bool:
    clip = decls.get("clip", "")
    rect = _CLIP_RECT.search(clip)
    if rect:
        values = [length_px(item) for item in re.split(r"[\s,]+", rect.group(1).strip()) if item]
        if values and all(item is not None and item <= 1 for item in values):
            return True
    clip_path = decls.get("clip-path", "")
    inset = _INSET.search(clip_path)
    if inset and float(inset.group(1)) >= 50:
        return True
    return bool(re.search(r"circle\(\s*0(?:px|%)?\s*[)a]", clip_path))


def _offscreen(decls: Mapping[str, str]) -> bool:
    if decls.get("position") in ("absolute", "fixed"):
        for side in ("left", "top", "right", "bottom"):
            value = length_px(decls.get(side, ""))
            if value is not None and value <= -OFFSCREEN_PX:
                return True
    return False


def _collapsed(decls: Mapping[str, str]) -> bool:
    if decls.get("overflow") != "hidden":
        return False
    for side in ("width", "height"):
        value = length_px(decls.get(side, ""))
        if value is not None and value <= 1:
            return True
    return False


def hiding_reason(decls: Mapping[str, str]) -> str:
    """这组声明是否让元素内的文字对读者不可见；返回说明，不隐藏时返回空串。"""
    if decls.get("display") == "none":
        return "display:none"
    if decls.get("visibility") in ("hidden", "collapse"):
        return f"visibility:{decls['visibility']}"
    if "opacity" in decls:
        opacity = _alpha(decls["opacity"])
        if opacity is not None and opacity <= OPACITY_MAX:
            return "opacity:0"
    if "font-size" in decls:
        size = _font_px(decls["font-size"])
        if size is not None and size <= FONT_SIZE_MAX_PX:
            return "font-size:0"
    color = text_color(decls)
    if color is not None and color[3] <= OPACITY_MAX:
        return "color:transparent"
    if _offscreen(decls):
        return "绝对定位移出屏幕"
    indent = length_px(decls.get("text-indent", ""))
    if indent is not None and indent <= -OFFSCREEN_PX:
        return "text-indent 移出屏幕"
    if _clip_hidden(decls):
        return "clip 裁剪为不可见"
    if _collapsed(decls):
        return "尺寸为 0 且 overflow:hidden"
    if _SCALE_ZERO.search(decls.get("transform", "")):
        return "transform:scale(0)"
    return ""


@dataclass(frozen=True)
class Rule:
    """`<style>` 中的一条简单选择器规则。"""

    tag: str | None
    classes: frozenset[str]
    element_id: str | None
    decls: Mapping[str, str]

    def matches(self, tag: str, classes: frozenset[str], element_id: str | None) -> bool:
        if self.tag is not None and self.tag != tag:
            return False
        if self.element_id is not None and self.element_id != element_id:
            return False
        return self.classes <= classes


def _skip_block(css: str, brace: int) -> int:
    depth = 0
    for index in range(brace, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return len(css)


def _parse_selector(text: str, decls: Mapping[str, str]) -> Rule | None:
    selector = text.strip()
    match = _SIMPLE_SELECTOR.match(selector)
    if not selector or match is None:
        return None
    tag = match.group(1)
    parts = re.findall(r"[.#][A-Za-z_][\w-]*", match.group(2))
    ids = [part[1:] for part in parts if part.startswith("#")]
    if len(ids) > 1:
        return None
    return Rule(
        tag=None if tag in (None, "*") else tag,
        classes=frozenset(part[1:] for part in parts if part.startswith(".")),
        element_id=ids[0] if ids else None,
        decls=decls,
    )


def parse_stylesheet(css: str) -> list[Rule]:
    """解析样式表中顶层的简单选择器规则；@ 规则整体跳过。"""
    text = _COMMENT.sub(" ", css)
    rules: list[Rule] = []
    position = 0
    while position < len(text):
        brace = text.find("{", position)
        stripped = text[position:].lstrip()
        if stripped.startswith("@"):
            at = len(text) - len(stripped)
            semicolon = text.find(";", at)
            if brace == -1 or (semicolon != -1 and semicolon < brace):
                position = len(text) if semicolon == -1 else semicolon + 1
                continue
            position = _skip_block(text, brace)
            continue
        if brace == -1:
            break
        close = text.find("}", brace)
        close = len(text) if close == -1 else close
        decls = declarations(text[brace + 1 : close])
        for selector in text[position:brace].split(","):
            rule = _parse_selector(selector, decls)
            if rule is not None:
                rules.append(rule)
        position = close + 1
    return rules


def computed(
    rules: Iterable[Rule],
    tag: str,
    classes: frozenset[str],
    element_id: str | None,
    inline: str | None,
) -> dict[str, str]:
    """样式表规则按出现顺序叠加，行内 style 最后覆盖（不计算选择器优先级，文档写明）。"""
    result: dict[str, str] = {}
    for rule in rules:
        if rule.matches(tag, classes, element_id):
            result.update(rule.decls)
    result.update(declarations(inline))
    return result
