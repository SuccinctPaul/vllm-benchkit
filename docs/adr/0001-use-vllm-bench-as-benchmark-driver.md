# 用 vLLM core 的 `vllm bench` 作为基准驱动

黑盒阶段统一使用 vLLM core 提供的 `vllm bench serve/throughput` 作为唯一基准驱动，而不是 vllm-ascend 自带工具或任何自造引擎。

我们从两个候选里选了它：
A) vLLM core 的 `vllm bench`；
B) vllm-ascend 自家 `tools/vllm_bench.py`。

选 A 因为它最贴近上游、被持续维护、能一站式出指标、且 profiling 可挂在同一个 serve 进程上；
B 是插件生态内工具，远离 vLLM 上游，参数/能力可能落后。官方 `performance_benchmark.md` 也明确"为保持与 vLLM 对齐，使用 vllm 项目提供的 benchmark 脚本"。

不引入任何自造 benchmark 引擎——脚本只 wrap 官方命令。