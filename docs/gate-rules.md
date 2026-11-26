# 发布前闸门规则（`geo-facts gate`）

本文档列出 `geo-facts gate` 的全部检查规则、触发条件、判定结果、依据条款号与处理建议。

> **重要声明**
>
> - **闸门只给建议和状态，不负责发布**，不构成独立第三方验证（`047 §15.2`）。
> - **词库命中只产生“需复核”（`review`），永远不会单独作为“拦截”（`block`）依据**（`046 §8.3.5`）。每条规则的结果类型在代码层固定绑定，外部词库无法将词库命中升级为 `block`。
> - 公开仓库内置的通用示例词库（`src/geo_facts/lexicons/generic.json`）**未经法务审阅，仅作演示**；用于真实业务前须由法务审阅词条并通过 `--lexicon-dir` 加载外部行业词库。
> - 本工具只支持纠正错误和过期信息，**不提供任何“压制、挤出真实负面”的功能**（`047 §9.3`）。

---

## 1. 状态汇总与退出码

闸门依次运行所有已登记的检查项，按最严重结果汇总整篇稿件状态：

| 状态（`status`） | 中文标签（`status_label`） | 触发条件 | CLI 退出码 |
|---|---|---|---|
| `pass` | 未发现问题 | 已执行的检查项均无发现 | `0` |
| `review` | 需复核 | 无 `block` 发现，且至少有 1 条 `review` 发现 | `5` |
| `block` | 拦截 | 至少有 1 条 `block` 发现（不论是否同时存在 `review`） | `1` |

输入文件不存在、格式错误或词库/meta 字段非法时返回退出码 `2`。

每个定位到正文的发现均给出 `span: [start, end]`（稿件原文的 Unicode 码点左闭右开区间），并严格满足 `原文[start:end] == excerpt`；整篇级别的发现 `span` 为 `null`、`excerpt` 为 `""`。

---

## 2. 规则编号表

