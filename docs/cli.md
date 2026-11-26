# 命令与接口说明

本文是 `geo-facts` 的正式接口文档：事实库目录结构、每个命令的参数与输出结构、导入模板列说明、闸门规则编号表，以及词库、`meta` 与 `corpus` 格式。所有命令说明均与源码及 `--help` 核对一致。

`geo-facts` 与 `geo-verify` 是两个独立的公开仓库，**只通过文件（数据契约）交换数据，互不 import**。契约副本位于 [`contracts/v1/`](../contracts/v1/) 并由 [`contracts/contracts.lock`](../contracts/contracts.lock) 锁定哈希。

---

## 通用约定

- **输出格式**：全部叶子命令支持 `--json`，输出机器可读 JSON；不加时输出中文可读说明。
- **退出码**：

| 退出码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 检查未通过（如 `validate` 不通过、`import` 含非法行、`conflicts` 发现冲突、`gate` 存在 `block`） |
| `2` | 输入错误（参数缺失、格式不合法、路径已存在拒绝覆盖、词库或 `meta` 字段错误等） |
| `3` | 数据不足（如 `calibrate` 无对齐样本，或判定精度不足） |
| `4` | 内部错误 |
| `5` | `gate` 专用：无 `block`、仅存在 `review` |

- **可复现性**：会写入时间戳的命令支持 `--now <UTC 时间>`（如 `2026-10-08T02:00:00Z`），传入后输出逐字节一致；`claim_id` 与 `judgment_id` 由内容哈希派生。
- **只追加与防覆盖**：事实库的 `entities.jsonl`、`facts.jsonl`、`audit.jsonl` 只追加不改写；`claims extract`、`judge` 的输出文件及 `import --template --out` 若目标已存在则直接拒绝（退出 `2`）。
- **默认离线**：规则实现默认离线可用，不访问任何外部网络。

---

## 事实库目录结构

```text
STORE/
├── store.json        元数据：{schema_version, store_id, project_id, timezone, created_at, synthetic?}
├── entities.jsonl    entity 契约（品牌、产品、竞品及别名、排除词、自有域名），只追加
├── facts.jsonl       fact 契约，只追加；同一 fact_id 的多个 version 全部保留
└── audit.jsonl       audit_event 契约（初始化、导入等变更记录，含前后状态哈希链），只追加
```

---

## 命令参考

### `geo-facts init`

新建事实库目录（目录已存在且非空时拒绝覆盖，退出 `2`）。

```text
geo-facts init STORE [--example auto] [--project-id ID] [--store-id ID] [--timezone TIMEZONE] [--synthetic] [--now T] [--json]
```

- `--example auto`：写入内置的虚构汽车品牌样例（10 个实体、25 条事实共 26 个版本，全部 `synthetic: true`），随 wheel 分发。
- `--project-id ID`：项目标识；使用 `--example` 时可省略，默认 `auto-demo`。
- `--store-id ID`：事实库标识，默认 `store-<project_id>`。
- `--timezone TIMEZONE`：按日统计时区，默认 `Asia/Shanghai`。
- `--synthetic`：标记事实库为合成数据（使用 `--example` 时自动为 `true`）。
- `--now T`：写入记录的 UTC 时间。

### `geo-facts import`

把 `xlsx` / `csv` / `json` / `md` 文件导入事实库，或输出导入模板与列说明。

```text
geo-facts import STORE --input FILE [--format xlsx|csv|json|md] [--reviewer NAME] [--now T] [--json]
geo-facts import --template csv|json|md [--out FILE] [--json]
```

- 合法行写入，非法行不写入并逐行报错（行号、列、原因）；存在任何非法行时退出 `1`，仍会写入审计事件留痕。
- 同一 `fact_id` 内容有变化时自动追加 `version + 1`；内容完全相同则跳过并计数；出现未知列名时整份文件按输入错误拒绝（退出 `2`）。
- `--format` 省略时按文件扩展名推断；支持导入 `json` / `jsonl` 格式的实体记录（`entity` 契约）或事实记录。
- `--reviewer NAME`：补进证据中未填 `reviewer` 的行，并记为审计事件的变更人。
- `--template csv|json|md`：输出导入模板；搭配 `--out FILE` 写出文件（`csv` 自动带 UTF-8 BOM；目标已存在时拒绝）。

