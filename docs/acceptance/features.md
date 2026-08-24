# 功能（Features）

> 本文回答「**这套系统到底能干什么**」。设计见 [design.md](./design.md)，怎么跑见 [how-to-run.md](./how-to-run.md)。

> **⚠️ 先看整体状态，别误会「功能」=「已交付」**
>
> 截至 verify19：**A4-MT-FP16-PC 全指标 PASS、C/Q/M/I/K/Z 各组离线自测全过**，但**这套系统还不能直接拿去对外做正式交付**——正式测量前还差 3 件事：① `engine_seed=0` 渲染进 serve argv；② `structured_outputs_backend=xgrammar` 传入引擎；③ 证据链/签名组件未实现。且**D 组正式数据子集未接入**，当前只用 14B + SHORT smoke 数据在验证链路。所以下面 A1–A4 的"考纲"讲的是**这套系统设计上要考什么**，不代表"四门都已正式交付"。完整状态见 [features.md §7](#L69) 与 [v41-coverage.md](./v41-coverage.md)。

## 1. 一句话全景

vllm-xcheck 是一台**自动化的验收"考场"**：你给一份配置（`config/vllm-xcheck/`），它起一个 vLLM 服务，按《V4.1 标准交付测试方案》自动跑四门考核（A1–A4），每门都给出"过/不过"的明确结论，并把原始数据和判定一起归档备用。**不达标绝不给你"默认通过"**（fail-closed）。

它的**价值**，是把"模型/服务达没达标"从主观说法变成**可复现、可对账、可追溯的事实**——同一个口径、seed=0 可复现、判定带原始证据留档（详见 [acceptance/README.md 真实价值](./README.md)、[maintenance.md](../maintenance.md)）。

接下来这张表是四门课的"考纲"——先不用逐格看懂，只需要知道**每门在量什么、卡什么样的线**。

## 2. 四大验收单元（A1–A4）——四门课

| 单元 | 负载口径 | 关键指标 / 形状 | 验收门槛 |
|------|---------|----------------|---------|
| **A1 算力口径** | closed-loop 固定形状：主 4096×8→1；辅 1024×32 / 8192×4 | effective FLOPs（MFU）、峰值分母、msprof 对账 | MFU≥90%、msprof 误差≤3%、A1 eager 0 图捕获 |
| **A2 SLO 组**（5 cell） | poisson seed=0 容量扫描，每 rate 600s、repeats=3 | dialogue / tool / reason / struct / long | TTFT p99≤4000ms、TPOT p99≤80ms、完成率≥99%、error_rate≤0.1% |
| **A3 窗口稳定** | closed-loop 深长文本，并发=1、总上下文 32768、6×5min 窗 | 窗口吞吐、TTFT/TPOT、KV 用量、队列深度 | 吞吐 CV≤5%、中位漂移≤10%、p99≤20%、0 失败 |
| **A4 多租户成本** | mixed 4 租户共享一服务各 25%；B0 扫点→0.70× 正式负载；隔离基线 solo 满载 | Jain、份额偏差、p99 隔离、每百万 token 成本 | Jain≥0.90、份额偏差≤10%、p99≤1.25×、成本口径一致 |

> **阈值口径说明**：上表数值是**代表值/考察重点**，非一律如此。真正的 `slo` 阈值（配置真相）见 [config-reference.md §4.3](./config-reference.md#L164)（来自 `slo.yaml`）；A2 各 cell 的**完整档位**（尤其 **long 放宽到 8/12/16s、16/24/32s**）见 [acceptance-tasks.md 组 C4](./acceptance-tasks.md#L61)（权威）；对话式 SLO 仍是 TTFT p99≤4000ms。判定的"考纲"读上表，精确门槛以 C4 / 配置为准。

**这张表用大白话念一遍**：

- **A1 算力口径**：问"这块芯片你真给发挥满了吗？"——按固定大小的输入跑，算它实际用出了理论峰值的多少（MFU≥90% 才算够）。
- **A2 SLO 组**：问"网上同时来一堆请求，你回得过来、还够快吗？"——5 种对话/工具/推理/结构化/长文本，量首 token 和每 token 延迟有没有超时。
- **A3 窗口稳定**：问"让它一口气读 32k 字的长文本，跑 30 分钟（6×5 分钟窗口）会不会越来越慢、甚至出错？"——稳定即"吞吐不抖、延迟不漂、零失败"。
- **A4 多租户成本**：问"4 个客户挤一台机器，谁都不该被"欺负"，最后每百万 token 花多少钱？"——公平性（Jain）、延迟隔离都在线，才算真·多租户好用。

## 3. 支撑判定模块（Q / M / I / K / Z）——几位"阅卷老师"

四门课考完，还有几位"阅卷老师"从不同角度把关。你不用记代号，只需知道**它们分别盯着：答得对不对、优化真不真、是不是同一份考卷、钱花多少、整体敢不敢盖章**。

- **组 Q（质量 oracle）** — 判断输出对不对：工具调用正常化、数值答案抽取、JSON Schema 校验、长文本/多轮事实命中、W8A8 工件资格（质量下降≤1pp）。
- **组 M（机制生效）** — 证明「优化真的生效」：图执行计数器、prefix cache、量化生效、A3 资源监控、CPU 核表；机制缺失即拒测。
- **组 I（B0/B1 角色）** — 基线/候选双角色，`comparison_id`/`config_id` 身份绑定，代码/镜像 SHA 绑定 fail-closed。
- **组 K（A4 成本）** — 全生命周期成本口径（资产÷21600h + 0.6 元/kWh + 每百万 token）+ 利用率/公平性/隔离门禁。
- **组 Z（集成与门禁）** — ≥3 生命周期中位数、证据包总门禁、数据就绪校验。

## 4. 三层配置 → 15 profile

配置分层 → [config-reference.md §1 目录与分层](./config-reference.md#L23)；8 个 cell → 15 个 profile 的映射见 [README.md §8 个 cell](./README.md#L49)。每次验收用 `src/acceptance.py` 展开成单实例（argv/env/effective）。

## 5. 主要入口（CLI/脚本）

| 入口 | 作用 |
|------|------|
| `python src/acceptance.py` | 配置展开/校验/渲染（`--list`/`--dry-run`/`--argv`/`--env`） |
| `python src/receipt.py` | 生效值快照 + fail-closed 门禁（数据就绪等） |
| `python src/client/run.py` | 客户端执行引擎：扫描/SLO/质量/机制判定/离线自测（`--selftest`） |
| `python src/client/generate.py` | C1+C2 合同生成与自测 |
| `scripts/acceptance.sh` | 验收 harness：list / profile / server / client / run / stop / metrics |
| `scripts/prepare.sh` | 准备资源：模型 / 数据集 / 工作负载 / 固定子集 |
| `scripts/a1_peak.sh` | A1-1 峰值 FLOPs 分母采集 |
| `scripts/msprof_reconcile.sh` | A1-2 msprof 算子对账 |

## 6. 通用工具能力（bench / profile）

> 这是**黑盒通用基准/剖析**能力（wrap 官方 `vllm bench`），不是 vllm-xcheck 验收主线的负载口径。它提供 vllm-xcheck 之外的快速冒烟与宏观指标，两者互补。

- **bench.sh**：wrap 官方 `vllm bench` —— `serve`（在线基准）/ `throughput`（离线吞吐）/ `latency`（离线延迟），产物按双 commit 归档到 `runs/<date>-<vllm_sha7>-<va_sha7>/` + `manifest.yaml`。
- **profile.sh**：Ascend PyTorch Profiler 四段式（serve/start/stop/analyse），产出 `profile_out/*_ascend_pt`。

> 完整用法见 [../guide/commands.md](../guide/commands.md)、[../guide/how-to-run.md](../guide/how-to-run.md)。

## 7. 当前功能状态（截至 verify19）

- **已达标**：A4-MT-FP16-PC verify19 公平/隔离/SLO/容量全 PASS；组 C/Q/M/I/K/Z 离线自测全过；A1 峰值真机落地。**逐项数值与 verify16→19 轮次演化见 [acceptance-tasks.md 组 C5/C6/C7 与组 A](./acceptance-tasks.md#L69)**（此处不重复数值，避免漂移）。
- **正式测量前待补**：`engine_seed=0` 渲染进 argv、`structured_outputs_backend=xgrammar` 传引擎、证据链/签名组件（本条是待补事项的唯一权威）。
- **数据口径**：当前用 14B + SHORT smoke 数据验证；正式数据子集（D 组）未接入，K2 用合成回退（本条与 D 组口径的唯一权威）。

> **那正式验收到底用哪个模型？**
> 所有 profile 指向的**对准模型是 `Qwen/Qwen2.5-14B-Instruct`**（API 逻辑名 `qwen2.5-14b-instruct-{precision}`，见 [config-reference.md §3.2 model](./config-reference.md#L65)），跑的时候用 `--model-ref Qwen/Qwen2.5-14B-Instruct` 命中离线缓存。**D 组（ShareGPT/BFCL/GSM8K/JSONSchema/长文本真实数据子集）正式接入之前，当前跑出的结果是"用简短 smoke 数据验证链路能通"的验证性结果，不等同于最终正式交付报告**——正式交付要等 D 组数据接入 + 上面本节列出的 3 项待补完成后才能跑。

> 明细以 [acceptance-tasks.md](./acceptance-tasks.md) 的勾选状态为准。