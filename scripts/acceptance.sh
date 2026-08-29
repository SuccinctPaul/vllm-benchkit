#!/usr/bin/env bash
# vllm-xcheck 验收 harness（L5）：
# 消费 config/vllm-xcheck 配置（经 src/acceptance.py 展开 profile），编排
#   "独立 vllm serve + 客户端基准"，并把 argv/env/effective 归档为 receipt。
# 依赖：src/acceptance.py；在线(client)客户端复用 scripts/bench.sh（openai 后端）。
# 子命令:
#   list                      枚举全部 15 个正式 profile
#   profile <cell> <PREC>     打印单 profile 的 argv/env/effective（--dry-run）
#   server  <cell> <PREC> [--port P] [--model-ref M]   启动独立 vllm serve，等待健康
#   client  <cell> <PREC> [--base-url U] [--num-prompts N] [--request-rate R]  跑客户端基准
#   run     <cell> <PREC> [同 client 参数]              server + client + 收尾(一次性 smoke)
#   stop    <cell> <PREC>                              停掉该 profile 的服务
#   metrics <cell> <PREC> [--pid PID] [--serve-log LOG] [--metrics-url U]
#           [--npu-log JSONL] [--out OUT]              聚合 M1/M2/M3/M4 → server_metrics.json
# PREC ∈ FP16 | W8A8；A1 只允许 FP16。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # scripts/
ROOT="$(cd "$HERE/.." && pwd)"
VENV="${VLLM_BENCHKIT_VENV:-$ROOT/.venv}"
VLLM="$VENV/bin/vllm"
PY="$VENV/bin/python"
[ -x "$PY" ] || { echo "[acceptance] 未找到 $VENV/bin/python，先 uv sync" >&2; exit 2; }
source "$HERE/npu_env.sh"

require_vllm() {   # list/profile 只需 python+yaml；真正起服务/跑客户端才需要 vllm 二进制
  [ -x "$VLLM" ] || { echo "[acceptance] 未找到 $VENV/bin/vllm，先 uv sync" >&2; exit 2; }
}

ACCEPT="$PY $ROOT/src/acceptance.py"
ACCEPTED_BASE="${VLLM_BENCHKIT_ACCEPTED:-$ROOT/runs/accepted}"
PID_DIR="${VLLM_BENCHKIT_PID_DIR:-$ROOT/runs/accepted/.pids}"

# 默认值（可被环境变量覆盖；与 bench.sh 同 ADR-0005 习惯）
: "${PORT:=8010}"
: "${NUM_PROMPTS:=16}"
: "${REQUEST_RATE:=1}"
: "${MODEL_REF:=}"
DATASETS_DIR="${VLLM_BENCHKIT_DATASETS:-$ROOT/datasets}"
# 0=fail-closed（缺工件即拒绝）；1=smoke 豁免缺工件（其余门禁仍强制）
ALLOW_MISSING_DATASETS="${ALLOW_MISSING_DATASETS:-0}"

usage() { echo "用法: $0 {list|profile|server|client|run|stop|metrics} [cell] [PREC] [--port ...]" >&2; exit 1; }

# 解析 profile 元信息（profile/api/endpoint）
profile_info() {
  local cell="$1" prec="$2"
  local info
  info="$($ACCEPT --cell "$cell" --precision "$prec")"
  PROFILE_ID="$(printf '%s\n' "$info" | awk -F': ' '/^profile/{print $2; exit}')"
  API="$(printf '%s\n' "$info" | awk -F': ' '/^api/{print $2; exit}')"
  ENDPOINT="$(printf '%s\n' "$info" | awk -F': ' '/^endpoint/{print $2; exit}')"
  [ -n "$PROFILE_ID" ] && [ -n "$API" ] || { echo "[acceptance] 无法解析 $cell/$prec" >&2; exit 1; }
}

run_dir() {   # 每 profile 一个归档目录（同一 profile 重复 run 递增后缀）
  local base="$ACCEPTED_BASE/$PROFILE_ID"
  local d="$base-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$d"
  RUNS="$d"
}

# P3 fail-closed 门禁（src/receipt.py）：任一不变式不过即不启动 server。
run_gate() {
  local cell="$1" prec="$2"
  local -a rcmd=( --cell "$cell" --precision "$prec" --datasets-dir "$DATASETS_DIR" )
  [ "$ALLOW_MISSING_DATASETS" = 1 ] && rcmd+=( --allow-missing-datasets )
  rcmd+=( --out "$RUNS/receipt.json" )
  if ! "$PY" "$ROOT/src/receipt.py" "${rcmd[@]}"; then
    echo "[acceptance] 门禁未通过，已中止（见 $RUNS/receipt.json）；" \
         "可设 ALLOW_MISSING_DATASETS=1 仅豁免缺工件，其余门禁仍强制" >&2
    return 1
  fi
  echo "[acceptance] 生效值已固化 -> $RUNS/receipt.json"
}

