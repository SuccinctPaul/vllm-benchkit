# How to run

本文只回答一件事：**怎么把 benchmark 和 profile 跑起来**。命令行功能见 [commands.md](./commands.md)，参数怎么配见 [config-reference.md](./config-reference.md)，跑完怎么看结果见 [output.md](./output.md)。

跑之前先选对命令：三个基准子命令的差异总览如下（详细说明见 [commands.md §2](./commands.md#L14)）。

| 维度 | `serve`（在线基准） | `throughput`（离线吞吐） | `latency`（离线延迟） |
|------|--------------------|-------------------------|----------------------|
| 官方命令 | `vllm bench serve` | `vllm bench throughput` | `vllm bench latency` |
| 运行方式 | 按 request-rate 请求流打服务，走完整链路 | 单进程直跑引擎，无网络/API/排队 | 单请求纯延迟，无并发压力 |
| 请求模型 | `num-prompts` 个请求 + `request-rate` 控制到达速率 | `input-len`/`output-len` 固定长度合成请求 | `input-len`/`output-len` 固定长度 + `batch-size` 并发 |
| 核心指标 | TTFT / TPOT / ITL / E2EL（带 percentile）+ 吞吐 | 吞吐（tokens/s）、总耗时 | 单请求延迟（mean/percentiles） |
| 关注点 | 真实服务体验、延迟是否达标 | 硬件 + 引擎算力上限 | kernel 级回归（单请求成本） |
| 适用场景 | 上线前压测、SLO 验证 | 硬件/版本对比、回归测试 | 单 token 路径的延迟回归 |
| 用到的 YAML 键 | `bench.backend/.endpoint/.num_prompts/.request_rate` | `bench.input_len/.output_len/.ignore_eos` | `bench.input_len/.output_len/.batch_size` |

## 1. 前置条件

| 项 | 值 | 来源 |
|----|----|------|
| 机器 | SSH 目标机：`config/topology.yaml` 的 `server` | [topology.yaml](../../config/topology.yaml) |
| 硬件 | Atlas 910B2 × 8（HBM 64GB） | [CONTEXT.md](../../CONTEXT.md) |
| CANN | 9.0.0 | [CONTEXT.md](../../CONTEXT.md) |
| 代码源 | `config/topology.yaml` 的 `dir`/`repos` 钉的 `vllm`/`vllm-ascend` @ `releases/v0.18.0` | [topology.yaml](../../config/topology.yaml) / [ADR-0003](../adr/0003-canonical-source-repos.md) |
| 环境 | uv 建的 `.venv`（torch 2.9.0 / torch-npu 2.9.0.post2） | [ADR-0002](../adr/0002-manage-environment-with-uv.md) |

### 1.0 机器层面基础工具链（需手动装，deploy.sh 假定已就位）

`deploy.sh` 假定目标机已具备这些**机器层面的地基**，本仓库不自动安装；全新裸机上要先用 `scripts/bootstrap.sh` 补齐或手工装齐：

| 工具 | 用途（为什么必需） | 默认安装方式 |
|------|--------------------|-------------|
| `git` | 拉取/切换 vllm、vllm-ascend（`deploy.sh` clone/fetch/checkout） | 发行版包 |
| `curl` | [可选] 下载 uv 官方安装器 | 发行版包 |
| `gcc` / `g++` / `make` | 编译 vllm-ascend / numba / eplb 等 C/C++ 依赖 | 发行版包 |
| `python3.11` | `uv venv --python 3.11` 需要（`deploy.sh` 建 .venv 的版本） | 发行版包/官方 |
| `uv` | 建 venv 与 pip 安装（`deploy.sh` + `uv sync` 都靠它） | 官方安装器（`~/.local/bin`） |
| CANN 9.0.0 + NPU 驱动 | vllm-ascend 运行前提；`npu_env.sh` 只 source 已装好的 set_env.sh | **必须手工安装**（华为官方包，root） |

> **强烈建议用 `scripts/bootstrap.sh`**：一条命令检查/补齐除 CANN 外的全部基础工具链（它不装 CANN，只检测并提示手工装，因为 CANN 需 root + 华为安装包，机型/版本各异不适合自动装）。

```bash
# 只读检查还缺什么（不安装）
./scripts/bootstrap.sh check
# 补齐基础工具链（需 root 装系统包；uv 装到用户目录）
./scripts/bootstrap.sh
```

## 2. 安装（一次性）

```bash
cd <topology.yaml 的 dir>/vllm-benchkit   # dir 见 config/topology.yaml
uv sync   # 读 pyproject.toml：torch/torch-npu/vllm/vllm-ascend/pyyaml
```

闸门：`uv sync` 须能解析 torch-npu==2.9.0.post2 的 wheel 源；装完确认 `.venv/bin/vllm` 存在。

## 3. 快速开始

所有脚本在 `scripts/`，都要求 `.venv` 已装好；脚本会自行 source `npu_env.sh`（CANN 环境），无需手动加载。

### 3.1 基准 benchmark

官方 CLI 能力全景与可增补项见 [官方能力清单](../official-capabilities.md)（复用官方，少造轮子）。

```bash
# 离线吞吐（合成 random 数据集，零下载）—— 推荐先跑这个做 smoke
./scripts/bench.sh throughput

# 在线（默认 random；换 sharegpt 需先下载并指定路径）
BENCH_DATASET=sharegpt BENCH_DATASET_PATH=/path/to/SampleShareGPTData.jsonl ./scripts/bench.sh serve

# 离线延迟（单请求纯延迟，kernel 回归；batch 由 bench.batch_size 控制）
./scripts/bench.sh latency
```

命令差异（serve / throughput / latency 选哪个）见 [commands.md §2](./commands.md#L14) 的对照表。

### 3.2 profiling

```bash
./scripts/profile.sh serve      # 终端 1：启动带 profiler 的 vLLM server（阻塞）
./scripts/profile.sh start      # 终端 2：开始采集
# ...期间发送推理请求（另跑 bench.sh serve，或用 curl 打 /v1/completions）...
./scripts/profile.sh stop       # 停止采集
./scripts/profile.sh analyse    # 解析 profile_out/*_ascend_pt，打印算子数据
```

### 3.4 指定 commit 跑基准（按 commit 归档）

`bench.sh` 每次运行会把产物归档到 `runs/<date>-<vllm_sha7>-<va_sha7>/`（目录名同时含 vllm 与 vllm-ascend 的短哈希，完整哈希与参数快照在同目录 `manifest.yaml`，见 [ADR-0007](../adr/0007-commit-parameterized-benchmark-run.md)）。默认测的是拓扑清单钉的版本；想测任意 commit，先手动 checkout 目标仓库再跑：

```bash
# 远程：目标仓库（vllm 或 vllm-ascend）checkout 到任意 commit（哈希/branch/tag 均可）
git -C <dir>/vllm fetch --all --prune
git -C <dir>/vllm checkout --detach <commit>

# 归档自动带两仓短哈希
./scripts/bench.sh throughput
```

注意：单一 `.venv` 串行，跑完回清单钉版本用 `./scripts/deploy.sh install`。

### 3.5 覆盖参数（环境变量 > YAML）

不改配置文件即可临时覆盖：

```bash
MODEL=facebook/opt-125m ./scripts/bench.sh throughput
ASCEND_RT_VISIBLE_DEVICES=0,1 ./scripts/bench.sh serve
VLLM_BENCHKIT_RUNS=/tmp/my-runs ./scripts/profile.sh serve
```

优先级与全部可覆盖键见 [config-reference.md §2](./config-reference.md#L28)。

## 4. P0 验收（黑盒跑通）

口径：**退出码 0 + 关键指标非零（throughput>0、TTFT/ITL 有限）+ 日志无 NPU 报错**。五步顺序：

1. `uv sync`（venv 全量装齐）
2. NPU smoke：`.venv/bin/python -c "import torch_npu"` 并识别 910B2
3. `./scripts/bench.sh throughput`（random 128/128）
4. `./scripts/bench.sh serve`（random，10 prompts）
5. `./scripts/profile.sh serve` + start/stop/analyse

## 5. 待核实项

官方 flag 语法/冲突核实**收敛到单一权威**：[official-capabilities.md「已知冲突/待核实」](../official-capabilities.md)（含 `--profiler-config`、`--batch-size`/`--save-result`/`--result-dir` 等）。装好环境后实测回填**仅在官方权威处**进行，本文件与 [commands.md](./commands.md)、[ADR-0004](../adr/0004-profiling-via-ascend-pytorch-profiler.md) 不另行维护待核实清单（避免多处漂移）。
