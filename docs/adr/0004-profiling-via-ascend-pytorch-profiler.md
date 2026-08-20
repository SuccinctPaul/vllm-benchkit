# profiling 本期用 Ascend PyTorch Profiler（内置）

v0.18.0 黑盒阶段的 profiling 统一走华为内置的 Ascend PyTorch Profiler（`torch_npu.profiler`），由 vLLM 的 `--profiler-config.profiler=torch` 开启、`/start_profile` `stop_profile` 接口触发。

候选对比：内置 Ascend PyTorch Profiler 装即用/算子级/API 请求控制，适合定位算子与硬件瓶颈；MS Service Profiler 需另装 `msserviceprofiler`、面向服务框架工作流；msprobe 面向精度调试（NaN/漂移）。本期目标是"黑盒看算子性能"，故选最小链路的内置方案；后两者标记为 Todo，待拆解阶段再评估。

注意：vLLM 主线的 `VLLM_TORCH_PROFILER_DIR` 已废弃，v0.16 起改用 `--profiler-config` 参数。

待核实（P0）：`--profiler-config` 的合法传法有点号记法（`--profiler-config.profiler=torch`）与 JSON blob（`{"profiler":"torch",...}`，profile.sh 当前所用）两种表述，v0.18.0 实际接受哪一种须在装好环境后以 `vllm serve --help` / canonical 源码实测为准，并回填本文与 profile.sh，不臆造。