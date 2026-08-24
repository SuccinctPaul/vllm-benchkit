# 配置文件参考（config/vllm-xcheck/）

本文档说明 `config/vllm-xcheck/` 下各类配置文件的参数含义，适合新手对照阅读。

> **想理解"每条任务为什么这么配"、以及每项配置对应的需求意图**，见 [task-expertise.md](./task-expertise.md)（任务专家速成）。本文只讲「字段是什么」，那片讲「为什么」。

## 先说清楚：为什么是"三层"

配置不写在一个大文件里，而是拆成三层，**下面的盖上面的**（后翻覆盖前翻）：

```
common.yaml(共同规矩)  ┐
cells/<cell>.yaml(某门课专属) ├─► 合成出一份能跑的 profile 考试单
precision/<p>.yaml(精度加减分) ┘
```

- **common**：所有考试都一样的共同规矩（大家都用同一套采样、同一份 SLO、同一个模型家族）。
- **cells**：每门课（A1/A2-dialogue/…）的专属要求，比如 A1 要 eager、A3 并发只能是 1。
- **precision**：同样的课跑 FP16 还是 W8A8（8-bit）时，模型字段不同的那点差别。

`src/acceptance.py` 把三层合并、校验、渲染成 final 的 argv/环境变量，**多一个没被登记的字段就报错**（fail-closed）。下面每个文件逐项解释字段。

## 1. 目录与分层

```
config/vllm-xcheck/
├── common.yaml            # A1–A4 共同基线（表附-2 服务端 + 附-4 env + 附-5 采样）
├── schema.yaml            # 字段契约（必填/可选白名单），fail-closed 校验依据
├── precision/
│   ├── fp16.yaml          # FP16 精度 overlay
│   └── w8a8.yaml          # W8A8（INT8 权激活）精度 overlay
├── cells/                 # 任务覆盖（8 个 cell）
│   ├── a1.yaml            # A1 算力口径
│   ├── a2-dialogue.yaml   # A2 通用问答/多轮
│   ├── a2-tool.yaml       # A2 工具调用
│   ├── a2-reason.yaml     # A2 推理
│   ├── a2-struct.yaml     # A2 结构化输出
│   ├── a2-long.yaml       # A2 长文本
│   ├── a3-32k.yaml        # A3 窗口稳定
│   └── a4-mt.yaml         # A4 多租户成本
└── schema.yaml
```

三层的各 section 都遵循同一组顶层键：`meta / model / server / env / sampling / workload`。
`src/acceptance.py` 按此展开成 **15 个 profile 实例**（5 个 A2 cell ×2 精度 + A3×2 + A4×2 + A1×1）。

## 2. 校验规则（fail-closed）

- 每个 profile 的字段必须出现在 **required** 集（或任一 overlay 提供）；
- 只允许出现在 **allowed** 集；出现未知字段 = 直接拒绝（fail-closed）。
- **命名约定（隐私边界）**：配置文件只写泛化字段，**不写入**机器名/仓库 URL/revision 具体值；具体 revision / 容器 digest 由实例化方在 `config/`（已 `.gitignore`）里填。

## 3. 通用 section 说明

### 3.1 `meta` —— 任务身份

| 字段 | 含义 | 示例 |
|------|------|------|
| `cell` | 任务标识 | `a2-dialogue` |
| `profile` | 正式 profile_id（`{precision}` 占位展开） | `A2-DIALOGUE-{precision}-PC` |
| `precisions` | 本 cell 合法精度列表 | `[FP16, W8A8]` |
| `endpoint` | HTTP 端点 | `/v1/completions` 或 `/v1/chat/completions` |
| `api` | 协议族 | `completions` \| `chat` |

### 3.2 `model` —— 模型身份

| 字段 | 含义 | 示例 |
|------|------|------|
| `family` | model_family | `Qwen2.5-14B-Instruct` |
| `served_name` | API 逻辑名（可含 `{precision}`） | `qwen2.5-14b-instruct-{precision}` |
| `load_format` | 加载格式 | `auto`（effective= safetensors） |
| `kv_cache_dtype` | requested KV 缓存精度 | `auto` |
| `kv_cache_effective` | effective KV 精度（仅 receipt 校验） | `float16` |
| `block_size` | KV block 大小 | `128` |
| `revision` | 具体版本（由实例化方在 .gitignore 的 config 填） | — |

### 3.3 `server` —— 服务端参数

