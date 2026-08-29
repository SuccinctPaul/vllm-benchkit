# 0011 真机运行硬约束收口（SKIP_GRAPH_ARGS / engine_seed / xgrammar / --model-ref）

> **决策摘要**：把 4 条"在真机上不守必翻车"的运行级硬约束收口成唯一权威，避免它们只散落在 how-to-run 的"常见坑"里、不能形成决策记录。

| 元数据 | 值 |
|--------|----|
| **Status** | accepted |
| **Date** | 2026-08-25 |
| **Type** | standard / operational |
| **Supersedes** | — |
| **Related** | ADR-0002（环境版本）、ADR-0008（确定性四原则）、ADR-0009（软参数校准） |
| **映射** | 表附-2（`engine_seed=0`）、表附-3（`structured_outputs_backend=xgrammar`） |

## 背景与为什么（Context）

验收链路有 4 条约束**无法从《标准交付测试方案》推导、也不是设计取舍，而是 ascend build 的 v0.18.0 serve + 离线缓存的真机事实**。它们此前只写在 [how-to-run §8 常见坑](../acceptance/how-to-run.md#L107) 与 `config/vllm-xcheck/common.yaml` 注释里，没有决策身份；一旦被误改，会直接导致"服务起不来"或"结果不可复现""判定作弊"。故单独成 ADR，作为这些守则的唯一权威。

## 四条硬约束（决策 / Decision）

1. **`--model-ref` 必须带组织前缀**：模型用 `Qwen/Qwen2.5-14B-Instruct` 这类 `组织/模型` 形式，才能命中离线缓存（`--model model_ref`，见 [src/acceptance.py](../../src/acceptance.py) `model_server_argv`）。只写裸名会匹配不到离线缓存，加载失败。
2. **serve 不接受图参数**：ascend build 的 serve 无 `--compile-mode`/`--cudagraph-mode`，启动时设 `VLLM_BENCHKIT_SKIP_GRAPH_ARGS=1` 让装配跳过这两个 flag（见 [src/acceptance.py](../../src/acceptance.py) `smoke 豁免` 段）；否则命令行报错。
3. **`engine_seed` 必须显式 =0**：随机种子经 CLI 显式下发（`--seed 0`），防随机抖动，保证两次测量可对账（见 [src/acceptance.py](../../src/acceptance.py)）。
4. **`structured_outputs_backend` 必须 `xgrammar`（禁止 auto）**：A2-STRUCT 逐 case 发 JSON Schema，`auto` 会挑一个不稳定的后端、结果不可复现也判不了 schema 合法率；显式钉 `xgrammar` 才能批量判（见 [config/vllm-xcheck/common.yaml](../../config/vllm-xcheck/common.yaml)）。

## 与既有 ADR 的关系（Related / Lexicon）

- 第 3、4 条服务于 ADR-0008 的"确定性优先"与 fail-closed，且落在 `schema.yaml` allowed 集（`engine_seed`、`structured_outputs_backend`）。
- 第 1、2 条是真机环境事实（离线缓存匹配、ascend build flag 缺失），与 ADR-0002 的版本 pin 绑定：换 ascend 版本时需复核这几条是否仍成立。
- `max_num_seqs`/`capture_sizes`/`isolation_token_rate` 等软参数虽也是"真机调出来"，但已由 ADR-0009 专档，不在此重复。

## 候选对比（Alternatives）

- 只当"how-to-run 常见坑"：可被误当约定俗成，且多处漂移；不成决策身份 —— 否。
- 并入 ADR-0008 后果：0008 是设计原则，混入真机操作守则会稀释主题 —— 否。
- **独立成 ADR**：给运行守则一个决策身份、一个验证回填处 —— 选此方案。

## 后果（Consequences）

- 新增/调整任一硬约束，先改本 ADR 与 `config/vllm-xcheck/common.yaml`，并同步 [how-to-run §8](../acceptance/how-to-run.md#L107) 的"常见坑"作为操作指引。
- 换 ascend/vllm 版本时，`--model-ref` 前缀行为、`--compile-mode` 支持、`structured_outputs_backend` 缺省值都要按新版本复核后回填本文。

## 兑现回填（Verification）

- ✅ **已落地**：四条约束均已写入 `config/vllm-xcheck/common.yaml`（`engine_seed: 0`、`structured_outputs_backend: xgrammar`）与 [src/acceptance.py](../../src/acceptance.py)（`SKIP_GRAPH_ARGS` 豁免、`--seed` 下发、`--model` 用 `model_ref`），并在 [how-to-run §8](../acceptance/how-to-run.md#L107) 标注为"不踩必翻车"。
- 待补：真机完整跑一轮后，回填 `engine_seed=0` 是否真的渲染进 serve argv（见 [acceptance-coverage](../acceptance/acceptance-coverage.md) 的"正式测量前待补"）。