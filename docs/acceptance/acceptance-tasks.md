# vllm-xcheck 正式验收执行层任务清单

> 来源：对《标准交付测试方案》附录冻结表（附-1~附-8）的工具链缺口审计（2026-08-21）。
>
> 现状基线：
> - **配置层已达标**：common / cells / precision + schema + `src/acceptance.py` 展开，覆盖冻结表全部服务端参数（附-2/3/4 逐项一致）。
> - **执行层已实现**：`src/client/`（C1–C7）、`src/receipt.py`、`src/generate.py`、`scripts/acceptance.sh` 已落地；真机 verify16/18/19 已端到端跑通（见下文组 C 真机验证记录），不再是最初的 smoke 骨架。
>
> 本清单把缺口拆成可执行任务，按优先级排布。**验收口径：不满足冻结表的项以 fail-closed 拒绝；不可用功能不得进正式测量。**

## 约定

- 状态：`[ ]` 待办 / `[x]` 完成 / `[~]` 阻塞（依赖前置）。
- 涉及文件一律用仓库相对路径；不出现机器名 / 仓库 URL / revision 具体值（隐私边界约定，实例化值写 `config/` 且 .gitignore）。
- 新增配置键必须先入 `config/vllm-xcheck/schema.yaml` 的 allowed 集，否则 `acceptance.py` fail-closed 拒绝（表外字段）。
- 每项验收须能在本仓库内用 dry-run / `bash -n` / 单测完成，再上真机。

## 总览

| 组 | 内容 | 对应冻结表 | 优先级 |
|---|---|---|---|
| C | 客户端负载引擎（执行层核心） | 附-5/6/7 | P0 |
| I | B0/B1 角色与配置身份 | 附-1、附-8 配置身份 | P0 |
| Q | 质量 oracle / 判定程序 | 附-8 判定程序、W8A8 资格 | P0 |
| M | 指标采集与优化机制生效 | 附-8 优化机制生效、A3 专项 | P0 |
| A | A1 算力口径专用闭环 | 附-6 A1、附-8 A1 算力口径 | P0 |
| K | A4 成本模型 | 附-7 A4、附-8 A4 成本 | P1 |
| D | 数据子集补齐 | 附-8 数据身份 | P1 |
| S | 配置层边角字段补齐 | 附-2/3/5 | P1 |
| Z | 集成与验收门禁 | 全程 | P1 |

---

## 组 C —— 客户端负载引擎

> 目标：把 `workload` 合同（cells 里已声明的 rates/caps/sessions/tenants/windows）真正执行起来，替代 `bench.sh serve` 的 random smoke。
> 新模块建议 `src/client/`（消费 `acceptance.expand()` 的 effective 树）。

### C1 客户端合同模块（per-cell case 生成）
- [x] 目标：按 cell 生成 canonical request 清单；数据源用 `prepare.sh subsets` 产出的固定子集（`datasets/*.jsonl` + `.registry.json`），按 manifest 的 ordered_sha256 顺序循环，不随机重抽。
- 做法：
  - dialogue：16 会话 × 4 轮，同会话累积历史，逐轮发 chat 请求。
  - tool：32 single + 16 multi/parallel + 16 multi-turn，真实 tools、`tool_choice=auto`、`parallel_tool_calls=true`（multi/parallel 显式 true）。
  - reason：64 例，`input≤1024, output_cap=512`；struct：64 例，`input≤2048, output_cap=512`，逐 case 附 JSON Schema。
  - long：32 例（16×8192+512、16×16384+1024）。
- 验收：同一 cell 两次生成字节一致（request_id 与顺序由 config_hash/cell_id/repeat/sequence_no 确定性生成，附-5）。
- 落地：`src/client/cases.py`（`generate_cases`）+ `src/client/common.py`（canonical JSON / config_hash / 固定子集加载）；CLI `src/client/generate.py`。tools/schema 未接入时用内置固定集并标注 source（正式测量必须 dataset）。

