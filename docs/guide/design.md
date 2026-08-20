# 设计说明（Design）

本文解释**为什么这么设计**：数据流、优先级、取舍，以及「出问题该查哪个仓库」。怎么运行见 [how-to-run.md](./how-to-run.md)，参数见 [config-reference.md](./config-reference.md)。

## 1. 数据流与优先级

```
config/config.yaml ──src/getconf.py──▶ YAML_* 环境变量（eval 载入）
                                      │
        环境变量（若已设置）◀─优先─┼──▶ : "${KEY:=${YAML_...:-}}"
                                      │
                                      ▼
                 bench.sh / profile.sh 组装官方 vllm 命令
                                      │ tee
                                      ▼
                              runs/<时间戳>-<mode>.log
```

**优先级链：环境变量（已设置）> config.yaml 值。** 脚本不再内置默认值，config.yaml 是唯一默认来源；缺键时变量为空、vllm 显式报错，不静默兜底。

## 2. 为什么这么设计

- **薄脚本**：脚本只 wrap 官方 `vllm bench` / `api_server`（[ADR-0001](../adr/0001-use-vllm-bench-as-benchmark-driver.md)），不造引擎，参数层同样保持薄——不引入 CLI `--set`、多 profile、分层合并等（[ADR-0005](../adr/0005-config-via-yaml-env-precedence.md) 的取舍）。
- **配置外置**：不同环境/版本只改 `config.yaml`，脚本零改动；临时覆盖用环境变量，不用编辑文件。
- **解析用 pyyaml**：bash 不适合解析嵌套 YAML；pyyaml 是 torch_npu 传递依赖、已显式声明，零额外安装。
- **目录分层**：`config/`（参数）、`scripts/`（可执行）、`docs/`（知识）、根目录（pyproject/README/CONTEXT）。
- **产物留痕**：tee 到 `runs/` 满足「可复现」承诺；`profile_out/` 保留原始 trace；两者均在 `.gitignore`。

## 3. 三层归属（出问题该查哪个仓库）

| 层 | 是什么 | 出问题查哪 |
|----|--------|-----------|
| vLLM core | 引擎/基准驱动：`vllm bench` 命令、服务入口、调度器 | vLLM 官方文档 / `vllm bench --help` |
| vllm-ascend | NPU 后端插件：替换 platform、Worker 跑在 torch_npu | vllm-ascend 文档 / `vllm serve --help` |
| Ascend PyTorch Profiler | profiling 引擎：华为 `torch_npu.profiler` | CANN 文档 / `profile.sh analyse` 产物 |

完整术语表见 [CONTEXT.md](../../CONTEXT.md)。
