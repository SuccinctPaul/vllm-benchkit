# 输出与结果说明（Output）

本文说明**跑完命令后，输出/产物落在哪里、每个指标是什么意思**，帮你确认黑盒跑通（P0）并读懂结果。运行方法见 [how-to-run.md](./how-to-run.md)，命令功能见 [commands.md](./commands.md)。

## 1. 产物清单

| 产物 | 位置 | 内容 | 谁产生 |
|------|------|------|--------|
| 终端 stdout | — | 指标打印 | bench.sh / profile.sh |
| 日志 | `runs/<时间戳>-<vllm_sha7>-<va_sha7>/<mode>.log` | tee 全量输出（`<mode>` 为 `serve`/`throughput`/`latency` 等）；目录名含 vllm 与 vllm-ascend 短哈希 | bench.sh |
| 结构化结果 | 同目录 `*.json` | `--save-result`/`--output-json` 的 benchmark 结果（各指标数值） | bench.sh（serve/throughput/latency） |
| 运行清单 | 同目录 `manifest.yaml` | 两仓**完整** commit 哈希 + 生效参数快照（getconf 输出）+ 时间/主机 | bench.sh |
| profiling trace | `profile_out/`（`profile.prof_dir`） | Ascend PyTorch Profiler 原始产物（`*_ascend_pt`） | profile.sh stop |

> 归档：远程部署环境下 `bench.sh` 把每次运行的 log/json/manifest 都收进 `runs/<时间戳>-<vllm_sha7>-<va_sha7>/`，一眼可知两 commit、可检索可回溯（[ADR-0007](../adr/0007-commit-parameterized-benchmark-run.md)）；本地无拓扑/无仓库时回退平铺 `runs/<时间戳>-<mode>.log`。

`runs/` 与 `profile_out/` 均在 `.gitignore`，不会误提交。

## 2. 指标解读：benchmark vs profile

两者输出**没有共同指标单位可比**：**benchmark 是服务/请求层的宏观数字（成绩单），profile 是算子/内核层的微观明细（X 光片）**。benchmark 发现问题，profile 解释原因。

### 2.1 benchmark（vllm bench）——宏观成绩单

官方指标定义见 [vLLM 基准测试 CLI](https://docs.vllm.com.cn/en/latest/benchmarking/cli/) 与 [BenchmarkMetrics API](https://docs.vllm.ai/en/v0.9.1/api/vllm/benchmarks/serve.html)：

| 指标 | 含义 |
|------|------|
| TTFT | 首 Token 时间（排队+调度+prefill 全链路） |
| TPOT | 每个输出 token 的生成时间 |
| ITL | 相邻输出 token 的间隔延迟 |
| E2EL | 端到端请求延迟 |
| request_throughput | 请求吞吐（req/s） |
| output_throughput / total_token_throughput | 输出 / 总 token 吞吐（token/s） |
| completed / total_input / total_output | 完成请求数、输入 / 输出 token 总数 |

- 延迟类指标（TTFT/TPOT/ITL/E2EL）各带 mean/median/std/percentiles；
- 离线 `throughput` 模式输出吞吐（tokens/s）等，无延迟维度；
- 三个子命令的核心指标差异见 [commands.md](./commands.md#2-benchsh-子命令)。

### 2.2 profile（Ascend PyTorch Profiler）——微观明细

官方说明见 [CANN Ascend PyTorch Profiler 采集文档](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/80RC3alpha001/devaids/auxiliarydevtool/atlasprofiling_16_0038.html)，产物在 `profile_out/`：

| 产物 | 指标 |
|------|------|
| op_statistic.csv | AI Core / AI CPU 算子调用次数 + 耗时（定位瓶颈核心） |
| operator_details.csv | 算子耗时详情 |
| step_trace_time.csv | 迭代中计算 vs 通信时间 |
| operator_memory.csv / memory_record.csv / npu_module_mem.csv | 算子 / 模块内存占用 |
| l2_cache.csv | L2 Cache 命中 |
| trace_view.json / kernel_details.csv | Chrome trace 时间线、kernel 级数据 |

## 3. 黑盒跑通判定（P0）

三个口径全部满足即「黑盒跑通」：

1. **退出码 0**（命令正常结束）；
2. **关键指标非零**：throughput > 0、TTFT/ITL 有限（不超时、不 NaN/Inf）；
3. **日志无 NPU 报错**（无 ascend/torch_npu 相关错误）。

判定顺序见 [how-to-run.md](./how-to-run.md#4-p0-验收黑盒跑通)。

## 4. 待核实项

v0.18.0 实测输出的字段名与 `profile.sh analyse` 打印格式、`runs/*.json` 的字段结构，装好环境后回填（同 [ADR-0004](../adr/0004-profiling-via-ascend-pytorch-profiler.md) 待核实项）。