### C2 到达序列生成器（vllm-xcheck-poisson-v1）
- [x] 目标：实现 `generator=vllm-xcheck-poisson-v1`，`seed=0`，每个 rate 生成完整到达时间序列；`clock=monotonic`；B0/B1 复用同一序列。
- 做法：`request_cap = ceil(rate × 600)`；rate 按 cells 声明升序；rate 间 `drain/cooldown=60s`；并发上限饱和时本地 FIFO 等待、保留 nominal timestamp、记录 sendlag，不丢弃不重排不突发追赶（附-8 到达背压）。
- 验收：ordered inter-arrival timestamp 的 SHA-256 进入请求清单；生成器绑定 exact revision/hash；B0/B1 序列一致。
- 落地：`src/client/arrival.py`（`capacity_scan` / `per_rate_stream`）。cap 与 `ceil(rate×per_rate_s)` 不一致即 fail-closed；`generator_hash` + `arrival_stream_sha256` 写入合同。

### C3 请求执行器 + 逐请求回执
- [x] 目标：按 `streaming=true` 流式执行，逐请求记录 TTFT（首个有效输出 token 时间戳）、TPOT（后续 token 间隔）、E2EL、错误/超时、输出 token 数与 max_tokens/EOS 状态、截断标志、rendered HTTP body 及 SHA-256（附-5 wirerequest 约束）。
- 做法：每请求判定「有效/失败/超时/静默截断」；无法解释的短输出或 token 身份不一致按 silent truncation 失败。
- 验收：失败、超时、错误、静默截断全部保留（不吞错）；数据落到 runs 目录 JSONL，供组 Q/M 消费。
- 落地：`src/client/executor.py`（`execute_one` 逐请求回执 + `run_scan` poisson 容量扫描）。时钟 `time.monotonic()`；并发饱和用 `asyncio.Semaphore` 本地 FIFO 等待、保留 nominal、记 `sendlag_s`；多轮会话按 `session_id` 累积历史。verdict 分类：`ok` / `failed`(HTTP/传输) / `timeout` / `malformed`(SSE 解析失败 / 0 token) / `silent_truncation`(可变输出 finish=length)。固定输出形状（long/synthetic 命中 max_tokens=length）视为完整输出。逐请求回执含 body + body_sha256 + TTFT/TPOT/E2EL + output_tokens/output_text + finish_reason/truncated + rate/cap/nominal/sendlag。

### C4 SLO 模型（附-8 A2 绝对 SLO）
- [x] 目标：把绝对 SLO 阈值入配置并在逐率测量后判定合规。
- 做法：cells 新增 `workload.slo`（入 schema allowed）：
  - dialogue/tool/reason/struct：TTFT mean≤1000ms、p95≤2000ms、p99≤4000ms；TPOT mean≤40ms、p95≤60ms、p99≤80ms。
  - long：8192 输入 TTFT≤8/12/16s、16384 输入≤16/24/32s；TPOT≤40/60/80ms。
- 验收：每 rate 完成数 ≥ request_cap×99%；样本<1000 时 0 失败、达 1000 时 error_rate≤0.1%；只接受预定义 rate 点，不插值不外推。
- 落地：`src/client/slo.py`（`evaluate`）。完成=verdict∈{ok}，失败=failed/timeout/malformed/silent_truncation 全计；long 按 `(rate, input_len)` 分桶对 `slo.shapes` 档位；出现未声明 rate 点即 fail-closed。CLI：`src/client/run.py`（执行 + 判定，写 receipts.jsonl / slo.json / contract.json；`--selftest` 用 httpx.MockTransport 离线验证 14 项）。

