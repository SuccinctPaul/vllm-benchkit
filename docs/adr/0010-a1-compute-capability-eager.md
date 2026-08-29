# 0010 A1 算力口径：MFU≥90% + 强制 eager（裸算力，禁图优化兜底）

> **决策摘要**：A1（算力峰值）判定口径钉死为**主形状 MFU≥90%**，且测量时**强制关闭图优化**（`compile_mode: none` + `enforce_eager: true` + `cudagraph_mode: none`），使图捕获=0——开图优化等于"考试作弊"，MFU 不再诚实反映裸算力。

| 元数据 | 值 |
|--------|----|
| **Status** | accepted |
| **Date** | 2026-08-24 |
| **Type** | tuning / standard |
| **Supersedes** | — |
| **Related** | ADR-0008（fail-closed 落点）、ADR-0012（A2/A3 量测口径同属分档） |
| **映射** | A1 算力考核 ≥90% |

## 决策

A1（算力峰值）的判定口径钉死为：**主形状 MFU≥90%**，且测量时**强制关闭图优化**（`compile_mode: none` + `enforce_eager: true` + `cudagraph_mode: none`），使图捕获=0。

## 为什么强制 eager

MFU 要看的是**裸算力**——硬件在不动用"加速技巧"时能不能真实跑到 90% 利用率。开图优化则等于"考试作弊"：图捕获能掩盖真实算力不足，MFU 不再诚实地反映硬件，验收也就失去意义。故图捕获必须归零。

## 测量工具链

`ascend-dmi` + `effective_compute_spec v1`（分母）+ `msprof`（逐算子校验，误差≤3%）。

## A1 计量口径：只算 prefill、防复用、防中途停

MFU≥90% 只是"多少"，还得钉死"怎么算"才不会漂。三条口径（详见 [task-expertise §1](../acceptance/task-expertise.md) 的逐项推理，本 ADR 为决策权威）：

1. **只算 prefill 算力**：主形状固定 `output_len:1` + `max_tokens:1`、`ignore_eos:true`。decode 主要吃访存、不计入 FLOPs 峰值，MFU 口径统一按 prefill；`ignore_eos` 强制不中途停，保证每问都稳定产出那 1 个 unit、采样可复现（eos 提前打断则请求体不可比）。
2. **禁前缀复用**：`enable_prefix_caching:false`。复用前缀会少算 FLOPs、低估算力，破坏"裸算力"口径。
3. **固定 FP16**：峰值算力按张量单元的 FP16 定义；W8A8 是降精度优化，不做算力峰值口径。

## 代价与局限

- **真机依赖重**：`ascend-dmi`/`msprof` 均为真机项；峰值分母来源与实测值见 [acceptance-tasks.md 组 A](../acceptance/acceptance-tasks.md)，设计决策以本 ADR 为准。
- 误差与口径校准需要真机 msprof 对账（脚本 `a1_peak.sh` / `msprof_reconcile.sh`）。

## 后果

- 新增/调整算力测量参数先入 `schema.yaml` allowed 集。
- "开图优化=作弊"是 A1 的硬约束，任何改动不得破坏 eager 强制。

## 兑现回填（Verification）

- ✅ **已兑现**：`scripts/a1_peak.sh` + `src/client/a1_peak.py` 落地（缺 ascend-dmi 时回退 torch-npu fp16 大矩阵乘取中位，注入 `VLLM_BENCHKIT_PEAK_FLOPS` 作 MFU 分母）；`scripts/msprof_reconcile.sh` 用于逐算子对账；eager 强制由 `compile_mode: none` / `enforce_eager: true` / `cudagraph_mode: none` 保证。