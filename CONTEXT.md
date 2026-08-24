# vllm-ascend benchmark/profile（vllm-benchkit）

本仓库用于沉淀「成为 vLLM Ascend 插件专家」的知识：从 benchmark 与 profiling 入手，先黑盒跑通、再逐步拆解，产出可复现、低耦合、模块化的薄脚本与文档。基准版本钉在 v0.18.0。

## Language

### 目标与阶段

**黑盒运行（black-box run）**:
在不改动、不深入源码的前提下，直接用官方命令把小模型跑出可复现指标的过程。
_Avoid_: 深挖、探索、debug

**黑盒跑通（P0 acceptance）**:
本期验收口径 = 退出码 0、关键指标非零（throughput>0、TTFT/ITL 有限）、日志无 NPU 报错。不引入 summary/归一化等工具层。
_Avoid_: 未达标就进入拆解、加指标解析/可视化工具

**拆解（dissect）**:
阅读源码与调用链，弄清每一层"是谁的、做了什么、为什么"。在「性能对比」功能达标（Gate-C，见 docs/roadmap.md）后启动。
_Avoid_: 探索、研究（语义过宽时）

**成专家（含短期/长期两层）**:
短期 = 能一键跑通 benchmark/profile；长期 = 能复现官方指标、定位瓶颈、给出有依据的调优建议。
_Avoid_: 目标不拆层地谈"成为专家"

### 三层归属（"用的是谁的框架"）

**vLLM core（引擎/基准驱动）**:
服务入口、调度器、以及 `vllm bench serve/throughput/latency` 命令的归属方。基准的真实驱动层。
_Branch_: `vllm` @ `releases/v0.18.0`（url/branch 见 config/topology.yaml 的 `repos`）
_Avoid_: 把基准命令当成 vllm-ascend 提供的

**vllm-ascend（NPU 后端插件）**:
把 vLLM 的默认 platform 替换为 Ascend、让 Worker 跑在 torch_npu 上的插件层。不是另一套独立框架。
_Branch_: `vllm-ascend` @ `releases/v0.18.0`（url/branch 见 config/topology.yaml 的 `repos`）
_Avoid_: 把 vllm-ascend 自身当基准驱动框架

**Ascend PyTorch Profiler（profiling 引擎）**:
华为 CANN 的 `torch_npu.profiler`，算子/CANN/NPU 级采集引擎。由 vLLM 的 `--profiler-config` 编排触发。
_Avoid_: 与 MS Service Profiler、msprobe 混淆

### 三类工作（必须区分）

**benchmark（宏观指标）**:
测 TTFT/ITL/TPOT/E2EL 与吞吐等宏观性能指标。用官方 `vllm bench`。
_Avoid_: profile、debug

**profile（瓶颈定位）**:
定位算子/调度/硬件瓶颈，采集并解析性能数据。v0.18.0 本期用 Ascend PyTorch Profiler。
_Avoid_: benchmark、debug、msprobe

**debug（精度调试）**:
msprobe 一类 dump 算子输入输出查 NaN/精度漂移。本期不在范围内，标记 Todo。
_Avoid_: profile

### 性能对比（版本级基准，下一主攻）

**性能对比（perf comparison）**:
给任意 git commit（vllm / vllm-ascend）跑基准并归档结果，从而在版本间（如某优化 PR 前后）对照指标的能力。主形态为 A/B 单变量对比；sweep / QPS 压力曲线 / 官方全套等形态写入 docs/roadmap.md 备查。
_Avoid_: 混同为 profile/debug；无运行清单就下结论

**commit 版本变体（version variant）**:
「跑基准」输入的代码版本钉 = repo + commit 哈希（vllm 或 vllm-ascend）。由 deploy.sh 的固定 pin 泛化而来：默认取 topology.yaml 清单钉，实验时覆盖为任意 commit。
_Avoid_: 只写分支不写哈希；把 commit 塞回 pyproject/config.yaml

**按 commit 归档（run archive）**:
每次基准运行的结果落在 `runs/`，目录**同时带 vllm 与 vllm-ascend 两个 commit 短哈希**（runs/<date>-<vllm_sha7>-<va_sha7>/），完整哈希见同目录 manifest.yaml。缺任一个 commit 即不可归因到准确版本组合。
_Avoid_: 覆盖旧结果；目录只带一个 commit 或零 commit；结果散落

