# vllm-notes

「成为 vLLM Ascend 插件专家」的笔记与工具仓库。切入点：**benchmark 与 profiling**。策略：先黑盒跑通，再逐步拆解；只 wrap 官方命令、低耦合、模块化；用 uv 管理独立环境。

基准版本：`v0.18.0`（vLLM core 与 vllm-ascend 插件）。

## 文档入口

- [CONTEXT.md](./CONTEXT.md) —— 术语表（目标/边界/三层归属/踢出项）
- [docs/guide/](./docs/guide/README.md) —— 使用与设计指南（how to run / 命令行功能 / 配置参考 / 输出说明 / 设计原理）
- [docs/official-capabilities.md](./docs/official-capabilities.md) —— 官方能力清单与可增补项（复用官方，少造轮子）
- [docs/roadmap.md](./docs/roadmap.md) —— 未来路线图（先做性能对比，达标后拆解）
- [config/config.yaml](./config/config.yaml) —— 参数默认配置（环境变量优先，见 ADR-0005）
- [docs/adr](./docs/adr/0001-use-vllm-bench-as-benchmark-driver.md) —— 架构决策记录
  - `0001` 用 `vllm bench` 当基准驱动
  - `0002` 用 uv 建独立环境，不继承预装环境
  - `0003` canonical 源 = `config/topology.yaml` 钉的双仓库
  - `0004` profiling 用 Ascend PyTorch Profiler（内置）
  - `0005` 参数外置到 config.yaml，环境变量优先

## 核心结论（简述）

- **框架归属**：引擎/基准 = vLLM core；NPU 后端 = vllm-ascend 插件；profiling 引擎 = 华为 `torch_npu.profiler`。
- **目标硬件**：Atlas 910B2 × 8（CANN 9.0.0），裸机无 docker。
- **执行环境**：SSH 目标机 / 远程工作目录 / 仓库源见 `config/topology.yaml`（server/dir/repos，canonical 双仓库 @ `releases/v0.18.0`）。
- **本期范围**：在线 `vllm bench serve` + 离线吞吐 `vllm bench throughput` + 离线延迟 `vllm bench latency` + Ascend PyTorch Profiler；smoke 用 `Qwen3-0.6B` / `opt-125m`；用 `random` 合成数据集零下载。
- **脚本与配置**：脚本统一在 `scripts/`（bench.sh / profile.sh / npu_env.sh），参数默认值统一在 `config/config.yaml`，环境变量可覆盖（ADR-0005）。
- **踢出并标记 Todo**：量化 / LoRA / Spec Decode / 多模态 / MS Service Profiler / msprobe / 多节点与 PD 分离与 EP。