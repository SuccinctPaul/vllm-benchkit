# vllm-xcheck 验收子系统：薄脚本 / fail-closed / 确定性 / 隐私边界

在「黑盒基准/剖析」（ADR-0001~0007 的 guide 玩法）之上，长出一个正式的验收执行系统 `vllm-xcheck`：把《V4.1 标准交付测试方案》（`config/vllm-xcheck/vLLM-HUST-standard-delivery-test-plan-V4.1.pdf`）落成一套**可实例化、可复现、fail-closed** 的执行链路（客户端负载引擎 + 判定 + 指标采集 + 成本口径 + 证据归档）。

本篇把散在 features/design 正文里的子系统设计原则收口为决策记录，作为「为什么这么设计」的唯一权威。

## 四条设计原则

- **薄脚本 / 少造轮子**：功能层全部复用官方 `vllm bench` / API server（ADR-0001），只做参数化与编排。不引入任何自造负载引擎。
- **fail-closed**：配置表外字段、机制失效、静默截断一律拒绝进正式测量——不达标绝不给出「假通过」。
- **确定性优先**：`seed=0`、确定性合成数据、canonical JSON + SHA，使 B0/B1 字节可比、版本可复现。
- **隐私边界**：机器名/仓库 URL/版本只出现在 `config/topology.yaml`（.gitignore）；文档引用拓扑清单键名，不写具体值。

## 分层与分工

- 配置层三源合并 → 15 个 profile 实例：`common.yaml`（共同基线）+ `precision/{fp16,w8a8}.yaml`（精度 overlay）+ `cells/<cell>.yaml`（任务覆盖）；字段契约见 `schema.yaml`。
- 执行层：`src/client/`（C1–C7）、`src/acceptance.py`（配置渲染）、`src/receipt.py`（证据）、`scripts/acceptance.sh`（harness）。

## 后果

- 新增配置键必须先入 `schema.yaml` 的 allowed 集，否则 `acceptance.py` fail-closed 拒绝。
- 契约与任务明细唯一事实来源 = `schema.yaml` + `acceptance-tasks.md`；features/design/v41-coverage 只做解释与导航、不另立版本。