start_server() {
  local cell="$1" prec="$2" port="${PORT}" model_ref="${MODEL_REF:-}"
  require_vllm
  profile_info "$cell" "$prec"
  run_dir
  run_gate "$cell" "$prec" || return $?
  eval "$($ACCEPT --cell "$cell" --precision "$prec" --env)"
  [ -n "$ASCEND_VISIBLE_DEVICES" ] && : "${ASCEND_RT_VISIBLE_DEVICES:=$ASCEND_VISIBLE_DEVICES}"
  local -a argcmd=( --cell "$cell" --precision "$prec" --argv )
  [ -n "$model_ref" ] && argcmd+=( --model-ref "$model_ref" )
  local args
  args="$("$PY" "$ROOT/src/acceptance.py" "${argcmd[@]}")"
  args="${args#vllm serve }"          # 去掉命令名
  mkdir -p "$PID_DIR"
  echo "[acceptance] 启动 $PROFILE_ID @ 127.0.0.1:$port"
  # 重要：用 openai 后端 + 独立 server 才能接受引擎级 args（见 project_memory「serve 在线基准」）
  nohup "$VLLM" serve $args --port "$port" > "$RUNS/serve.log" 2>&1 &
  local pid=$!
  echo "$pid" > "$RUNS/server.pid"
  echo "$pid" > "$PID_DIR/$PROFILE_ID.pid"
  echo "[acceptance] server pid=$pid -> $RUNS"
  health_wait "127.0.0.1:$port" "$RUNS/serve.log" || {
    echo "[acceptance] 服务未就绪，见 $RUNS/serve.log" >&2; return 1; }
  { $ACCEPT --cell "$cell" --precision "$prec" --dry-run; } > "$RUNS/effective.txt"
}

health_wait() {
  local host_port="$1" log="$2" t=0
  until curl -sf --noproxy '*' "http://$host_port/v1/models" >/dev/null 2>&1; do
    if { [ "$t" -ge "${STARTUP_TIMEOUT:=600}" ]; } || ! kill -0 "$(cat "$RUNS/server.pid")" 2>/dev/null; then
      return 1
    fi
    sleep 2; t=$((t+2))
  done
  echo "[acceptance] 服务健康就绪（${t}s）"
}

stop_server() {
  local cell="$1" prec="$2"
  profile_info "$cell" "$prec"
  local pidfile="$PID_DIR/$PROFILE_ID.pid"
  if [ ! -f "$pidfile" ] || ! kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "[acceptance] $PROFILE_ID 未在运行（无 $pidfile）"
    return 0
  fi
  local pid t
  pid="$(cat "$pidfile")"
  echo "[acceptance] 停止 $PROFILE_ID (pid=$pid)"
  pkill -TERM -P "$pid" 2>/dev/null || true     # 先释放子进程（EngineCore 等共享 NPU worker）
  kill -TERM "$pid" 2>/dev/null || true          # 优雅停；SIGTERM 可能被 serve 拦截/延迟
  t=0
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$t" -ge "${STOP_TIMEOUT:=15}" ]; then  # SIGTERM 未退出则升级 SIGKILL，杜绝残留占 NPU
      echo "[acceptance] $PROFILE_ID pid=$pid SIGTERM 未退出，升级 SIGKILL" >&2
      pkill -KILL -P "$pid" 2>/dev/null || true
      kill -KILL "$pid" 2>/dev/null || true
      break
    fi
    sleep 1; t=$((t+1))
  done
  rm -f "$pidfile"
  return 0
}

