# 0003 Canonical 代码源 = config/topology.yaml 钉的双仓库（v0.18.0）

> **决策摘要**：一切脚本、环境、排错都以 `config/topology.yaml` 钉的 `vllm`/`vllm-ascend`（`releases/v0.18.0`）为唯一事实来源；可编辑安装只允许指向 canonical 源，import 解析到其它副本即为 bug。

| 元数据 | 值 |
|--------|----|
| **Status** | accepted |
| **Date** | 2026-08-20 |
| **Type** | standard |
| **Supersedes** | — |
| **Related** | ADR-0006（拓扑承载）、ADR-0007（commit 可覆盖） |
| **映射** | 可复现（跑的是 pin 的版本） |

所有脚本、环境、排错都以 `config/topology.yaml`（`dir`/`repos` 钉的 `vllm`/`vllm-ascend`，分支 `releases/v0.18.0`）为唯一事实来源。

机器上存在多个历史副本（内部改造版、reference-repos、workspace 目录等），分支/版本/是否是同一代码不一致，误用会导致"跑得通的其实是错的东西"。钉死 canonical 源，防止后来者"修复"到错误副本。

判定：本仓库的可编辑安装仅允许指向 canonical 源；任何 import 路径若解析到其它副本即为 bug。

## 兑现回填（Verification）

- ✅ **已兑现**：`deploy.sh install` 从 topology.yaml 推导路径后显式 `uv pip install -e <dir>/vllm -e <dir>/vllm-ascend`；import 到非 canonical 副本按 bug 处理（见 [ADR-0006](./0006-deployment-topology-manifest.md) 后果）。