**服务协议 / 拓扑**：`protocol`(openai)、`host`、`uvicorn_workers`、`tensor_parallel_size` / `pipeline_parallel_size` / `data_parallel_size` / `expert_parallel_size`（V4.1 均 =1，单张 910B2）、`serving_mode`(unified)、`pd_disaggregation`(false)、`replicas`、`distributed_executor_backend`(mp)、`dcp_comm_backend`(ag_rs)、`disable_custom_all_reduce`(true)、`cpu_binding_policy`(topo_affinity)。

**SM 容量**（对延迟/吞吐影响最大，见 4.2）：
- `gpu_memory_utilization`：GPU 内存利用率（KV 缓存容量上限）。
- `max_num_seqs`：并发请求天花板（batching 上限，**决定峰值延迟**）。
- `max_num_batched_tokens`：单 batch 最大 token 数。
- `enable_chunked_prefill` / `enable_prefix_caching`：分块 prefill / 前缀缓存。
- `capture_sizes`：图捕获的 batch 形状集合（须覆盖峰值 batch）。
- `compile_mode`：`vllm_compile`（A2–A4 通用）/ `none`（A1）。
- `enforce_eager`：是否强制 eager（A1=true，图捕获=0）。
- `cudagraph_mode` / `cudagraph_num_of_warmups`：图模式 / 预热次数。
- `max_model_len`：最大模型长度（A1/A3 显式给出）。

**采样 / 结构化 / 模板**：
- `structured_outputs_backend`：结构化输出后端，**必须** `xgrammar`（禁止 auto）。
- `tool_call_parser` / `auto_tool_choice`：工具调用解析 / 自动工具选择（A4 tool 租户）。
- `chat_template` / `trust_request_chat_template` / `content_format`：模板与内容格式。
- `engine_seed`：引擎随机种子（**必须显式 =0**，防随机抖动，正式测量待渲染进 serve argv）。

**超时**：`startup_health_timeout_s`、`health_interval_s`、`graceful_shutdown_timeout_s`。

### 3.4 `env` —— 环境变量

| 变量 | 值 | 含义 |
|------|----|------|
| `VLLM_TARGET_DEVICE` | `npu` | NPU 后端 |
| `VLLM_USE_V1` | `1` | 用 vLLM V1 引擎 |
| `VLLM_WORKER_MULTIPROC_METHOD` | `spawn` | 多进程方式 |
| `PYTHONHASHSEED` | `0` | 哈希种子固定（确定性） |
| `TOKENIZERS_PARALLELISM` | `false` | 禁用并行分词 |
| `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE` / `HF_DATASETS_OFFLINE` | `1` | 离线模式 |
| `HCCL_CONNECT_TIMEOUT` | `120` | HCCL 连接超时 |
| `ASCEND_VISIBLE_DEVICES` | `""` | 独占单卡（实例化方填 ID） |
| `OMP_NUM_THREADS` | `1` | CPU 线程数 |
| `PYTORCH_NPU_ALLOC_CONF` | `expandable_segments:True` | NPU 显存分配 |
| `TASK_QUEUE_ENABLE` | `1` | 任务队列 |

### 3.5 `sampling` —— 采样参数

统一采样（所有 cell 一致）：`temperature: 0.0`、`top_p: 1.0`、`top_k: -1`、`min_p: 0.0`、`n: 1`、`best_of: 1`、`beam_search: false`、`ignore_eos`、`max_tokens`、`stop`、`streaming: true`、`stream_options`、`client_seed: 0`、`truncate_prompt_tokens`、`add_generation_prompt`、`continue_final_message`、`echo`、`detokenize`、`skip_special_tokens` 等。

- `ignore_eos`：A1/A3 设 `true`（强制生成长度），A2/A4 设 `false`。
- `repeats`：重复完整生命周期次数（=3，取中位数口径）。
- `client_seed`：客户端确定性种子（=0），保证可复现。

### 3.6 `workload` —— 负载定义（四类 mode）