#### 导入模板列说明

模板共 25 列（与 `geo-facts export --format csv` 兼容；导出文件末尾多一列可选的 `version`）：

| 列名 | 是否必填 | 说明 |
|---|---|---|
| `fact_id` | 必填 | 事实编号（字母或数字开头，可含 `. _ : -`）；内容变化时自动追加新版本 |
| `entity_id` | 必填 | 实体编号，须已存在于 `entities.jsonl`（或在同一文件内一并导入） |
| `attribute` | 必填 | 属性名，如 `续航里程`、`售价`、`上市时间` |
| `value` | 必填 | 值：数字、范围（`600~650`）、布尔（`true`/`false`）或文本（如 `2026-03-18`） |
| `value_type` | 可选 | `number` / `range` / `boolean` / `text`；留空自动识别 |
| `unit` | 可选 | 单位，如 `km`、`万元`、`kWh`、`s`；无单位留空 |
| `conditions` | 可选 | 适用条件，写成 `键=值; 键=值`（如 `test_cycle=CLTC; trim=长续航版`） |
| `zone` | 必填 | 分区：`fact`（事实）/ `opinion`（观点）/ `marketing`（营销表达） |
| `source_tier` | 事实区必填 | 来源等级 `A`～`D`，与 `content_use` 独立；事实区留空且 `source_ref` 属自有域名时默认补 `C` 并提示 |
| `source_type` | 可选 | 来源类型文字说明（如 `企业官网`），仅作记录，不影响来源等级 |
| `content_use` | 必填 | 内容用途：`specification` / `pricing` / `policy` / `test_result` / `review` / `promotion` / `other` |
| `is_core` | 可选 | 是否核心事实：`true` / `false`，默认 `false`；核心事实须绑定证据（`026 §6.2`） |
| `evidence_id` | 有证据时必填 | 证据编号（以下 6 个证据列须全填或全留空） |
| `source_name` | 有证据时必填 | 来源名称 |
| `source_ref` | 有证据时必填 | 原始链接或文件路径 |
| `collected_at` | 有证据时必填 | 采集时间（UTC ISO 8601，如 `2026-09-20T02:00:00Z`） |
| `last_verified_at` | 有证据时必填 | 最近核验时间（UTC，不得早于 `collected_at`） |
| `reviewer` | 有证据时必填 | 审核责任人；留空时取命令行 `--reviewer` |
| `valid_from` | 必填 | 生效日期 `YYYY-MM-DD`（含当天） |
| `valid_until` | 可选 | 失效日期 `YYYY-MM-DD`（含当天）；留空表示尚未失效 |
| `status` | 可选 | `active`（默认）/ `retracted`（撤回）/ `disputed`（有争议） |
| `aliases` | 可选 | 属性别名，分号分隔（如 `续航; 续航能力`） |
| `note` | 可选 | 备注 |
| `synthetic` | 可选 | 是否合成数据 `true` / `false`；合成事实库一律记为 `true` |
| `ext` | 可选 | 扩展字段（JSON 对象） |
| `version` | 可选 | 导出文件附带；留空自动编号，填写时已有同内容版本跳过、不同内容报错，新版本须等于最新版本号 + 1 |

### `geo-facts validate`

支持两种用法：检查事实库整体一致性，或按数据契约校验单个 `.json` / `.jsonl` 文件。

```text
geo-facts validate STORE [--as-of DATE] [--json]
geo-facts validate FILE --type CONTRACT [--json]
```

- **事实库检查**（位置参数为目录，不带 `--type`）：检查契约、项目归属、实体引用、版本号、有效期先后、核心事实证据绑定、冲突与审计链完整性；存在 `errors` 退出 `1`，仅有 `warnings` 退出 `0`。`--as-of` 默认取事实库时区的今天；不给 `--as-of` 时冲突按“任意一天重叠”检查。
- **契约校验**（带 `--type CONTRACT`，不能与 `--as-of` 同用）：逐条按 `contracts/v1/<CONTRACT>.schema.json` 校验，存在不符合契约的记录退出 `1`。

### `geo-facts conflicts`

列出事实库中的冲突清单（同实体、同属性/别名、同条件、有效期重叠但归一值不同）。只陈列，不自动裁决。

```text
geo-facts conflicts STORE [--as-of DATE] [--json]
```

