# A4-MT 隔离调优与软参数校准（max_num_seqs 演化 + 隔离口径）

> 本篇记录这套验收系统里最"真机调出来"的一批决策。它们的共同点：**无法从 V4.1 方案直接推导**，必须靠真机反复校准（见 [design.md §6 局限](../acceptance/design.md)）。这类"软参数"最容易被误当成固定事实，故单独立档，作为唯一权威。

## 问题：隔离性 K2 从 verify15 调到 verify19 才达标

初版配置（`max_num_seqs=16`）导致 KV cache 欠利用率（~5%）与吞吐偏低（<192 tok/s），隔离基线反复出现「假 FAIL」。经过多轮真机校准，收敛为一组有效参数。

## 校准关键：`max_num_seqs` 演化 16→32→36

- **16**：初版，KV cache 利用率不足、吞吐不达标（源码 `bench.sh`/初版 a4-mt）。
- **32**：第一轮修复，`per_tenant_concurrency=8`、`global_concurrency=32`，提升 KV 利用与隔离度。
- **36**：进一步匹配 `per_tenant_concurrency` 与 `max_num_seqs`（4×9），`capture_sizes` 显式扩到 36 覆盖峰值 batch；隔离基线解除 per-tenant 限流后，单租户能独占满 36 并发，与混载同并发天花板对比，才是公平口径。

## 隔离判定口径（A4）

A2 逐租户按同组阈值判定；**A4 改为相对隔离判定**：p99 比值（隔离/基线）≤1.25× 判过，绝对阈值仅记录。

## 代价与局限

- 真机依赖重：A 组 ascend-dmi/msprof、K1 功耗/资产原值均为真机项，部分只能回退估算。
- 配置漂移风险：`max_num_seqs`/`capture_sizes`/`isolation_token_rate` 等软参数需真机反复校准，无法从 PDF 直接推导——此为设计取舍而非缺陷。

## 后果

- `a4-mt.yaml` 是本组软参数的落地位置（唯一权威）；`task-expertise.md` 记录的"36 并发天花板"口径以本 ADR 为准。
- 真机换卡/换版本时，先复校本组参数再跑隔离；`max_num_seqs` 改动须先入 `schema.yaml` allowed 集。