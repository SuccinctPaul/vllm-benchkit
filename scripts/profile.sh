#!/usr/bin/env bash
# 黑盒 profiling 封装：Ascend PyTorch Profiler，由 vLLM --profiler-config 编排（docs/adr/0004）。
# 参数默认值来自 config/config.yaml，同名环境变量优先（docs/adr/0005）。
# 子命令: serve | start | stop | analyse
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # scripts/
ROOT="$(cd "$HERE/.." && pwd)"
VENV="${VLLM_NOTES_VENV:-$ROOT/.venv}"
PY="$VENV/bin/python"
[ -x "$PY" ] || { echo "[profile] 未找到 $VENV/bin/python，先 uv sync" >&2; exit 2; }

source "$HERE/npu_env.sh"

MODE="${1:-}"

# 载入 config.yaml 默认值（YAML_* 命名空间）；环境变量优先于 YAML，见 ADR-0005
YAML_ENV="$("$PY" "$ROOT/src/getconf.py" "${VLLM_NOTES_CONFIG:-$ROOT/config/config.yaml}")"
eval "$YAML_ENV"

: "${MODEL:=${YAML_MODEL:-}}"
: "${ASCEND_RT_VISIBLE_DEVICES:=${YAML_DEVICES:-}}"
: "${PORT:=${YAML_PROFILE_PORT:-}}"
: "${PROF_DIR:=$ROOT/${YAML_PROFILE_PROF_DIR:-profile_out}}"
: "${PROF_WITH_STACK:=${YAML_PROFILE_WITH_STACK:-false}}"

RUNS_DIR="${VLLM_NOTES_RUNS:-$ROOT/runs}"
LOG="$RUNS_DIR/$(date +%Y%m%d-%H%M%S)-$MODE.log"
mkdir -p "$RUNS_DIR"

PROF_CFG="$(printf '{"profiler":"torch","torch_profiler_dir":"%s","torch_profiler_with_stack":%s}' \
  "$PROF_DIR" "$PROF_WITH_STACK")"

serve()   { "$PY" -m vllm.entrypoints.openai.api_server \
              --port "$PORT" --model "$MODEL" --profiler-config "$PROF_CFG" 2>&1 | tee "$LOG"; }
start()   { curl -fsS -X POST "localhost:$PORT/start_profile"; echo; }
stop()    { curl -fsS -X POST "localhost:$PORT/stop_profile"; echo; }
analyse() { "$PY" - "$PROF_DIR" <<'EOF' 2>&1 | tee -a "$LOG"
import sys
from torch_npu.profiler.profiler import analyse
analyse(sys.argv[1] + "/*_ascend_pt")
EOF
}

case "$MODE" in
  serve|start|stop|analyse) "$MODE" ;;
  *) echo "用法: $0 {serve|start|stop|analyse}" >&2; exit 1 ;;
esac
