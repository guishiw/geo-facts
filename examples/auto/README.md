# 虚构汽车品牌完整样例（`examples/auto`）

本目录提供一套可离线运行的完整样例，演示 `geo-facts` 从建库、导入、核验、校准到发布前闸门的全部核心命令。

> **合成数据声明**
>
> 本样例中的品牌（澜序、岑野、朔风）、车型（澜序 S7 / X5 / E3 / M9、岑野 K5 / S7、朔风 T6）、域名（`*.example`）、回答文本、待检稿件与校准数据均为合成虚构（`synthetic: true`），不代表任何真实品牌、产品或真实业务准确率。本工具只给预检结论与建议，不构成独立第三方验证。

## 目录内容

| 文件 | 用途 |
|---|---|
| `run.sh` | 一键串联 `init → import → validate → conflicts → export → claims extract → judge → calibrate → gate` 的完整脚本 |
| `facts_supplement.csv` | 按 `geo-facts import --template csv` 模板编写的补充事实（澜序 X5 电池容量 `F-X5-BATTERY`） |
| `observations.jsonl` | 4 条符合 `observation` 契约的回答样本：包含正确参数、错误参数、过期价格、不同测试工况（NEDC vs CLTC）、竞品同名车型（岑野 S7）以及 1 条无效观测（`timeout`） |
| `prompt_set.json` | 符合 `prompt_set` 契约的封存题库样例，为 Q001～Q004 配置了 `core_fact_ids` |
| `draft.md`、`draft.meta.json` | 待检稿件及 meta：包含隐藏 CSS 提示词注入、过期价格、极限词、竞品比较以及缺少 AI 标识状态 |
| `calibration_gold.jsonl`、`calibration_predicted.jsonl` | 20 条对齐的合成校准样例（19 条一致、1 条误判），用于演示样本量不足时触发“判定精度不足” |

内置于包内的数据（`geo-facts init STORE --example auto`，随 wheel 分发）包含：

- 10 个虚构实体（3 个品牌 + 7 款车型，含与目标车型同名的竞品车型“岑野 S7”）。
- 覆盖三区分治的事实、观点与营销条目，其中特意包含：
  - 带适用条件的续航（`F-S7-RANGE-CLTC` 与 `F-S7-RANGE-WLTC`）；
  - 已过期的旧价版本（`F-S7-PRICE` 版本 1：23.98 万元，`2026-03-18`～`2026-08-31`）与现行价格版本（版本 2：21.98 万元，`2026-09-01` 起有效）；
  - 观点区（`O-S7-COMFORT`、`O-M9-POSITION`）与营销区（`M-S7-SLOGAN`、`M-LANXU-SLOGAN`）条目；
  - 1 条缺少证据的核心事实（`F-M9-ACCEL`）；
  - 1 处未裁决冲突（`F-E3-WEIGHT-A` 1850 kg 与 `F-E3-WEIGHT-B` 1790 kg）。

## 运行方式

在仓库根目录完成 `make setup` 后，无需联网即可运行：

```bash
# 指定输出目录（目录须尚未包含旧的 store / 结果文件）
sh examples/auto/run.sh out/auto-demo

# 或省略参数，自动输出到临时目录
sh examples/auto/run.sh
```

脚本已设可执行权限，也可直接执行 `./examples/auto/run.sh [输出目录]`。

## 按设计非 0 的退出码说明

脚本启用 `set -eu`，仅对下列按设计触发的业务检查退出码显式接收并打印说明，其余意外错误会直接中止脚本：

| 步骤 | 预期退出码 | 原因 |
|---|---|---|
| `validate STORE` | `1` | 样例事实库刻意包含 1 条无证据核心事实和 1 处未裁决冲突 |
| `conflicts STORE` | `1` | 检出 `F-E3-WEIGHT-A` 与 `F-E3-WEIGHT-B` 冲突（只陈列，不自动裁决） |
| `calibrate` | `3` | 20 条样本中 1 条误判，错误率 Wilson 95% 区间上限大于 `1 - 0.98 = 0.02`，输出“判定精度不足，无法证明达标” |
| `gate` | `1` | 稿件触发拦截项（`G-HIDDEN-TEXT`、`G-PROMPT-INJECTION`、`G-FACT-STALE`），同时产出需复核项（`G-LEXICON`、`G-COMPETITOR-COMPARISON`、`G-AI-LABEL`） |

## 关键结果核对

运行结束后可在输出目录查看：

- `judge-summary.json` / `judgments.jsonl`：
  - `obs-auto-002` 中的过期价格 23.98 万元判为 `contradicted`（`ext.reason_code = "stale_match"`），错误参数 580 km / 75 kWh 判为 `contradicted`；
  - `obs-auto-003` 中的 NEDC 续航 680 公里因测试工况不同判为 `unverifiable`（`ext.reason_code = "condition_differs"`），不被误判为矛盾；
  - `obs-auto-004` 因 `status = "invalid"` 被跳过，不进入分母；
  - `facts_to_answer` 同时呈现 `present_correct`、`present_wrong` 与 `missing`。
- `gate.json`：
  - `status` 为 `"block"`，同时包含隐藏文本与提示词注入、过期价格、极限词、竞品比较与 AI 标识状态发现；所有词库命中仅产生 `review`。
