# How to run（怎么跑）

> 本文回答「**怎么把 vllm-xcheck 验收跑起来**」。功能见 [features.md](./features.md)，设计见 [design.md](./design.md)。
> 全程遵循隐私边界：不写具体机器名/仓库路径，统一读 `config/topology.yaml` 的 `server/dir/repos` 键。

## 别急，整体就这几步

```
装好环境(部署) → 备好数据子集 → 起服务 → 跑客户端 → 收真机指标 → 停服务/看结果
  第1节            第2节          第3.2      第3.3      第3.4       第3.5
```

第 3 节以 A4（多租户）为例把 6 步走一遍；你是新手的话，**先只照第 3 节做一遍**，跑通一次看产物长啥样，再回来抠细节。第 5 节的"离线自测"不用真机，适合先在普通机器上验证链路没写错。

## 0. 前置条件

| 项 | 值 | 来源 |
|----|----|------|
| 机器 | `config/topology.yaml` 的 `server` | [topology.yaml](../../config/topology.yaml) |
| 硬件 | Atlas 910B2（单卡或 8 卡） | [CONTEXT.md](../../CONTEXT.md) |
| CANN | 9.0.0 | [CONTEXT.md](../../CONTEXT.md) |
| 代码源 | `dir`/`repos` 钉的 `vllm`/`vllm-ascend` @ `releases/v0.18.0` | topology / ADR-0003 |
| 环境 | uv 建的 `.venv`（torch 2.9.0 / torch-npu 2.9.0.post2） | ADR-0002 |

## 1. 部署（一次性）

```bash
# 在 vllm-benchkit 目录下：把双仓库 clone 到清单钉的 commit 并 editable 安装
./scripts/deploy.sh install

# 只读核对已部署的仓库状态
./scripts/deploy.sh check
```

## 2. 准备数据子集（固定子集）

```bash
# 物化 datasets/*.jsonl + *.manifest.json + .registry.json（供 receipt 门禁）
./scripts/prepare.sh subsets
# 模型 / 数据集 / 工作负载同族命令：model | dataset | workload | all
```

## 3. 跑一次正式验收（A4 为例）

```bash
# 3.1 看有哪些 profile 可跑
python src/acceptance.py --list

# 3.2 起独立服务（先跑帧：启动、健康等待、写 receipt）
bash scripts/acceptance.sh server a4-mt FP16 --port 8010 --model-ref Qwen/Qwen2.5-14B-Instruct

# 3.3 跑客户端基准（容量扫描 → b0_max → 0.70× 正式负载 → 隔离基线）
bash scripts/acceptance.sh run a4-mt FP16 --port 8010 --base-url http://127.0.0.1:8010

# 3.4 采集真机指标（M1/M2/M3/M4 → server_metrics.json）
bash scripts/acceptance.sh metrics a4-mt FP16 --serve-log <serve.log> --pid <pid> --out <out>

# 3.5 停服务
bash scripts/acceptance.sh stop a4-mt FP16
```

> `run` 子命令自带 `EXIT` trap：任何一步失败也会停掉本轮服务，避免残留占 NPU（SIGTERM 15s 未退出则升级 SIGKILL）。

## 4. 常用子命令总览

- `acceptance.sh list`：枚举 profile。
- `acceptance.sh profile <cell> <PREC>`：打印单个 profile 的 argv/env/effective（dry-run）。
- `acceptance.sh server <cell> <PREC> [--port P] [--model-ref M]`：起独立 serve，健康等待。
- `acceptance.sh client <cell> <PREC> [--base-url U] [--num-prompts N] [--request-rate R]`：跑客户端。
- `acceptance.sh run <cell> <PREC> [...]`：server + client + 收尾一次性。
- `acceptance.sh stop <cell> <PREC>`：停服务。
- `acceptance.sh metrics <cell> <PREC> [...]`：聚合 M1–M4。

## 5. 离线自测（不起服务，验证链路）

无需真机即可验证组 C 各模块逻辑正确：

```bash
python src/client/run.py --cell a4-mt --precision FP16 --selftest
python src/client/run.py --cell a3-32k --precision FP16 --selftest
python src/client/run.py --cell a1 --precision FP16 --selftest
python src/client/generate.py   # C1+C2 合同生成/自测
```

## 6. 环境变量覆盖开关（smoke / 资源）

| 环境变量 | 作用 | 默认 |
|---------|------|------|
| `VLLM_BENCHKIT_SHORT=1` | 把 workload 时长/规模缩到分钟级（smoke） | 关 |
| `VLLM_BENCHKIT_GPU_MEM_UTIL` | 降显存利用率（共享机防 OOM） | 配置值 |
| `VLLM_BENCHKIT_SKIP_GRAPH_ARGS=1` | 跳过 `--compile-mode`/`--cudagraph-mode`（ascend build 不支持） | 关 |
| `ALLOW_MISSING_DATASETS=1` | 豁免缺数据集工件（仅 smoke，其余门禁仍强制） | 0 |
| `VLLM_BENCHKIT_VENV` / `VLLM_BENCHKIT_CONFIG` / `VLLM_BENCHKIT_RUNS` | 覆盖 venv / 配置 / 归档目录 | `$ROOT/.venv` 等 |

> A1 离线基准（`completions` API）走 `bench.sh throughput`，须显式设 `BENCH_GPU_UTIL`（不继承 `VLLM_BENCHKIT_GPU_MEM_UTIL`）。

## 7. 产物查看

- 服务日志：`runs/accepted/<PROFILE>-verifyN/serve.log`
- 生效值快照/门禁：`runs/accepted/<PROFILE>-verifyN/receipt.json`
- 逐请求回执：`receipts.jsonl`；SLO：`slo.json`；证据包：`evidence/`、`quality/`、`gate/` 等
- 服务指标：`server_metrics.json`

> 各指标的读法见 [../guide/output.md](../guide/output.md) 的 benchmark 篇与 [features.md §2 四大验收单元](./features.md#L15)。

## 8. 常见坑（from 真机经验）

- **模型加载**：用 `--model-ref` 带组织前缀（如 `Qwen/Qwen2.5-14B-Instruct`）才能命中离线缓存。
- **serve 不接受图参数**：ascend build 的 serve 无 `--compile-mode`/`--cudagraph-mode` → 设 `VLLM_BENCHKIT_SKIP_GRAPH_ARGS=1`。
- **isolation 基线并发口径**：隔离基线要先 full_load——单租户独占至 `max_num_seqs` 并发，否则基线 p99 被低估导致假 FAIL（verify18 教训）。
- **进程残留占 NPU**：`stop_server` 15s SIGTERM 未退出则 SIGKILL，防止共用机残留。