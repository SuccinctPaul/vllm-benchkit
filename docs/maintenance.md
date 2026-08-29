# 维护与原理（Maintenance）

> 本文回答两件**"为什么要信这套东西"**的事：**为什么这套文档能长期维护**、**为什么这套运行能复现**。答案一样：因为是**单一事实来源 + 明确同步规则 + 全链路留痕**。新手想"为什么文档没乱、运行时对得上、结果敢信"，读这篇就够。

## 0. 一句话

**让"文档"和"运行"都能维护，靠的不是自觉，而是"每个事实只允许一个权威出处 + 改动必须同步 + 全过程留痕"三件事。**

## 1. 为什么这套文档能维护：单一事实来源

最怕同一件事在好几处各写各的、改一处漏一处。所以一切"事实"都用一个权威出处，其他文档只做解释和导航、**不另立版本**：

| 事实 | 唯一权威出处 | 谁只能引用它 |
|------|-------------|-------------|
| 机器/仓库/路径/版本 | [config/topology.yaml](../config/topology.yaml)（已 .gitignore） | 所有文档的 `server/dir/repos` 键名 |
| 验收配置字段契约 | [config/vllm-xcheck/schema.yaml](../config/vllm-xcheck/schema.yaml) | config-reference.md 的字段表 |
| 验收任务明细与进度 | [acceptance/acceptance-tasks.md](./acceptance/acceptance-tasks.md) | features / acceptance-coverage / design 的状态 |
| 通用运行参数默认值 | [config/config.yaml](../config/config.yaml) | guide/config-reference.md |
| 为什么这么设计 | [adr/](./adr/)（0001–0012，只追加不改） | guide/design、acceptance/design 的"取舍" |
| 术语/目标/边界 | [CONTEXT.md](../CONTEXT.md) | 各文档引出处 |

**推论**：改配置先改 schema 白名单；改任务先改 acceptance-tasks；版本先改 topology。其余文档只做"指向"。

## 2. 同步规则：改一处，正确的事情被驱动

| 你改了 | 必须同步 | 否则会怎样 |
|--------|---------|-----------|
| `config/**/schema.yaml`（新增/改配置键） | 必须先入 `allowed` 白名单 | 展开时 fail-closed 拒绝，配置文件起不来 |
| `src/` 代码（模块/入口/命令） | 对应 [acceptance/design.md](./acceptance/design.md) 模块表、[guide/commands.md](./guide/commands.md) | 文档"文件映射"失真，读者找不到实现 |
| 运行方式（脚本/参数/产物） | [guide/how-to-run.md](./guide/how-to-run.md)、[acceptance/how-to-run.md](./acceptance/how-to-run.md)、根 [README.md](../README.md) | 新人按文档跑不出来 |
| 验收任务进度 | [acceptance-tasks.md](./acceptance/acceptance-tasks.md) 勾选 | acceptance-coverage / design 状态失真 |

**约定**：能合并的别拆成两篇（这次把 `design-overview` 合并进 `design` 正是此意）；一处说明、他处引用。

## 3. 为什么这套运行能复现：全链路留痕

"别人换台机器能对得上"的关键是：**每个影响结果的量都被记录，且值恒定**。

| 手段 | 保证什么 |
|------|---------|
| `seed=0` 固定 | 采样/到达序列/生成可重现（`client_seed`、`engine_seed`） |
| 确定性合成数据 + canonical JSON + SHA | 请求体字节可比，B0/B1 能逐字节对账 |
| 生效参数归档进 `receipt.json` | "跑的是哪个配置"有据可查，不含糊 |
| 双 commit 归档进 `runs/<date>-<vllm_sha7>-<va_sha7>/` + `manifest.yaml` | 代码版本锁定，可回溯（ADR-0007） |
| `runs/`、`runs/accepted/`、`profile_out/` 留痕 | 原始数据+判定都在，不靠记忆 |
| 独立生命周期 ≥3 次取中位数 | 抗单次抖动，异常只追加不替换（Z2） |

