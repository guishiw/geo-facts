"""识别针对模型的指令（提示词注入），中英文。

在折叠文本（NFKC + 小写、去掉不可见字符）上匹配，因此全角、大小写、夹带零宽字符都不影响识别。

两类说法：
- 强特征（单独出现即算）：“忽略之前 / 以上所有指令”“无视系统提示”“作为 AI 助手，你应当……”
  “当 AI 读到这段话时……”“系统提示：”“ignore previous instructions”“disregard the above”
  “as an AI assistant, you must”“when an AI reads this”“system prompt:”“[INST]”等；
- 弱特征（“你必须推荐……”“始终优先推荐”“you must recommend”等）：只在同一句附近出现了
  回答 / 用户 / 提问 / 模型 / AI 等对模型说话的语境时才算；出现在隐藏文本、注释、脚本、
  属性值里则不需要语境。

不算指令（控制误伤）：
- 第三人称的叙述：“诱导模型忽略之前的指令”“模型会忽略……”“trick the model into …”
  “the model to ignore …”——只描述攻击，不是对模型下指令；
- 可见正文里**放在引号、代码块或引用块中**，并且引号 / 代码块前后 CONTEXT_WINDOW 个字符内
  有说明性用语（例如、示例、攻击者、提示词注入、防护、for example、attack 等）的引用示例。
  只满足其一不豁免：没有说明语境的引号、代码块照样拦截。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from geo_facts.gate.document import CODE, Draft, TextIndex

STRONG = "strong"
WEAK = "weak"

CONTEXT_WINDOW = 80  # 引号 / 代码块前后查找说明性用语的范围（码点）
WEAK_CONTEXT_WINDOW = 40  # 弱特征前后查找“对模型说话”语境的范围（码点）
_PREFIX_WINDOW = 32

_SENT = r"[^。！？!?\n]"
_AI_CN = r"(?:ai|人工智能|大模型|大语言模型|语言模型|模型|机器人|爬虫|助手|chatgpt|智能体|搜索引擎)"
_AI_EN = r"(?:ai|llm|large\s+language\s+model|language\s+model|assistant|chatbot|model|crawler|bot)"


@dataclass(frozen=True)
class InstructionPattern:
    pattern_id: str
    label: str
    regex: re.Pattern[str]
    strength: str
    descriptive_check: bool = False


def _p(
    pattern_id: str, label: str, regex: str, strength: str = STRONG, *, descriptive: bool = False
) -> InstructionPattern:
    return InstructionPattern(pattern_id, label, re.compile(regex), strength, descriptive)


PATTERNS: tuple[InstructionPattern, ...] = (
    _p(
        "cn-ignore-instructions",
        "要求忽略之前的指令",
        r"(?:忽略|忽视|无视|忘记|忘掉|不要理会|不用理会|别管|抛开|撇开|放弃|跳过|丢弃)\s*(?:掉)?\s*"
        r"(?:你|您)?\s*(?:之前|以上|上面|上述|前面|先前|此前|原有|原来|原先|已有|全部|所有|一切|系统|开发者)"
        + _SENT
        + r"{0,12}?(?:指令|指示|提示词|提示|规则|设定|要求|约束|命令|限制|instructions?|prompts?)",
        descriptive=True,
    ),
    _p(
        "cn-system-label",
        "伪造系统提示",
        r"(?:系统提示词?|系统指令|system\s*prompt)\s*:"
        r"|【\s*(?:系统|system)\s*(?:指令|提示|消息)?\s*】"
        r"|\[\s*/?\s*(?:system|inst)\s*\]|<\|\s*(?:im_start|system)\s*\|>|<<\s*sys\s*>>",
    ),
    _p(
        "cn-as-ai",
        "以“作为 AI 助手”口吻下指令",
        r"作为\s*(?:一个|一名|一款)?\s*(?:ai|人工智能|大模型|大语言模型|语言模型|模型|智能)\s*"
        r"(?:助手|助理|模型|系统)?\s*[，,、]?\s*你\s*(?:应当|应该|必须|需要|要|务必|只能|一定要)",
    ),
    _p(
        "cn-when-ai-reads",
        "“当 AI 读到这段话时”",
        r"(?:当|如果|若|一旦|假如)\s*" + _AI_CN + _SENT + r"{0,6}?"
        r"(?:读到|看到|读取|处理|抓取|检索到|收到|解析|遇到|阅读)\s*(?:到)?\s*"
        r"(?:这段|本段|此段|这些|以下|这条|这篇|本文|本页|这个页面|该段|这句|此文|此页)",
    ),
    _p(
        "cn-to-ai",
        "写给模型的指令",
        r"(?:给|致|写给|对)\s*" + _AI_CN + r"\s*(?:的)?\s*(?:指令|说明|提示|话|留言)\s*:",
    ),
    _p(
        "cn-role-hijack",
        "改写模型角色",
        r"从现在(?:开始|起)\s*[，,]?\s*你\s*(?:是|扮演|将|必须|要|只能)|你的新(?:任务|指令|角色|身份|设定)是",
    ),
    _p(
        "cn-in-answer",
        "要求在回答中推荐或提及",
        r"(?:^|(?<=[\s，,。.!?;:、]))(?:请|务必|必须|一定要|记得)?\s*在\s*(?:你的|您的)?\s*"
        r"(?:回答|回复|答案|输出|生成的?内容|结果)\s*(?:中|里|时|当中)\s*[，,]?\s*"
        r"(?:请|务必|必须|始终|总是|一律|都|优先|首先|一定|要|永远|\s)*"
        r"(?:推荐|首推|提及|提到|声明|强调|引用|写上|加上|列出|置顶|排在)",
    ),
    _p(
        "cn-you-must",
        "“你必须推荐……”",
        r"(?:你|您)\s*(?:现在)?\s*(?:必须|务必|一定要|一定得|只能|应当|应该|需要|得)\s*"
        r"(?:始终|总是|永远|一律|只|优先|首先|都|\s)*"
        r"(?:推荐|首推|提及|提到|声称|宣传|称赞|夸赞|回答|引用)",
        WEAK,
    ),
    _p(
        "cn-always-recommend",
        "“始终优先推荐”",
        r"(?:始终|总是|永远|一律)\s*(?:优先)?\s*(?:推荐|首推)",
        WEAK,
    ),
    _p(
        "cn-conceal",
        "要求向用户隐瞒",
        r"(?:不要|别|切勿|不得)\s*(?:告诉|透露|提及|告知)\s*(?:给)?\s*用户",
        WEAK,
    ),
    _p(
        "en-ignore-instructions",
        "ignore previous instructions",
        r"\b(?:ignore|disregard|forget|override|bypass|skip)\s+"
        r"(?:(?:all|any|of|the|your|my|these|those)\s+)*"
        r"(?:previous|prior|above|earlier|preceding|foregoing|former|original|initial|existing|system|developer)\s+"
        r"(?:instructions?|prompts?|directions?|rules|guidelines|context|messages?|directives?|commands?)\b",
        descriptive=True,
    ),
    _p(
        "en-ignore-above",
        "disregard the above",
        r"\b(?:ignore|disregard|forget)\s+(?:(?:all|of)\s+)*(?:the\s+|everything\s+)?"
        r"(?:above|foregoing)(?=\s*[.,;:!]|\s+and\b|\s*$)",
        descriptive=True,
    ),
    _p(
        "en-system-label",
        "system prompt:",
        r"\bsystem\s*prompt\s*:|(?:^|\n)\s*system\s*:\s*you\b|\bnew\s+instructions?\s*:",
    ),
    _p(
        "en-as-ai",
        "as an AI assistant, you must",
        r"\bas\s+an?\s+(?:ai|artificial\s+intelligence|large\s+language\s+model|language\s+model|llm|"
        r"ai\s+assistant|assistant|chatbot)\b[^.!?\n]{0,30}?\byou\s+"
        r"(?:must|should|need\s+to|have\s+to|will|are\s+required\s+to)\b",
    ),
    _p(
        "en-when-ai-reads",
        "when an AI reads this",
        r"\b(?:when|if|once|whenever)\s+(?:an?\s+|the\s+|any\s+)?" + _AI_EN + r"s?\s+"
        r"(?:reads?|sees?|processes|encounters?|crawls?|parses?|ingests?|finds?|is\s+reading)\s+"
        r"(?:this|these|the\s+following)\b",
    ),
    _p(
        "en-note-to-ai",
        "note to AI",
        r"\bnote\s+(?:to|for)\s+(?:the\s+|all\s+|any\s+)?(?:ai|llms?|assistants?|models?|chatbots?|"
        r"language\s+models?)\b",
    ),
    _p(
        "en-role-hijack",
        "from now on you are",
        r"\bfrom\s+now\s+on\s*,?\s+you\s+(?:are|will|must|should|shall)\b",
    ),
    _p(
        "en-you-must",
        "you must recommend",
        r"\byou\s+(?:must|should|need\s+to|have\s+to|are\s+required\s+to|shall)\s+"
        r"(?:(?:always|only|first|now)\s+)*"
        r"(?:recommend|mention|promote|state|claim|rank|cite|prefer|endorse)\b",
        WEAK,
    ),
    _p("en-always-recommend", "always recommend", r"\b(?:always|only)\s+recommend\b", WEAK),
    _p(
        "en-conceal",
        "do not tell the user",
        r"\bdo\s+not\s+(?:tell|reveal|mention|disclose)\s+(?:this\s+)?(?:to\s+)?the\s+user\b",
        WEAK,
    ),
)

# 指令动词前的第三人称叙述（描述攻击，而不是对模型下指令）。
_DESCRIPTIVE_CN = re.compile(
    r"(?:(?:让|使|令|叫|要求|诱导|诱使|欺骗|迫使|引导|企图|试图|如何|怎样|怎么|是否|防止|避免|阻止|禁止|不让|教唆)"
    r"\s*(?:模型|ai|大模型|语言模型|助手|系统|机器人|它|其|智能体|对方)?"
    r"|(?:模型|ai|大模型|语言模型|助手|系统|机器人|它|智能体)\s*"
    r"(?:会|能|可能|可以|就会|便会|将|被|不会|无法|不能|不应))\s*(?:去)?\s*$"
)
_DESCRIPTIVE_EN = re.compile(
    r"(?:\b(?:the\s+|a\s+)?(?:model|models|llm|llms|ai|assistant|assistants|chatbot|chatbots|system|"
    r"systems|it|them)\s+(?:to|into)"
    r"|\b(?:make|makes|making|made|let|lets|letting|cause|causes|causing|get|gets|getting|force|"
    r"forces|forcing|trick|tricks|tricking|convince|convinces|persuade|persuades)\s+(?:the\s+|a\s+)?"
    r"(?:model|models|llm|llms|ai|assistant|assistants|chatbot|chatbots|system|systems|it|them)"
    r"|\b(?:attempts?|tries|trying|tried|designed|meant|intended|used)\s+to"
    r"|\b(?:cannot|can't|won't|would|could|might|never|doesn't)"
    r"|\b(?:will|does|do)\s+not"
    r"|\b(?:model|models|llm|llms|ai|assistant|chatbot|it|they|system)\s+"
    r"(?:will|may|can|might|would|could|should))\s+$"
)
# 弱特征需要的“对模型说话”语境。
_AI_CONTEXT = re.compile(
    r"回答|回复|答案|用户|提问|问到|问起|询问|咨询|搜索|检索|对话|模型|助手|机器人|大模型|"
    r"(?<![a-z])(?:ai|chatgpt)(?![a-z])|"
    r"\b(?:answers?|responses?|respond|users?|quer(?:y|ies)|questions?|asked|asks|search|assistant|"
    r"model|llm|chatbot)\b"
)
# 引用示例前后的说明性用语。
_EXPLANATORY = re.compile(
    r"例如|比如|譬如|例子|示例|举例|样例|案例|典型|常见|攻击|恶意|注入|越狱|防护|防御|防范|识别|检测|"
    r"拦截|过滤|警惕|小心|切勿|这类|此类|类似|所谓|话术|手法|"
    r"e\.g\.|\bfor\s+(?:example|instance)\b|\bsuch\s+as\b|\bexamples?\b|\battack(?:s|ers?)?\b|"
    r"\bmalicious\b|\binjection\b|\bjailbreak\b|\bdefen[cs]es?\b|\bdefend\b|\bdetect(?:s|ion|ed)?\b|"
    r"\bbeware\b|\bpayloads?\b|\btypical\b|\bclassic\b|\bcommon\b|\bphrases?\s+like\b"
)
_QUOTE_PAIRS: dict[str, str] = {"“": "”", "「": "」", "『": "』", "‘": "’", "«": "»", '"': '"'}
_MAX_QUOTE = 300


@dataclass(frozen=True)
class InstructionMatch:
    """折叠文本上的一处指令。"""

    start: int
    end: int
    pattern_id: str
    label: str
    strength: str


def quote_regions(text: str) -> list[tuple[int, int]]:
    """引号对的内容区间 [开引号后, 闭引号)；不跨空行，长度有上限。"""
    regions: list[tuple[int, int]] = []
    position = 0
    while position < len(text):
        char = text[position]
        closer = _QUOTE_PAIRS.get(char)
        if closer is None:
            position += 1
            continue
        close = text.find(closer, position + 1, position + 1 + _MAX_QUOTE)
        if close == -1 or "\n\n" in text[position:close]:
            position += 1
            continue
        regions.append((position + 1, close))
        position = close + 1
    return regions


def _descriptive(text: str, start: int) -> bool:
    prefix = text[max(0, start - _PREFIX_WINDOW) : start]
    return bool(_DESCRIPTIVE_CN.search(prefix) or _DESCRIPTIVE_EN.search(prefix))


def _has_ai_context(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - WEAK_CONTEXT_WINDOW) : start].rsplit("\n", 1)[-1]
    after = text[end : end + WEAK_CONTEXT_WINDOW].split("\n", 1)[0]
    return bool(_AI_CONTEXT.search(before) or _AI_CONTEXT.search(after))


def _region_around(
    index: TextIndex, draft: Draft, start: int, end: int, quotes: Sequence[tuple[int, int]]
) -> tuple[int, int] | None:
    """包住 [start, end) 的引号、代码或引用块区间（折叠文本坐标）。"""
    for left, right in quotes:
        if left <= start and end <= right:
            return left - 1, right + 1

    def flag(position: int) -> str:
        owner = index.owners[position]
        if owner < 0:
            return ""
        piece = draft.pieces[owner]
        if piece.channel == CODE:
            return "code"
        return "quoted" if piece.quoted else ""

    kinds = {flag(position) for position in range(start, end) if index.owners[position] >= 0}
    if len(kinds) != 1 or "" in kinds:
        return None
    kind = kinds.pop()
    left, right = start, end
    while left > 0 and (index.owners[left - 1] < 0 or flag(left - 1) == kind):
        left -= 1
    while right < len(index.text) and (index.owners[right] < 0 or flag(right) == kind):
        right += 1
    return left, right


def _explained(text: str, region: tuple[int, int]) -> bool:
    left, right = region
    before = text[max(0, left - CONTEXT_WINDOW) : left]
    after = text[right : right + CONTEXT_WINDOW]
    return bool(_EXPLANATORY.search(before) or _EXPLANATORY.search(after))


def find_instructions(index: TextIndex, draft: Draft, *, lenient: bool) -> list[InstructionMatch]:
    """在一段折叠文本里找指令。lenient=True 用于可见正文：弱特征要求语境，引用示例可豁免；
    隐藏文本、注释、脚本、属性值用 lenient=False。"""
    text = index.text
    quotes = quote_regions(text) if lenient else []
    found: list[InstructionMatch] = []
    for pattern in PATTERNS:
        for match in pattern.regex.finditer(text):
            start, end = match.span()
            while start < end and text[start].isspace():
                start += 1
            if start >= end:
                continue
            if pattern.descriptive_check and _descriptive(text, start):
                continue
            if lenient and pattern.strength == WEAK and not _has_ai_context(text, start, end):
                continue
            if lenient:
                region = _region_around(index, draft, start, end, quotes)
                if region is not None and _explained(text, region):
                    continue
            found.append(
                InstructionMatch(start, end, pattern.pattern_id, pattern.label, pattern.strength)
            )
    return sorted(found, key=lambda item: (item.start, item.end, item.pattern_id))


def contains_instruction(text_index: TextIndex, draft: Draft) -> bool:
    return bool(find_instructions(text_index, draft, lenient=False))
