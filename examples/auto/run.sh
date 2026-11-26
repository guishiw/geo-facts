#!/usr/bin/env sh
# 虚构汽车品牌（澜序汽车）事实核验完整流程示例。
# 全部数据为合成数据（synthetic: true），不代表任何真实品牌或效果。
# 用法：sh examples/auto/run.sh [输出目录]
# 未指定输出目录时自动在临时目录下创建。

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)

if [ -n "${GEO_FACTS:-}" ]; then
    CLI="$GEO_FACTS"
elif [ -x "$REPO_ROOT/.venv/bin/geo-facts" ]; then
    CLI="$REPO_ROOT/.venv/bin/geo-facts"
elif command -v geo-facts >/dev/null 2>&1; then
    CLI="geo-facts"
else
    echo "未找到 geo-facts 可执行文件，请先在仓库根目录运行 make setup" >&2
    exit 2
fi

if [ "$#" -ge 1 ] && [ -n "$1" ]; then
    OUT="$1"
    mkdir -p "$OUT"
else
    OUT=$(mktemp -d "${TMPDIR:-/tmp}/geo-facts-auto.XXXXXX")
fi

NOW="2026-10-08T02:00:00Z"
AS_OF="2026-10-08"

echo "=== 虚构汽车品牌（澜序）事实核验演示 ==="
echo "输出目录：$OUT"
echo "提示：以下全部数据为合成数据（synthetic: true），仅用于演示预检流程。"
echo ""

echo "[1/10] init：创建内置虚构汽车品牌事实库"
"$CLI" init "$OUT/store" --example auto --now "$NOW"

echo ""
echo "[2/10] import：通过 CSV 导入补充事实（F-X5-BATTERY）"
"$CLI" import "$OUT/store" \
    --input "$SCRIPT_DIR/facts_supplement.csv" \
    --reviewer "事实库审核人（虚构）" \
    --now "$NOW"

echo ""
echo "[3/10] validate：检查事实库（样例刻意含 1 处冲突与 1 条无证据核心事实，按设计退出 1）"
set +e
"$CLI" validate "$OUT/store" --as-of "$AS_OF" --json > "$OUT/validate-store.json"
rc=$?
set -e
if [ "$rc" -eq 1 ]; then
    echo "  已按设计返回退出码 1（检出样例中的无证据核心事实与未裁决冲突），明细写入 $OUT/validate-store.json"
elif [ "$rc" -eq 0 ]; then
    echo "  事实库校验通过（退出码 0），明细写入 $OUT/validate-store.json"
else
    echo "  validate 意外失败，退出码：$rc" >&2
    exit "$rc"
fi

echo ""
echo "[4/10] conflicts：列出冲突清单（不自动裁决；有冲突按设计退出 1）"
set +e
"$CLI" conflicts "$OUT/store" --as-of "$AS_OF" --json > "$OUT/conflicts.json"
rc=$?
set -e
if [ "$rc" -eq 1 ]; then
    echo "  已按设计返回退出码 1（发现未裁决冲突，不自动裁决），明细写入 $OUT/conflicts.json"
elif [ "$rc" -eq 0 ]; then
    echo "  未发现冲突（退出码 0），明细写入 $OUT/conflicts.json"
else
    echo "  conflicts 意外失败，退出码：$rc" >&2
    exit "$rc"
fi

echo ""
echo "[5/10] export：按查询日 $AS_OF 导出有效事实（CSV，可再导入）"
"$CLI" export "$OUT/store" --as-of "$AS_OF" --format csv --out "$OUT/facts-as-of.csv"

echo ""
echo "[6/10] claims extract：从回答样本拆解陈述"
"$CLI" claims extract \
    --store "$OUT/store" \
    --observations "$SCRIPT_DIR/observations.jsonl" \
    --out "$OUT/claims-extracted.jsonl"

echo ""
echo "[7/10] judge：双向判定（回答 → 事实库准确率，事实库 → 回答呈现率）"
"$CLI" judge \
    --store "$OUT/store" \
    --observations "$SCRIPT_DIR/observations.jsonl" \
    --prompt-set "$SCRIPT_DIR/prompt_set.json" \
    --as-of "$AS_OF" \
    --out "$OUT/judgments.jsonl" \
    --claims-out "$OUT/claims.jsonl" \
    --now "$NOW" \
    --json > "$OUT/judge-summary.json"
echo "  判定结果写入 $OUT/judgments.jsonl，摘要写入 $OUT/judge-summary.json"

echo ""
echo "[8/10] validate --type：按数据契约校验产出的 claims.jsonl 与 judgments.jsonl"
"$CLI" validate "$OUT/claims.jsonl" --type claim
"$CLI" validate "$OUT/judgments.jsonl" --type judgment

echo ""
echo "[9/10] calibrate：对照小样本金标做判定校准（错误率区间上限超阈值，按设计退出 3）"
set +e
"$CLI" calibrate \
    --gold "$SCRIPT_DIR/calibration_gold.jsonl" \
    --predicted "$SCRIPT_DIR/calibration_predicted.jsonl" \
    --target 0.98 \
    --json > "$OUT/calibrate.json"
rc=$?
set -e
if [ "$rc" -eq 3 ]; then
    echo "  已按设计返回退出码 3（判定精度不足，无法证明达标），明细写入 $OUT/calibrate.json"
elif [ "$rc" -eq 0 ]; then
    echo "  校准完成（退出码 0），明细写入 $OUT/calibrate.json"
else
    echo "  calibrate 意外失败，退出码：$rc" >&2
    exit "$rc"
fi

echo ""
echo "[10/10] gate：发布前闸门检查待检稿件（有 block 按设计退出 1，仅 review 按设计退出 5）"
set +e
"$CLI" gate \
    --store "$OUT/store" \
    --draft "$SCRIPT_DIR/draft.md" \
    --meta "$SCRIPT_DIR/draft.meta.json" \
    --as-of "$AS_OF" \
    --now "$NOW" \
    --out "$OUT/gate.json"
rc=$?
set -e
if [ "$rc" -eq 1 ]; then
    echo "  已按设计返回退出码 1（稿件被拦截 block，同时检出隐藏指令、过期价格、极限词与竞品比较），明细写入 $OUT/gate.json"
elif [ "$rc" -eq 5 ]; then
    echo "  已按设计返回退出码 5（仅存在需复核 review 项），明细写入 $OUT/gate.json"
elif [ "$rc" -eq 0 ]; then
    echo "  未发现问题（退出码 0），明细写入 $OUT/gate.json"
else
    echo "  gate 意外失败，退出码：$rc" >&2
    exit "$rc"
fi

echo ""
echo "=== 演示全部完成，产出文件清单（${OUT}）==="
echo "  - $OUT/store/"
echo "  - $OUT/validate-store.json"
echo "  - $OUT/conflicts.json"
echo "  - $OUT/facts-as-of.csv"
echo "  - $OUT/claims-extracted.jsonl"
echo "  - $OUT/claims.jsonl"
echo "  - $OUT/judgments.jsonl"
echo "  - $OUT/judge-summary.json"
echo "  - $OUT/calibrate.json"
echo "  - $OUT/gate.json"