- `--as-of DATE`：仅列出该日仍存在的冲突；不给则列出任意一天有重叠的冲突。
- 发现冲突退出 `1`，无冲突退出 `0`。

### `geo-facts export`

导出事实库记录为 JSONL 或 CSV。

```text
geo-facts export STORE [--as-of DATE] [--format jsonl|csv] [--out FILE] [--json]
```

- `--as-of DATE`：仅导出该日有效的最高版本；省略则导出全部版本。
- `--format`：默认 `jsonl`；`csv` 写出时带 UTF-8 BOM 并防公式注入，可直接由 `geo-facts import` 再导入。
- `--out FILE`：写入指定文件；省略则打印到标准输出。

### `geo-facts claims extract`

从回答样本或单个文本文件拆出结构化陈述，写出逐条符合 `claim` 契约的 `claims.jsonl`。

```text
geo-facts claims extract --store STORE --observations FILE --out FILE [--extractor EXTRACTOR] [--json]
geo-facts claims extract --store STORE --text FILE --source-id ID [--source-type draft|observation] --out FILE [--extractor EXTRACTOR] [--json]
```

- `--observations` 与 `--text` 必须且只能二选一；`--observations` 只处理 `status = "valid"` 且 `answer_text` 非空的观测。
- `--source-id` 与 `--source-type`（默认 `draft`）仅用于 `--text`。
- `--extractor`：拆解器名称，默认且当前已注册 `rules`（离线规则实现）。
- `--out FILE`：已存在时拒绝覆盖（退出 `2`）。

### `geo-facts judge`

执行双向判定，写出逐条符合 `judgment` 契约的 `judgments.jsonl`（供 `geo-verify` 读取计算准确率与呈现率），可选同时写出 `claims.jsonl`。

```text
geo-facts judge --store STORE --observations FILE [--prompt-set FILE] --as-of DATE --out FILE [--claims-out FILE] [--now T] [--json]
```

- `--observations FILE`：符合 `observation` 契约的 JSONL；只处理 `status = "valid"` 且 `answer_text` 非空的记录。
- `--prompt-set FILE`：符合 `prompt_set` 契约的封存题库，其中 `prompts[].core_fact_ids` 为按题配置的核心事实清单；未提供时不执行 `facts_to_answer` 方向。
- `--as-of DATE`：必填，`YYYY-MM-DD`，按该日取事实库有效版本。
- `--out FILE` 与 `--claims-out FILE` 不能指向同一文件，且已存在时拒绝覆盖。

#### `judge --json` 摘要结构

```json
{
  "as_of": "2026-10-08",
  "observations": 3,
  "observation_stats": {"total": 4, "used": 3, "skipped_invalid": 1, "skipped_empty": 0},
  "claims": 11,
  "answer_to_facts": {
    "supported": 7,
    "contradicted": 3,
    "unverifiable": 1,
    "total": 11,
    "accuracy_verifiable": 0.7,
    "accuracy_strict": 0.6363636363636364,
    "unverifiable_rate": 0.09090909090909091,
    "null_reasons": {},
    "unverifiable_reasons": {"condition_differs": 1},
    "stale_contradictions": 1
  },
  "facts_to_answer": {
    "present_correct": 4,
    "present_wrong": 2,
    "missing": 1,
    "total": 7,
    "presence_rate": 0.5714285714285714,
    "null_reason": null,
    "observations_with_core_facts": 3,
    "excluded": 0,
    "excluded_core_facts": []
  },
  "basis_note": "准确率有两个口径……验收用哪个口径须在项目预先登记里事前登记……",
  "outputs": {"judgments": "out/judgments.jsonl", "claims": "out/claims.jsonl"}
}
```

- `judgments.jsonl` 中：`direction = "answer_to_facts"` 对应陈述（`claim_id` 非空）；`direction = "facts_to_answer"` 对应（观测，核心事实）（`claim_id` 为 `null`，`fact_id` 非空）。`source` 为 `{"type": "observation", "id": "<observation_id>"}`。

### `geo-facts calibrate`

对照人工金标集校准判定器精度。

```text
geo-facts calibrate --gold GOLD --predicted PREDICTED [--target TARGET] [--confidence CONFIDENCE] [--json]
```