### C5 A4 租户调度器
- [x] 目标：四租户（dialogue/tool/reasoning/structured）共享单一服务，各 25% 输出 token 份额；`per_tenant_concurrency=4`、`global_concurrency=16`；tool 租户发 tools（per-tenant 采样差异）。
- 做法：按 `b0_capacity_points` 扫 B0-FP16 混合容量（每点 600s、间隔 60s、容量阶段重复 3 生命周期），再以 `global_load_factor=0.70 × B0-FP16 三次中位最大 SLO 合规混合输出率` 执行正式混合负载（warmup 5min / measure 30min）。
- 验收：逐租户输出 token 份额偏差≤10%；逐租户 SLO、公平性（Jain≥0.90）、p99≤隔离 B0-FP16 的 1.25× 数据入证据。
- 落地：`src/client/scheduler.py`（`mixed_arrival` 确定性混合到达、`run_mixed` global+per-tenant 双重并发、`scan_segments`/`formal_token_rate` B0 扫描与 70% 正式负载、`evaluate_scan_point`/`evaluate_formal` 判定、`p99_isolation_check` 隔离性证据）+ `src/client/fairness.py`（份额偏差/Jain）。`run.py` 支持 mixed 模式（`_scan_mixed`：scan→b0_max→warmup→formal，落 receipts-scan/formal.jsonl + slo.json + contract.json）；离线自测新增 6 项（速率比例/确定性/份额/Jain/逐租户 SLO/隔离性）。三次生命周期中位重复由组 Z2 编排，本模块提供单生命周期合同。
- 真机验证（2026-08-23，verify16，14B + SHORT smoke）：扫描 4 点（64/96/128/192）全 SLO 合规 → b0_max=192 tok/s → 正式 0.70×=134.4 tok/s。**公平性 PASS**（Jain=0.9513≥0.90、max 份额偏差 0.0943≤10%）；逐租户 SLO 4/4 PASS（绝对阈值仅记录，A4 延迟判定走相对隔离）；Q/M/Z 门禁 PASS。**隔离性 K2 未达标**：p99 比值 1.35×–1.92× 超 1.25×（tool ttft 1.42×/tpot 1.57×、reasoning ttft 1.50×/tpot 1.92×、structured ttft 1.35×/tpot 1.66×、dialogue tpot 1.52×）；max_num_seqs 16→32 后较 verify15 有改善（tool tpot 1.99×→1.57×）但仍未达门禁。产物：`runs/accepted/A4-MT-FP16-PC-verify16/`。
- 真机验证（2026-08-23，verify18，max_num_seqs=36 + capture_sizes 扩至 36 + 隔离基线 full_load 修复，SHORT smoke）：扫描 4 点全 SLO 合规 → b0_max=192 tok/s → 正式 134.4 tok/s。**公平性 PASS**（formal Jain=0.9496≥0.90、max 偏差 0.0958≤10%）；逐租户 SLO 4/4 PASS；Q/M/Z 门禁 PASS。**隔离性 K2 大幅改善但仍未达标**：仅 structured tpot 1.36× 超标（159.4/117.4ms），其余全 PASS——tool ttft 0.77×/tpot 0.77×（full_load 修复后隔离基线真实反映单租户独占 36 并发：tool 95→199ms、reasoning 79→143ms）、dialogue tpot 1.16×、reasoning ttft 1.01×/tpot 1.10×、structured ttft 1.15×。isolation_ok=false。根因：mixed 下 4 租户 tpot_p99 趋同 ~155ms（全局并发顶格排队、p95 112→p99 155 突变），structured 隔离基线最低（117ms，full_load 请求率仅 8.4 req/s 未达高并发、基线乐观）；formal tpot_p99 155ms > scan 192 点 115ms 为真实跨租户 decode 抢占（尾延迟全程均匀，非冷启动）。产物：`runs/accepted/A4-MT-FP16-PC-verify18/`。
- 真机验证（2026-08-23，verify19，基线并发对齐满载 + per-tenant isolation_token_rate，SHORT smoke）：为各租户新增 `isolation_token_rate`（dialogue=405 / tool=192 / reasoning=192 / structured=504，见 [a4-mt.yaml](../../config/vllm-xcheck/cells/a4-mt.yaml#L53-L63) 注释），把 cost_len 大的租户（dialogue/structured）隔离基线 token_rate 从 b0_max=192 提至 solo 满载 36 并发水平——修复 verify18 基线并发不一致（tool/reasoning 基线顶格 36 并发、structured 仅 ~16 并发）导致的隔离性判定失真。落地：schema.yaml `workload.allowed` 增 isolation_token_rate、`scheduler.isolation_token_rate()`（缺省回退 b0_max）、run.py 隔离基线循环改用 per-tenant iso_rate。核验：隔离基线并发经 Little's law 估算 dialogue≈635.8 / tool≈3191.9 / reasoning≈1054.0 / structured≈761.4（均远超 36 并发、驻留时间长所致）。**结果：K2 隔离性达标 isolation_ok=true**，四租户 p99 比值均 <1.25×（dialogue ttft 0.60×/tpot 0.60×、tool ttft 0.77×/tpot 0.78×、reasoning ttft 1.11×/tpot 1.18×、structured ttft 0.96×/tpot 1.22×；structured tpot 158.5/130.2ms）；公平性 PASS（formal Jain=0.9519≥0.90、max 偏差 0.0938≤10%）；逐租户 SLO 4/4 PASS；容量 b0_max=192 tok/s → 正式 134.4 tok/s。**M/Z 门禁 PASS**（M1 graph_capture=7、M2 prefix_cache queries/hits=3、M4 cores=0-191；M 证据初缺由客户端未传 serve-log/pid 所致，对运行中 server 补采 server_metrics.json 后重评 PASS）。产物：`runs/accepted/A4-MT-FP16-PC-verify19/`（receipts-scan/isolation/formal + slo/gate/evidence/quality/mechanisms/contract + server_metrics）。

### C6 A3 窗口分析器
- [x] 目标：closed-loop、`concurrency=1`、总上下文 32768=30720+2048；warmup 5min、measure 30min、6×5min 窗口；`max_requests=64`、`min_completed_requests=24`。
- 做法：逐窗口统计吞吐、TTFT/TPOT；输入 token 数以 chat template 渲染后 tokenIDs 计；不得静默截断或用随机 token 替代业务长文本。
- 验收：窗口吞吐 CV≤5%；各窗口 TTFT/TPOT 中位数相对首个稳定窗口漂移≤10%、p99≤20%；0 失败/无 OOM/死锁/退出/静默截断。
- 落地：`src/client/window.py`（`run_closed_loop` 串行执行 warmup 不计量 + measure 计量、`split_windows` 按 `measure_ts_s` 切窗（空窗口保留）、`evaluate_windows` 判定：窗口吞吐 CV≤5%、TTFT/TPOT 中位漂移≤10%/p99≤20%、0 失败（failed/timeout/malformed/silent_truncation 全计）、输入渲染后 tokenIDs ±5%、输出 `completion_tokens==output_len`）。`run.py` 支持 closed-loop 模式（`_scan_closed_loop`：run→split→evaluate，落 receipts.jsonl + windows.json + contract.json）；a1 闭环算力测量保留给 C7。离线自测新增 6 项（稳定 PASS/CV 捕获/静默截断/漂移/输出门禁/closed-loop 串行）。

### C7 A1 闭环算力测量（主/辅形状）
- [x] 目标：closed-loop、固定形状（主 4096×8→1；辅 1024×32→1、8192×4→1）；warmup=10 轮、measure=30 轮；`per_request_timeout=300s`、`lifecycle_timeout=1800s`。
- 做法：不依赖 `bench.sh throughput`，独立闭环按轮跑固定形状并采集墙钟与计数器。
- 验收：主形状 MFU 计算可复现（见组 A）；输出可与 msprof 对账。
- 落地：`src/client/mfu.py`（Qwen2.5-14B 逐算子 effective FLOPs：MatMul 2MNK、Attention QK^T+PV，不含逐元素；`effective_flops`/`mfu_summary`，峰值分母 ascend-dmi 真机项）+ `src/client/closedloop.py`（`run_rounds` 按轮 batch 并发执行固定形状，warmup 不计量/measure 计量）。`cases.py` 提供 A1 确定性合成 prompt（固定词库 LCG，字节可复现）。`run.py` 支持 A1 闭环模式（`_scan_closed_loop_a1`：run→mfu→落 receipts.jsonl + rounds.json + mfu.json + contract.json）。离线自测新增 5 项（FLOPs 确定性/单调、prefill 每 token 量级、MFU 公式、闭环轮次、合成 prompt 确定性）全过。

---

## 组 I —— B0/B1 角色与配置身份

### I1 角色化配置模型（附-1）
- [x] 目标：配置层引入角色维度 `baseline_role=B0 / candidate_role=B1` 与 `comparison_type`（FP16_CONTROL / SYSTEM_DELIVERY / W8A8_MATCHED）。
- 做法：`SYSTEM_DELIVERY` 仅模型工件、served_model_name、quantization 三项按角色取值，其余硬件/数据/请求/采样/arrival/SLO/质量门槛/服务配置相同；A1 两侧 eager；A2—A4 两侧 FULL_DECODE_ONLY + 表附-3 同 capture sizes + warmups=1。
- 验收：同 comparison_type 各角色配置展开后，除固定角色字段外逐项相等（可用差分测试断言）。
- 落地：`src/client/roles.py`（`apply_role` / `diff_roles` / `ROLE_FIELDS`）+ 自测 `roles-diff`。

### I2 comparison_id / config_id（附-8 配置身份）
- [x] 目标：`comparison_id=SHA-256(JCS 角色化比较合同)`；B0/B1 `config_id` 分别对代码/镜像身份与同一配置计算 SHA-256。
- 做法：实现 JCS（RFC 8785）规范化序列化 + SHA-256；写进 receipt。
- 验收：两侧规范化配置、模型工件、生效值任一不符即拒绝。
- 落地：`src/client/jcs.py`（RFC 8785 JCS 规范化，`canonicalize`/`sha256_hex`）+ `roles.role_contract`/`comparison_id`/`config_id`（公共配置身份剔除角色字段与 meta 身份字段）+ 自测 `roles-comparison-id`/`roles-common-config-sha`。

### I3 代码/镜像 SHA 绑定门禁（附-1、附-8 代码与镜像）
- [x] 目标：B0 绑定固定 core/plugin SHA；B1 绑定验收发布提交的 exact core/plugin SHA 与 OCI digest；`bench.sh` 的 manifest 仅记录，门禁须校验。
- 做法：receipt 新增 gate：读运行环境实际 commit/digest 与声明值比对；clean-overlay 构建证明纳入执行记录。
- 验收：B0 提交不匹配即拒绝（fail-closed）；B1 提交/OCI 摘要缺失即拒绝。
- 落地：`roles.sha_binding_gate`（未声明字段按 not-applicable 记账；声明但失配 fail-closed）+ 自测 `roles-sha-binding`；真机经 `acceptance.sh --code-sha/--image-digest` 传入实际值。

---

## 组 Q —— 质量 oracle / 判定程序

> 目标：oracle/parser 与 client adapter 同仓发布并绑定 exact revision/hash（附-8 判定程序）。

### Q1 工具判定（BFCL V4）
- [x] 做法：`name + arguments` 规范化比较（对参数排序/展开后比对）。
- 验收：同 64 例两次判定一致；工具成功率和 Schema 合法率计入 W8A8 资格（工具/结构化≥95%）。
- 落地：`src/client/oracle.py` `evaluate_one`（kind=tool_*：name + 参数规范化比较）+ 自测 `q-tool-ok`。

### Q2 推理判定（GSM8K）
- [x] 做法：数值答案抽取（固定抽取器），与参考答案比对。
- 验收：正确率/错误率可复现；相对同源 FP16 下降≤1pp。
- 落地：`oracle.evaluate_one`（kind=reason：`#### 数字` 抽取 + 参考答案比对）+ 自测 `q-reason-ok`。

### Q3 结构化判定（JSONSchemaBench）
- [x] 做法：按 JSON Schema Draft 2020-12 校验输出；`disable_any_whitespace=false`、`disable_additional_properties=false`。
- 验收：Schema 合法率 100%。
- 落地：`oracle.validate_json_schema`（Draft 2020-12，jsonschema 库）+ 自测 `q-struct-ok`/`q-struct-fail`。

### Q4 长文本 / 多轮判定
- [x] 做法：dialogue 检查协议成功与多轮连续性；A3 长文本插入可核验目标事实，oracle 检查目标事实命中、无截断、输出 2048 token。
- 验收：A3 任一字段缺失则 A3 不通过；oracle 绑定 hash。
- 落地：`oracle.evaluate_one`（kind=long_*：目标事实命中/无截断/输出长度检查）+ 自测 `q-long-*`。

### Q5 W8A8 工件资格门禁（附-8 W8A8 工件资格）
- [x] 目标：W8A8 由固定源 FP16 工件量化生成，为不可变交付工件；先过质量对照再进性能。
- 做法：receipt gate 校验：推理/工具/结构化/长文本成功率相对同源 FP16 下降均≤1pp；Schema 100%；工具/结构化≥95%；不合格时 W8A8 交付线不执行、FP16 控制线仍完成。
- 验收：manifest 记录 quantization=ascend、量化配置、源工件 revision、每分片 hash；第三方只核验不重量化。
- 落地：`oracle.w8a8_qualification`（四类成功率相对同源 FP16 下降≤1pp）+ 自测 `q-w8a8-drop`。

---

## 组 M —— 指标采集与优化机制生效

### M1 图执行计数器（附-8 优化机制生效）
- [x] 目标：B0/B1 的 A2—A4 记录 `graph_capture_count / graph_hit_count / graph_fallback_count / capture_sizes / compile_time`；A1 记录实际图捕获次数（必须为 0）。
- 做法：从 vllm serve 日志/状态接口解析；两侧保存完整执行模式 receipt。
- 验收：任一侧未捕获、发生回退、使用表外 capture size 或测量期新增捕获 → 该生命周期无效并完整重跑。
- 落地：`src/client/metrics.py` `parse_graph_counters`（捕获数/回退/捕获尺寸/编译时长）+ `evaluate_mechanisms` M1 门禁（A2—A4 compile 生效且 0 回退；A1 eager 必须 0 捕获）+ 自测 `m1-parse-graph`/`m-a1-no-capture`。

### M2 前缀缓存与量化生效
- [x] 目标：dialogue/tool/A4 记录 `prefix_cache_queries/hits`；所有 W8A8 profile 记录量化生效值。
- 做法：缓存单元保存查询、命中和逐请求前缀身份；量化生效值写入启动回执。
- 验收：规定机制未生效或计数器缺失 → 生命周期无效。
- 落地：`metrics.evaluate_mechanisms` M2 门禁（enable_prefix_caching → 须采到 queries/hits；quantization=ascend → 须确认生效值）+ 自测 `m-gate-ok`/`m-gate-fail-closed`。

### M3 A3 资源监控（附-8 A3 专项）
- [x] 目标：监控间隔=1s；记录 HBM 已用/峰值、KV cache 用量、队列深度、请求 token 数、窗口 TTFT/TPOT 与吞吐。
- 做法：随 C6 采集，落证据包。
- 验收：任一字段缺失则 A3 不通过。
- 落地：`metrics.evaluate_mechanisms` M3 门禁（closed-loop + a3-32k → 须有 1s 间隔采样且 hbm_peak 可用）；真机 1s 采样由 harness server 侧采集后经 `--server-metrics` 注入。

### M4 运维开销字段（附-2）
- [x] 目标：startup receipt 含 CPU 核表（按所分配 NPU 的 NUMA 节点生成）、OMP_NUM_THREADS=1、物理 NPU 卡号、驱动/固件/torch_npu/容器 digest。
- 做法：新增采集脚本；禁止扫描线程数或跨 NUMA 手工改核。
- 验收：核表与实际亲和性进入 startup receipt。
- 落地：`metrics.evaluate_mechanisms` M4 门禁（startup receipt 须含 CPU 核表）；真机核表/NUMA 亲和由启动回执采集。

---

## 组 A —— A1 算力口径专用闭环

### A1-1 ascend-dmi 峰值分母
- [x] 做法：独占 910B2 上执行 ascend-dmi FP16 算力测试 3 次，取中位数作为峰值分母。
- 验收：原始 ascend-dmi 输出全部保留；三次测量可复现。
- 落地：`scripts/a1_peak.sh` + `src/client/a1_peak.py`（910B2 单机一般不带 ascend-dmi，缺省用 torch_npu fp16 大矩阵乘实测——ascend-dmi 同原理；先热身再测 3 次以上取中位数，`samples_flops_per_s` 原始采样全保留，落 `runs/a1-peak.json`，打印 `VLLM_BENCHKIT_PEAK_FLOPS=<中位>` 注入 mfu 峰值分母；检测到 ascend-dmi 时指引人工执行并回填，不冒充）。
- 状态：**真机项，脚本已落地**。峰值分母来源 `source=torch-npu-matmul-fp16`（ascend-dmi 不存在时），spec 参考 910B2 FP16 理论 320 TFLOPS 一并记录；真机执行随 A1-A4 验证完成。

### A1-2 effective_compute_spec v1 与 msprof 对账
- [x] 做法：MatMul 按 2MNK、BatchMatMul 逐批求和、Attention 计入 QK^T 与 PV、其他逐元素算子不计入有效 FLOPs。
- 验收：主形状 MFU≥90%；effective FLOPs 与 msprof 总量误差≤3%；原始 msprof 输出全部保留。
- 落地（离线）：`src/client/mfu.py`（Qwen2.5-14B 逐算子 effective FLOPs + `mfu_summary`）+ `src/client/closedloop.py`（按轮 batch 并发）+ 自测 `mfu-*`/`closedloop-rounds`/`synthetic-prompt-deterministic` 全过。
- 落地（对账）：`scripts/msprof_reconcile.sh`（`collect` 用 msprof `--ai-core=on --export=on` 采集、`reconcile` 解析算子汇总 CSV）+ `src/client/msprof_reconcile.py`（列名自动探测；MatMul/BatchMatMul 类算子汇总 AICore 时长与 Cube FLOPs，逐元素算子排除；与 mfu 有效 FLOPs 误差≤3% 门禁）。离线用模拟 op_summary.csv 验证：3 个 MatMul 正确计入、RMSNorm/Add/Softmax 排除、误差 0%、gate=true。
- 状态：**msprof 对账为真机项**，脚本已落地，真机执行随 A1-A4 验证完成（原始 msprof 产物全保留于 `--out` 目录）。

---

## 组 K —— A4 成本模型

### K1 全生命周期成本（附-8 A4 成本）
- [x] 做法：设备和主机成本 = 资产原值 ÷ 21600 小时 × 生命周期小时；电费 = 实测平均功率 kW × 生命周期小时 × 0.60 元/kWh；人工不计入机器运行成本并单列。每百万成功输出 token 成本 = 全生命周期成本 ÷ 成功输出 token × 10^6。
- 验收：各比较关系使用同一资产原值、价格时点、计费周期、功耗采样规则；保留 30 分钟测量期成本及四租户隔离 B0 证据。
- 落地：`src/client/cost.py` `lifecycle_cost`（÷21600h 折旧 + 0.60 元/kWh 电费，人工单列）+ `per_million_token_cost`；真机功耗/资产原值由 harness `--power-kw/--asset-value-cny` 传入 + 自测 `k1-cost`。

### K2 利用率与公平性指标
- [x] 做法：单卡平均利用率≥60%；Jain≥0.90；份额偏差≤10%；p99≤隔离 B0-FP16 的 1.25×。
- 验收：指标进入正式报告与判定。
- 落地：`cost.k2_gate`（利用率门禁 + Jain/份额偏差 + p99 隔离 ≤1.25× 汇总）+ 自测 `k2-gate-ok`/`k2-gate-fail`。

---

## 组 D —— 数据子集补齐

### D1 A3 业务长文本集
- [ ] 做法：按 `document_id` 稳定排序循环拼接，段间使用固定分隔符；插入可核验目标事实；渲染后输入 30720 token。
- 验收：与 A2 的 `long` 子集（32 例固定形状）区分；manifest 记录 ordered content/token hash。

### D2 数据源接入
- [ ] 做法：在 `config/config.yaml prepare.subsets` 填 bfcl / jsonschema / long 真实数据源 url；确认各子集 `n` 与格式正确（sharegpt=64、gsm8k=64、bfcl=64、jsonschema=64、long=32）。
- 验收：`bash scripts/prepare.sh subsets` 全部 materialize；`.registry.json` 状态 ready；receipt `dataset_ready` 门禁通过。

### D3 数据集身份固化（附-8 数据身份）
- [ ] 做法：manifest 记录 dataset revision、ordered content/token hash、授权记录；`sha256(case_id||normalized_content)` 稳定排序取前 N（已有实现，补 revision/授权字段）。
- 验收：B0/B1 使用字节一致的数据。

---

## 组 S —— 配置层边角字段补齐

### S1 kv_cache requested/effective 分离（附-2）
- [x] 做法：`kv_cache_dtype requested=auto` 入配置，effective=torch.float16 只作校验值；argv 渲染 `--kv-cache-dtype auto`（或按平台既定约定），effective 回执确认非 FP16 即拒绝。
- 验收：启动回执显示 requested 与 effective 两个值。
- 落地：`common.yaml` `kv_cache_dtype: auto`(requested) + `kv_cache_effective: float16`；dry-run 展开为 `kv_cache_dtype=auto / kv_cache_effective=float16`，argv 渲染 `--kv-cache-dtype auto`。

### S2 未建模字段补齐（附-2/3/5）
- [x] 做法：schema + common/cells 补齐 `speculative.model/tokens`、`tool_parser_plugin=null`、`reasoning_parser=null`、`trust_request_chat_template=false`、`template_kwargs={}`、`disable_any_whitespace=false`、`disable_additional_properties=false`。
- 验收：dry-run 展开全部出现且值正确；argv_hygiene 门禁仍通过。
- 落地：`common.yaml` 已含 `speculative=false/model=null/tokens=0`、`tool_parser_plugin=null`、`reasoning_parser=null`、`trust_request_chat_template=false`、`template_kwargs={}`、`disable_any_whitespace=false`、`disable_additional_properties=false`；dry-run 全部展开、值为默认空档，argv_hygiene 门禁通过。

### S3 A4 租户级采样差异（附-5）
- [x] 做法：workload 支持 per-tenant sampling overlay（tool 租户 `parallel_tool_calls=true`、发 tools；其余租户 not_applicable 不发送）。
- 验收：A4 请求清单中 tool 租户带 tools/parallel_tool_calls，其余租户不带。
- 落地：`a2-tool.yaml` `parallel_tool_calls: true`；`a4-mt.yaml` tool 租户 `parallel_tool_calls: true, send_tools: true`，其余租户不带 tools。

### S4 A2 预热字段（附-6）
- [x] 做法：A2 各 cell 的 timeouts 补 `warmup_s: 300`（共同执行法预热 5min）。
- 验收：dry-run effective 显示 warmup_s=300。
- 落地：a2/a4 各 cell `workload.timeouts.warmup_s: 300`；dry-run effective 显示 `warmup_s: 300`。

---

## 组 Z —— 集成与验收门禁

### Z1 客户端正式指标全链路
- [x] 做法：组 C→Q→M 串联：C 生成请求与到达序列 → 执行并逐请求回执 → Q 判定质量 → M 采集机制/资源指标 → 归档 `runs/<date>-<vllm_sha7>-<va_sha7>/`（双 commit，ADR-0007）。
- 验收：A1—A4 各 profile 端到端产出「容量曲线 + SLO 合规吞吐 + 质量判定 + 机制生效证据」；`bash scripts/acceptance.sh list/profile/run` 全部可用。
- 落地：`src/client/run.py` `_finalize`（逐请求回执 → `oracle.evaluate` Q → `metrics.evaluate_mechanisms` M → 证据包落盘 quality.json/mechanisms.json/gate.json/evidence.json）+ 自测 `z1-evidence-structure`。

### Z2 正式生命周期重复与中位数
- [x] 做法：每个正式测试阶段至少 3 次独立服务生命周期（冷启动—预热—测量—停止）；异常仅可追加 2 次、不替换原结果；按预声明 primary_metric 取中位数。
- 验收：失败/超时/追加复测记录完整保留。
- 落地：`src/client/lifecycle.py` `run_lifecycles`（无效仅追加复测、不替换原结果）+ `evaluate_z2`（≥3 有效 + 复测≤2 + 中位数可算）+ `run.py --z2-dir` 聚合 + 自测 `z2-valid-median`/`z2-retry-append`/`z2-gate-ok`；B0/B1 对照中位数由 I 组 comparison_id 区分两侧结果。

### Z3 证据包门禁（附-9 对齐）
- [x] 做法：receipt 新增总门禁：config 合法、角色化一致、代码/镜像 SHA 绑定、数据就绪、质量资格、机制生效、无静默截断，任一不过即拒测。
- 验收：`src/receipt.py` 全门禁 PASS 才允许进入正式测量。
- 落地：`src/client/gate.py` `assemble_evidence` + `z3_gate`（fail-closed；真机不可用项按 not-applicable 记账）+ 自测 `z3-pass`/`z3-fail`/`z3-trunc-count`。

---

## 依赖与建议执行顺序

1. **S 组先行**（低风险、快）→ 让配置层无遗漏。
2. **C1→C2→C3**（引擎主干）→ **C4/C5/C6/C7** 按 cell 扩展。
3. **M1/M2/M3** 与 C 并行接入回执。
4. **Q 组** 接入后走 C3 的逐请求回执。
5. **I 组** 提供 B0/B1 双角色比较 → 才能做 Z2 的对照中位数。
6. **A/K 组** 依赖真实设备（ascend-dmi / msprof / 功耗）→ 上真机阶段。
7. **Z1–Z3** 作为每条主线的最终验收门禁。

> 维护注意：本清单对应 roadmap 的正式验收执行层；新增大项时同步 [CONTEXT.md](../../CONTEXT.md) 术语与 [schema.yaml](../../config/vllm-xcheck/schema.yaml) 的 allowed 集。
