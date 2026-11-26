# 变更记录

格式参考 Keep a Changelog；版本号遵循语义化版本。

## [0.1.0 - 未发布]

### 新增

- **项目骨架与质量工具**：`make setup lint type test check bench`，按模块行覆盖与分支覆盖 ≥ 90% 门槛，CI 配置（Python 3.12 / 3.13 / 3.14）。
- **数据契约副本**：`contracts/v1` 与 `contracts.lock`，`tools/sync_contracts.py` 自检，`geo-facts validate FILE --type CONTRACT`。
- **事实库（F1）**：三区分治（事实/观点/营销表达）、来源等级与内容用途独立、企业自有域名默认 C 级、`--as-of` 版本时效解析、冲突只陈列不自动裁决、只追加哈希链审计日志；命令 `init`、`validate`、`conflicts`、`export`。
- **多格式导入**：`geo-facts import` 支持 `xlsx` / `csv` / `json` / `md` 逐行报错导入与版本递增，并提供 `--template csv|json|md`。
- **归一引擎（F2）**：阿拉伯数字与中文数字、范围、约数容差与上下界、多量纲单位换算与组合数量、三级日期精度、型号与年款、适用条件可比规则。
- **陈述拆解（F3）**：`geo-facts claims extract` 离线规则拆解，保留原文码点位置，支持实体归属、表格表头、否定/问句/假设/变化量排除，并保留插件扩展接口。
- **双向判定（F4）**：`geo-facts judge` 分离“回答 → 事实库”（准确率可核验口径、严格口径与不可核验率）与“事实库 → 回答”（按题核心事实呈现率），条件不同不判矛盾，过期旧版本判矛盾并注明。
- **判定校准（F5）**：`geo-facts calibrate` 输出混淆矩阵、各类 P/R/F1 与 Wilson 双侧置信区间；错误率区间上限超过 `1 − 目标准确率` 时输出“判定精度不足，无法证明达标”并退出 `3`。
- **发布前闸门（F6）**：`geo-facts gate` 实现 11 条规则（事实矛盾、过期/撤回/无证据核心事实、隐藏文本、提示词注入、时间伪造、词库命中、竞品比较、药品功效比较、处方药大众推广、同品牌同主题近似重复、AI 标识状态），内置通用示例词库并支持 `--lexicon-dir` 外部行业词库；词库命中固定只产生 `review`。
- **合成标注集与基准**：`benchmarks/generate.py` 与 `benchmarks/run_benchmark.py`（开发集与保留集分离，不 import 被测实现）。
- **虚构汽车品牌完整样例**：包内 `--example auto` 随 wheel 分发，`examples/auto/` 提供完整输入样本、说明与端到端脚本 `run.sh`。
- **文档与检查测试**：`docs/cli.md`、`docs/quickstart.md`、`docs/methodology.md`、`docs/gate-rules.md`、`docs/clause-map.md`、`tests/test_examples_auto.py` 与 `tests/test_docs.py`。
