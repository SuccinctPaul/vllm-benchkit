# 架构决策记录（ADR）索引

> **ADR = Architecture Decision Record（架构决策记录）**，是软件工程界对「把一次重大设计决策连同其背景、取舍、后果固化成文」这一做法的标准称呼（源自 ThoughtWorks 的建档实践，与 `address`/`advertisement` 无关）。初次看到这三个字母的新人，请直接把它当「**决策记录**」读即可。
>
> 这里是**全部架构决策记录的唯一入口**。每一条 ADR 记录一个"**为什么这么设计**"的决策：背景、候选、取舍、后果，以及它后来有没有兑现。只追加、不改历史；新决策接当前最大序号递增。
>
> 第一次读：先看下面这**张决策地图**（依赖方向 = "箭头指向后序"），再顺着自己关心的主题挑 ADR 精读。正文只讲"决策怎么来的"，想理解整条运行链路看 [../run.md](../run.md)、[../understand.md](../understand.md)。

## 决策地图（谁依赖谁）

```
guide 黑盒地基（0001–0007，2026-08-20）
  0001 用官方 vllm bench ───────────────► 0008（薄脚本）
  0002 用 uv 隔离环境 ─┬───────────────► 0003（canonical 源）──► 0006（拓扑外置）──► 0007（commit 归档）
  0004 内置 Ascend Profiler ────────────►（独立）
  0005 配置外置 YAML+env ───────────────► 0006（与拓扑分层）
                      │
acceptance 验收（0008–0012，2026-08-24）
  0008 验收四原则（在 0001–0007 之上长出）──┬──► 0009 A4 隔离调优（软参数）
                                          └──► 0010 A1 eager+算力（含 prefill 计量口径）
                                          └──► 0011 运行硬约束（收口 SKIP_GRAPH_ARGS / seed / xgrammar / model-ref）
                                          └──► 0012 A2/A3 量测口径（容量裕量+SLO 分档+串行切窗）
```

## 决策一览

| ADR | 主题 | Type | Status | Date | 映射 | 兑现 |
|-----|------|------|--------|------|------|------|
| [0001](./0001-use-vllm-bench-as-benchmark-driver.md) | 用官方 `vllm bench` 作基准驱动，不造轮子 | architecture | ✅ accepted | 2026-08-20 | 基础 | ✅ 已兑现 |
| [0002](./0002-manage-environment-with-uv.md) | uv 隔离环境 + 官方版本 pin | architecture | ✅ accepted | 2026-08-20 | 环境基线 | ✅ 已兑现 |
| [0003](./0003-canonical-source-repos.md) | canonical 源 = topology 钉的双仓库 | standard | ✅ accepted | 2026-08-20 | 可复现 | ✅ 已兑现 |
| [0004](./0004-profiling-via-ascend-pytorch-profiler.md) | 内置 Ascend PyTorch Profiler | architecture | ⏳ pending-verify | 2026-08-20 | 瓶颈定位 | ⏳ 待核实 P0 |
| [0005](./0005-config-via-yaml-env-precedence.md) | 参数外置 YAML，环境变量优先 | architecture | ✅ accepted | 2026-08-20 | 通用 | ✅ 已兑现 |
| [0006](./0006-deployment-topology-manifest.md) | 部署拓扑外置 topology.yaml | architecture | ✅ accepted | 2026-08-20 | 隐私边界 | ✅ 已兑现 |
| [0007](./0007-commit-parameterized-benchmark-run.md) | commit 参数化 + 按 commit 归档 | architecture | ✅ accepted | 2026-08-20 | 可复现/对比 | ✅ 已兑现 |
| [0008](./0008-vllm-xcheck-acceptance-design-principles.md) | 验收子系统四原则 | architecture | ✅ accepted | 2026-08-24 | A1–A4 总纲 | ✅ 已兑现 |
| [0009](./0009-a4-mt-isolation-tuning.md) | A4 隔离软参数校准 + 隔离口径 | tuning | ✅ accepted | 2026-08-24 | A4 ≤1.25× | ✅ verify15→19 |
| [0010](./0010-a1-compute-capability-eager.md) | A1 MFU≥90% + 强制 eager | tuning/standard | ✅ accepted | 2026-08-24 | A1 ≥90% | ✅ 已兑现 |
| [0011](./0011-operational-hard-constraints.md) | 真机运行硬约束收口 | standard/operational | ✅ accepted | 2026-08-25 | 表附-2/3 | ✅ 已落地 |
| [0012](./0012-a2a3-measurement-calibration.md) | A2/A3 量测口径：容量裕量 + SLO 分档 + 真数据 + 串行切窗 | standard/tuning | ✅ accepted | 2026-08-25 | A2/A3 判定口径 | ✅ 已落地 |

> **Type 说明**：`architecture`＝设计取舍；`tuning`＝真机校准出的软参数；`standard`＝必须遵守的规范/约束；`operational`＝真机运行守则。
> **Status 说明**：`✅ accepted`＝已生效；`⏳ pending-verify`＝决策定了但关键点待实测回填。

## 怎么读 / 怎么新增

- **想搞懂运行链路**：按 `0001→0002→0003→0005→0006→0007→0008→0009/0010/0011/0012` 顺着读。
- **只想读决策**：挑 Type 相关的那几条精读，正文按统一的"背景/决策/候选对比/后果/兑现"结构组织。
- **新增决策**：在 [../../docs/adr/](./) 追加 `NNNN-<slug>.md`（序号递增），套用与现有条目一致的前置元数据表；只追加、不改历史。改完回填本文 [决策一览](#L25) 一行。