**fail-closed 的意义**：不是"尽量拦截"，而是让"有问题"根本进不了正式测量——配置表外字段、机制失效、静默截断一律拒绝，杜绝"假通过"，所以结果才敢信。

### 3.1 一个完整例子：证据链是怎么串起来的（接待一位新人问"verify19 凭什么算过"）

以 **A4-MT-FP16-PC verify19** 为例，讲清"源头 → 判定 → 留档"每一环，证明不是拍脑袋（逐项数值不在此重复，见 [acceptance-tasks.md 组 C5](./acceptance/acceptance-tasks.md#L69)，这里只讲"每一环靠什么证明它是真事"这条链）：

| 环 | 内容 | 落在哪 | 谁保证它是真事 |
|----|------|--------|---------------|
| ①配置 | `max_num_seqs`/`isolation_token_rate`/`seed=0` 等关键参数 | `config/vllm-xcheck/cells/a4-mt.yaml` + `schema.yaml` allowed 集 | schema 表外字段会被 `acceptance.expand()` 拒绝，配置起不来 |
| ②协议 | 确定的请求清单(B0/B1 角色) + 回执，body SHA 逐一记录 | `runs/accepted/.../receipts*.jsonl` + `contract.json` | canonical JSON + SHA，B0/B1 能逐字节对账 |
| ③判定 | 公平性(Jain≥0.90)、隔离(p99 比值<1.25×)、SLO 4/4、容量(正式负载=0.70×b0_max) | `slo.json`、`fairness/`、`isolation/`、`quality/` | oracle/gate.py 的判定逻辑，fail-closed，不够就拒 |
| ④证据 | Q/M/Z 门禁 + `server_metrics.json` + ≥3 生命周期中位数打包 | `runs/accepted/A4-MT-FP16-PC-verify19/` + `manifest.yaml` | 目录同时带 vllm/vllm-ascend 双 commit SHA，版本锁死 |

> 关键：**每一步都同时记「值」和「这是谁的判定/哪来的版本」**。别人拿到这个目录，能按 ①②③④ 反向对账到原始配置和代码版本，而不是只收到一句"通过了"。这就是"留痕"的意思。

## 4. 目录结构与导航（这次重组的目标）

```
docs/
├── README.md          ★唯一总入口：定位 + 新手路线 + 名词 + 系统地图
├── understand.md      ●这是什么：一根树干两档玩法、三层归属、三档工作、硬约束
├── run.md             ●怎么跑：装环境 → 冒烟 → 验收，一页串完
├── maintenance.md     ★（本文）为什么能维护＆运行：单一事实来源 + 同步规则 + 留痕
├── guide/             ▲黑盒工具细节：commands / config / output / how-to-run / design
├── acceptance/        ▲正式验收细节：features / design / how-to-run / config-ref / acceptance-coverage / tasks
├── adr/               决策记录 0001–0012（入口：adr/README.md；只追加）
├── official-capabilities.md   官方能力清单与可增补项
└── roadmap.md                未来路线图
```

`guide/` 与 `acceptance/` 是"同一条路上的两站"（冒烟→验收），由 `understand.md` 讲清关系、`run.md` 串起操作，而不是两个互不相干的目录。

## 5. 新手改动自查清单（防漏改）

- [ ] 新增/改了 YAML 键 → 进了 `schema.yaml` `allowed`？示例在 `config-reference.md` 同步？
- [ ] 改了模块/命令 → `design.md` 模块表 / `commands.md` 更新？文件映射对得上？
- [ ] 改了运行方式 → guide / acceptance 两个 how-to-run 和根 README 三处都同步？
- [ ] 提了验收结果 → `acceptance-tasks.md` 打了勾？状态文档（design/acceptance-coverage）引用它而非另写？
- [ ] 出现了机器名/仓库 URL/版本 → 是否只写在 `topology.yaml`？文档里换成键名了吗？

> ADR 约定：新决策记录追加到 [adr/](./adr/)，序号递增，正文只描述"背景/决策/后果"，不改历史。