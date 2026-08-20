#!/usr/bin/env bash
# 黑盒基准封装：wrap 官方 `vllm bench`（docs/adr/0001）。
# 参数默认值来自 config/config.yaml，同名环境变量优先（docs/adr/0005）。
# 子命令: serve | throughput | latency
# 归档：产物落 runs/<date>-<vllm_sha7>-<va_sha7>/ 并写 manifest.yaml（docs/adr/0007）。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # scripts/
ROOT="$(cd "$HERE/.." && pwd)"
VENV="${VLLM_NOTES_VENV:-$ROOT/.venv}"
VLLM="$VENV/bin/vllm"
[ -x "$VLLM" ] || { echo "[bench] 未找到 $VENV/bin/vllm，先 uv sync" >&2; exit 2; }

source "$HERE/npu_env.sh"

MODE="${1:-}"

# 载入 config.yaml 默认值（YAML_* 命名空间）；环境变量优先于 YAML，见 ADR-0005
YAML_ENV="$("$VENV/bin/python" "$ROOT/src/getconf.py" "${VLLM_NOTES_CONFIG:-$ROOT/config/config.yaml}")"
eval "$YAML_ENV"

: "${MODEL:=${YAML_MODEL:-}}"
: "${ASCEND_RT_VISIBLE_DEVICES:=${YAML_DEVICES:-}}"
# 在线基准
: "${BENCH_BACKEND:=${YAML_BENCH_BACKEND:-}}"
: "${BENCH_ENDPOINT:=${YAML_BENCH_ENDPOINT:-}}"
: "${BENCH_BASE_URL:=${YAML_BENCH_BASE_URL:-}}"
: "${BENCH_NUM_PROMPTS:=${YAML_BENCH_NUM_PROMPTS:-}}"
: "${BENCH_REQUEST_RATE:=${YAML_BENCH_REQUEST_RATE:-}}"
: "${BENCH_DATASET:=${YAML_BENCH_DATASET:-}}"
: "${BENCH_DATASET_PATH:=${YAML_BENCH_DATASET_PATH:-}}"
: "${BENCH_LOAD_FORMAT:=${YAML_BENCH_LOAD_FORMAT:-}}"
# 离线基准
: "${BENCH_INPUT_LEN:=${YAML_BENCH_INPUT_LEN:-}}"
: "${BENCH_OUTPUT_LEN:=${YAML_BENCH_OUTPUT_LEN:-}}"
: "${BENCH_IGNORE_EOS:=${YAML_BENCH_IGNORE_EOS:-}}"
: "${BENCH_BATCH_SIZE:=${YAML_BENCH_BATCH_SIZE:-}}"
: "${BENCH_GPU_UTIL:=${YAML_BENCH_GPU_UTIL:-}}"
: "${BENCH_MAX_MODEL_LEN:=${YAML_BENCH_MAX_MODEL_LEN:-}}"

# --- 双 commit 归档（ADR-0007）：目录名带 vllm / vllm-ascend 短哈希，同目录写 manifest.yaml ---
# 单一 .venv 串行：并发跑会互踩归档目录；本地无拓扑/无仓库时回退平铺 runs/。
RUNS_BASE="${VLLM_NOTES_RUNS:-$ROOT/runs}"
DIR=""
if eval "$("$VENV/bin/python" "$ROOT/src/gettopo.py" "${VLLM_NOTES_TOPOLOGY:-$ROOT/config/topology.yaml}")" 2>/dev/null; then
  DIR="${VLLM_NOTES_DIR:-${TOPO_DIR:-}}"
fi
vllm_sha7="$(git -C "$DIR/vllm" rev-parse --short HEAD 2>/dev/null || true)"
va_sha7="$(git -C "$DIR/vllm-ascend" rev-parse --short HEAD 2>/dev/null || true)"
if [ -n "$vllm_sha7" ] && [ -n "$va_sha7" ]; then
  RUNS_DIR="$RUNS_BASE/$(date +%Y%m%d-%H%M%S)-${vllm_sha7}-${va_sha7}"
  LOG="$RUNS_DIR/$MODE.log"
else
  RUNS_DIR="$RUNS_BASE"
  LOG="$RUNS_DIR/$(date +%Y%m%d-%H%M%S)-$MODE.log"
fi
mkdir -p "$RUNS_DIR"