- `--gold`、`--predicted`：JSONL 文件，每行至少含 `{"id": "...", "label": "..."}`（均为非空字符串）；同一文件内 `id` 重复报输入错误（退出 `2`）。
- `--target`：目标准确率（`(0, 1)`，默认 `0.98`）；`--confidence`：Wilson 双侧区间置信水平（`(0, 1)`，默认 `0.95`）。
- 仅在一边出现的 `id` 计数并写入 `unmatched`，不参与计算；对齐样本数为 0 时退出 `3`。
- 当 `error_rate_interval.high > 1 - target` 时，`conclusion.code = "insufficient_precision"`、`conclusion.text = "判定精度不足，无法证明达标"`，退出码 **`3`**；否则 `conclusion.code = "adequate"`，退出码 `0`。

#### `calibrate --json` 输出结构

包含：`counts`（`gold`、`predicted`、`aligned`、`correct`、`errors`、`only_in_gold`、`only_in_predicted`）、`unmatched`、`unknown_predicted_labels`、`labels`、`confusion_matrix`、`per_label`（各标签的 `support`、`predicted`、`true_positive`、`precision`、`precision_interval`、`recall`、`recall_interval`、`f1`、`null_reasons`）、`accuracy`、`accuracy_interval`、`error_rate`、`error_rate_interval`、`target`、`allowed_error_rate`、`confidence`、`method`、`conclusion`、`notes`、`notice`。

### `geo-facts gate`

对发布前稿件执行预检，输出 `pass` / `review` / `block` 状态、逐条原文位置与建议。有 `block` 退出 `1`，只有 `review` 退出 `5`，无发现退出 `0`。

```text
geo-facts gate --draft DRAFT [--meta META] [--store STORE] [--lexicon-dir LEXICON_DIR]... [--corpus CORPUS] [--as-of AS_OF] [--out OUT] [--now NOW] [--json]
```

- `--draft DRAFT`：待检稿件，支持 `.md` / `.markdown` / `.txt` / `.html` / `.htm`。
- `--meta META`：稿件元数据 JSON（可缺省）。
- `--store STORE`：事实库目录，提供事实检查、竞品名称识别与时区；缺省时事实类与竞品检查记入 `skipped_checks`。
- `--lexicon-dir LEXICON_DIR`：外部行业词库目录（可重复指定），加载其中全部 `*.json`；内置通用示例词库总会先加载。
- `--corpus CORPUS`：既有稿件目录，用于同品牌同主题近似重复检查。
- `--as-of AS_OF`：按该日（`YYYY-MM-DD`）取有效事实；缺省时取 `--now` 在事实库时区（默认 `Asia/Shanghai`）下的自然日。
- `--out OUT`：将完整 JSON 报告写入文件。

#### `gate` 输出结构

```json
{
  "status": "block",
  "status_label": "拦截",
  "findings": [
    {
      "rule_id": "G-LEXICON",
      "result": "review",
      "span": [83, 87],
      "excerpt": "同级第一",
      "reason": "命中词库 generic-sample 的极限词词条……",
      "suggestion": "交人工复核：改为可核验的客观表述，或补充证据",
      "clause": "046 §8.3.5",
      "risk_level": "high",
      "requires": [],
      "details": {"lexicon_id": "generic-sample", "category": "superlative", "term": "...", "match": "regex"}
    }
  ],
  "counts": {"block": 3, "review": 4},
  "rule_counts": {"G-AI-LABEL": 1, "G-COMPETITOR-COMPARISON": 1, "G-FACT-STALE": 1, "G-HIDDEN-TEXT": 1, "G-LEXICON": 2, "G-PROMPT-INJECTION": 1},
  "checks": [],
  "skipped_checks": [],
  "notice": "闸门只给建议和状态，不负责发布",
  "draft": {"file": "draft.md", "format": "md", "chars": 184, "sha256": "..."},
  "meta_provided": true,
  "as_of": "2026-10-08",
  "timezone": "Asia/Shanghai",
  "lexicons": [],
  "entities": {"total": 10, "competitors": 5},
  "corpus": null,
  "precheck_notice": "本工具只给出预检结论，不构成独立第三方验证。"
}
```

#### 规则编号一览

详见 [闸门规则](gate-rules.md)：

