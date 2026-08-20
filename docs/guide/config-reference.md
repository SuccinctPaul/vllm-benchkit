# 参数配置（Config Reference）

本文说明 `config/config.yaml` 的**全部参数**，以及**环境变量如何覆盖 YAML 默认值**。默认配置是 [config/config.yaml](../../config/config.yaml)。

## 1. 全参数参考表

| YAML 键 | 对应环境变量 | 默认值 | 说明 |
|---------|-------------|--------|------|
| `model` | `MODEL` | `Qwen/Qwen3-0.6B` | smoke 模型，bench 与 profile 统一 |
| `devices` | `ASCEND_RT_VISIBLE_DEVICES` | `0` | 使用哪几张 NPU 卡 |
| `bench.backend` | `BENCH_BACKEND` | `vllm` | `vllm bench serve --backend` |
| `bench.endpoint` | `BENCH_ENDPOINT` | `/v1/completions` | 在线基准的 OpenAI 端点 |
| `bench.num_prompts` | `BENCH_NUM_PROMPTS` | `10` | 在线基准请求数 |
| `bench.request_rate` | `BENCH_REQUEST_RATE` | `inf` | 请求速率；`inf` 为打满 |
| `bench.dataset` | `BENCH_DATASET` | `random` | 数据集；`sharegpt` 需下载 |
| `bench.dataset_path` | `BENCH_DATASET_PATH` | 空 | sharegpt 本地 jsonl 路径 |
| `bench.load_format` | `BENCH_LOAD_FORMAT` | 空 | 权重加载方式；smoke 填 `dummy`（随机权重免下载） |
| `bench.input_len` | `BENCH_INPUT_LEN` | `128` | 离线输入长度 |
| `bench.output_len` | `BENCH_OUTPUT_LEN` | `128` | 离线输出长度 |
| `bench.ignore_eos` | `BENCH_IGNORE_EOS` | `true` | 忽略 EOS、保证输出长度 |
| `bench.batch_size` | `BENCH_BATCH_SIZE` | `8` | latency 子命令的并发 batch 数 |
| `profile.port` | `PORT` | `8080` | profile server 端口 |
| `profile.prof_dir` | `PROF_DIR` | `profile_out` | profiling trace 目录（相对仓库根） |
| `profile.with_stack` | `PROF_WITH_STACK` | `false` | 是否带调用栈（JSON 布尔，须小写） |

> 注意：bench 已不使用端口（`--backend vllm` 进程内驱动，删除了 `bench.port`）；`profile.port` 仍映射 `PORT`，全局设 `PORT` 会影响 profile server。

## 2. 覆盖机制（环境变量 > YAML）

**优先级链：环境变量（已设置）> config.yaml 值。** 脚本不再内置默认值，config.yaml 是唯一默认来源；缺键时变量为空、vllm 显式报错，不静默兜底。

临时覆盖示例（不改文件）：

```bash
MODEL=facebook/opt-125m ./scripts/bench.sh throughput     # 换模型
ASCEND_RT_VISIBLE_DEVICES=0,1 ./scripts/bench.sh serve    # 换 NPU 卡
BENCH_DATASET=sharegpt BENCH_DATASET_PATH=... ./scripts/bench.sh serve  # 换数据集
```

长期要改（如固定环境）就直接编辑 `config/config.yaml`，脚本零改动。

### 脚本运行开关（非参数）

| 环境变量 | 作用 | 默认值 |
|---------|------|--------|
| `VLLM_NOTES_VENV` | venv 路径 | `$ROOT/.venv` |
| `VLLM_NOTES_CONFIG` | 配置文件路径 | `$ROOT/config/config.yaml` |
| `VLLM_NOTES_RUNS` | 日志/结果目录 | `$ROOT/runs` |

## 3. 参数怎么生效（装配链路）

`src/getconf.py` 把 YAML 拍平为 `YAML_*` 环境变量，脚本再用 `: "${KEY:=${YAML_...:-}}"` 合并出最终值，然后组装官方 vllm 命令。详细数据流见 [design.md](./design.md#1-数据流与优先级)。