**运行清单（run manifest）**:
随每次运行归档的元数据快照：vllm 与 vllm-ascend 的完整 commit 哈希、生效运行参数（getconf 输出）、seed、时间与硬件。缺它则结果不可归因、不可复现。
_Avoid_: 只存日志不存参数快照；用短哈希冒充完整哈希

### 工具与命令

**vllm bench CLI（基准统一命令）**:
vLLM core 提供的子命令：`serve`（在线）、`throughput`（离线）、`latency`、`sweep`。
_Avoid_: 裸 benchmarks/*.py、ascend 自家 vllm_bench.py

**sharegpt 数据集**:
官方在线基准示例数据集（需下载）。
_Avoid_: 无

**random 数据集（合成）**:
`vllm bench serve/throughput --dataset-name random`，零下载，适合快速 smoke。
_Avoid_: 无

**smoke 模型**:
本期黑盒用小模型：`Qwen/Qwen3-0.6B`（默认，bench 与 profile 统一）、`facebook/opt-125m`（本地已缓存备用）。
_Avoid_: 大模型、量化版

**config/config.yaml（运行参数配置）**:
描述"怎么跑"：bench.sh/profile.sh 参数默认值的唯一来源；读取采用「环境变量 > YAML 默认」优先级（ADR-0005）。与 deployment 拓扑（config/topology.yaml）严格分层，两者键不重叠。
_Avoid_: 把参数默认值再写回脚本、引入多 profile/CLI --set；把部署拓扑塞进本文件

**config/topology.yaml（部署拓扑清单）**:
描述"在哪里跑、跑哪些代码"：`server`（SSH 目标机）、`dir`（远程工作目录）、以及 vllm-benchkit/vllm/vllm-ascend 三个仓库各自的 url/branch/commit（ADR-0006）。与运行参数 config.yaml 分层不重合。
_Avoid_: 把 server/repo 路径/commit 写回 pyproject.toml、guide、脚本内

**隐私边界（基础设施信息仅存 topology.yaml）**:
机器名 / SSH 目标机、远程路径、仓库 URL / 用户名、预装环境名等隐私与基础设施信息，只允许出现在 `config/topology.yaml`（已 .gitignore，不入库）；任何文档（README/ADR/guide/CONTEXT）一律引用拓扑清单键名（`server`/`dir`/`repos`），不出现具体值。
_Avoid_: 在文档里硬编码机器名、`/root/...` 路径、`git@github.com:...`、用户名

**deploy.sh（配置驱动布置）**:
唯一消费 topology.yaml 的脚本：ssh 到 server → 确保 dir → clone/checkout 到清单钉的 commit → editable 安装 vllm/vllm-ascend（路径由清单推导）。CI/CD 式 provisioning（ADR-0006）。
_Avoid_: 手动 rsync/scp 工作副本、在别处硬编码仓库路径

### 本期范围（边界）

**910B2（Atlas 卡型）**:
目标硬件 Atlas 910B2 × 8 卡，HBM 64GB，CANN 9.0.0，裸机（无 docker）。
_Avoid_: 推到 A2/A3；无 docker 却按容器文档走

**踢出项（本期明确的 OUT，标记 Todo）**:
量化、LoRA、Speculative Decoding、多模态、MS Service Profiler、msprobe、多节点/PD 分离/EP。
_Avoid_: 混入本期 smoke

**uv（环境管理器）**:
用 uv 建专用虚拟环境，管理 torch/torch_npu/vllm/vllm-ascend 依赖；不继承预装环境。
_Avoid_: conda、复用预装环境

**canonical 仓库源**:
脚本/环境仅从 vllm-benchkit / vllm / vllm-ascend 三个仓库取用；其远程路径与 branch/commit 的唯一事实来源是 `config/topology.yaml`（ADR-0006），默认部署在拓扑清单 `server`/`dir` 下、@ `releases/v0.18.0`。
_Avoid_: 机器上其它历史副本；在 pyproject/guide 里另写仓库路径