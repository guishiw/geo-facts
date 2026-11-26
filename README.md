# geo-facts

GEO（生成式引擎优化）项目的**事实核验工具**：事实库三区分治、数值与单位归一、陈述拆解、双向判定、判定校准和发布前闸门。

它回答两个问题：**AI 说得对不对**；**待发布的稿件能不能发**。

> **重要声明**
>
> - 本工具只给出**预检结论**和建议，不构成独立第三方验证，也不负责发布（`047 §15.2`）。
> - 合规词库命中只产生“需复核”（`review`），永远不会单独作为拦截（`block`）依据（`046 §8.3.5`）；内置通用示例词库未经法务审阅，词库和报告措辞须经法务审阅后才能用于真实业务。
> - 本工具只支持纠正错误和过期信息，不提供压制或挤出真实负面信息的功能（`047 §9.3`），不输出对第三方平台结果的任何保证性表述（`046 §7.1.4`、`047 §13.2`）。
> - **合成标注集上的结果只能说明实现符合预期，不代表真实业务准确率**；**自由文本判定在没有真实人工金标前标为“未验证”**。
> - 仓库内全部样例数据为合成数据（`synthetic: true`），品牌、产品、域名（`*.example`）均为虚构。

## 安装与验证

需要 Python ≥ 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
make setup    # 安装依赖，需联网一次
make check    # ruff + mypy --strict + pytest + 模块覆盖率门槛（可断网）
make bench    # 运行合成标注集开发集基准（可断网）
```

## 快速体验（虚构汽车品牌完整样例）

安装依赖后，无需联网即可运行完整演示：

```bash
sh examples/auto/run.sh out/auto-demo
```

不传目录参数时自动写入系统临时目录。脚本串联 `init → import → validate → conflicts → export → claims extract → judge → calibrate → gate` 全流程，说明见 [examples/auto/README.md](examples/auto/README.md) 与 [快速开始](docs/quickstart.md)。

## 命令一览

```text
geo-facts init | import | validate | conflicts | export
geo-facts claims extract
geo-facts judge | calibrate
geo-facts gate
```

所有叶子命令支持 `--json`，详尽参数、模板列与 JSON 输出结构见 [命令与接口说明](docs/cli.md)。

### 退出码

| 退出码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 检查未通过（`validate` / `import` 含非法行 / `conflicts` 有冲突 / `gate` 存在 `block`） |
| `2` | 输入错误 |
| `3` | 数据不足（`calibrate` 无对齐样本或判定精度不足） |
| `4` | 内部错误 |
| `5` | `gate` 存在 `review` 且无 `block` |

## 与 geo-verify 的关系

`geo-facts` 与 `geo-verify` 是两个独立的公开仓库，**只通过文件交换数据，互不依赖对方代码**。`geo-facts judge` 产出符合 `judgment` 契约的 `judgments.jsonl`，`geo-verify` 读取后计算回答准确率与核心信息呈现率。

数据契约的唯一源在 `geo-verify` 的 `spec/`；本仓库在 [`contracts/`](contracts/) 保存副本，并用 [`contracts/contracts.lock`](contracts/contracts.lock) 记录版本和每个文件的 SHA-256。可执行 `python tools/sync_contracts.py --check` 校验副本与锁是否一致。

## 合成数据与局限说明

- 内置样例（`geo-facts init --example auto`）、[`examples/auto/`](examples/auto/) 与 [`benchmarks/`](benchmarks/) 均采用虚构品牌与合成数据，全部标记 `synthetic: true`。
- 规则实现目前面向**数值、型号、日期**三类结构化陈述；自由文本陈述一律返回 `unverifiable`（未验证）。
- 合成标注集用于验证实现逻辑与回归门槛，不代表真实平台回答的准确率。在真实业务中启用自动判定前，应使用真实双人标注金标集运行 `geo-facts calibrate` 验证判定器误差上限。

## 文档索引

- [快速开始](docs/quickstart.md)：安装与分步上手
- [命令与接口说明](docs/cli.md)：事实库目录、全部命令、导入模板、JSON 结构与词库格式
- [方法学](docs/methodology.md)：三区分治、归一规则、陈述拆解、双向判定与两个分母、校准与合成标注集
- [闸门规则](docs/gate-rules.md)：全部规则编号、触发条件、建议与缺省检查说明
- [条款对照表](docs/clause-map.md)：功能与团体标准条款号的对应关系（仅引用条款号，开发者整理，须人工复核）
- [贡献指南](CONTRIBUTING.md) · [安全说明](SECURITY.md) · [变更记录](CHANGELOG.md)

## 许可证

[Apache-2.0](LICENSE)
