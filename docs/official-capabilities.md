# 官方能力清单与可增补项（Official Capabilities）

本文盘点官方已提供的基准/性能能力（vLLM `vllm bench` CLI + vllm-ascend 官方 `benchmarks/`），对照 vllm-benchkit 现状，列出**可增补的功能**。原则：**功能层全部复用官方能力，脚本只做薄 wrap + 参数化，不重写官方逻辑（少造轮子）**。

## 官方来源

| 来源 | 链接 |
|------|------|
| vLLM 基准测试 CLI 文档 | [https://docs.vllm.com.cn/en/latest/benchmarking/cli/](https://docs.vllm.com.cn/en/latest/benchmarking/cli/) |
| vLLM 参数扫描（sweeps） | [https://docs.vllm.com.cn/en/latest/benchmarking/sweeps/](https://docs.vllm.com.cn/en/latest/benchmarking/sweeps/) |
| vLLM 性能仪表盘（dashboard） | [https://docs.vllm.com.cn/en/latest/benchmarking/dashboard/](https://docs.vllm.com.cn/en/latest/benchmarking/dashboard/) |
| vllm-ascend v0.18.0 性能基准文档 | [https://docs.vllm.ai/projects/ascend/zh-cn/v0.18.0/developer_guide/performance_and_debug/performance_benchmark.html](https://docs.vllm.ai/projects/ascend/zh-cn/v0.18.0/developer_guide/performance_and_debug/performance_benchmark.html) |
| vllm-ascend 服务剖析指南（Ascend PT Profiler + MS Service Profiler） | [https://docs.vllm.ai/projects/ascend/zh-cn/v0.18.0/developer_guide/performance_and_debug/service_profiling_guide.html](https://docs.vllm.ai/projects/ascend/zh-cn/v0.18.0/developer_guide/performance_and_debug/service_profiling_guide.html) |
| vllm-ascend 优化与调优（torch_npu / HCCL / OS） | [https://docs.vllm.ai/projects/ascend/zh-cn/v0.18.0/developer_guide/performance_and_debug/optimization_and_tuning.html](https://docs.vllm.ai/projects/ascend/zh-cn/v0.18.0/developer_guide/performance_and_debug/optimization_and_tuning.html) |
| vllm-ascend 官方 benchmarks repo | [https://github.com/vllm-project/vllm-ascend/tree/main/benchmarks](https://github.com/vllm-project/vllm-ascend/tree/main/benchmarks) |

## 表1：基准同族 — 官方 flag 直接增补（零/低自研）

> **⭕ 待增补 = 本期能力候选池（正式 backlog）**：这些都是官方 flag、零/低自研即可接入的能力，**接不接入由 P0 验收与本期范围决定，非承诺**。要启用某项时，按本文「维护注意」五处同改。标注 ⭐ 的优先接入（可复现承诺）；「语法待核实」的在官方权威区（见下）确认后再动手。

| 官方能力 | 官方入口 / flag | 出处 | 现状 | 增补动作 |
|---|---|---|---|---|
| 在线基准 | `vllm bench serve` | ascend doc §3.2.1 | ✅ 已复用（bench.sh serve） | — |
| 离线吞吐 | `vllm bench throughput` | ascend doc §3.2.2 | ✅ 已复用（bench.sh throughput） | — |
| 离线延迟 | `vllm bench latency` | ascend repo README | ✅ 已落地（bench.sh latency） | `--batch-size` 走 `bench.batch_size` |
| JSON 结果落盘 | `--save-result --result-dir` / `--output-json` | CLI doc | ✅ 已落地 | serve 用 `--save-result --result-dir`；throughput 用 v0.18.0 的 `--output-json`；latency 无落盘 flag（结果打印 stdout） |
| 结果可视化 | `--plot-timeline --plot-dataset-stats` | CLI doc | ⭕ 待增补 | 加 flag 产 HTML 时间线 / 数据集统计 |
| 固定 QPS / 打满 | `--request-rate 1/4/16/inf` | ascend repo README | ⭕ 待增补 | config 加 `bench.qps`，对齐官方方法论（Poisson 到达） |
| 固定随机种子 | `--seed` / `--random-seed` | ascend repo README | ⭕ 待增补 ⭐ | **支撑「可复现指标」承诺**；config 加 `bench.seed` |
| 渐进式请求速率（ramp-up） | `--request-rate` 带渐进式取值 | CLI doc | ⭕ 待增补 | 平滑加压，语法待核实 |
| 负载模式（load-pattern） | load-pattern 相关 flag | CLI doc | ⭕ 待增补 | 官方新能力，语法待核实 |
| 探测请求（probe） | probe 相关 flag | CLI doc | ⭕ 待增补 | 语法待核实 |
| 自定义数据集 | `--dataset-name custom` | CLI doc | ⭕ 待增补 | 支持本地 jsonl（当前仅 random/sharegpt） |
| HuggingFace 数据集 | `--dataset-name hf` + `--hf-split`/`--hf-name` | CLI doc | ⭕ 待增补 | 真实数据集（MTBench/GSM8K/HumanEval/BurstGPT 等），config 透传 |
| 前缀重复数据集 | `--dataset-name prefix_repetition` | CLI doc | ⭕ 待增补 | 合成、零下载，prefix caching 同族 |
| 逐请求明细 | `--save-detailed` | CLI doc | ⭕ 待增补 | 结果 JSON 带 per-request 明细 |
| 预热 | `-w` / `--warmup` | CLI doc | ⭕ 待增补 | 预热后再测，结果更稳定 |
| 自定义百分位 | `--percentile-metrics` | CLI doc | ⭕ 待增补 | 自定义 P50/P95/P99 输出 |
| 指定结果文件名 | `--result-filename` / `--output-json` | ascend repo（latency 用 `--output-json`） | ⭕ 待增补 | 指定 JSON 落盘名；与 `--save-result` 差异待核实 |
| 采样参数 | `--temperature --top-p` | CLI doc | ⭕ 待增补 | config 透传 |
| dummy 权重免下载 | `--load-format dummy` | ascend repo README | ✅ 已落地 | config `bench.load_format` 透传 |

## 表2：官方已有一键/报告脚本 — 直接复用，勿重写

| 官方能力 | 官方实现 | 建议 |
|---|---|---|
| 一键全套三测（serving/throughput/latency） | `benchmarks/scripts/run-performance-benchmarks.sh` + `benchmarks/tests/*.json`（JSON 定义 server/client 参数 + qps_list） | 官方一体化脚本；未来要"同模型多 QPS 全套跑"时直接跑官方脚本 |
| 报告生成 | `benchmarks/scripts/convert_json_to_markdown.py` + `perf_result_template.md` | 官方 JSON→Markdown 报告，P0 后需要时复用 |

## 表3：profile 同族 — 官方剖析能力（Ascend PT Profiler + MS Service Profiler）

出处：vllm-ascend 服务剖析指南（官方明确对比两种方案：算子级 vs 服务框架级）。

| 官方能力 | 官方入口 / flag | 出处 | 现状 | 增补动作 |
|---|---|---|---|---|
| 在线算子级剖析 | `api_server --profiler-config` + `/start_profile` `/stop_profile` | 服务剖析指南 §Ascend PT Profiler | ✅ 已落地（profile.sh serve/start/stop/analyse） | — |
| python 调用栈开关 | `torch_profiler_with_stack`（默认 true，数据量更大） | 服务剖析指南 | ✅ 已落地（profile.with_stack） | — |
| 离线剖析 | `vllm bench throughput/latency --profiler-config`（离线 `profiler_config` 参数） | 服务剖析指南 | ⭕ 待增补 | 离线也能采算子 trace；注意 `VLLM_TORCH_PROFILER_DIR` 已弃用 |
| 服务框架级剖析 | MS Service Profiler：`pip install msserviceprofiler` + `SERVICE_PROF_CONFIG_PATH` | 服务剖析指南 §MS Service Profiler | 踢出（官方现成） | 产出 request/kvcache/batch/service 统计 CSV，未来需要时复用 |

## 表4：顶层能力 — 官方专页，本期记 Todo

| 能力 | 官方入口 | 出处 | 状态 |
|---|---|---|---|
| 参数扫描 | `vllm bench sweep`（多配置扫描） | vLLM sweeps 文档 | Todo |
| 性能仪表盘 | 官方 dashboard（基准结果汇总展示） | vLLM dashboard 文档 | Todo |

## 已知冲突 / 待核实

> **本区是全部「官方 flag 语法/冲突待核实」的唯一权威**。其它文档（guide/how-to-run、guide/commands、guide/output、roadmap、ADR-0004）不再另立清单，只在此引用。装好环境后实测，结果直接回填这里（含本行上方表1备注、下方维护注意）。

- **latency 语法不一致**：ascend 官方 latency CLI 用 `--num-iters-warmup 5 --num-iters 15`（见 benchmarks repo），我们 `bench.sh latency` 用 `--batch-size`。装好环境后以 `vllm bench latency --help` 实测，确认走哪套语法。
- **结果落盘两种写法**：`serve` 走 `--save-result --result-dir`；v0.18.0 的 `throughput` 移除 `--save-result/--result-dir`、改用 `--output-json <file>`；`latency` 无落盘 flag（stdout 表格）。bench.sh 已按此收敛（见 [commands.md §2](./guide/commands.md)）。
- **离线剖析弃用**：vLLM 主线已弃用 `VLLM_TORCH_PROFILER_DIR` 环境变量，统一用 `--profiler-config`（PR #5928）。

## 表5：本期踢出（CONTEXT.md 边界）— 官方现成，未来需要时加 flag 即用

| 能力 | 官方入口 | 出处 |
|---|---|---|
| 多模态（图像/视频） | `vllm bench serve --backend openai-chat` + ShareGPT4V / random-mm | ascend doc §3.2.3 |
| Embedding | `vllm bench serve --backend openai-embeddings --endpoint /v1/embeddings` | ascend doc §3.2.4 |
| Reranker / 多模态处理器 | `vllm bench serve`（详见 CLI doc） | CLI doc |
| Spec Decoding（InstructCoder / Spec Bench / SPEED-Bench） | `vllm bench serve --dataset-name hf` | CLI doc |
| 结构化输出（JSON schema / grammar / regex / XGrammar） | `vllm bench serve` | CLI doc |
| 长文档 QA / 前缀缓存 / Trace 重放 / 哈希 / 请求优先级 | `vllm bench serve` | CLI doc |

## 复用原则

1. **功能全透传，薄脚本只做编排**：`latency`、`--save-result`、`--plot-timeline`、`--load-format dummy` 等都是官方 flag，加一行 `cmd+=` + config 键即可，不写解析逻辑。
2. **官方一键脚本优先**：`run-performance-benchmarks.sh` 已覆盖官方方法论（ShareGPT 200 prompts 固定种子、QPS 1/4/16/inf），与我们 `bench.sh serve` 同层能力；多 QPS 全套时直接跑它。
3. **踢出项不动手**：等边界外推时直接透传对应 backend/dataset flag，官方文档即说明书。
4. **可复现优先**：官方方法论用固定随机种子（`--seed`），与我们「可复现指标」承诺一致，增补时优先接入 seed。
5. **profile 与 benchmark 分层**：算子级用 Ascend PT Profiler（已落地），服务框架级用 MS Service Profiler（踢出）；勿混用。

## 维护注意

- 增补任一功能时：同时改 `config/config.yaml`、`scripts/bench.sh` / `scripts/profile.sh` 内对应 `cmd+=` 行、本文对应表格、[guide/commands.md](./guide/commands.md) 与 [guide/config-reference.md](./guide/config-reference.md) 的相关小节。
- 本文只记录"官方有、我们可增补"的能力；是否增补由 P0 验收与本期范围决定（见 [CONTEXT.md](../CONTEXT.md)）。
- 官方 flag 具体语法以装好环境后的 `vllm bench serve --help` / `vllm bench latency --help` / `vllm serve --help` 实测为准，回填本文「已知冲突 / 待核实」（本区即单一权威；guide/how-to-run 等只在此引用）。
