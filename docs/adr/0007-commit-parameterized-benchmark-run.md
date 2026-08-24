# 0007 运行级实验：commit 参数化基准 + 按 commit 归档（runs/ + 运行清单）

> **决策摘要**：给任意 git commit（vllm / vllm-ascend）→ 跑基准 → 结果按 commit 归档 `runs/<date>-<vllm_sha7>-<va_sha7>/` 并附运行清单（两仓完整哈希 + 参数快照 + seed + 时间/硬件），是版本间性能对比的可归因地基。

| 元数据 | 值 |
|--------|----|
| **Status** | accepted |
| **Date** | 2026-08-20 |
| **Type** | architecture |
| **Supersedes** | — |
| **Related** | ADR-0006（默认版本来源）、ADR-0003（canonical 约束） |
| **映射** | 可复现 / 版本对比（B0/B1 归因） |

新增「给任意 git commit（vllm / vllm-ascend）→ 跑基准 → 结果按 commit 归档」的运行级能力：代码版本钉从 topology.yaml 的固定 pin 泛化为可覆盖的 repo+commit，跑完自动归档到 `runs/<date>-<vllm_sha7>-<va_sha7>/`（目录名**同时带 vllm 与 vllm-ascend 两个 commit 短哈希**，缺任一个即不可归因到准确版本组合），并随附**运行清单**（两仓库完整 commit 哈希 + 生效运行参数快照 + seed + 时间/硬件）。该能力是「性能对比」的地基：没有两仓库的 commit 标识与参数快照，任何版本间指标对照都不可归因、不可复现。

背景：ADR-0006 把部署钉死在 topology.yaml 的一个版本，一次只能测一版。为支撑「对某个性能优化 PR 的 commit 前后做基准对比」，需要把「跑哪个代码版本」从清单默认提升为可指定的运行输入。

候选对比：复用官方 `vllm bench sweep`（只扫运行参数矩阵，不解决版本级历史归档，归因也不如单变量严格）；只拉取 git 历史不动环境（产不出真机结果）；每 commit 独立 .venv（可并行可回溯，但首版成本高）。所选方案：版本钉参数化 + 复用单一 .venv 串行跑，归档带完整 commit 哈希，为将来独立 .venv 升级留路径（见 docs/roadmap.md）。

取舍：归档格式（目录命名 + manifest 字段）会成为未来对比/回溯工具的地基，多个结果落地后再改成本高，故本轮就钉死规范；环境先复用单一 .venv，接受「同时只能跑一个 commit」的串行限制。

后果：归档规范（目录命名 + manifest 字段）内聚进 bench.sh——每次运行自动落 `runs/<date>-<vllm_sha7>-<va_sha7>/` 并写 manifest.yaml；「跑任意 commit」= 手动 checkout 目标仓库后直接跑 bench.sh（单一 .venv 串行约束），回清单版本用 deploy.sh install；运行清单是将来性能对比报告（Track 3）与拆解（Track 4）的输入；默认版本仍由 topology.yaml 提供，任意 commit 是一次性覆盖。

## 兑现回填（Verification）

- ✅ **已兑现**：`bench.sh` 每次运行自动落 `runs/<date>-<vllm_sha7>-<va_sha7>/` 并写 `manifest.yaml`（两仓完整哈希 + 生效参数快照 + seed + 时间/硬件）；默认版本来自 topology.yaml，任意 commit 为一次性覆盖（见 [guide/how-to-run §3.4](../guide/how-to-run.md#L89)）。
