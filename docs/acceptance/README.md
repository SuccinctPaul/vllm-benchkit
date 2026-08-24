# 正式验收（acceptance）—— vllm-xcheck 子系统入口

> 本文是 **vllm-xcheck 正式验收执行系统**（`acceptance/`）的子系统入口。全仓**唯一总入口、全局新手路线与名词**见 [../README.md](../README.md)；"这是什么/怎么跑"见 [../understand.md](../understand.md) 与 [../run.md](../run.md)。
> 它把 `config/vllm-xcheck/` 配置层 + `src/` 执行层 + `scripts/` harness 整理成一套**可实例化、可复现、fail-closed** 的验收系统，规格覆盖见 [acceptance-coverage.md](./acceptance-coverage.md)，逐项任务见 [acceptance-tasks.md](./acceptance-tasks.md)。
> 一句话玩法：**三层配置合成 profile → 独立 vllm serve + 客户端基准引擎 → 判定门禁 → 证据归档**。

## 先用大白话说：这是干嘛的

可以把它想成给大模型办**一场正式考试**（对应《标准交付测试方案》）。分三步：

1. **考什么**：A1–A4 四门考核（算力够不够、延迟达不达标、长文本稳不稳、多租户公不公平、便宜不便宜）。
2. **怎么考**：先按 `config/vllm-xcheck/` 里的配置把服务一键起好，再用一套客户端自动按验收要求发请求、量指标。
3. **结果留证**：每项判定（过/不过）和原始数据都打包归档到 `runs/`，**哪个没过就明确拒绝**（fail-closed），不许"差不多就行"。

所以：**改配置 → 起服务 → 自动考 → 留证据**。下面的功能/设计/how-to-run/覆盖度/配置参数，都是在讲这套考场的细节。

## 这套系统的真实价值（按《方案》的需求讲）

《方案》要的是交易双方（交付方 / 验收方）能**就同一套办法、同一个口径，把"模型和服务的真实能力"对齐并留证**——但直接手做，会踩三个坑：指标算得不一致、结果无法复现、判定全凭感觉。**vllm-xcheck 就是把《方案》的「要什么」翻译成「怎么算、怎么判、怎么留证」的自动执行器**，逐一消除这三个坑：

| 《方案》的需求 | 手写会踩的坑 | vllm-xcheck 怎么兑现 |
|---|---|---|
| 指标得**一个口径** | 各人量法不同，数字对不上 | seed=0 确定性采样、指标定义收敛到唯一权威（SLO/数据口径），全链只认一套量法 |
| 结果要**能复现** | 换台机器就对不上 | seed=0 + 确定性数据 + canonical JSON+SHA + receipt 全量归档，两次测量可逐字节对账 |
| 判定要**有标准、留证据** | "差不多就行"没人认账 | fail-closed：不达标明确不过；每项判定带原始数据打包成证据包进 `runs/` |

**一句话价值**：让"这模型/服务达没达标"从**主观说法**变成**可复现、可对账、可追溯的事实**——必须过 8 张附录表的硬门槛才算数，过了就是过了，谁也赖不掉。

## 文档导航（按主题）

| 主题 | 文档 | 回答什么 | 什么时候读 |
|------|------|---------|-----------|
| **功能** | [features.md](./features.md) | 系统能做什么：A1–A4 验收单元、支撑模块（Q/M/I/K/Z）、以及 bench/profile 通用工具能力 | 想先知道「这系统到底能干哪些事」 |
| **任务专家速成** | [task-expertise.md](./task-expertise.md) | 每条任务（A1–A4）的需求/目标、判定，以及「配置为什么这么设、改了会怎样」 | 想成为某条任务的配置专家、理解设计意图（读完 features 再读） |
| **设计** | [design.md](./design.md) | 按模块划分的架构：四段式链路、每模块职责、数据流、依赖关系 | 想理解代码怎么组织、改代码前先看懂 |
| **How to run** | [how-to-run.md](./how-to-run.md) | 怎么跑：前置、部署、起服务、跑验收/自测、看结果 | 第一次上手跑验收 |
| **验收覆盖度** | [acceptance-coverage.md](./acceptance-coverage.md) | 验收需求各项功能的实现覆盖度（已实现/未实现/真机项） | 想核对「方案里要的东西我们做了没」 |
| **配置参数** | [config-reference.md](./config-reference.md) | 配置文件（common/cells/precision/schema）每个参数的含义 | 想改/新增一个配置项 |
| **任务清单** | [acceptance-tasks.md](./acceptance-tasks.md) | 按组 C/I/Q/M/A/K/D/S/Z 的逐项任务、做法与验收（进度打勾） | 想知道某项做到哪了、下一步做啥 |

## 新手阅读路线（30 分钟足够入门）

