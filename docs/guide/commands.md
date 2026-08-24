# 命令行功能（Commands）

本文说明每个脚本、每个子命令的**功能与适用场景**，帮你回答「该跑哪个命令、它到底做什么」。怎么执行见 [how-to-run.md](./how-to-run.md)，参数怎么配见 [config-reference.md](./config-reference.md)。

## 1. 脚本总览

| 脚本 | 职责 | 子命令 |
|------|------|--------|
| `scripts/bench.sh` | 基准 benchmark：wrap 官方 `vllm bench` | `serve` \| `throughput` \| `latency` |
| `scripts/profile.sh` | profiling：启动带 profiler 的 server 并采集/解析 | `serve` \| `start` \| `stop` \| `analyse` |

两者都：读 `config/config.yaml` 默认值（环境变量优先）、source `npu_env.sh` 加载 CANN、tee 输出到 `runs/`。具体装配逻辑见 [design.md §1](./design.md#L5)。

## 2. bench.sh 子命令

`bench.sh` wrap 官方 `vllm bench`（[ADR-0001](../adr/0001-use-vllm-bench-as-benchmark-driver.md)），三个子命令对应官方三个子命令：

| 维度 | `serve`（在线基准） | `throughput`（离线吞吐） | `latency`（离线延迟） |
|------|--------------------|-------------------------|----------------------|
| 官方命令 | `vllm bench serve` | `vllm bench throughput` | `vllm bench latency` |
| 运行方式 | 按 request-rate 请求流打服务，走完整链路 | 单进程直跑引擎，无网络/API/排队 | 单请求纯延迟，无并发压力 |
| 请求模型 | `num-prompts` 个请求 + `request-rate` 控制到达速率 | `input-len`/`output-len` 固定长度合成请求 | `input-len`/`output-len` 固定长度 + `batch-size` 并发 |
| 核心指标 | TTFT / TPOT / ITL / E2EL（带 percentile）+ 吞吐 | 吞吐（tokens/s）、总耗时 | 单请求延迟（mean/percentiles） |
| 关注点 | 真实服务体验、延迟是否达标 | 硬件 + 引擎算力上限 | kernel 级回归（单请求成本） |
| 适用场景 | 上线前压测、SLO 验证 | 硬件/版本对比、回归测试 | 单 token 路径的延迟回归 |
| 用到的 YAML 键 | `bench.backend/.endpoint/.num_prompts/.request_rate` | `bench.input_len/.output_len/.ignore_eos` | `bench.input_len/.output_len/.batch_size` |

**一句话**：`serve` 测「生产环境跑得怎么样」（含网络/排队/调度全链路，偏延迟），`throughput` 测「引擎本身能跑多快」（纯计算，偏吞吐、理论上限），`latency` 测「单请求纯延迟」（kernel 回归）。

三者共用 `model`、`devices`、`bench.dataset`、`bench.dataset_path`、`bench.load_format`；结果除 stdout/tee 外，还会 `--save-result` 落盘 JSON 到 `runs/`（见 [output.md §1 产物清单](./output.md#L5)）。

### 底层实际命令

`bench.sh` 会打印 `[bench] $ <完整命令>`，即它实际执行的官方命令，例如：

```
[bench] $ ./.venv/bin/vllm bench throughput --model Qwen/Qwen3-0.6B \
  --dataset-name random --input-len 128 --output-len 128 --save-result --result-dir runs
```

## 3. profile.sh 子命令

`profile.sh` 走「启动 server → 采集中 → 停止 → 解析」四段式，采集引擎是 Ascend PyTorch Profiler（[ADR-0004](../adr/0004-profiling-via-ascend-pytorch-profiler.md)）：

| 子命令 | 做什么 | 说明 |
|--------|--------|------|
| `serve` | 启动带 profiler 的 vLLM server | 阻塞式；通过 `--profiler-config` 注入采集配置（[design.md §1](./design.md#L5)）；端口由 `profile.port` 控制 |
| `start` | 向 server 发 `POST /start_profile` | 触发开始采集；需在 serve 运行中的另一终端执行 |
| `stop` | 向 server 发 `POST /stop_profile` | 停止采集并落盘 trace 到 `profile_out/` |
| `analyse` | 调用 `torch_npu.profiler.profiler.analyse` 解析 trace | 打印算子统计数据；输入 `profile_out/*_ascend_pt` |

典型时序：终端 1 `serve` → 终端 2 `start` → 期间发推理请求（`bench.sh serve` 或 curl 打 `/v1/completions`）→ `stop` → `analyse`。

## 4. 输出去向

- stdout：指标打印到终端
- 归档目录 `runs/<时间戳>-<vllm_sha7>-<va_sha7>/`（bench.sh）：`<mode>.log`（tee 全量）、`*.json`（结构化结果）、`manifest.yaml`（两仓完整 commit 哈希 + 参数快照，见 [output.md §1](./output.md#L5)）；目录名同时含 vllm 与 vllm-ascend 短哈希（ADR-0007）
- 本地无拓扑/无仓库时回退平铺 `runs/<时间戳>-<mode>.log`
- `profile_out/`：profile trace 原始产物

## 5. 待核实项

`--backend vllm` 是否进程内驱动、`latency` 与 `--save-result`/`--result-dir` 的真实 flag 语法等**核实收敛到单一权威**：[official-capabilities.md「已知冲突/待核实」](../official-capabilities.md)（见 [how-to-run.md §5](./how-to-run.md#L126)）。装好环境后实测回填仅在官方权威处进行，本文件不另行维护清单（避免多处漂移）。