run_client() {
  local cell="$1" prec="$2" base_url="${BASE_URL:-}" num="${NUM_PROMPTS}" rate="${REQUEST_RATE}"
  require_vllm
  profile_info "$cell" "$prec"
  export BENCH_BASE_URL="$base_url"
  export BENCH_NUM_PROMPTS="$num"
  export BENCH_REQUEST_RATE="$rate"
  # 真机代理坑（project_memory）：保留 http_proxy 供下载，但 client 打 localhost 必须绕过代理
  export no_proxy="${no_proxy:-},127.0.0.1,localhost"
  export NO_PROXY="${NO_PROXY:-},127.0.0.1,localhost"
  # A1 离线走 bench.sh throughput（不经 serve 侧 argv），须把 VLLM_BENCHKIT_GPU_MEM_UTIL
  # 传导为 BENCH_GPU_UTIL（export 才能传给子进程），否则 bench 默认 gpu_util=0.9
  # 在共享机显存紧张时 OOM；显式设置的 BENCH_GPU_UTIL 优先（仅离线下行有用）。
  export BENCH_GPU_UTIL="${BENCH_GPU_UTIL:-${VLLM_BENCHKIT_GPU_MEM_UTIL:-}}"
  if [ "$API" = completions ]; then
    # A1 离线（MFU）：engine 内联，无需外部 server
    echo "[acceptance] A1 离线基准：$PROFILE_ID"
    "$HERE/bench.sh" throughput
  else
    export BENCH_BACKEND=openai
    export BENCH_DATASET=random
    [ -n "$base_url" ] || { echo "[acceptance] 需要 --base-url（openai 后端）" >&2; exit 1; }
    echo "[acceptance] 在线基准（openai 后端）：$PROFILE_ID"
    "$HERE/bench.sh" serve
  fi
}

case "${1:-}" in
  list)  "$PY" "$ROOT/src/acceptance.py" --list ;;
  profile)  [ $# -ge 3 ]; "$PY" "$ROOT/src/acceptance.py" --cell "$2" --precision "$3" --dry-run ;;
  server)   shift; cell="${1:?}"; prec="${2:?FP16}"; shift 2
            while [ $# -gt 0 ]; do case "$1" in --port) PORT="$2"; shift 2;; --model-ref) MODEL_REF="$2"; shift 2;; *) shift;; esac; done
            start_server "$cell" "$prec" ;;
  client)   shift; cell="${1:?}"; prec="${2:?FP16}"; shift 2
            while [ $# -gt 0 ]; do case "$1" in --base-url) BASE_URL="$2"; shift 2;; --num-prompts) NUM_PROMPTS="$2"; shift 2;; --request-rate) REQUEST_RATE="$2"; shift 2;; *) shift;; esac; done
            run_client "$cell" "$prec" ;;
  run)      shift; cell="${1:?}"; prec="${2:?FP16}"; shift 2
            while [ $# -gt 0 ]; do case "$1" in
              --port) PORT="$2"; shift 2;;
              --base-url) BASE_URL="$2"; shift 2;;
              --num-prompts) NUM_PROMPTS="$2"; shift 2;;
              --request-rate) REQUEST_RATE="$2"; shift 2;;
              --model-ref) MODEL_REF="$2"; shift 2;;
              *) echo "[acceptance] 未知 run 参数: $1" >&2; exit 2;;
            esac; done
            BASE_URL="${BASE_URL:-http://127.0.0.1:${PORT}}"
            # 兜底：设 EXIT trap，start_server 之后任何一步失败（如 OOM 令 set -e 提前退出）
            # 也会停掉本轮 serve，避免残留占着共享 NPU；正常路径显式 stop_server 后清 trap。
            TRAP_CELL="$cell" TRAP_PREC="$prec"
            _run_cleanup() { stop_server "$TRAP_CELL" "$TRAP_PREC"; }
            trap _run_cleanup EXIT
            start_server "$cell" "$prec"
            run_client "$cell" "$prec"
            stop_server "$cell" "$prec"
            trap - EXIT ;;
  stop)     shift; stop_server "${1:?}" "${2:-FP16}" ;;
  metrics)  shift; cell="${1:?}"; prec="${2:-FP16}"; shift 2
            PID="" SERVE_LOG="" METRICS_URL="" NPU_LOG="" OUT=""
            while [ $# -gt 0 ]; do case "$1" in
              --pid) PID="$2"; shift 2;; --serve-log) SERVE_LOG="$2"; shift 2;;
              --metrics-url) METRICS_URL="$2"; shift 2;; --npu-log) NPU_LOG="$2"; shift 2;;
              --out) OUT="$2"; shift 2;; *) shift;; esac; done
            OUT="${OUT:-$ACCEPTED_BASE/$cell-$(date +%Y%m%d-%H%M%S)-server_metrics.json}"
            "$PY" "$ROOT/src/client/collect_metrics.py" aggregate \
              --serve-log "$SERVE_LOG" --metrics-url "$METRICS_URL" \
              --pid "$PID" --npu-log "$NPU_LOG" --out "$OUT" ;;
  *)        usage ;;
esac