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

把官方高价值 flag 透传进 config/bench.sh（official-capabilities.md 表1 ⭕ 待增补项，按价值排）：

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

### Track 5 —— vllm-xcheck 正式验收执行层（V4.1）

> 目标：把 `config/vllm-xcheck` 的配置层骨架升级为可执行正式验收的完整链路（客户端负载引擎 + 判定 + 指标采集 + 成本口径）。
> 配置层（common / cells / precision + schema）已覆盖冻结表服务端参数（附-2/3/4）；本轨道补齐执行层。
> **详细可执行任务清单见 [docs/acceptance/acceptance-tasks.md](./acceptance/acceptance-tasks.md)**（按组 C/I/Q/M/A/K/D/S/Z 编号，每项含目标/做法/验收；新增配置键必须先入 schema allowed 集，fail-closed）。

| # | 任务（组） | 验收 |
|---|---|---|
| T5.1 | 组 S：配置层边角字段补齐（kv_cache requested/effective、模板/结构化字段、A4 租户采样、A2 warmup） | dry-run 展开与冻结表逐项一致；schema 表外字段拒绝 |
| T5.2 | 组 C：客户端负载引擎（合同案例、Poisson seed=0 到达序列、逐请求回执、SLO、A4 租户、A3 窗口、A1 闭环形状） | A1—A4 各 profile 产出容量曲线 + SLO 合规吞吐 + 逐请求回执 |
| T5.2s | 组 C 进度：**C1–C7 已完成**；真机 verify19（2026-08-23）公平/隔离/SLO/容量四门全达标。逐项做法、数值与 verify16→19 轮次演化见 acceptance-tasks.md 组 C5/C6/C7 | 见 [acceptance-tasks.md](./acceptance/acceptance-tasks.md#C5) |
| T5.3 | 组 Q：质量 oracle（BFCL/GSM8K/JSONSchema/长文本）与 W8A8 资格门禁 | 判定可复现；W8A8 不过质量门禁即拒绝进入性能 |
| T5.4 | 组 M：指标采集（图计数器、prefix/量化生效、A3 资源监控、CPU 核表） | 机制生效证据入 receipt；缺失即拒测 |
| T5.5 | 组 I：B0/B1 角色化 + comparison_id/config_id + 代码/镜像 SHA 绑定 | 双角色对照合同逐项一致；B0 SHA 不匹配即拒绝 |
| T5.6 | 组 A：A1 算力口径（ascend-dmi + effective_compute_spec v1 / msprof） | 主形状 MFU≥90%、msprof 误差≤3% |
| T5.7 | 组 K：A4 成本模型（÷21600h、0.60 元/kWh、每百万 token） | 各比较关系同一价格口径；成本报告产出 |
| T5.8 | 组 D：数据子集补齐（A3 长文本集、真实源接入、身份固化） | prepare subsets 全 materialize；B0/B1 数据字节一致 |
| T5.9 | 组 Z：集成门禁（3 生命周期中位数、证据包总门禁） | receipt 全 PASS 才允许正式测量 |
| T5.3s | I/Q/M/K/Z 离线自测已完成；A 真机项脚本已落地（A1-1 峰值实测值见 acceptance-tasks.md 组 A） | 见 [acceptance-tasks.md](./acceptance/acceptance-tasks.md#L174) |

## 阶段门（Gate）

**Gate-C（对比可用）**：Track 1 归档落地 + 一次真实版本级对比可复现 → 开启 Track 3 报告与 Track 4 拆解。

对应 [CONTEXT.md](../CONTEXT.md)「未达标不进入拆解、不加指标解析/可视化工具」边界。

**Gate-H（正式验收可执行）**：Track 5 的 S/C/Q/M/I/A/K/D/Z 各主任务完成 + 一次真机端到端 A1—A4 全链路可复现（receipt 全 PASS）→ 开放正式验收执行。

## 环境策略（默认决定）

第一版**复用单一 .venv**（串行跑 commit，最简单）；归档带**完整 commit 哈希**，为将来「每 commit 独立 .venv」的并行/回溯升级留路径（目录命名已含 commit，可直接映射）。若后续需要并行跑多个 commit，再升级为 `.venv-<repo>-<sha>`。

## 待核实

全部官方 flag 语法/冲突核实**收敛到单一权威** [official-capabilities.md「已知冲突/待核实」](./official-capabilities.md)；roadmap 不再重复，避免多处漂移。

> 维护注意：新增/调整任何轨道任务时，同步 [CONTEXT.md](../CONTEXT.md) 术语、[ADR 0007](./adr/0007-commit-parameterized-benchmark-run.md)、以及 [official-capabilities.md](./official-capabilities.md) 对应表格。Track 5 的具体任务以 [docs/acceptance/acceptance-tasks.md](./acceptance/acceptance-tasks.md) 为唯一明细来源。