| 规则编号（`rule_id`） | 结果 | 触发条件 | 依据条款号 | 建议 |
|---|---|---|---|---|
| `G-FACT-CONTRADICTION` | `block` | 稿件可见正文拆出的陈述，按 `--as-of` 与事实库当日有效事实矛盾（且不命中 `G-FACT-STALE`） | 待人工核对 | 更正为事实库当日有效的值 |
| `G-FACT-STALE` | `block` | 稿件可见正文拆出的陈述命中事实区的以下四类之一（优先级从高到低）：已过期版本（`expired`）、已撤回版本（`retracted`）、同有效期内已被更高版本取代的旧版本（`superseded`）、或**没有绑定证据的核心事实**（`no_evidence`） | 无证据核心事实：`026 §6.2`；过期/撤回/已更正版本：待人工核对 | 补充证据并复核、更正为当日有效事实，或删除该内容 |
| `G-HIDDEN-TEXT` | `block` | ① 含异常不可见字符（零宽字符、双向控制字符、Unicode 标签字符，含 HTML 字符引用解码所得；豁免文件头 BOM、emoji ZWJ、合理连写/方向符/软连字符）；② 隐藏容器（`hidden`、`template`、`display:none` 等 CSS 隐藏）含针对模型的指令，或单段内容量 ≥ 8（导航元素 ≥ 16）；③ `aria-hidden` 区域含针对模型的指令；④ 非条件 HTML 注释内容量 ≥ 20 或含针对模型的指令 | 无对应条款，本工具自定规则 | 删除不可见字符或隐藏内容，或改为对读者可见的正文 |
| `G-PROMPT-INJECTION` | `block` | 出现针对模型的中英文提示词注入指令。分 6 个通道扫描：正文与代码块（对引用示例与弱特征有语境豁免）、隐藏文本、HTML 注释、脚本或样式、属性值、未解析标记（后五类不豁免） | 无对应条款，本工具自定规则 | 删除针对模型的指令性内容 |
| `G-TIME-FORGED` | `block` | ① `meta.updated_at` 早于 `meta.created_at`；② `meta` 中的 `created_at` / `updated_at` / `published_at` 晚于 `--now`；③ 可见正文声称的更新时间（如“更新于 2026-10-01”“Last updated: ...”）与 `meta.updated_at` 不一致、早于 `meta.created_at` 或晚于当前日期 | `026 §7.3` | 核对并如实填写时间；本工具不改写任何时间 |
| `G-LEXICON` | `review` | 在可见正文或隐藏文本中命中内置示例词库或 `--lexicon-dir` 外部词库的 `superlative`（极限词）、`exaggerated_promise`（夸大承诺）或 `industry_sensitive`（行业敏感词）词条 | `046 §8.3.5`（外部词条填写的条款号仅在白名单内照用，其余写“待人工核对”） | 交人工复核：改为可核验的客观表述，或补充证据 |
| `G-COMPETITOR-COMPARISON` | `review` | 在可见正文或隐藏文本的同一分句中提及 `--store` 中的竞品（`role = competitor`），且出现比较/测评表达（如“优于、不如、对比、横评、碾压、vs”），或出现弱比较词（如“高于、领先”）且同一分句含 ≥ 2 个不同实体 | 无对应条款，本工具自定规则 | 补充可核验的对比证据（测试条件、来源、日期） |
| `G-DRUG-COMPARISON` | `review`（`risk_level = highest`，`requires` 含 `legal_confirmation`） | ① 命中词库中 `category = "drug_comparison"` 的词条；或 ② `meta.product_type` 为药品（`prescription_drug` / `otc_drug` / `drug`）且触发竞品比较 | 待人工核对（若词库词条在白名单内给出条款号则照用） | 交法务确认；补充可核验的证据 |
| `G-RX-PROMOTION` | `review`（`risk_level = highest`，`requires` 含 `legal_confirmation`） | ① `meta.product_type == "prescription_drug"` 且 `meta.audience == "public"`（整篇级别）；或 ② 命中词库中 `category = "prescription_promotion"` 的词条 | 待人工核对（若词库词条在白名单内给出条款号则照用） | 交法务确认 |
| `G-NEAR-DUPLICATE` | `review` | 与 `--corpus` 中同 `brand_entity_id`、同 `topic`（折叠去空白与连字符后相同）的既有稿件，按可见正文 4-gram 字符 shingle 计算的 Jaccard 相似度 ≥ `0.5`（双方 shingle 数均 ≥ `10`；稿件自身不参与比较） | 无对应条款，本工具自定规则 | 人工复核是否重复发布；必要时合并或改写 |
| `G-AI-LABEL` | `review` | 整篇级别检查 `meta`：① 未提供 `meta`（标识状态未知）；② `meta` 缺少 `ai_generated` 字段（标识状态未知）；③ `ai_generated = true` 且 `ai_label` 缺失、为 `false` 或为空字符串 | 无对应条款，本工具自定规则 | 在 `meta` 中如实填写 `ai_generated`；AI 生成的内容补充 `ai_label` 标识 |

---

## 3. 缺省输入与 `skipped_checks`

闸门绝不把“因缺少输入而未执行检查”伪装成“未发现问题”。缺少必要参数时，对应检查项记入输出 JSON 的 `skipped_checks`：

| 检查项（`check`） | 涉及规则 | 跳过条件 |
|---|---|---|
| `facts` | `G-FACT-CONTRADICTION`、`G-FACT-STALE` | 未提供 `--store` |
| `competitor_comparison` | `G-COMPETITOR-COMPARISON`、`G-DRUG-COMPARISON` | 未提供 `--store` |
| `near_duplicate` | `G-NEAR-DUPLICATE` | 未提供 `--corpus`，或 `meta` 缺少 `brand_entity_id` / `topic` |

其余检查项（`hidden_text`、`prompt_injection`、`time_forged`、`lexicon`、`rx_audience`、`ai_label`）在仅提供 `--draft` 时也会执行。

---

## 4. 词库加载与外部行业词库

- 每次运行 `gate` 都会先加载内置通用示例词库 `src/geo_facts/lexicons/generic.json`（`lexicon_id = "generic-sample"`）。
- 通过 `--lexicon-dir DIR`（可重复指定多个目录）加载外部行业词库，按文件名顺序读取目录下的所有 `*.json`。
- 词库格式错误、正则无法编译或 `lexicon_id` 重复均按输入错误拒绝（退出码 `2`）。详细字段要求见 [接口文档](cli.md#词库格式)。
- 无论外部词库如何配置 `risk_level` 或 `requires`，词库规则永远只产出 `review`，绝不会变为 `block`；其中 `drug_comparison` 和 `prescription_promotion` 两类由引擎强制赋予 `risk_level = "highest"` 且 `requires` 包含 `"legal_confirmation"`。
