# 0012 A2/A3 量测口径：容量裕量 + SLO 分档 + 真数据 + 串行切窗

> **决策摘要**：A2（在线 SLO）与 A3（窗口稳定）的量测口径无法从方案推导，只能靠"号称的玩法 + 真机校准"定下来——A2 用 **容量扫描求 `b0_max` + 正式负载取 `0.70×`** 而不是直接压满；5 个 cell **各自贴真实业务数据**；SLO 阈值**按输入长度分档放宽**；A3 用 **串行单请求 + 分段切窗** 专测长请求自身稳定。这些口径决定了"过/不过"怎么判、以及为什么这么判，故单独专档作为唯一权威。

| 元数据 | 值 |
|--------|----|
| **Status** | accepted |
| **Date** | 2026-08-25 |
| **Type** | standard / tuning |
| **Supersedes** | — |
| **Related** | ADR-0008（fail-closed/确定性落点）、ADR-0009（同为软参数校准）、ADR-0010（A1 算力口径）、ADR-0011（`structured_outputs_backend` 硬约束，A2-STRUCT 逐 case 依赖它） |
| **映射** | A2 SLO（b0_max + 0.70×）、A3 窗口稳定（CV≤5% / 漂移≤10% / p99≤20%） |

> **它与 task-expertise 的关系**：[task-expertise.md](../acceptance/task-expertise.md) 给"任务配置专家"讲**完整推理过程**（逐配置项的"为什么这么设、改了会怎样"）；本 ADR 只收口**决策与取舍本身**，两者一致，改动以本 ADR 为准。

## 背景与为什么（Context）

A2/A3 的判定口径不是字面就能定死的：方案只说要"测在线并发满足 SLO""测长稳"，但**怎么取正式负载、用什么数据、拿什么阈值卡**都可自由发挥，而不同选择会得到不同甚至相反的验收结论。若不定成决策，就会被当"怎么都行"而漂移。故把几条关键口径收口。

## 关键决策（Decision）

### A2-SLO：留裕量的容量口径

1. **求 `b0_max` 而不是直接压满**：`poisson` + `rates` 升序扫描找到"所有 SLO 都满足的最大吞吐"（= `b0_max`）；正式负载取 **`0.70 × b0_max`**。Why：扫描是**证明上界**（能到哪），`0.70×` 是**证明在留了裕量的现实负载下依然合规**。直接压满既证明不了日常可用性，还容易把服务打崩导致量不到稳态。
2. **5 个 cell 各自贴真实业务数据**（dialogue=ShareGPT、tool=BFCL、reason=GSM8K、struct=JSONSchema 逐 case、long=长文本）：质量 oracle 只能贴着真实业务判——"结构化输出排不合法 schema"这类结论用假数据验不出来。
3. **`max_num_seqs` 各 cell 不同**（dialogue=32、reason/tool/struct=16、long=4）：尾生成越短、越可并行的越能高并发；reason/struct 长生成降并发避免排队长尾，long 超长输入降并发防内存爆。增大 → p99 尾延迟因队列变长而恶化。
4. **SLO 阈值按输入长度分档放宽**（long 的 8192/16384/32768 档 TTFT 放宽到 16000/32000ms，对话档 ≤4000ms）：TTFT ∝ prefill 量，宽输入天然慢，**拿一个数卡所有任务不公平**。
5. **A2-STRUCT 咬死 `structured_outputs_backend=xgrammar`（禁 auto）**：细项见 [ADR-0011](./0011-operational-hard-constraints.md)，此处只记其属 A2 判定硬约束。

### A3-窗口：串行单请求 + 分段切窗

1. **串行单请求打满**（`closed-loop` + `concurrency:1` + `max_num_seqs:1` + `capture_sizes:[1]`）：排除并发/抢占干扰，**只测"一个长请求自身稳不稳"**，便于归因；加并发会把"稳定问题"和"调度抢占"搅在一起。
2. **总上下文 `32768` = 输入 `30720` + 输出 `2048`**：贴满 `max_model_len` 的**最坏压力**，测极限长文本；`ignore_eos:true` + `max_tokens:2048` 强制定长输出，窗口之间可比。
3. **`6 × window_s 300` 分段切窗**：CV/漂移本就要"分段看波动"，不切窗无法量化"抖不抖"，也不给 0 失败留记录窗口。
4. **`enable_chunked_prefill:true` 必须开**：30720 的 prefill 不分块会**内存溢出**——这是"依赖分块的正常能力项"，与 A2 里刻意关前缀缓存（保真实学力）是两回事，勿混。

## 与既有 ADR 的关系

- 服务于 ADR-0008 的"确定性 + fail-closed"与"薄脚本"：A2/A3 决定的是**判什么**，执行仍靠官方 `vllm bench`/API server 与 `src/client/`。
- 与 ADR-0009（A4）、ADR-0010（A1）并列：A1–A4 各自口径分档，互不覆盖；改动先入 `schema.yaml` allowed 集。
- `max_num_seqs` 与 A4 基调相关但**口径不同**（A2 按 cell 区分、A4 按 4 租户公平），不合并。

## 候选对比（Alternatives）

- **A2 直接压满（不取 0.70×）**：量不到合规稳态、易打崩服务 —— 否。
- **A2 用假数据**：质量 oracle 无从真判，伪通过 —— 否。
- **A3 加并发测**：稳定问题与调度抢占混淆、无法归因 —— 否。
- **A3 不切窗**：无法量化抖动、无窗口可留 0 失败证据 —— 否。
- **独立成 ADR**：给量测口径决策身份与验证回填处、避免被当"怎么都行" —— 选此方案。

## 后果（Consequences）

- 正式负载一律 `0.70× b0_max`，不得直接压满；改 SLO 阈值/cell 数据/窗口数前先改本 ADR，再入 `schema.yaml` allowed 集。
- 换模型（如更大参数量）时，`0.70×` 系数、各 cell `max_num_seqs`、long 档 SLO 放宽值都需按新机型复核后回填本文。
- A3 的 `enable_chunked_prefill` 为正常能力项，任何"为性能刻意关闭"都要先过本 ADR。

## 兑现回填（Verification）

- ✅ **已落地**：`config/vllm-xcheck/cells/{a2-dialogue,a2-tool,a2-reason,a2-struct,a2-long}.yaml` 与 `a3-window.yaml` 按上述口径配置；`src/client/` 实现容量扫描取 `b0_max` + `0.70×` 正式负载；A2/A3 判定逻辑在 `src/client/`（C 组）落地。
- 待补：真机完整跑一轮后，回填各 cell 实测 `b0_max`、A3 窗口实测 CV/漂移（正式测量前待补见 [acceptance-coverage](../acceptance/acceptance-coverage.md)）。