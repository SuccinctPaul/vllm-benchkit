# 设计（按模块划分）

> 本文解释**代码是怎么组织的**：四段式链路 + 每个模块的职责、数据流、依赖与文件映射，以及系统的**取舍与现状**。改代码前先读这页。
> "为什么要做这个东西"见 [understand.md](../understand.md)，怎么跑见 [run.md](../run.md)。

## 先讲人话：代码分几步干活

只看文件你会晕，但整条链其实很像一条**流水线**：

1. **配** —— 把散在三处的配置（共同规矩 common + 每门课 cells + 精度 precision）**合成一份**能跑的考试单（profile）。
2. **起活** —— 客户端按考试单生成请求（考什么题）、算出到达节奏（啥时候发）、逐条执行并记下成绩（回执）。
3. **判** —— 阅卷老师组（Q/M/I/K/Z）对着成绩打分，最后统一盖章。
4. **存档** —— 全部原始数据 + 判定结果打包进 `runs/accepted/<profile>-verifyN/`，**fail-closed**。

下面从"三层归属"到"模块职责"都是这条流水线的展开细节。

## 1. 三层归属（出问题查谁）

三层（vLLM core 引擎 / vllm-ascend NPU 后端 / Ascend PyTorch Profiler）的职责与排查入口见 [understand.md §2](../understand.md)；本文不再重复。

## 2. 四段式链路

```
①配置层 ──▶ ②展开校验 ──▶ ③执行引擎(组C) ──▶ ④判定门禁 ──▶ ⑤证据归档(fail-closed)
```

1. **配置层**：`common.yaml` + `precision/<p>.yaml` + `cells/<cell>.yaml` 三层合并。字段契约（必填/允许集）见 `schema.yaml`。
2. **展开器**：`src/acceptance.py` 合并、渲染 argv/env、对未知字段 fail-closed。
3. **执行引擎**：`src/client/`（组 C）——生成请求 → 到达序列 → 执行 + 回执 → 判定。
4. **判定门禁 + 归档**：组 Q/M/I/K 判定，组 Z 汇总后落证据包。

## 3. 数据流

```
common/cells/precision (yaml)
        │ acceptance.expand()
        ▼
 effective 配置树 (含 workload 合同)
        │ run.py / generate.py
        ▼
 C1 cases(请求清单) ── C2 arrival(到达序列) ── C3 executor(逐请求回执)
        │
        ▼
 C4 slo · C5 scheduler(fairness) · C6 window · C7 closedloop+mfu
        │
        ▼
 Q oracle · M mechanisms · I roles · K cost · Z lifecycle/gate
        ▼
 runs/accepted/<profile>-verifyN/ (receipt + evidence 包)
```

## 4. 模块分组与职责

### 组 C —— 客户端负载引擎（执行层核心）

| 模块（文件） | 职责 | 对应附录 |
|------------|------|----------|
| [cases.py](../../src/client/cases.py) | C1 canonical 请求清单生成（对话/工具/推理/结构化/长文本），确定性 | 附-5 |
| [arrival.py](../../src/client/arrival.py) | C2 Poisson(seed=0) 到达序列，drain/cooldown，绑定参数哈希 | 附-8 |
| [executor.py](../../src/client/executor.py) | C3 流式执行 + 逐请求回执（TTFT/TPOT/E2EL、body SHA、verdict） | 附-5/6/7 |
| [slo.py](../../src/client/slo.py) | C4 SLO 判定（完成率/错误率/延迟阈值），只认预声明 rate | 附-8 |
| [scheduler.py](../../src/client/scheduler.py) | C5 A4 租户调度：混合到达 + 双重并发 + B0 扫描 + 隔离基线 | 附-7/8 |
| [fairness.py](../../src/client/fairness.py) | A4 份额偏差 + Jain 指数判定 | 附-7/8 |
| [window.py](../../src/client/window.py) | C6 A3 窗口分析：串行 closed-loop + 切窗 + CV/漂移/失败判定 | 附-8 |
| [closedloop.py](../../src/client/closedloop.py) | C7 A1 按轮 + batch 并发闭环执行 | 附-8 |
| [mfu.py](../../src/client/mfu.py) | C7 A1 effective FLOPs + MFU（2MNK / QKᵀPV / 逐元素不计） | 附-8 |

### 组 Q / M / I / K / Z —— 判定与门禁

| 模块 | 职责 |
|------|------|
| [oracle.py](../../src/client/oracle.py) | Q 质量判定（工具/推理/结构化/长文本）+ W8A8 资格 |
| [metrics.py](../../src/client/metrics.py) | M 机制生效（图计数/prefix/量化/A3 资源/CPU 核表） |
| [roles.py](../../src/client/roles.py) | I B0/B1 角色、comparison_id/config_id、SHA 绑定 |
| [cost.py](../../src/client/cost.py) | K A4 成本模型 + 利用率/公平/隔离门禁 |
| [lifecycle.py](../../src/client/lifecycle.py) | Z2 生命周期重复 + 主指标中位数 |
| [gate.py](../../src/client/gate.py) | Z3 证据包总门禁（fail-closed） |

### 公共 / 支撑 / 入口