1. **[features.md §1 一句话全景](./features.md#L9)** —— 先看一句话全景，建立心智模型。
2. **[design.md §2 四段式链路](./design.md#L21)** —— 理解四段式（配置 → 展开 → 执行 → 门禁归档）与模块划分。
3. **[how-to-run.md §0 前置条件](./how-to-run.md#L15)** —— 一次性装环境，跑一个最小的离线自测验证链路通。
4. 再按需深入：改配置读 [config-reference.md](./config-reference.md)，核对合规读 [acceptance-coverage.md](./acceptance-coverage.md)；想搞懂"每条任务为什么这么配"读 [task-expertise.md](./task-expertise.md)。

## 系统速览

```
①三层配置源 ──▶ ②展开校验 ──▶ ③执行引擎(组C) ──▶ ④判定门禁 ──▶ ⑤证据归档(fail-closed)
common +      src/acceptance.py
cells +        (argv/env/effective)
precision
```

- **配置层**：`common.yaml`（共同基线）+ `precision/{fp16,w8a8}.yaml`（精度 overlay）+ `cells/<cell>.yaml`（任务覆盖）三层合并 → **15 个 profile 实例**；字段契约见 [schema.yaml](../../config/vllm-xcheck/schema.yaml)。
- **执行层**：`src/client/`（组 C）负责生成请求、到达、执行、SLO/窗口/MFU 判定。
- **门禁/归档**：组 Q/M/I/K 判定 + 组 Z 汇总，证据落 `runs/accepted/<PROFILE>-verifyN/`，fail-closed。

### 8 个 cell → 15 个 profile

| cell | 任务 | profile（精度展开） |
|------|------|--------------------|
| `a1` | A1 算力口径（MFU） | `A1-FP16` |
| `a2-dialogue` | A2 通用问答/多轮 | `A2-DIALOGUE-{FP16\|W8A8}-PC` |
| `a2-tool` | A2 工具调用 | `A2-TOOL-{FP16\|W8A8}-PC` |
| `a2-reason` | A2 推理 | `A2-REASON-{FP16\|W8A8}` |
| `a2-struct` | A2 结构化输出 | `A2-STRUCT-{FP16\|W8A8}` |
| `a2-long` | A2 长上下文容量 | `A2-LONG-{FP16\|W8A8}` |
| `a3-32k` | A3 长上下文稳定 | `A3-32K-{FP16\|W8A8}` |
| `a4-mt` | A4 多租户成本 | `A4-MT-{FP16\|W8A8}-PC` |

> - 上表 profile 名里的 **`-PC` 后缀 = 该任务开启前缀缓存（prefix caching）**（dialogue/tool/A4 开着；reason/struct/long/A3/A1 关闭，故不带此后缀）。
> - 用 `python src/acceptance.py --list` 实时枚举全部 profile。

## 名词小词典

> 名词统一见总入口 [../README.md §3 名词小词典](../README.md#L41)，这里只补充**本子系统特有**的一项：

| 黑话 | 大白话 |
|------|--------|
| **profile（15 个）** | cell(8) × 精度(FP16/W8A8) 展开成的可跑考试；执行路径 `runs/accepted/<PROFILE>-verifyN/` |

## 常见问题速查

- **这是不是另一套基准框架？** 不是。它消费 `vllm bench` 无法表达的 **验收口径**（多轮会话、工具/结构化、租户份额、窗口稳定、算力 MFU）；通用单测基准能力复用官方 `vllm bench`（见 [features.md §6](./features.md#L60)）。
- **想看某模块怎么实现的？** 读 [design.md §5.1 读代码第一站](./design.md#L101) 的「你想做什么 → 第一站」映射。
- **想核对验收某条做没做？** 读 [acceptance-coverage.md](./acceptance-coverage.md)，逐组有状态。

## 相关文档（仓库级）

- [../README.md](../README.md) —— 全仓文档唯一总入口（地图 + 新手路线 + 名词 + 维护）
- [../understand.md](../understand.md) —— 这是什么（两档玩法 / 三层归属 / 硬约束）
- [../run.md](../run.md) —— 怎么跑（冒烟 → 验收一页串完）
- [../maintenance.md](../maintenance.md) —— 为什么能维护＆运行、改动自查
- [../../CONTEXT.md](../../CONTEXT.md) —— 术语表（目标/阶段/三层归属/三类工作）
- [../roadmap.md](../roadmap.md) —— Track 5 = vllm-xcheck 正式验收执行层；阶段门 Gate-H
- [../guide/]() —— 通用 benchmark/profile 工具指南（黑盒能力）
- [../adr/]() —— 架构决策记录（0001~0010）
- [../../config/vllm-xcheck/](../../config/vllm-xcheck/) —— 配置层本体（README + schema + common + cells + precision）
- [task-expertise.md](./task-expertise.md) —— 任务专家速成：每条任务的需求/判定/为什么这么配

> 维护注意：本目录是 `acceptance/` 子系统入口；**契约与任务明细的唯一事实来源是 [schema.yaml](../../config/vllm-xcheck/schema.yaml) 与 [acceptance-tasks.md](./acceptance-tasks.md)**，本文及各主题文档只做解释与导航，代码/配置演进后应同步更新（规则见 [../maintenance.md](../maintenance.md)）。