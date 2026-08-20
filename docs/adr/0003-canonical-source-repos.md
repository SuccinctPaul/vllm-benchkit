# Canonical 代码源 = config/topology.yaml 钉的双仓库（v0.18.0）

所有脚本、环境、排错都以 `config/topology.yaml`（`dir`/`repos` 钉的 `vllm`/`vllm-ascend`，分支 `releases/v0.18.0`）为唯一事实来源。

机器上存在多个历史副本（内部改造版、reference-repos、workspace 目录等），分支/版本/是否是同一代码不一致，误用会导致"跑得通的其实是错的东西"。钉死 canonical 源，防止后来者"修复"到错误副本。

判定：本仓库的可编辑安装仅允许指向 canonical 源；任何 import 路径若解析到其它副本即为 bug。