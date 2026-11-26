# 合成标注集（benchmarks/）

> **合成集结果只能说明实现符合预期，不代表真实业务准确率。**
> 自由文本判定没有真实金标，在有真实金标之前标为“未验证”。
> 调参只用开发集（dev）；保留集（holdout）只在里程碑验收时运行，每次运行都要记录。

这里有两个脚本：

- `generate.py` 是“出题人”：先定下真值，再按真值写出回答文本和金标。
- `run_benchmark.py` 只通过 CLI 子进程（`python -m geo_facts ...`）驱动被测实现，再按门槛判定。

两个脚本都**不 import geo_facts**，测试会扫描源码确认这一点。契约校验用 jsonschema 直接读 `contracts/v1/*.schema.json`。生成的数据不提交：`benchmarks/out/` 和 `benchmarks/runs.jsonl` 已在 .gitignore 里。

## 生成数据

```sh
python benchmarks/generate.py --split dev|holdout --out DIR [--seed N]
```

- 默认 seed：dev 为 20261008，holdout 为 90417。同一 split、同一 seed 的输出逐字节一致。
- 输出目录：

```text
DIR/
├── store-src/entities.jsonl   entity 契约：3 个品牌 + 各车型（含同名的竞品车型“岑野 S7”）
├── store-src/facts.csv        事实源（可由 geo-facts import 导入），列见下
├── store-src/facts.json       与 facts.csv 同内容的 fact 契约记录（数组），便于核对或用 json 格式导入
├── observations.jsonl         observation 契约；每条回答含 1～3 条待判定陈述和若干不含事实的填充句
├── gold.jsonl                 金标，每行一条
├── injection/                 attack-NNN.{md,html,txt}、benign-NNN.{md,html,txt}
├── injection/labels.jsonl     {"file", "label": "attack" | "benign", "kind", "synthetic": true}
└── meta.json                  {split, seed, generator_version, as_of, project_id, counts{...}, synthetic: true, note}
```

- `facts.csv` 的列依次是：fact_id、entity_id、attribute、aliases、value、unit、conditions、zone、source_tier、content_use、is_core、evidence_id、source_name、source_ref、collected_at、last_verified_at、reviewer、valid_from、valid_until、status。
  - aliases 用 `|` 分隔；conditions 写成 `key=value;key=value`；空值留空。
  - 同一 fact_id 有两个版本（过期旧价在前、现价在后），版本号由导入时按顺序递增。

### 开发集与保留集的区别

- **车型**：dev 有澜序 S7 / X5 / E3 / M9、岑野 K5 / S7、朔风 T6；holdout 有澜序 S7 / X5 / S9 / X3、岑野 K5 / S7 / K7、朔风 T6 / T8。
- **属性**：holdout 多出快充时间（带 SOC 区间条件）、最高车速、整备质量、电池型号。
- **取值**：每个 split 按各自的 seed 随机生成。
- **写法**：句式模板分为共用、dev 专用、holdout 专用三组。holdout 有开发集没有的列表写法、括号工况、`2026/3/19` 日期、吨、公里/小时、`10% 充到 80%` 等变体。
- **注入类**：holdout 多出 clip、transform、height:0、template、字符引用写的零宽字符等攻击，以及另一组提示词说法。

## 金标字段

```json
{"id", "observation_id", "span": [start, end], "text", "entity_id", "attribute",
 "category": "numeric" | "model" | "date",
 "expected": "supported" | "contradicted" | "unverifiable",
 "hard": bool, "hard_kind": str | null, "condition_differs": bool, "synthetic": true}
```

- `span` 是陈述所在句子在 answer_text 中的码点位置，满足 `answer_text[start:end] == text`。
- 一句里有两条陈述时，两条金标的 span 相同（同一句）。

### hard_kind

| 取值 | 写法 |
|---|---|
| unit_conversion | 219800 元 ↔ 21.98 万元、千米、度 / 度电、吨、公里/小时 |
| approx | 约 / 大约 / 左右 / 上下 / X 多 / 近 X |
| range | 600～650、600-650、600 至 650 |
| chinese_numeral | 六百二十公里、二十一万九千八百元、五点九秒、二〇二六年三月十八日 |
| thousands_separator | 219,800 元、1,850 kg |
| width_case | 全角、大小写、空格、连字符（澜序Ｓ７、lanxu s7、澜序 S-7、ＬＸ－Ｍ２２０） |
| condition_differs | 陈述写 WLTC / NEDC，而事实库只有 CLTC；陈述写的版本或 SOC 区间与事实库不同。一律 unverifiable，`condition_differs = true` |
| expired_version | 值等于已过期的旧价，判 contradicted |
| competitor_same_name | “岑野 S7”的参数：与岑野 S7 自己的事实比，不得拿澜序 S7 的事实去判；岑野 S7 没有的属性判 unverifiable |
| date_precision | “2026 年 3 月上市”“2026 年上市”对 2026-03-18 |
| entity_omission | “它的……”“该车……”“这款车……”指上一句提到的车型 |
| multi_claim | 一句两条陈述，包括两车型对比句“A 续航 x 公里，B 为 y 公里” |
| tolerance_boundary | 约数恰在容差边界内（60%～90%）或外（115%～160%） |

