# vllm-notes 路线图（Roadmap）

> 规划产物（grill-with-docs 会话）。现状：基础设施齐（deploy / bench / profile / prepare + config + ADR 0001–0006），P0 真机冒烟由另一 agent 进行。总方针：**先做好「性能对比」能力，达标后再进入拆解**。

## 核心能力（本轮主攻）

给任意 git commit（vllm / vllm-ascend）→ 跑基准 → 结果**按 commit 归档**（`runs/`，含运行清单），从而在版本间（如某优化 PR 前后）做性能对照。相关术语见 [CONTEXT.md](../CONTEXT.md)：性能对比 / commit 版本变体 / 按 commit 归档 / 运行清单。

## 轨道与阶段门

### Track 1 —— commit 参数化基准（按 commit 归档，先做）

| # | 任务 | 验收 |
|---|---|---|
| T1.1 | bench.sh 按双 commit 归档：产物落 `runs/<date>-<vllm_sha7>-<va_sha7>/` + manifest.yaml；指定 commit 手动 checkout 后直接跑 | 每次基准结果一眼可知两 commit、可检索可回溯 |
| T1.2 | 归档规范：`runs/<date>-<vllm_sha7>-<va_sha7>/`（vllm_sha7= vllm 短哈希，va_sha7= vllm-ascend 短哈希；log + result json + manifest.yaml 含两仓库完整哈希） | 一眼可知两 commit、可检索可回溯 |
| T1.3 | 运行清单：vllm 与 vllm-ascend 完整 commit ×2 + 生效参数快照（getconf 输出）+ seed + 时间/硬件 | 任意结果可归因、可复现 |
| T1.4 | 真机首验（与 P0 agent 同机并行） | 两次同 commit 结果可复现 |

### Track 2 —— 可复现能力增补（支撑归因）

把官方高价值 flag 透传进 config/bench.sh（official-capabilities.md 表1 🟢 项，按价值排）：

`--seed`（可复现承诺，优先）→ `--save-detailed` / `--percentile-metrics`（指标分析）→ `--warmup` / `--request-rate`（方法论）→ `--plot-timeline`（可视化，阶段门后再考虑）。

**产出物**：config.yaml 新增键 + bench.sh `cmd+=` + 文档回填（commands.md / config-reference.md / official-capabilities.md）。

### Track 3 —— 性能对比报告（归档之上做薄层）

- **主形态 A/B 单变量对比**：同 commit 内 flag 开/关；版本级 = commit A vs B（其余全钉死）。读两个归档 json 出对照表（TTFT / ITL / TPOT / 吞吐），**不重写指标计算**，只做编排与展示。
- **备查形态（写入本文档，后续真机测试验证，先不做）**：
  - 官方 `vllm bench sweep`：多配置矩阵扫描
  - QPS 压力曲线：固定优化，`request-rate 1/4/16/inf`，看吞吐/延迟拐点（官方方法论）
  - 官方 `run-performance-benchmarks.sh` 全套三测 + `convert_json_to_markdown` 报告

### Track 4 —— 拆解阶段（阶段门后）

读 vllm core / vllm-ascend 源码，弄清每层「是谁的、做了什么、为什么」（调度器 / attention / kv-cache / 算子 / HCCL）。触发条件见下方 Gate-C 与 CONTEXT.md「拆解」条目。

## 阶段门（Gate）

**Gate-C（对比可用）**：Track 1 归档落地 + 一次真实版本级对比可复现 → 开启 Track 3 报告与 Track 4 拆解。

对应 [CONTEXT.md](../CONTEXT.md)「未达标不进入拆解、不加指标解析/可视化工具」边界。

## 环境策略（默认决定）

第一版**复用单一 .venv**（串行跑 commit，最简单）；归档带**完整 commit 哈希**，为将来「每 commit 独立 .venv」的并行/回溯升级留路径（目录命名已含 commit，可直接映射）。若后续需要并行跑多个 commit，再升级为 `.venv-<repo>-<sha>`。

## 待核实（同步 official-capabilities.md「已知冲突/待核实」）

- `vllm bench latency` 语法：`--batch-size` vs `--num-iters`，以装好环境后 `--help` 实测为准
- 结果落盘：`--save-result --result-dir` 与 `--output-json` 的关系
- `--request-rate` 渐进式 / load-pattern / probe 语法

> 维护注意：新增/调整任何轨道任务时，同步 [CONTEXT.md](../CONTEXT.md) 术语、[ADR 0007](./adr/0007-commit-parameterized-benchmark-run.md)、以及 [official-capabilities.md](./official-capabilities.md) 对应表格。