| `rule_id` | 结果 | 简述 |
|---|---|---|
| `G-FACT-CONTRADICTION` | `block` | 与有效事实矛盾 |
| `G-FACT-STALE` | `block` | 使用过期、撤回、已更正或无证据的核心事实 |
| `G-HIDDEN-TEXT` | `block` | 隐藏文本、零宽/双向/标签字符、CSS 隐藏、HTML 注释 |
| `G-PROMPT-INJECTION` | `block` | 针对模型的提示词注入 |
| `G-TIME-FORGED` | `block` | 伪造、晚于当前时间或早于创建的更新时间 |
| `G-LEXICON` | `review` | 词库命中（极限词、夸大承诺、行业敏感词） |
| `G-COMPETITOR-COMPARISON` | `review` | 涉及竞品的比较、测评 |
| `G-DRUG-COMPARISON` | `review` | 药品与其他药品比较功效（`risk_level = highest`，`requires` 含 `legal_confirmation`） |
| `G-RX-PROMOTION` | `review` | 处方药面向大众推广（同上） |
| `G-NEAR-DUPLICATE` | `review` | 同品牌同主题近似重复 |
| `G-AI-LABEL` | `review` | AI 生成内容缺少标识状态 |

---

## 词库格式

内置通用示例词库位于 `src/geo_facts/lexicons/generic.json`；外部行业词库通过 `--lexicon-dir DIR` 加载该目录下所有 `*.json`。

```json
{
  "schema_version": "1.0",
  "lexicon_id": "generic-sample",
  "industry": "generic",
  "description": "通用示例词库，未经法务审阅，仅作演示",
  "synthetic": true,
  "entries": [
    {
      "term": "最佳",
      "match": "literal",
      "category": "superlative",
      "risk_level": "medium",
      "clause": "046 §8.3.5",
      "note": "极限词"
    },
    {
      "term": "根治|治愈率\\s*100%",
      "match": "regex",
      "category": "exaggerated_promise",
      "risk_level": "high",
      "clause": "046 §8.3.5",
      "note": "夸大承诺",
      "requires": ["legal_confirmation"]
    }
  ]
}
```

- 顶层必填：`schema_version`（`1.x`）、`lexicon_id`、`industry`、`entries`；可选：`description`、`synthetic`、`ext`。不允许未知字段。
- 词条必填：`term`（≤ 200 字符）、`match`（`literal` / `regex`）、`category`（`superlative` / `exaggerated_promise` / `industry_sensitive` / `drug_comparison` / `prescription_promotion`）、`risk_level`（`low` / `medium` / `high` / `highest`）；可选：`clause`、`note`、`requires`、`id`。不允许未知字段。
- `lexicon_id` 重复、字段非法或正则无法编译时按输入错误拒绝（退出 `2`）。

---

## `meta` 与 `corpus` 格式

### 稿件 `meta` JSON

```json
{
  "title": "澜序 S7 产品介绍（合成演示稿件）",
  "brand_entity_id": "brand-lanxu",
  "topic": "澜序 S7 产品介绍",
  "created_at": "2026-10-06T02:00:00Z",
  "updated_at": "2026-10-08T02:00:00Z",
  "published_at": null,
  "author": "内容编辑（虚构）",
  "ai_generated": true,
  "ai_label": false,
  "audience": "public",
  "product_type": "ev_suv"
}
```

- 时间字段（`created_at`、`updated_at`、`published_at`）只接受 `YYYY-MM-DD` 或带时区的 ISO 8601 时间（如 `2026-10-08T02:00:00Z` / `2026-10-08T10:00:00+08:00`），不带时区的时间按输入错误拒绝（退出 `2`）。
- `ai_generated` 为布尔值；`ai_label` 为布尔值或非空字符串。其余未列出字段会被忽略；字段类型错误退出 `2`。

### `corpus` 目录格式

仅扫描目录第一层，不递归：

```text
CORPUS/
├── article-a.md
├── article-a.meta.json
├── article-b.html
└── article-b.meta.json
```

- 支持 `.md` / `.markdown` / `.txt` / `.html` / `.htm`，按文件名主干匹配同名 `.meta.json`。
- 同一主干出现多份不同扩展名稿件时按输入错误拒绝（退出 `2`）。
- 缺少 `.meta.json` 或缺少 `brand_entity_id` / `topic` 的稿件不参与重复比较，仅在输出 `corpus.without_brand_or_topic` 中计数。
