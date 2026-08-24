# vllm-benchkit

「成为 vLLM Ascend 插件专家」的笔记与工具仓库。切入点：**benchmark 与 profiling**。策略：先黑盒跑通，再逐步拆解；只 wrap 官方命令、低耦合、模块化；用 uv 管理独立环境。

基准版本：`v0.18.0`（vLLM core 与 vllm-ascend 插件）。

## 文档入口

- [CONTEXT.md](./CONTEXT.md) —— 术语表（目标/边界/三层归属/踢出项）
- **[docs/README.md](./docs/README.md) —— 文档唯一总入口**（文档地图 + 新手路线 + 名词 + 维护，先读它）
- [docs/understand.md](./docs/understand.md) —— 这是什么（两档玩法：黑盒冒烟 / 正式验收；三层归属；硬约束）
- [docs/run.md](./docs/run.md) —— 怎么跑（装环境 → 冒烟 → 验收，一页串完）
- [docs/maintenance.md](./docs/maintenance.md) —— 为什么能维护＆运行（单一事实来源 / 同步规则 / 留痕 / 改动自查）
- [docs/guide/](./docs/guide/README.md) —— 黑盒工具细节（bench/profile 命令 / 参数 / 输出）
- [docs/acceptance/](./docs/acceptance/README.md) —— vllm-xcheck 正式验收子系统（A1–A4：功能 / 设计 / how-to-run / 配置参数 / 覆盖度）
- [docs/acceptance/acceptance-tasks.md](./docs/acceptance/acceptance-tasks.md) —— 验收执行层任务清单（按组 C/I/Q/M/A/K/D/S/Z）
- [docs/official-capabilities.md](./docs/official-capabilities.md) —— 官方能力清单与可增补项（复用官方，少造轮子）
- [docs/roadmap.md](./docs/roadmap.md) —— 未来路线图（先做性能对比，达标后拆解）
- [config/config.yaml](./config/config.yaml) —— 运行参数默认配置（环境变量优先，见 ADR-0005）
- [config/vllm-xcheck/](./config/vllm-xcheck/) —— V4.1 正式验收配置层（common + precision + cells + schema）
- [docs/adr](./docs/adr/0001-use-vllm-bench-as-benchmark-driver.md) —— 架构决策记录
  - `0001` 用 `vllm bench` 当基准驱动
  - `0002` 用 uv 建独立环境，不继承预装环境
  - `0003` canonical 源 = `config/topology.yaml` 钉的双仓库
  - `0004` profiling 用 Ascend PyTorch Profiler（内置）
  - `0005` 参数外置到 config.yaml，环境变量优先
  - `0006` 部署拓扑外置到 config/topology.yaml（从 pyproject 移除仓库路径）
  - `0007` 基准按双 commit（vllm / vllm-ascend）归档到 runs/，附运行清单

## 核心结论（简述）

- **框架归属**：引擎/基准 = vLLM core；NPU 后端 = vllm-ascend 插件；profiling 引擎 = 华为 `torch_npu.profiler`。
- **目标硬件**：Atlas 910B2 × 8（CANN 9.0.0），裸机无 docker。
- **执行环境**：SSH 目标机 / 远程工作目录 / 仓库源见 `config/topology.yaml`（server/dir/repos，canonical 双仓库 @ `releases/v0.18.0`）。
- **本期范围**：在线 `vllm bench serve` + 离线吞吐 `vllm bench throughput` + 离线延迟 `vllm bench latency` + Ascend PyTorch Profiler；smoke 用 `Qwen3-0.6B` / `opt-125m`；用 `random` 合成数据集零下载。
- **脚本与配置**：脚本统一在 `scripts/`（bench.sh / profile.sh / npu_env.sh），参数默认值统一在 `config/config.yaml`，环境变量可覆盖（ADR-0005）。
- **踢出并标记 Todo**：量化 / LoRA / Spec Decode / 多模态 / MS Service Profiler / msprobe / 多节点与 PD 分离与 EP。