| 字段 | 含义 |
|------|------|
| `mode` | `closed-loop`（A1/A3）\| `poisson`（A2）\| `mixed`（A4） |
| `dataset` | 数据集：`synthetic` / `ShareGPT` / `BFCL V4` / `GSM8K` / `JSONSchemaBench` / `long` / `mixed` |
| `dataset_fixed` | 固定样本结构（如 `{sessions:16, rounds:4}`） |
| `shapes` | A1 固定形状列表（见 4.1） |
| `rates` / `caps` | A2 容量扫描 rate 升序 + 每 rate 请求上限 |
| `max_concurrency` | A2 并发上限 |
| `tenants` | A4 租户与份额（见 4.2） |
| `b0_capacity_points` | A4 B0 混合扫描点 |
| `global_load_factor` | A4 正式负载系数（0.70） |
| `per_tenant_concurrency` / `global_concurrency` | A4 租户/全局并发 |
| `total_context_tokens` / `window_s` / `window_count` | A3 窗口参数 |
| `timeouts` | 各阶段超时（warmup/measure/per_request/lifecycle/capacity 等） |
| `slo` | A2 绝对 SLO 阈值（见 4.3） |
| `primary_metric` | 该任务主判定指标 |

## 4. 按 cell 精讲

### 4.1 A1 —— `cells/a1.yaml`（算力口径）

- 固定 **FP16**，`closed-loop`，主形状 `4096×8→1`，辅助 `1024×32→1`、`8192×4→1`。
- 服务端：`compile_mode: none`、`enforce_eager: true`、`cudagraph_mode: none`（实际图捕获 =0）。
- `sampling.ignore_eos: true`（**严禁**渲染成 serve CLI 参数）、`max_tokens: 1`。
- `workload.shapes`：三维组 `{input_len, batch, output_len}`。
- 主指标：`main-shape MFU`，判定 MFU≥90%、msprof 误差≤3%。

### 4.2 A4 —— `cells/a4-mt.yaml`（多租户成本）

- `mode: mixed`：B0 混合容量扫描 + 70% 正式混合负载，四租户共享单服务、各 25% 输出 token 份额。
- `tenants`：每租户 `share`/`output_cap`/`cost_len`/`isolation_token_rate`。
  - `output_cap`：每请求 max_tokens 上限；`cost_len`：期望输出长度/请求（调度成本用）；`isolation_token_rate`：隔离基线 per-tenant token_rate（缺省回退 b0_max）。
- `max_num_seqs: 36`（= 4 租户 × `per_tenant_concurrency: 9`，与 `global_concurrency: 36` 匹配；capture_sizes 显式扩到 36 覆盖峰值 batch）。
- `b0_capacity_points`：B0-FP16 扫描点 `[32,64,96,128,192,256,384]`。
- `global_load_factor: 0.70`：正式负载系数。
- 延迟判定改用**相对隔离 ≤1.25×**（绝对 SLO 仅作记录）。
- 主指标：`cost per 1e6 successful output tokens (full-lifecycle)`。

### 4.3 `slo` —— 绝对 SLO 阈值（附-8, ms，A2/A4）

```yaml
slo:
  ttft: {mean_ms: 1000, p95_ms: 2000, p99_ms: 4000}
  tpot: {mean_ms: 40,  p95_ms: 60,   p99_ms: 80}
```

- **TTFT / TPOT** 的量法与单位见 [guide/output.md 指标定义](../guide/output.md)（此处只列阈值，不重复定义）。
  - TTFT（首 token 延迟）阈值：mean≤1000 / p95≤2000 / p99≤4000 ms。
  - TPOT（每输出 token 延迟）阈值：mean≤40 / p95≤60 / p99≤80 ms。
- A2 逐租户按同组阈值判定；A4 改为相对隔离判定 ≤1.25×，绝对阈值仅记录。

### 4.4 A3 —— `cells/a3-32k.yaml`（窗口稳定）

- `closed-loop`、`concurrency: 1`、`max_num_seqs: 1`、`capture_sizes: [1]`。
- 总上下文 `total_context_tokens: 32768` = 输入 30720 + 输出 2048。
- `window_count: 6`、`window_s: 300`（6×5min 窗口）。
- 主指标：`window throughput CV + TTFT/TPOT drift + 0 failure`（CV≤5%、中位漂移≤10%、p99≤20%）。

### 4.5 A2（5 个 cell）—— 容量扫描

- `mode: poisson`，`dataset` 各自不同（ShareGPT / BFCL V4 / GSM8K / JSONSchemaBench / long）。
- `rates`/`caps` 升序扫描，`slo` 判定完成率≥99%、error_rate≤0.1%、TTFT/TPOT p99 阈值。
- 主指标：`max compliant output token/s + common-load TTFT/TPOT`。

> 更早的运行记录与任务明细见 [acceptance-tasks.md](./acceptance-tasks.md)。