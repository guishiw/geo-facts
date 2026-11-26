# 快速开始

本指南演示如何在离线环境中安装 `geo-facts`，并用内置的虚构汽车品牌（澜序汽车）样例跑通从建库、双向判定、精度校准到发布前闸门的完整预检流程。

> 本样例所有数据均为合成虚构（`synthetic: true`），不代表真实品牌或真实业务准确率。工具只提供预检结论，不构成独立第三方验证。

## 1. 环境准备

需要 Python ≥ 3.12 与 [uv](https://docs.astral.sh/uv/)：

```bash
make setup    # 安装依赖（仅首次需要联网）
make check    # 运行静态检查、类型检查、单元测试与覆盖率门槛（离线）
```

## 2. 一键运行完整样例

仓库根目录执行：

```bash
# 指定输出目录
sh examples/auto/run.sh out/auto-demo

# 或省略参数，自动输出到系统临时目录
sh examples/auto/run.sh
```

脚本会串联 `init → import → validate → conflicts → export → claims extract → judge → calibrate → gate` 共 10 个步骤，并显式说明按设计触发的非 0 退出码。详细文件清单见 [examples/auto/README.md](../examples/auto/README.md)。

## 3. 分步上手

以下演示各核心命令的独立用法（默认使用 `.venv/bin/geo-facts`，若已激活虚拟环境可直接用 `geo-facts`）：

### 3.1 初始化与导入事实

```bash
# 使用内置虚构汽车品牌样例初始化事实库
.venv/bin/geo-facts init out/demo/store --example auto --now 2026-10-08T02:00:00Z

# 导出导入模板（支持 csv / json / md）
.venv/bin/geo-facts import --template csv --out out/demo/template.csv

# 导入补充事实
.venv/bin/geo-facts import out/demo/store \
  --input examples/auto/facts_supplement.csv \
  --reviewer "事实库审核人（虚构）" \
  --now 2026-10-08T02:00:00Z
```

### 3.2 检查事实库、查看冲突与导出

```bash
# 检查事实库（样例刻意包含 1 条无证据核心事实与 1 处冲突，返回退出码 1）
.venv/bin/geo-facts validate out/demo/store --as-of 2026-10-08

# 列出冲突清单（只陈列，不自动裁决；有冲突返回退出码 1）
.venv/bin/geo-facts conflicts out/demo/store --as-of 2026-10-08

# 按日期导出当日有效事实（CSV 可直接再导入）
.venv/bin/geo-facts export out/demo/store --as-of 2026-10-08 --format csv --out out/demo/facts.csv
```

### 3.3 陈述拆解与双向判定

```bash
# 仅拆解陈述
.venv/bin/geo-facts claims extract \
  --store out/demo/store \
  --observations examples/auto/observations.jsonl \
  --out out/demo/claims-only.jsonl

# 双向判定：回答 → 事实库（准确率）+ 事实库 → 回答（呈现率）
.venv/bin/geo-facts judge \
  --store out/demo/store \
  --observations examples/auto/observations.jsonl \
  --prompt-set examples/auto/prompt_set.json \
  --as-of 2026-10-08 \
  --out out/demo/judgments.jsonl \
  --claims-out out/demo/claims.jsonl \
  --now 2026-10-08T02:00:00Z

# 按契约校验产物
.venv/bin/geo-facts validate out/demo/judgments.jsonl --type judgment
```

产出的 `judgments.jsonl` 可直接复制到 `geo-verify` 项目目录中，供其计算回答准确率与核心信息呈现率；两个仓库只通过文件交换数据。

### 3.4 判定校准

```bash
# 对照人工金标集校准判定器（小样本误差上限超预算时按设计退出 3）
.venv/bin/geo-facts calibrate \
  --gold examples/auto/calibration_gold.jsonl \
  --predicted examples/auto/calibration_predicted.jsonl \
  --target 0.98
```

### 3.5 发布前闸门

```bash
# 对待检稿件做发布前检查（有 block 退出 1，仅 review 退出 5，无发现退出 0）
.venv/bin/geo-facts gate \
  --store out/demo/store \
  --draft examples/auto/draft.md \
  --meta examples/auto/draft.meta.json \
  --as-of 2026-10-08 \
  --now 2026-10-08T02:00:00Z \
  --out out/demo/gate.json
```

如需加载外部行业词库或既有稿件库，可追加 `--lexicon-dir <词库目录>`（可重复）和 `--corpus <既有稿件目录>`。

## 4. 下一步阅读

- [命令与接口说明](cli.md)
- [方法学](methodology.md)
- [闸门规则](gate-rules.md)
- [条款对照表](clause-map.md)
