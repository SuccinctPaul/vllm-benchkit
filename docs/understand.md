# 这是什么东西（Understand）

> 本文回答「**这个仓库到底在干嘛、里面有哪些档位、出问题找谁**」。想动手跑，见 [run.md](./run.md)；逐模块设计见 [acceptance/design.md](./acceptance/design.md)。

## 1. 一张图认识它

这个仓库最初是「成为 vLLM Ascend 插件专家」的**笔记 + 工具仓**：先黑盒跑通官方基准，再逐步拆解。后来在此基础上长出了一个子项目 —— 把《V4.1 标准交付测试方案》落成一套**正式验收执行系统**（也叫 vllm-xcheck）。

```
                        ┌─────────────────────────────┐
  一条树干（vLLM + NPU + Profiler 三层地基）           │
  两种玩法：                                           │
   ① 黑盒冒烟（快、探索）◄── guide/       跑bench/profile 冒烟、对比版本
   ② 正式验收（严、留证）◄── acceptance/   按V4.1跑A1–A4，出判定+证据
                        └─────────────────────────────┘
```

**别误以为有两套系统**：它们是同一根树干的两种用力程度。② 在更高层，内部还会复用到 ① 的冒烟能力。

## 2. 三层归属（谁出问题查谁）

| 层 | 是什么 | 出问题查哪 |
|----|--------|-----------|
| vLLM core | 引擎 / 基准驱动、服务入口、调度器 | vLLM 文档 / `vllm bench --help` |
| vllm-ascend | NPU 后端插件：替换 platform、Worker 跑 torch_npu | vllm-ascend 文档 |
| Ascend PyTorch Profiler | profiling 引擎：华为 `torch_npu.profiler` | CANN 文档 |

**用大白话讲这三层**（都是同一台 910B2 机器上配合，不是三套系统）：

- **vLLM core = "大脑"**：负责把推理请求排成批次、安排好 KV 缓存、向外提供 HTTP 接口，也是 `vllm bench` 这些基准命令的宿主。
- **vllm-ascend = "翻译官/适配层"**：vLLM 默认为了 GPU 书写，无法直接在华为 NPU 上跑；这层把它的平台接口换成 Ascend 能用的（跑在 `torch_npu` 上），让它能在 NPU 上正常推理。它不是另一套独立框架。
- **Ascend PyTorch Profiler = "体检仪"**：真机做算子级排障时，逐算子采集耗时，告诉你哪一步慢、卡在哪。

## 3. 三类工作（做了测、测了断、断了查）

| 档位 | 做什么 | 主要入口 |
|------|--------|---------|
| **benchmark（宏观指标）** | 跑官方 `vllm bench`，看吞吐/延迟等宏观数字 | [guide/how-to-run.md](./guide/how-to-run.md)、[guide/commands.md](./guide/commands.md) |
| **profile（定位瓶颈）** | Ascend PyTorch Profiler 采集算子，找堵点 | [guide/how-to-run.md](./guide/how-to-run.md) |
| **验收（正式判定）** | 按 V4.1 跑 A1–A4，判通过与证据归档 | [acceptance/README.md](./acceptance/README.md) |

新手别混淆：**冒烟是"能不能跑"，验收是"这套 V4.1 我要的东西合不合规"**。前者快、可反复，后者严格、留证据。

## 4. 四条硬约束（为什么文档里从不写具体机器名）

这些约束决定了整套东西"怎么设计、怎么维护"，贯穿所有文档，先说清楚：

- **隐私边界**：机器名 / SSH / 仓库 URL / revision 只准出现在 `config/topology.yaml`（已 `.gitignore`）；文档一律引用拓扑清单键名 `server/dir/repos`，不写具体值。
- **薄脚本 / 少造轮子**：只 wrap 官方 `vllm bench`，参数默认值唯一来源是 YAML（ADR-0001 / 0005）；用 uv 隔离环境（ADR-0002）。
- **fail-closed**：不满足冻结表的项一律明确拒绝，不搞"这次算了"。
- **可复现**：seed=0、确定性数据、canonical JSON + SHA、生效参数全归档，让两次测量可对比。

> 为什么会定这些约束、每个约束背后的取舍，见 [acceptance/design.md](./acceptance/design.md) 与 [adr/](./adr/)。
> 术语全表见 [CONTEXT.md](../CONTEXT.md)。