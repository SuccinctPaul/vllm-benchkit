# 怎么跑（Run）

> 本文回答「**这个仓库怎么用起来**」：先冒烟，再验收，一条路串到底。细节在各自入口。

## 快速理解：三层顺序

```
装好环境 → 冒烟（黑盒bench/profile） → 验收（A1–A4 正式测量）
  run#1        run#2                    run#3
```

- **#1/ #2** 是随时可跑的基础玩法，用在日常验证、版本对比、定位瓶颈（见 [guide/](./guide/README.md)）。
- **#3** 是严格验收，跑验收方案的 A1–A4，判通过与留证据（见 [acceptance/](./acceptance/README.md)）。

## 1. 前置与安装（一次性）

| 前置 | 说明 |
|------|------|
| 机器/硬件 | Atlas 910B2，见 `config/topology.yaml` |
| CANN | 9.0.0 |
| 代码源 | topology 钉的 `vllm`/`vllm-ascend` @ `releases/v0.18.0` |
| 环境 | uv 建的 `.venv` |

安装（两条玩法共用同一套环境；全新裸机先补基础工具链，再装仓库依赖）：

> **先决（必须一次）**：`config/topology.yaml` 被 `.gitignore`，**刚 clone 下来是不存在的**。第一次布置前先拷模板并填真实目标机信息：
>
> ```bash
> cp config/topology.example.yaml config/topology.yaml
> # 然后编辑 config/topology.yaml，把 server / dir / repos 三项换成你的真机值
> ```

```bash
# ① 补基础工具链（git/编译/python3.11/uv + 检测 CANN；清单见 guide/how-to-run.md §1.0）
./scripts/bootstrap.sh check   # 只读看还缺什么
./scripts/bootstrap.sh         # 补齐（需 root 装系统包；CANN 仍须手工装）

# ② 按 topology 拉/装双仓库并 editable 装到 .venv
./scripts/deploy.sh install

# ③ 读 pyproject：torch / torch-npu / vllm / vllm-ascend
uv sync
```

## 2. 冒烟（黑盒 bench / profile，随时可跑）

```bash
# 离线吞吐（random 数据集，零下载）—— 推荐先跑做 smoke
./scripts/bench.sh throughput
# 应看到：退出码 0；stdout 有吞吐(Throughput tokens/s > 0)；产物落 runs/<date>-<sha>-<sha>/，同目录生成 manifest.yaml

# 在线基准（默认 random）
./scripts/bench.sh serve
# 应看到：退出码 0；stdout 输出 TTFT/ITL/TPOT/E2EL 的 mean/分位值（有限非 NaN）；同归档目录生成 serve 的 .json 结果

# profiling：终端1 serve → 终端2 start/stop → analyse
./scripts/profile.sh serve
./scripts/profile.sh stop
./scripts/profile.sh analyse
# 应看到：terminal1 serve 起来后，profile_out/ 下出现 <名>_ascend_pt 采集目录；
#          analyse 后 stdout 打印出算子耗时表（能找到 kernel 明细），无 "empty profile" 报错
```

命令怎么选、参数怎么配、结果怎么读：见 [guide/how-to-run.md](./guide/how-to-run.md)、[guide/commands.md](./guide/commands.md)、[guide/output.md](./guide/output.md)。

## 3. 正式验收（acceptance，A1–A4）

起独立的 vLLM 服务，用客户端引擎按验收方案跑测量、判门禁、归档证据。完整操作（部署/数据子集/起服务/跑客户端/收指标/停服务/离线自测/常见坑）见 [acceptance/how-to-run.md](./acceptance/how-to-run.md)，这里只讲 A4 的完整流程图，其余 cell 类似。

> **跑之前先花 30 秒看 [acceptance/how-to-run.md §8 常见坑](./acceptance/how-to-run.md#L107)**——里面有两个不踩必翻车：`--model-ref` 要带组织前缀（`Qwen/...`）才能命中离线缓存；serve 不接受 `--compile-mode`/`--cudagraph-mode`（设 `VLLM_BENCHKIT_SKIP_GRAPH_ARGS=1`）。

**A4 判定逻辑**（其余 cell 类似）：`scan` 多点容量扫描筛 SLO 合规 → 取三次中位 `b0_max` → 正式负载 = `0.70 × b0_max` → `warmup 5min + measure 30min` → `isolation`（每租户 solo 满载做隔离基线）。证据过 Q/M/Z 门禁全过才留档；每正式阶段 ≥3 次独立生命周期取中位数（异常只追加、不替换）。

**为什么一次正式测量要挺久** —— A4 的一次完整流程长这样（其余 cell 阶段类似，只是没有 isolation 与多租户）：

```
①容量扫描 scan              ②取 b0_max    ③正式负载               ④隔离基线          ⑤归档
每点 600s、点间停 60s   ──▶  3 次中位  ──▶  warmup 5min  ──▶  每租户 solo 满载 ──▶  过 Q/M/Z 门禁
升序扫多个 rate 点               得到       measure 30min      p99 隔离量          才留 runs/…
（B0 混合扫描）
```

一整轮下来要**数小时**（扫描是多个 600s 点 + 3 次生命周期重复），所以很少"随时改随时验"，日常直接跑离线自测或单点 smoke 更快。

> 具体命令块与每个 profile 的配置参数：见 [acceptance/how-to-run.md](./acceptance/how-to-run.md)、[acceptance/config-reference.md](./acceptance/config-reference.md)。

## 4. 结果去哪看、靠不靠得住

所有运行产物都**留痕**（`runs/`、`runs/accepted/`），"为什么能复现/为什么能维护"是这套东西的立足点，详见 [maintenance.md](./maintenance.md)。