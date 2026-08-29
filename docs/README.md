# vllm-benchkit 文档中心（docs）

> 这是**全部文档的唯一总入口**。它把一整条"从冒烟到验收"的链路讲成一张地图：先读这篇，就知道接下来该去哪篇、读多久、读到什么程度。
> 一句话定位：**为一个 vLLM + Ascend 的工具仓，维护一套"两档玩法"（黑盒冒烟 → 正式验收）的、可复现、可维护的知识体系。**

---

## 0. 先花 30 秒建立骨架

这个仓库是做 **vLLM Ascend 基准与验收**的。它有两档用力程度：

- **① 黑盒冒烟**（快、探索）：跑官方 bench / profile，看吞吐延迟、对比版本、定位瓶颈 —— 细节在 `guide/`。
- **② 正式验收**（严、留证）：按《标准交付测试方案》跑 A1–A4，判通过与归档证据 —— 细节在 `acceptance/`。

二者**共用同一根树干的顶层三行**：vLLM core（引擎）、vllm-ascend（NPU 后端）、Ascend PyTorch Profiler（profiling）。先弄懂它们是谁、出问题查谁，再往下走：见 [understand.md](./understand.md)。

## 1. 新手阅读路线（约 30 分钟入门）

1. **[understand.md](./understand.md)** —— 这是什么仓库、两档玩法、三层归属、四条硬约束。（5 分钟，必须读）
2. **[run.md](./run.md)** —— 怎么跑：装环境 → 冒烟 → 验收，一条路串完。（10 分钟，照着敲一遍）
3. **[guide/README.md](./guide/README.md)** —— 黑盒玩法细节：命令 / 参数 / 输出。（想冒烟时读）
4. **[acceptance/README.md](./acceptance/README.md)** —— 正式验收细节：A1–A4、覆盖度、配置参数、任务清单。（想验收时读）
5. **[maintenance.md](./maintenance.md)** —— 为什么这套东西能维护/能复现、改动要同步哪些地方。（要改代码/文档时读）

> 想快点：只读 1、2 两步即可上手；要改东西再读 3–5。

## 2. 文档地图（一句话导航）

| 主题 | 文档 | 回答什么 |
|------|------|---------|
| **这是什么** | [understand.md](./understand.md) | 仓库干嘛的、两档玩法、三层归属、四条硬约束 |
| **怎么跑** | [run.md](./run.md) | 装环境 → 冒烟 → 验收，一页串完 |
| **黑盒冒烟** | [guide/README.md](./guide/README.md) | bench/profile 命令、参数、输出怎么读（入口） |
| **正式验收** | [acceptance/README.md](./acceptance/README.md) | A1–A4、验收覆盖度、配置参数、任务清单（入口） |
| **设计与实操实现** | [acceptance/design.md](./acceptance/design.md) | 按模块划分的架构：四段式、每个模块、依赖、取舍、现状 |
| **为什么能维护/运行** | [maintenance.md](./maintenance.md) | 单一事实来源、同步规则、全链路留痕、改动自查 |
| **架构决策** | [adr/](./adr/) | 决策地图 + 0001–0012：每个"为什么这么设计"的记录 |
| **官方能力** | [official-capabilities.md](./official-capabilities.md) | 官方 `vllm bench` 能做/不能做什么 |
| **路线图** | [roadmap.md](./roadmap.md) | 未来要做的事与阶段门 |

## 3. 名词小词典（先混个脸熟）

完整友好词表（黑话→大白话）是站点唯一权威，只维护一处：[CONTEXT.md](./../CONTEXT.md) 的「验收与 KPI 词」一节。此处不再另开同义表，避免两处漂移。

先记住这几个就够起步：**cell/profile**＝考什么/一次完整考试；**TTFT**＝多快开头；**TPOT**＝吐字多快；**SLO**＝延迟承诺上限；**A1–A4**＝算力/SLO/窗口/多租户成本；**two tiers**＝冒烟快而粗、验收严而全。

## 4. 这堆文档怎么保持不乱

答案全在 [maintenance.md](./maintenance.md)：**每个事实只留一个权威出处 + 改动必同步 + 全程留痕**。核心就几条：

- 机器/仓库/版本 → 只写 `config/topology.yaml`（.gitignore），文档用键名。
- 配置字段 → 以 `config/vllm-xcheck/schema.yaml` 白名单为准。
- 任务进度 → 以 `acceptance/acceptance-tasks.md` 勾选为准。
- 设计为什么 → 以 `adr/` 为准，只追加。

改任何东西前，先扫一遍 §5 的新手自查清单。

> 术语详见 [CONTEXT.md](../CONTEXT.md)；仓库根 README：[../README.md](../README.md)。