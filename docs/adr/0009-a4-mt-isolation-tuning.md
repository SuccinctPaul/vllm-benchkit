# 0009 A4-MT 隔离调优与软参数校准（max_num_seqs 演化 + 隔离口径）

> **决策摘要**：A4 多租户隔离的软参数无法从方案推导、只能真机校准——`max_num_seqs` 16→32→36 的演化、`capture_sizes` 对齐峰值 batch；隔离口径钉为 p99 比值（隔离/基线）≤1.25×。这类"软参数"最易被当成固定事实，故单独专档作为唯一权威。

| 元数据 | 值 |
|--------|----|
| **Status** | accepted |
| **Date** | 2026-08-24 |
| **Type** | tuning |
| **Supersedes** | — |
| **Related** | ADR-0002（软参数校准与版本 pin 并行）、ADR-0008（确定性/fail-closed 落点）、ADR-0012（A2/A3 量测口径同属分档） |
| **映射** | A4 多租户隔离门槛 ≤1.25× |

> 本篇记录这套验收系统里最"真机调出来"的一批决策。它们的共同点：**无法从方案直接推导**，必须靠真机反复校准（见 [design.md §6 局限](../acceptance/design.md)）。这类"软参数"最容易被误当成固定事实，故单独立档，作为唯一权威。

## 问题：隔离性 K2 从 verify15 调到 verify19 才达标

初版配置（`max_num_seqs=16`）导致 KV cache 欠利用率（~5%）与吞吐偏低（<192 tok/s），隔离基线反复出现「假 FAIL」。经过多轮真机校准，收敛为一组有效参数。

## 校准关键：`max_num_seqs` 演化 16→32→36

- **16**：初版，KV cache 利用率不足、吞吐不达标（源码 `bench.sh`/初版 a4-mt）。
- **32**：第一轮修复，`per_tenant_concurrency=8`、`global_concurrency=32`，提升 KV 利用与隔离度。
- **36**：进一步匹配 `per_tenant_concurrency` 与 `max_num_seqs`（4×9），`capture_sizes` 显式扩到 36 覆盖峰值 batch；隔离基线解除 per-tenant 限流后，单租户能独占满 36 并发，与混载同并发天花板对比，才是公平口径。

## 隔离判定口径（A4）

A2 逐租户按同组阈值判定；**A4 改为相对隔离判定**：p99 比值（隔离/基线）≤1.25× 判过，绝对阈值仅记录。

## A4 成本口径：`cost_len`/`output_cap` 拆分 + 唯一计价公式

多租户成本有两处易分歧（详见 [task-expertise §4](../acceptance/task-expertise.md) 的逐项推理，本 ADR 为决策权威）：

1. **`output_cap` 与 `cost_len` 拆开**：`output_cap` 是单请求 `max_tokens` 上限，**防不受控**（挡长生成打爆）；`cost_len` 是期望输出长度/**调度成本**，驱动 `request_rate = token_rate × share / cost_len`。拆开才不打架：调度按"预期多长"排班，防爆栈按"最强多长"兜底。`cost_len` 取真机实测（dialogue 13 / tool 5 / reason 2 / struct 23），否则 `request_rate×cost_len` 下份额收敛不到 25%。
2. **唯一计价公式**：「划算不划算」必须唯一口径，否则租户间、机型间无法横向比较：`÷21600h`（生命周期年化）+ `0.6 元/kWh`（电费单价） + 按成功输出 token 计价，全部在 [src/client/cost.py](../../src/client/cost.py) 落成同一算法。

## 代价与局限

- 真机依赖重：A 组 ascend-dmi/msprof、K1 功耗/资产原值均为真机项，部分只能回退估算。
- 配置漂移风险：`max_num_seqs`/`capture_sizes`/`isolation_token_rate` 等软参数需真机反复校准，无法从 PDF 直接推导——此为设计取舍而非缺陷。

## 后果

- `a4-mt.yaml` 是本组软参数的落地位置（唯一权威）；`task-expertise.md` 记录的"36 并发天花板"口径以本 ADR 为准。
- 真机换卡/换版本时，先复校本组参数再跑隔离；`max_num_seqs` 改动须先入 `schema.yaml` allowed 集。

## 兑现回填（Verification）

- ✅ **已兑现**：隔离性 K2 从 verify15 反复调到 verify19 才达标；`a4-mt.yaml` 为本组软参数唯一权威，`task-expertise.md` 的"36 并发天花板"口径以本 ADR 为准。