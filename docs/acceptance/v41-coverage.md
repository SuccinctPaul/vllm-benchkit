# V4.1 覆盖度

> 本文回答「**V4.1 方案里要的东西，我们做了没**」。逐组按**已实现 / 真机项 / 未实现**标注；状态实时值以 [acceptance-tasks.md](./acceptance-tasks.md) 勾选为准。
> 规格属性与逐组对接（表附-1~附-8 已按组归档）见 [acceptance-tasks.md](./acceptance-tasks.md)；本文只讲覆盖度，不重复状态、不重复清单。

## 先看结论

一句话：**能离线自动判的（配置、负载、质量、机制、门禁）都做完了**，剩下少数"必须真机才能跑"的项（芯片峰值、功耗/资产成本、msprof 对账）等真机上正式测量时一并验证。✅=已实现，🟡=真机/部分项。逐格对不上了再往下看明细。

## 覆盖度总览

| 组 | 内容 | 对应表附 | 状态 |
|----|------|---------|------|
| 配置层 | common / cells / precision + schema + acceptance 展开 | 附-2/3/4/5 | ✅ 已实现 |
| C | 客户端负载引擎（请求/到达/执行/回执） | 附-5/6/7 | ✅ 已实现（C1–C7 全过） |
| I | B0/B1 角色与配置身份 | 附-1、附-8 | ✅ 已实现（离线自测过） |
| Q | 质量 oracle / 判定程序 | 附-8 | ✅ 已实现（离线自测过） |
| M | 指标采集与优化机制生效 | 附-8、A3 专项 | ✅ 已实现（离线自测过 + 真机聚合脚本） |
| A | A1 算力口径（峰值 + msprof 对账） | 附-6、附-8 A1 | 🟡 真机项（峰值已落地，msprof 对账待真机） |
| K | A4 成本模型 | 附-7、附-8 A4 | ✅ 已实现（K1 功耗/资产原值为真机项） |
| D | 数据子集补齐 | 附-8 数据身份 | 🟡 部分（口径见 [features.md §7](./features.md#L73)） |
| S | 配置层边角字段补齐 | 附-2/3/5 | ✅ 已实现 |
| Z | 集成与验收门禁 | 全程 | ✅ 已实现（Z1–Z3 离线过） |

## 逐组明细

### 组 S —— 配置层字段

- ✅ kv_cache requested/effective、模板/结构化字段、A4 租户采样、A2 warmup 等边角字段已补齐；dry-run 展开与冻结表逐项一致；schema 表外字段拒绝。（逐项 S1–S4 见 `acceptance-tasks.md` 组 S，已勾选）
- ✅ `engine_seed=0` 已计入 schema/receipt；**待补**：渲染进 serve argv 的核对（见下「正式测量前待补」）。

### 组 C —— 客户端负载引擎

- ✅ **C1** 合同案例：canonical 请求清单，确定性（config_hash/cell/seq）。
- ✅ **C2** 到达：`vllm-xcheck-poisson-v1`，seed=0，drain/cooldown，绑定参数哈希。
- ✅ **C3** 执行器：流式 + 逐请求回执（TTFT/TPOT/E2EL/body SHA/verdict）。
- ✅ **C4** SLO：完成率/错误率/延迟门禁，只认预声明 rate。
- ✅ **C5** A4 租户调度：混合到达 + 双重并发 + B0 扫描 + 隔离基线。
- ✅ **C6** A3 窗口：CV≤5% / 漂移 / 0 失败 / token 门禁。
- ✅ **C7** A1 闭环形状 + MFU。

### 组 I —— B0/B1 角色与身份

- ✅ I1 角色化配置模型（B0/B1 + comparison_type）。
- ✅ I2 `comparison_id`/`config_id`（RFC 8785 JCS + SHA-256）。
- ✅ I3 代码/镜像 SHA 绑定门禁，fail-closed。

### 组 Q —— 质量 oracle

- ✅ Q1 工具判定（BFCL V4）、Q2 推理（GSM8K）、Q3 结构化（JSONSchema 2020-12）、Q4 长文本/多轮。
- ✅ Q5 W8A8 资格门禁：相对同源 FP16 成功率下降≤1pp；Schema 100%；工具/结构化≥95%。

### 组 M —— 指标采集与机制生效

- ✅ M1 图执行计数（A1 必须 0 图捕获）。
- ✅ M2 prefix cache / 量化生效。
- ✅ M3 A3 资源监控（1s 间隔 npu-smi 采样）。
- ✅ M4 CPU 核表 / OMP 线程 / digest。
- 🟡 真机采集依赖 `collect_metrics.py` + `acceptance.sh metrics`，需服务存活时聚合。

### 组 A —— A1 算力口径

- ✅ **A1-1 峰值分母**：`a1_peak.sh` + `a1_peak.py`，torch_npu fp16 matmul 实测（来源、相对理论比例与实测值见 [acceptance-tasks.md 组 A](./acceptance-tasks.md#L174)，此处不重复）。
- 🟡 **A1-2 msprof 对账**：`msprof_reconcile.sh` + `.py` 已落地 + 离线模拟 CSV 验证；**待真机 msprof 采集**执行。
- 🟡 峰值分母优先 ascend-dmi（910B2 单机一般不带，回退 torch_npu matmul）。

### 组 K —— A4 成本模型

- ✅ 设备/主机成本（资产÷21600h×生命周期）、电费（0.6 元/kWh）、每百万成功输出 token 成本。
- ✅ K2 利用率≥60%、Jain≥0.90、份额偏差≤10%、p99≤1.25× 隔离。
- 🟡 K1 功耗/资产原值为真机项，经 `--power-kw` / `--asset-value-cny` 传入；未传则 cost 门禁 not-applicable。

### 组 D —— 数据子集

- ✅ `prepare.sh subsets` 物化 registry；`load_subset` 确定性加载。
- 🟡 正式 A3 长文本集 / 真实源未接入；当前 smoke/合成回退（完整结论见 [features.md §7「数据口径」](./features.md#L73)，此处只标状态）。

### 组 Z —— 集成门禁

- ✅ Z1 证据包组装（C→Q→M 全链路落盘）。
- ✅ Z2 ≥3 生命周期 + 复测≤2 追加不替换 + 中位数。
- ✅ Z3 证据总门禁，fail-closed、真机项 not-applicable 记账。

## 真机端到端状态

- ✅ **A4-MT-FP16-PC verify19（2026-08-23）**：（公平/隔离/SLO/容量）全 PASS，M/Z 门禁 PASS。**逐项数值与轮次演化见 [acceptance-tasks.md 组 C5](./acceptance-tasks.md#L69)（此处不重复，避免漂移）。**
- ✅ A1-1 峰值真机实测（数值见 acceptance-tasks 组 A）。
- 🟡 A1-2 msprof 对账与 K1 功耗/资产随正式 A1–A4 服务器验证完成。

## 正式测量前待补（V4.1 合规审计）

清单见 [features.md §7「正式测量前待补」](./features.md)（此处不重复，避免多处漂移）；与本文件组 S 的状态差异以 [acceptance-tasks.md](./acceptance-tasks.md) 勾选为准。