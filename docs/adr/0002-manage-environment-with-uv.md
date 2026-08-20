# 用 uv 建立独立虚拟环境，不继承预装环境

用 `uv` 在本机/远端新建专用虚拟环境（.venv），从 `vllm-notes` 的 `pyproject.toml` 声明并安装 `torch` / `torch-npu`、以及 `vllm` / `vllm-ascend` 两个可编辑路径依赖。

机器上预装的 conda 环境被污染（其 `vllm`/`vllm_ascend` 指向其它仓库副本，非本目标 v0.18.0 双仓库）。为满足"干净、低耦合、不继承预装环境"，我们不复用它，而是新建隔离环境。选 uv 而非 conda：uv 更快、以 pyproject 声明为准、更贴合本仓库"薄脚本+文档"的定位。

## 版本栈：对齐 vllm-ascend 官方 pin（路线 X）

版本钉为 `torch==2.9.0` + `torch-npu==2.9.0.post2`，即 vllm-ascend @ v0.18.0 声明的依赖组合，与 CANN 9.0.0 匹配。

候选对比：路线 Y 曾用 `torch 2.10.0` 做 smoke 并验证 910B2 可用，但它偏离 vllm-ascend 官方 pin，越界即与上游版本契约脱节。为满足"与上游对齐、可复现"，选路线 X；官方 pin 意味着 torch_npu 的 wheel 应能走官方发布渠道（torch-npu 的 PyPI 包源同源于 gitcode.com/ascend/pytorch）。wheel 具体来源由安装动作落地时确认并如实记录，不臆造。