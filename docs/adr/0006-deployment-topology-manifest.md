# 部署拓扑外置到 config/topology.yaml，去除 pyproject 中的仓库路径

新增 `config/topology.yaml` 承载**部署拓扑**（SSH 目标机、远程工作目录、以及要 clone 的 vllm-benchkit / vllm / vllm-ascend 三个仓库及其 branch/commit）；`pyproject.toml` 不再书写 vllm / vllm-ascend 的仓库路径。部署动作（clone/checkout/install）由 `deploy.sh` 按清单驱动——配置驱动执行，而非可复现锚点。

背景：目前"这台机器是谁、代码在哪、跑哪个分支"散落三处——pyproject.toml 的 `[tool.uv.sources]`（远程仓库路径）、scripts/guide 的文字承诺（SSH 目标、releases/v0.18.0）、CONTEXT.md。当要新增/切换服务器或换仓库 commit 时只能改代码。为满足"像 CI/CD 一样用一张清单声明环境并按之布置"，把这一层抽成单文件。它与 `config/config.yaml`（bench/profile 运行参数）严格分层、不重合：运行参数描述"怎么跑"，拓扑描述"在哪里、跑哪些代码"。

候选对比：并入现有 config.yaml（两层含义混淆，违背"不重合"）；仅作文档记录、脚本不读（守不住"跑的是 pin 的 commit"）；在 pyproject 保留路径、清单只管校验（路径双写、两处维护）。所选方案让**路径与 commit 都只出现在清单一处**，pyproject 回归纯第三方运行时依赖。

取舍：仓库的 editable 安装不再由 pyproject `[tool.uv.sources]` 承载，改由 `deploy.sh` 从清单推导路径后显式 `uv pip install -e <dir>/vllm -e <dir>/vllm-ascend`。代价是安装顺序依赖 deploy 先跑、venv 里的仓库来源必须与清单一致（否则 import 到错误副本即是 bug，判定见 ADR-0003）。不做本地↔远程工作副本的同步模型，清单只描述远程侧。

后果：新增一台机器只需改 topology.yaml 的 `server`/`dir`；换仓库 commit 只需改对应 `commit` 字段；`config.yaml` 与 `topology.yaml` 语义互补、键不重叠；`deploy.sh` 是这份清单的唯一消费方。