## 金标口径

口径与 normalize 文档声明的规则一致。生成器在本文件里独立实现这套口径，没有调用被测代码。

- **精确值**：没有限定词的数值按精确值比较，不加容差。
- **约 X**：“约 / 大约 / 左右 / 上下 X”的容差 t = max(X 末位有效数字所在位的一半, |X| 的 2%)。例如约 620 对应 [607.6, 632.4]。
- **X 多**：取 [X, X + 末位所在位)，例如 600 多对应 [600, 700)。
- **近 X**：取 [0.9X, X]。
- **范围**：范围是闭区间。出题时真值严格落在区间内部，不出端点题。
- **日期**：按两边中较粗的精度比较。
- **条件**：陈述写明的每个条件，事实库里都要有同名同值的条件才可比，否则判 unverifiable，不得判矛盾。事实多出的条件（如“长续航版”）不影响可比。
- **没写工况**：陈述没写工况、事实库有多个工况时，只出与 CLTC 值一致（supported）或与所有工况值都不一致（contradicted）的题。
- **拿不准的不出题**：约数题与容差边界至少相差 10%；“近 X”离 0.9X 的下界太近时换题；“X 多”不出真值恰为整百的题；年款不出题，因为事实库不带年款条件。

## 运行基准

```sh
make bench                                          # = python benchmarks/run_benchmark.py --split dev
python benchmarks/run_benchmark.py --split dev|holdout [--workdir DIR] [--json]
       [--seed N] [--now T] [--only judgment|injection] [--show-failures] [--jobs N]
```

- 默认工作目录是 `benchmarks/out/<split>`，每次运行清空重建。目录里带 `.bench-workdir` 标记；指定的目录非空又没有标记时，拒绝运行。
- **判定类**按顺序执行：
  1. `geo-facts init STORE --project-id bench-auto --now T`；
  2. 把 store-src/entities.jsonl 追加到 `STORE/entities.jsonl`（CLI 约定里没有导入实体的命令，按事实库目录格式直接追加）；
  3. `geo-facts import STORE --input facts.csv --format csv --now T`；
  4. `geo-facts judge --store STORE --observations observations.jsonl --as-of <meta.as_of> --out judgments.jsonl --claims-out claims.jsonl --now T`。
- **注入类**：对每份稿件执行 `geo-facts gate --draft FILE --now T --json`，可以并行。
- 任一 CLI 步骤失败，该部分记为“运行失败”，整次运行不达标。

### 对齐规则

- 只取 `direction = answer_to_facts` 的判定，用 claim_id 找到 claims.jsonl 里的陈述，按“陈述的 span 落在金标的 span 内”（同一观测）对齐。
- 同一金标对应多条判定时，有 contradicted 取 contradicted，否则有 supported 取 supported，否则 unverifiable；没有对应判定的视为 unverifiable。
- **补充**：一句里有多条金标时，落进该句的判定只归给实体和属性都相同的金标；找不到这样的金标时，才归给该句的全部金标。

### 门槛

不达标时退出 1。

| 指标 | 门槛 | 分母 |
|---|---|---|
| 矛盾检出召回率 | ≥ 0.95 | expected = contradicted 的金标 |
| 矛盾判定精确率 | ≥ 0.95 | 对齐后判为 contradicted 的金标 |
| 条件不同被误判为矛盾的比例 | ≤ 0.02 | condition_differs = true 的金标 |
| 攻击样本检出率 | = 1（全部检出） | attack 样本；闸门给出 G-HIDDEN-TEXT 或 G-PROMPT-INJECTION 的 block 才算检出 |
| 正常样本误拦率 | ≤ 0.02 | benign 样本；闸门给出上述 block 即算误拦 |

只统计 numeric / model / date 三类金标。分母为 0 时该项不达标。

### 输出与记录

- **dev**：输出不达标样本的 id（漏检的矛盾、误判的矛盾、条件误判、漏检的攻击、误拦的正常样本），供分析。
- **holdout**：默认只输出汇总数值，不输出逐样本明细，也不输出分类明细。确实需要明细时必须加 `--show-failures`，输出和运行记录都会标明“已查看保留集明细”。
- `--only` 只运行一部分，供开发用。这时 `complete = false`，输出会写明另一部分“未运行”。
- 每次运行都往 `benchmarks/runs.jsonl` 追加一行：`{time, split, seed, commit, dirty, parts, metrics, passed, complete, holdout_details_viewed, synthetic}`。
- 里程碑验收时，把保留集的运行记录写进 VERIFICATION.md。
