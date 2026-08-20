# 参数外置到 config/config.yaml，环境变量优先

bench.sh / profile.sh 的参数默认值统一收口到 `config/config.yaml`，脚本统一放在 `scripts/`，读取采用「环境变量 > YAML 默认」的优先级；解析用 venv 里的 python + pyyaml。

背景：参数从脚本内散落的环境变量膨胀（bench 8 个、profile 5 个 + 一个硬编码的 profiler JSON），不同环境/版本要改默认值只能改脚本。为满足「不同环境、版本可用默认配置」，把默认值外置到单一 YAML，并把脚本与配置分目录组织（`scripts/` 与 `config/`）。

候选对比：单文件 config.yaml + 环境变量覆盖（所选）；多 profile / 分层多文件（结构重、合并逻辑复杂）；CLI `--set` 覆盖（多一层 CLI 解析）；外部 yq（额外安装）；纯 bash grep 解析（脆弱、不支持嵌套）。选最薄的一种：YAML 只承载默认值，临时覆盖仍走环境变量（含 `VLLM_NOTES_CONFIG` 指定配置路径、`VLLM_NOTES_RUNS` 指定 runs 目录、`ASCEND_TOOLKIT_SETENV` 等覆盖 CANN 环境路径），不引入 CLI 配置子命令。

后果：默认值单点维护于 `config/config.yaml`；新增参数需同时改 config.yaml 与脚本内对应的 `: "${KEY:=${YAML_...:-}}"` 行；YAML 缺键时脚本变量为空、vllm 显式报错（不静默兜底）。