| 模块 | 职责 |
|------|------|
| [common.py](../../src/client/common.py) | canonical JSON、SHA-256、百分位/中位、固定子集加载、config_hash |
| [jcs.py](../../src/client/jcs.py) | RFC 8785（JCS）canonicalize（组 I 用） |
| [acceptance.py](../../src/acceptance.py) | 配置展开/校验/渲染 argv/env |
| [receipt.py](../../src/receipt.py) | 生效值快照 + dataset 门禁 |
| [run.py](../../src/client/run.py) | 客户端执行引擎入口 + 离线下测 |
| [generate.py](../../src/client/generate.py) | C1+C2 合同生成/自测 |
| [collect_metrics.py](../../src/client/collect_metrics.py) | 组 M 真机指标采集（npu-smi monitor / aggregate） |
| [a1_peak.py](../../src/client/a1_peak.py) | A1-1 峰值 FLOPs 分母 |
| [msprof_reconcile.py](../../src/client/msprof_reconcile.py) | A1-2 msprof 对账 |

## 5. 模块依赖关系

- 组 C 各模块依赖 `common.py`（canonical/sha/percentile）。
- `scheduler.py` 依赖 `executor` + `fairness` + `common`。
- `run.py` 是编排入口，组装 C3→C4/C5/C6/C7 → Q/M/I/K/Z。
- 判定组（Q/M/I/K/Z）消费回执与证据，纯判定少 IO。
- `acceptance.expand()` 是配置的唯一下游，run.py/scheduler 都消费 effective 树。

### 5.1 读代码第一站（按需求定位文件）

新人别对着 42 个文件挨个看，先按"想干什么"找入口：

| 你想做什么 | 第一站 | 顺带看 |
|-----------|--------|--------|
| 改一条命令怎么跑（CLI） | [run.py](../../src/client/run.py)（执行入口） + [acceptance.py](../../src/acceptance.py)（配置渲染） | 对应 cell 的 [cells/*.yaml](../../config/vllm-xcheck/cells/) |
| 改 A4 多租户调度/隔离 | [scheduler.py](../../src/client/scheduler.py) | `fairness.py` + `cells/a4-mt.yaml` |
| 改某一门课的请求列什么题 | [cases.py](../../src/client/cases.py)（C1 请求清单） | `generate.py` + 对应 cell |
| 改延迟/SLO 判定口径 | [slo.py](../../src/client/slo.py) | `executor.py`（回执字段） |
| 看某门课"考什么/查哪条线" | [features.md](./features.md) | 对应 cell 的 yaml + `schema.yaml` |

> 原则：**入口文件 → 模块 → 数据流 → 判定**。先能跑通一次（[how-to-run.md §5 离线自测](./how-to-run.md#L74)）再改，能少走大半弯路。

## 6. 关键设计决策（取舍）

子系统的设计原则与逐项取舍，**收敛到 ADR 承载（此处只列条目与入口，不重复正文，避免多处漂移）**：

- **薄脚本 / 少造轮子 · fail-closed · 确定性优先 · 隐私边界** → 见 [ADR-0008](../adr/0008-vllm-xcheck-acceptance-design-principles.md)
- **A4-MT 隔离调优与软参数校准**（`max_num_seqs` 16→32→36、隔离判 ≤1.25× 口径）→ 见 [ADR-0009](../adr/0009-a4-mt-isolation-tuning.md)
- **A1 算力口径**（MFU≥90% + 强制 eager 裸算力）→ 见 [ADR-0010](../adr/0010-a1-compute-capability-eager.md)
- **配置外置分层** → [ADR-0005](../adr/0005-config-via-yaml-env-precedence.md) / [ADR-0006](../adr/0006-deployment-topology-manifest.md)；**复用官方 bench 驱动** → [ADR-0001](../adr/0001-use-vllm-bench-as-benchmark-driver.md)

**局限 / 短板**（要心里有数，不是缺陷而是成本）：

- **链路复杂、调试成本高**：隔离性 K2 从 verify15 调到 verify19 才达标，期间反复出现「假 FAIL」（基线并发口径、mixed 抢占）。**调优史详见 ADR-0009**。
- **真机依赖重**：A 组 ascend-dmi/msprof、K1 功耗/资产原值均为真机项，部分只能回退估算（峰值分母来源与实测值见 [acceptance-tasks.md 组 A](./acceptance-tasks.md#L174)，此处不重复）。
- **配置漂移风险**：`max_num_seqs`(16→32→36)、`capture_sizes`、`isolation_token_rate` 等软参数需真机反复校准（**权重见 ADR-0009**），无法从 PDF 直接推导。

## 7. 当前状态与遗留

- **已达标**：A4-MT-FP16-PC verify19 公平/隔离/SLO/容量全 PASS；C/Q/M/I/K/Z 组离线自测全过；A1 峰值真机落地。**逐项数值与轮次演化见 [acceptance-tasks.md 组 C5](./acceptance-tasks.md#L69)（已达标状态的唯一权威，此处不重复）。**
- **正式测量前待补**（对应合规审计）清单见 [features.md §7](./features.md)（此处不重复，避免多处漂移）。
- **数据口径**：同 [features.md §7「数据口径」](./features.md#L73)（唯一权威，此处不重复）。

> 明细与实时勾选见 [acceptance-tasks.md](./acceptance-tasks.md)、[acceptance-coverage.md](./acceptance-coverage.md)（本次由 design-overview.md 合并而来，避免两篇重复）。