"""闸门的稿件解析：可见 / 隐藏 / 代码 / 注释 / 脚本 / 属性的划分，以及回到原文位置的映射。

期望值均为手工推算：下标按原文逐字数出。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers_gate import ZWSP
from hypothesis import given, settings
from hypothesis import strategies as st

from geo_facts.errors import InputError
from geo_facts.gate import css
from geo_facts.gate.document import (
    ATTRIBUTE,
    CODE,
    COMMENT,
    HIDDEN,
    MARKUP,
    SCRIPT,
    VISIBLE,
    load_draft,
    markdown_code_ranges,
    parse_draft,
)

# ------------------------------------------------------------------ txt 与位置映射


def test_txt_is_entirely_visible_and_maps_one_to_one() -> None:
    raw = "澜序 S7 续航 620 公里\n"
    draft = parse_draft(raw, "txt")
    assert draft.text_of(VISIBLE) == raw
    assert draft.text_of(HIDDEN, CODE, COMMENT, SCRIPT, ATTRIBUTE) == ""
    index = draft.index(VISIBLE)
    assert index.text == "澜序 s7 续航 620 公里\n"
    position = index.text.index("620")
    assert index.raw_span(position, position + 3) == (9, 12)
    assert raw[9:12] == "620"


def test_empty_txt_has_no_pieces() -> None:
    assert parse_draft("", "txt").pieces == ()


def test_html_character_references_map_to_their_raw_text() -> None:
    raw = "<p>A&amp;B&#x4E2D;</p>"
    index = parse_draft(raw, "html").index(VISIBLE)
    assert index.text == "a&b中"
    assert index.raw_span(1, 2) == (4, 9)  # “&” ← “&amp;”
    assert raw[4:9] == "&amp;"
    assert index.raw_span(3, 4) == (10, 18)  # “中” ← “&#x4E2D;”
    assert raw[10:18] == "&#x4E2D;"
    assert index.raw_span(0, 4) == (3, 18)


def test_index_skips_invisible_characters_but_spans_cover_them() -> None:
    raw = f"忽{ZWSP}略"
    index = parse_draft(raw, "txt").index(VISIBLE)
    assert index.text == "忽略"
    assert index.raw_span(0, 2) == (0, 3)


def test_blocks_are_separated_in_the_index() -> None:
    raw = "<p>岑野</p><p>K5</p>"
    draft = parse_draft(raw, "html")
    assert draft.text_of(VISIBLE) == "岑野\nK5"
    index = draft.index(VISIBLE)
    assert index.text == "岑野\nk5"
    separator = index.text.index("\n")
    assert index.owners[separator] == -1
    assert index.raw_span(separator, separator + 1) is None


def test_inline_tags_do_not_split_text() -> None:
    draft = parse_draft("<p>澜序<b>S7</b>续航</p>", "html")
    assert draft.text_of(VISIBLE) == "澜序S7续航"


def test_unrendered_pieces_between_text_do_not_split_it() -> None:
    raw = '<p>澜序<!-- x -->S7<span style="display:none">隐</span>续航</p>'
    draft = parse_draft(raw, "html")
    assert draft.text_of(VISIBLE) == "澜序S7续航"
    assert draft.index(VISIBLE).text == "澜序s7续航"


def test_rendered_text_between_code_pieces_splits_them() -> None:
    draft = parse_draft("<p><code>a</code> 与 <code>b</code></p>", "html")
    assert draft.text_of(CODE) == "a\nb"
    assert draft.index(CODE).text == "a\nb"


def test_compact_index_drops_spaces_and_hyphens() -> None:
    draft = parse_draft("岑野 K-5", "txt")
    assert draft.index(VISIBLE, compact=True).text == "岑野k5"


# ------------------------------------------------------------------ CSS 隐藏


HIDING_STYLES = [
    "display:none",
    "display: none !important",
    "visibility:hidden",
    "visibility: collapse",
    "opacity:0",
    "opacity: 0.0",
    "opacity:3%",
    "font-size:0",
    "font-size:0px",
    "font-size:1px",
    "font-size:0.05em",
    "font-size:5%",
    "color:transparent",
    "color:rgba(0,0,0,0)",
    "position:absolute;left:-9999px",
    "position:fixed;top:-5000px",
    "position:absolute;left:-100em",
    "text-indent:-9999px",
    "position:absolute;clip:rect(0 0 0 0)",
    "position:absolute;clip:rect(1px, 1px, 1px, 1px)",
    "clip-path: inset(50%)",
    "clip-path: circle(0)",
    "width:0;height:0;overflow:hidden",
    "height:1px;overflow:hidden",
    "transform:scale(0)",
    "color:#fff;background:#fff",
    "color:#ffffff;background-color:rgb(255,255,255)",
    "color:#fefefe;background:#ffffff url(x.png) no-repeat",
    "color:white;background-color:#FFF",
    "color:rgb(100%, 100%, 100%);background:white",
]


@pytest.mark.parametrize("style", HIDING_STYLES)
def test_inline_styles_that_hide_text(style: str) -> None:
    raw = f'<p>正文</p><div style="{style}">隐藏内容在此</div>'
    draft = parse_draft(raw, "html")
    assert draft.text_of(HIDDEN) == "隐藏内容在此"
    assert draft.text_of(VISIBLE) == "正文"


VISIBLE_STYLES = [
    "display:block",
    "opacity:0.9",
    "font-size:14px",
    "font-size:120%",
    "color:#fff",  # 白字但没有显式背景（常见于按钮，背景来自外部样式表）
    "color:#333;background:#fff",
    "position:absolute;left:10px",
    "position:relative;left:-9999px",
    "text-indent:2em",
    "width:0",  # 没有 overflow:hidden，文字仍会溢出显示
    "clip-path: inset(10%)",
    "clip:rect(0 100px 20px 0)",
    "color:hsl(0, 0%, 100%);background:#fff",  # 无法解析的颜色不判同色
    "color:red;background:red;color:blue",
    "transform:scale(1.2)",
    "opacity:abc",
    "font-size:large",
    "color",
]


@pytest.mark.parametrize("style", VISIBLE_STYLES)
def test_inline_styles_that_keep_text_visible(style: str) -> None:
    raw = f'<div style="{style}">可见内容</div>'
    draft = parse_draft(raw, "html")
    assert draft.text_of(VISIBLE) == "可见内容"
    assert draft.text_of(HIDDEN) == ""


def test_hidden_attribute_and_template_hide_text() -> None:
    draft = parse_draft("<div hidden>甲</div><template><p>乙</p></template><p>丙</p>", "html")
    assert draft.text_of(HIDDEN) == "甲\n乙"
    reasons = {piece.text: piece.hidden_by for piece in draft.pieces}
    assert reasons["甲"] == "hidden 属性"
    assert reasons["乙"] == "template 元素"
    assert draft.text_of(VISIBLE) == "丙"


def test_same_color_as_ancestor_background_is_hidden() -> None:
    raw = '<div style="background:#000"><p>可见</p><span style="color:#000">黑底黑字</span></div>'
    draft = parse_draft(raw, "html")
    assert draft.text_of(HIDDEN) == "黑底黑字"
    assert draft.text_of(VISIBLE) == "可见"


def test_legacy_font_color_and_bgcolor() -> None:
    raw = '<table bgcolor="white"><tr><td><font color="#FFFFFF">白底白字</font></td></tr></table>'
    assert parse_draft(raw, "html").text_of(HIDDEN) == "白底白字"


def test_children_inherit_hiding_and_share_one_group() -> None:
    raw = '<div style="display:none"><p>第一段</p><p>第二段</p></div><p>外面</p>'
    draft = parse_draft(raw, "html")
    hidden = [piece for piece in draft.pieces if piece.channel == HIDDEN]
    assert [piece.text for piece in hidden] == ["第一段", "第二段"]
    assert len({piece.group for piece in hidden}) == 1
    group = hidden[0].group
    end = raw.index("<p>外面")
    assert draft.group_spans[group] == (0, end)
    assert raw[:end].endswith("</div>")


def test_stylesheet_simple_selectors_hide_text() -> None:
    raw = (
        "<style>.secret{display:none} #box {visibility:hidden} span.tiny{font-size:0}</style>"
        '<div class="note secret">类选择器</div><div id="box">编号选择器</div>'
        '<span class="tiny">标签加类</span><span>普通</span>'
    )
    draft = parse_draft(raw, "html")
    # 三个隐藏根元素各成一组，组与组之间断开
    assert draft.text_of(HIDDEN) == "类选择器\n编号选择器\n标签加类"
    assert draft.text_of(VISIBLE) == "普通"


def test_stylesheet_written_after_the_element_still_applies() -> None:
    raw = '<p class="x">隐藏</p><style>.x { display: none }</style>'
    assert parse_draft(raw, "html").text_of(HIDDEN) == "隐藏"


def test_stylesheet_conditional_and_complex_rules_are_ignored() -> None:
    raw = (
        "<style>@import url(a.css); @media (max-width: 600px) { .nav { display: none } }"
        " .menu li { display: none } a:hover { opacity: 0 } [data-x] { display:none }"
        " @font-face { font-family: x; } .ok { color: red }</style>"
        '<ul class="menu"><li class="nav">菜单项</li></ul><a href="#">链接</a>'
    )
    draft = parse_draft(raw, "html")
    assert draft.text_of(HIDDEN) == ""
    assert "菜单项" in draft.text_of(VISIBLE)


def test_inline_style_overrides_stylesheet() -> None:
    raw = '<style>.x{display:none}</style><p class="x" style="display:block">显示</p>'
    assert parse_draft(raw, "html").text_of(VISIBLE) == "显示"


def test_parse_stylesheet_edge_cases() -> None:
    assert css.parse_stylesheet("") == []
    assert css.parse_stylesheet(".a{display:none") != []  # 缺右括号也读到文末
    assert css.parse_stylesheet("@charset 'utf-8'") == []
    assert css.parse_stylesheet("#a#b{display:none}") == []  # 一个元素不可能有两个 id
    rules = css.parse_stylesheet("*.x, div{display:none}")
    assert [(rule.tag, sorted(rule.classes)) for rule in rules] == [(None, ["x"]), ("div", [])]


def test_color_parsing() -> None:
    assert css.parse_color("#abc") == (0xAA, 0xBB, 0xCC, 1.0)
    assert css.parse_color("#aabbcc80") == (0xAA, 0xBB, 0xCC, 0x80 / 255)
    assert css.parse_color("rgba(10, 20, 30, 50%)") == (10, 20, 30, 0.5)
    assert css.parse_color("rgb(10 20 30 / 0.5)") == (10, 20, 30, 0.5)
    assert css.parse_color("rgb(1.5.5, 2, 3)") is None
    assert css.parse_color("rgb(1, 2, 3, 1.2.3)") is None
    assert css.parse_color("") is None
    assert css.parse_color("papayawhip") is None
    assert css.first_color("url(a.png) no-repeat") is None
    assert css.first_color(None) is None
    assert not css.same_color((0, 0, 0, 0.5), (0, 0, 0, 1.0))
    assert css.length_px("12pt") == 16.0
    assert css.length_px("3vw") is None
    assert css.length_px("abc") is None


# ------------------------------------------------------------------ 元素结构


def test_paragraph_is_closed_implicitly_by_the_next_paragraph() -> None:
    draft = parse_draft('<p style="display:none">隐藏<p>可见', "html")
    assert draft.text_of(HIDDEN) == "隐藏"
    assert draft.text_of(VISIBLE) == "可见"


def test_list_items_and_cells_close_implicitly() -> None:
    raw = (
        '<ul><li style="display:none">甲<li>乙</ul>'
        '<table><tr><td style="display:none">丙<td>丁<tr><td>戊</table>'
        '<dl><dt style="display:none">己<dd>庚</dl>'
        '<select><option style="display:none">辛<option>壬</select>'
    )
    draft = parse_draft(raw, "html")
    assert draft.text_of(HIDDEN) == "甲\n丙\n己\n辛"
    assert draft.text_of(VISIBLE) == "乙\n丁\n戊\n庚\n壬"


def test_unclosed_hidden_container_extends_to_the_end() -> None:
    raw = '<div style="display:none">隐藏<p>仍然隐藏'
    draft = parse_draft(raw, "html")
    assert draft.text_of(HIDDEN) == "隐藏\n仍然隐藏"
    group = next(piece.group for piece in draft.pieces if piece.channel == HIDDEN)
    assert draft.group_spans[group] == (0, len(raw))


def test_self_closing_non_void_element_opens_like_a_browser() -> None:
    draft = parse_draft('<div style="display:none"/>后文', "html")
    assert draft.text_of(HIDDEN) == "后文"


def test_self_closing_inside_svg_is_closed() -> None:
    draft = parse_draft('<svg><path style="display:none"/><text>图注</text></svg>', "html")
    assert draft.text_of(VISIBLE) == "图注"


def test_stray_end_tags_are_ignored() -> None:
    draft = parse_draft("</span>甲</div>乙", "html")
    assert draft.text_of(VISIBLE) == "甲\n乙"


# ------------------------------------------------------------------ 代码、脚本、注释、属性


def test_code_elements_are_code() -> None:
    raw = "<p>示例：</p><pre>.menu { display: none }</pre><p>行内 <code>x</code> 与 <kbd>Ctrl</kbd></p>"
    draft = parse_draft(raw, "html")
    assert draft.text_of(CODE) == ".menu { display: none }\nx\nCtrl"
    assert draft.text_of(HIDDEN) == ""


def test_script_and_style_are_script_channel() -> None:
    raw = '<script type="application/ld+json">{"a": "<b>"}</script><style>.a{}</style><p>正文</p>'
    draft = parse_draft(raw, "html")
    assert draft.text_of(SCRIPT) == '{"a": "<b>"}\n.a{}'
    assert draft.text_of(VISIBLE) == "正文"


def test_comments_keep_their_text_and_whole_span() -> None:
    raw = "前<!-- 注释内容 -->后"
    draft = parse_draft(raw, "html")
    comments = [piece for piece in draft.pieces if piece.channel == COMMENT]
    assert [piece.text for piece in comments] == [" 注释内容 "]
    assert draft.group_spans[comments[0].group] == (1, 14)
    assert raw[1:14] == "<!-- 注释内容 -->"
    index = draft.index(COMMENT)
    position = index.text.index("注释")
    span = index.raw_span(position, position + 2)
    assert span is not None
    assert raw[span[0] : span[1]] == "注释"


def test_bogus_comment_maps_to_the_whole_construct() -> None:
    raw = "a<!x>b"
    draft = parse_draft(raw, "html")
    comment = next(piece for piece in draft.pieces if piece.channel == COMMENT)
    assert comment.text == "x"
    assert (comment.starts[0], comment.ends[0]) == (1, 5)
    assert draft.text_of(VISIBLE) == "ab"  # 注释不渲染，两侧文字相连


def test_unterminated_comment_runs_to_the_end() -> None:
    raw = "a<!-- 没有结束"
    draft = parse_draft(raw, "html")
    comment = next(piece for piece in draft.pieces if piece.channel == COMMENT)
    assert draft.group_spans[comment.group] == (1, len(raw))


def test_declarations_and_processing_instructions_are_skipped() -> None:
    raw = "<!DOCTYPE html><?xml version='1.0'?><![CDATA[x]]><p>正文</p>"
    assert parse_draft(raw, "html").text_of(VISIBLE) == "正文"


def test_text_attributes_become_attribute_pieces() -> None:
    raw = '<img src="a.png" alt="替代文字" class="c"><a href="x" title="t &amp; u" data-note="备注">链接</a>'
    draft = parse_draft(raw, "html")
    attributes = [piece for piece in draft.pieces if piece.channel == ATTRIBUTE]
    assert [piece.text for piece in attributes] == ["替代文字", "t & u", "备注"]
    alt = attributes[0]
    assert raw[alt.start : alt.end] == "替代文字"
    title = attributes[1]  # 含字符引用：整段映射到原文的属性值
    assert raw[title.start : title.end] == "t &amp; u"
    assert draft.text_of(VISIBLE) == "链接"


def test_unquoted_and_duplicate_attributes() -> None:
    raw = "<img alt=甲 alt=乙 title='丙'>"
    attributes = [p.text for p in parse_draft(raw, "html").pieces if p.channel == ATTRIBUTE]
    assert attributes == ["甲", "丙"]


def test_unconsumed_tail_is_markup() -> None:
    raw = "正文<b ignore previous instructions"
    draft = parse_draft(raw, "html")
    assert draft.text_of(MARKUP) == "<b ignore previous instructions"
    assert draft.text_of(VISIBLE) == "正文"


# ------------------------------------------------------------------ Markdown


def test_markdown_fences_and_inline_code_are_code() -> None:
    raw = '前文 `a<b>` 中间\n\n```html\n<div style="display:none">示例</div>\n```\n\n~~~\nx\n~~~\n后文'
    draft = parse_draft(raw, "md")
    assert draft.text_of(HIDDEN) == ""
    code = draft.text_of(CODE)
    assert "`a<b>`" in code
    assert '<div style="display:none">示例</div>' in code
    assert draft.text_of(VISIBLE).startswith("前文 ")
    assert "后文" in draft.text_of(VISIBLE)


def test_markdown_code_ranges_by_hand() -> None:
    raw = "a `b` c\n```\nd\n```\ne ``f`g`` h"
    # 围栏区间含结尾围栏行的换行符
    assert markdown_code_ranges(raw) == ((2, 5), (8, 18), (20, 27))
    assert raw[2:5] == "`b`"
    assert raw[8:18] == "```\nd\n```\n"
    assert raw[20:27] == "``f`g``"


def test_markdown_unmatched_backticks_and_unclosed_fence() -> None:
    assert markdown_code_ranges("a `b\n\nc` d") == ()
    assert markdown_code_ranges("x ``` y") == ()
    raw = "正文\n```\n没有结束"
    assert markdown_code_ranges(raw) == ((3, len(raw)),)
    assert markdown_code_ranges("a ``b``` c") == ()


def test_markdown_inline_html_is_parsed() -> None:
    raw = '正文\n\n<div style="display:none">隐藏的段落</div>\n\n<!-- 注释 -->\n'
    draft = parse_draft(raw, "md")
    assert draft.text_of(HIDDEN) == "隐藏的段落"
    assert draft.text_of(COMMENT) == " 注释 "


def test_markdown_comment_inside_code_fence_stays_code() -> None:
    draft = parse_draft("```\n<!-- 注释 -->\n```\n", "md")
    assert draft.text_of(COMMENT) == ""
    assert "<!-- 注释 -->" in draft.text_of(CODE)


def test_markdown_blockquote_lines_are_quoted() -> None:
    draft = parse_draft("> 引用的话\n正文\n", "md")
    flags = {piece.text.strip(): piece.quoted for piece in draft.pieces if piece.text.strip()}
    assert flags == {"> 引用的话": True, "正文": False}


def test_markdown_less_than_signs_are_text() -> None:
    raw = "价格 <20 万，x < y"
    assert parse_draft(raw, "md").text_of(VISIBLE) == raw


# ------------------------------------------------------------------ 读取文件


def test_load_draft_keeps_crlf_and_sets_name(tmp_path: Path) -> None:
    path = tmp_path / "稿件.md"
    path.write_bytes("第一行\r\n第二行".encode())
    draft = load_draft(path)
    assert draft.raw == "第一行\r\n第二行"
    assert draft.name == "稿件.md"
    assert draft.fmt == "md"


@pytest.mark.parametrize(
    ("name", "fmt"), [("a.HTML", "html"), ("a.htm", "html"), ("a.txt", "txt"), ("a.markdown", "md")]
)
def test_load_draft_formats(tmp_path: Path, name: str, fmt: str) -> None:
    path = tmp_path / name
    path.write_text("x", encoding="utf-8")
    assert load_draft(path).fmt == fmt


def test_load_draft_rejects_bad_input(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="不支持的稿件格式"):
        load_draft(tmp_path / "a.docx")
    with pytest.raises(InputError, match="不存在"):
        load_draft(tmp_path / "missing.md")
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe")
    with pytest.raises(InputError, match="UTF-8"):
        load_draft(bad)
    with pytest.raises(InputError, match="未知稿件格式"):
        parse_draft("x", "docx")


# ------------------------------------------------------------------ 随机输入

FRAGMENTS = [
    "澜序",
    "S7",
    " ",
    "\n",
    "\r\n",
    "&amp;",
    "&#8203;",
    "&#x4E2D;",
    "&copy",
    "&",
    "<",
    ">",
    "<p>",
    "</p>",
    "<b>",
    "</b>",
    '<div style="display:none">',
    "</div>",
    '<span style="color:#fff;background:#fff">',
    "</span>",
    "<!-- 注释 -->",
    "<!--",
    "-->",
    "<script>var a = 1 < 2;</script>",
    '<img alt="图&amp;文">',
    "<br/>",
    "`code`",
    "```\n",
    "> ",
    ZWSP,
    "Ｓ７",
    "ß",
]


@settings(max_examples=150, deadline=None)
@given(st.lists(st.sampled_from(FRAGMENTS), max_size=30), st.sampled_from(["md", "html", "txt"]))
def test_every_character_maps_back_into_the_raw_text(parts: list[str], fmt: str) -> None:
    raw = "".join(parts)
    draft = parse_draft(raw, fmt)
    previous = -1
    for piece in draft.pieces:
        assert piece.start >= previous
        previous = piece.start
        for char, start, end in zip(piece.text, piece.starts, piece.ends, strict=True):
            assert 0 <= start < end <= len(raw)
            if end - start == 1:
                assert raw[start] == char
    index = draft.index(VISIBLE, HIDDEN, CODE, COMMENT, SCRIPT, ATTRIBUTE, MARKUP)
    for start in range(0, len(index.text), 3):
        span = index.raw_span(start, min(start + 4, len(index.text)))
        if span is not None:
            assert 0 <= span[0] < span[1] <= len(raw)
