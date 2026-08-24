# A1 算力口径：MFU≥90% + 强制 eager（裸算力，禁图优化兜底）

## 决策

A1（算力峰值）的判定口径钉死为：**主形状 MFU≥90%**，且测量时**强制关闭图优化**（`compile_mode: none` + `enforce_eager: true` + `cudagraph_mode: none`），使图捕获=0。

## 为什么强制 eager

MFU 要看的是**裸算力**——硬件在不动用"加速技巧"时能不能真实跑到 90% 利用率。开图优化则等于"考试作弊"：图捕获能掩盖真实算力不足，MFU 不再诚实地反映硬件，验收也就失去意义。故图捕获必须归零。

## 测量工具链

`ascend-dmi` + `effective_compute_spec v1`（分母）+ `msprof`（逐算子校验，误差≤3%）。

## 代价与局限

- **真机依赖重**：`ascend-dmi`/`msprof` 均为真机项；峰值分母来源与实测值见 [acceptance-tasks.md 组 A](../acceptance/acceptance-tasks.md)，设计决策以本 ADR 为准。
- 误差与口径校准需要真机 msprof 对账（脚本 `a1_peak.sh` / `msprof_reconcile.sh`）。

## 后果

- 新增/调整算力测量参数先入 `schema.yaml` allowed 集。
- "开图优化=作弊"是 A1 的硬约束，任何改动不得破坏 eager 强制。