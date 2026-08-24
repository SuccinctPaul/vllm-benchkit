# 0002 用 uv 建立独立虚拟环境，不继承预装环境

> **决策摘要**：用 `uv` 建隔离 `.venv`，从 `pyproject.toml` 装 `torch`/`torch-npu` 与 `vllm`/`vllm-ascend`（可编辑依赖）；版本钉官方 pin（torch 2.9.0 / torch-npu 2.9.0.post2），不复用被污染的预装 conda。

| 元数据 | 值 |
|--------|----|
| **Status** | accepted |
| **Date** | 2026-08-20 |
| **Type** | architecture |
| **Supersedes** | — |
| **Related** | ADR-0009（软参数校准并行不覆盖）、ADR-0011（真机运行约束同挂版本 pin） |
| **映射** | 环境基线（可复现前提），不直接映射单项 |

用 `uv` 在本机/远端新建专用虚拟环境（.venv），从 `vllm-benchkit` 的 `pyproject.toml` 声明并安装 `torch` / `torch-npu`、以及 `vllm` / `vllm-ascend` 两个可编辑路径依赖。

机器上预装的 conda 环境被污染（其 `vllm`/`vllm_ascend` 指向其它仓库副本，非本目标 v0.18.0 双仓库）。为满足"干净、低耦合、不继承预装环境"，我们不复用它，而是新建隔离环境。选 uv 而非 conda：uv 更快、以 pyproject 声明为准、更贴合本仓库"薄脚本+文档"的定位。

## 版本栈：对齐 vllm-ascend 官方 pin（路线 X）

版本钉为 `torch==2.9.0` + `torch-npu==2.9.0.post2`，即 vllm-ascend @ v0.18.0 声明的依赖组合，与 CANN 9.0.0 匹配。

候选对比：路线 Y 曾用 `torch 2.10.0` 做 smoke 并验证 910B2 可用，但它偏离 vllm-ascend 官方 pin，越界即与上游版本契约脱节。为满足"与上游对齐、可复现"，选路线 X；官方 pin 意味着 torch_npu 的 wheel 应能走官方发布渠道（torch-npu 的 PyPI 包源同源于 gitcode.com/ascend/pytorch）。wheel 具体来源由安装动作落地时确认并如实记录，不臆造。

> 衔接（在 vllm-xcheck 验收阶段追加）：本文钉的是**黑盒基准**的版本栈基线；进入验收后，服务侧运行参数（`max_num_seqs`/`capture_sizes`/`isolation_token_rate` 等）受真机**软参数校准**制约，见 [ADR-0009](../adr/0009-a4-mt-isolation-tuning.md)。两条并行：版本栈保持官方 pin，验收软参数单独校准，互不覆盖。

## 兑现回填（Verification）

- ✅ **已兑现**：`.venv` 由 `uv sync` 建立，版本钉 torch 2.9.0 / torch-npu 2.9.0.post2；A4 软参数校准由 [ADR-0009](./0009-a4-mt-isolation-tuning.md) 专档，互不覆盖。
- ⚠️ **运营注意**：目标机若网络受限（访问不到外部 pypi），`uv sync` 拉取 torch-npu wheel 需先解决镜像/离线，属机器层面前提（见 [guide/how-to-run §1.0](../guide/how-to-run.md)），不是本文可自动规避的。