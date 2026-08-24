# 使用与设计指南（Guide）

本文是 `docs/guide/` 的索引，回答 vllm-benchkit 的 **how to run / 命令行功能 / 参数配置 / 输出与结果说明 / 设计原理**。开发与使用以本目录为准，勿凭印象猜参数。开发迭代时，涉及脚本/配置/运行方式的变化必须同步更新对应文档与根目录 `README.md`。

## 文档导航

| 文档 | 回答什么 | 什么时候读 |
|------|---------|-----------|
| [how-to-run.md](./how-to-run.md) | 怎么跑起来：前置条件、安装、快速开始、完整命令序列、P0 验收五步 | 第一次上手，或要验收"黑盒跑通" |
| [commands.md](./commands.md) | 命令行功能：`bench.sh` 三个子命令、`profile.sh` 四个子命令、serve/throughput/latency 差异 | 想知道每个命令干什么、选哪个 |
| [config-reference.md](./config-reference.md) | 参数配置：`config.yaml` 全参数表、环境变量如何覆盖 | 想改模型/卡/数据集/长度等参数 |
| [output.md](./output.md) | 输出与结果：产物落哪、指标含义（benchmark vs profile） | 跑完想读懂结果、确认黑盒跑通 |
| [design.md](./design.md) | 设计原理：数据流、环境变量 > YAML 优先级、薄脚本取舍、三层归属 | 想理解"为什么这么设计"、出问题查哪个仓库 |

## 一句话全景

```
config/config.yaml（默认参数）  +  环境变量（临时覆盖）
        │
        ▼
scripts/bench.sh  /  scripts/profile.sh   （只 wrap 官方命令）
        │ tee / --save-result
        ▼
runs/<时间戳>-<mode>.log  +  runs/*.json  +  profile_out/（trace）
```

## 快速开始（三步）

```bash
# 0. 一次性装环境（见 how-to-run.md §2）
uv sync

# 1. 基准：先跑离线吞吐（random 数据集，零下载）
./scripts/bench.sh throughput

# 2. profiling：三终端流程（见 how-to-run.md §3.2）
./scripts/profile.sh serve   # 终端 1
./scripts/profile.sh start   # 终端 2：开始采集
./scripts/profile.sh stop    # 停止采集
./scripts/profile.sh analyse # 解析算子数据
```

完整说明见 [how-to-run.md](./how-to-run.md)。

## 相关文档

- [../README.md](../README.md) —— 全仓文档唯一总入口（地图 + 新手路线 + 名词）
- [../understand.md](../understand.md) —— 这是什么：两档玩法（黑盒冒烟：本 guide；正式验收：../acceptance）、三层归属、硬约束
- [../run.md](../run.md) —— 怎么跑：先冒烟后验收，一条路串完
- [CONTEXT.md](../../CONTEXT.md) —— 术语表（目标/边界/三层归属/踢出项）
- [../acceptance/README.md](../acceptance/README.md) —— 正式验收子系统（A1–A4：功能/设计/how-to-run/覆盖度/配置参数）
- [../maintenance.md](../maintenance.md) —— 为什么能维护＆运行、改动自查
- [official-capabilities.md](../official-capabilities.md) —— 官方能力清单与可增补项
- [config/config.yaml](../../config/config.yaml) —— 参数默认配置
- [config/vllm-xcheck/](../../config/vllm-xcheck/) —— 正式验收配置层（common + precision + cells + schema）
- [../adr/](../adr/) —— 架构决策记录（0001–0012，入口见 adr/README.md）