# 归档模式写 manifest.yaml：两仓完整哈希 + 生效参数快照（getconf 输出）
if [ -n "$vllm_sha7" ] && [ -n "$va_sha7" ]; then
  {
    printf 'mode: %s\ndate: %s\nhostname: %s\n' "$MODE" "$(date "+%Y-%m-%dT%H:%M:%S%z")" "$(hostname)"
    printf 'vllm_commit: %s\nvllm_ascend_commit: %s\n' \
      "$(git -C "$DIR/vllm" rev-parse HEAD 2>/dev/null || true)" \
      "$(git -C "$DIR/vllm-ascend" rev-parse HEAD 2>/dev/null || true)"
    printf 'params_snapshot:\n'
    "$VENV/bin/python" "$ROOT/src/getconf.py" "${VLLM_NOTES_CONFIG:-$ROOT/config/config.yaml}" | sed 's/^/  /'
  } > "$RUNS_DIR/manifest.yaml"
fi

serve() {
  local -a cmd=( "$VLLM" bench serve --backend "$BENCH_BACKEND" \
    --dataset-name "$BENCH_DATASET" )
  if [ "$BENCH_BACKEND" = openai ]; then
    # openai 后端：请求发到外部已启动的 vllm server（$BENCH_BASE_URL），不带 --model
    cmd+=( --base-url "$BENCH_BASE_URL" )
  else
    # vllm 内联后端：引擎内联在 bench 进程里，不接受引擎级 args（见下方注释）
    cmd+=( --model "$MODEL" )
  fi
  [ -n "$BENCH_DATASET_PATH" ] && cmd+=( --dataset-path "$BENCH_DATASET_PATH" )
  cmd+=( --num-prompts "$BENCH_NUM_PROMPTS" --request-rate "$BENCH_REQUEST_RATE" \
    --save-result --result-dir "$RUNS_DIR" )
  # 注意：v0.18.0 的 `bench serve`（内联 --backend vllm）不接受引擎级 args
  # （--load-format/--gpu-memory-utilization/--max-model-len 均报 unrecognized）。
  # 结果 JSON 的 latency_stats 里含 TTFT/ITL/TPOT/E2EL。
  echo "[bench] $ ${cmd[*]}"
  "${cmd[@]}" 2>&1 | tee "$LOG"
}

throughput() {
  local -a cmd=( "$VLLM" bench throughput --model "$MODEL" \
    --dataset-name "$BENCH_DATASET" )
  if [ "$BENCH_DATASET" = random ]; then
    cmd+=( --random-input-len "$BENCH_INPUT_LEN" --random-output-len "$BENCH_OUTPUT_LEN" )
  else
    cmd+=( --input-len "$BENCH_INPUT_LEN" --output-len "$BENCH_OUTPUT_LEN" )
  fi
  [ -n "$BENCH_DATASET_PATH" ] && cmd+=( --dataset-path "$BENCH_DATASET_PATH" )
  [ -n "$BENCH_LOAD_FORMAT" ] && cmd+=( --load-format "$BENCH_LOAD_FORMAT" )
  cmd+=( --output-json "$LOG.json" )   # v0.18.0 移除了 --save-result/--result-dir，改用 --output-json
  echo "[bench] $ ${cmd[*]}"
  "${cmd[@]}" 2>&1 | tee "$LOG"
}

latency() {
  # v0.18.0 的 bench latency 只认这些 flag，结果打印到 stdout 表格（TTFT/ITL/TPOT/E2EL），无 --save-result。
  local -a cmd=( "$VLLM" bench latency --model "$MODEL" \
    --input-len "$BENCH_INPUT_LEN" --output-len "$BENCH_OUTPUT_LEN" \
    --batch-size "$BENCH_BATCH_SIZE" )
  [ -n "$BENCH_LOAD_FORMAT" ] && cmd+=( --load-format "$BENCH_LOAD_FORMAT" )
  [ -n "$BENCH_GPU_UTIL" ] && cmd+=( --gpu-memory-utilization "$BENCH_GPU_UTIL" )
  [ -n "$BENCH_MAX_MODEL_LEN" ] && cmd+=( --max-model-len "$BENCH_MAX_MODEL_LEN" )
  echo "[bench] $ ${cmd[*]}"
  "${cmd[@]}" 2>&1 | tee "$LOG"
}

case "$MODE" in
  serve|throughput|latency) "$MODE" ;;
  *) echo "用法: $0 {serve|throughput|latency}" >&2; exit 1 ;;
esac
