# geo-facts

GEO（生成式引擎优化）项目的**事实核验工具**：事实库、数值与单位归一、陈述拆解、双向判定、判定校准和发布前闸门。

它回答两个问题：AI 说得对不对；待发布的稿件能不能发。

> **重要声明**
>
> - 本工具只给出**预检结论**和建议，不构成独立第三方验证，也不负责发布。
> - 合规词库命中只产生“需复核”（`review`），不单独作为拦截依据；词库和报告措辞需经法务审阅后才能用于真实业务。
> - 本工具只支持纠正错误和过期信息，不提供压制或挤出真实负面信息的功能。
> - 合成标注集上的结果只能说明实现符合预期，**不代表真实业务准确率**；自由文本判定在没有真实金标前标为“未验证”。
> - 仓库内全部样例数据为合成数据（`synthetic: true`），品牌、产品、域名均为虚构。

## 安装

需要 Python ≥ 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
make setup    # 安装依赖，需联网一次
make check    # 静态检查 + 类型检查 + 测试（可断网）
```

## 快速开始

```bash
sh examples/auto/run.sh out/auto-demo
```

逐步说明见 [docs/quickstart.md](docs/quickstart.md)。

## 命令一览

```text
geo-facts init | import | validate | conflicts | export
geo-facts claims extract
geo-facts judge | calibrate
geo-facts gate
```

所有命令支持 `--json`。退出码：`0` 成功，`1` 检查未通过，`2` 输入错误，`3` 数据不足，`4` 内部错误。
`gate` 存在 `block` 时退出 `1`，只有 `review` 时退出 `5`。

## 与 geo-verify 的关系

两个工具只通过文件交换数据，互不依赖对方代码。`geo-facts` 产出 `judgments.jsonl`，`geo-verify` 读取后计算准确率和呈现率。

数据契约的唯一源在 `geo-verify` 的 `spec/`；本仓库在 [`contracts/`](contracts/) 保存副本，并用 `contracts.lock` 记录版本和每个文件的 SHA-256。`python tools/sync_contracts.py --check` 可校验副本与锁是否一致。

## 文档

- [方法学](docs/methodology.md)：三区分治、归一规则、双向判定与两个分母、校准
- [条款对照表](docs/clause-map.md)：功能与团体标准条款号的对应关系（只引用条款号）
- [快速开始](docs/quickstart.md)
- [闸门规则](docs/gate-rules.md)

## 许可证

[Apache-2.0](LICENSE)
