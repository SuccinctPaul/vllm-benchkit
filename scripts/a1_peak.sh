#!/usr/bin/env bash
# A1-1 峰值 FLOPs 分母采集（V4.1 附-8/组 A1-1；真机项）。
# 独占 910B2 测量 FP16 峰值，落 runs/a1-peak.json，并打印注入环境变量。
# 来源：优先 ascend-dmi（检测到则指引人工执行并回填）；缺省 torch_npu fp16 matmul
#       实测（ascend-dmi 同原理；910B2 单机一般不带 ascend-dmi）。
# 用法: bash scripts/a1_peak.sh [--n 8192] [--iters 7] [--warmup 3] [--devices 0]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PY="${VLLM_BENCHKIT_VENV:-$ROOT/.venv}/bin/python"
[ -x "$PY" ] || { echo "[a1_peak] 未找到 .venv/bin/python，先 uv sync" >&2; exit 2; }
source "$HERE/npu_env.sh"
N=8192; ITERS=7; WARMUP=3; DEVICES=0; OUT="$ROOT/runs/a1-peak.json"
while [ $# -gt 0 ]; do case "$1" in
  --n) N="$2"; shift 2;; --iters) ITERS="$2"; shift 2;;
  --warmup) WARMUP="$2"; shift 2;; --devices) DEVICES="$2"; shift 2;;
  --out) OUT="$2"; shift 2;; *) echo "未知参数 $1" >&2; exit 2;; esac; done
mkdir -p "$(dirname "$OUT")"
echo "[a1_peak] 峰值测量: n=$N iters=$ITERS warmup=$WARMUP devices=$DEVICES"
"$PY" "$ROOT/src/client/a1_peak.py" --n "$N" --iters "$ITERS" \
  --warmup "$WARMUP" --devices "$DEVICES" --out "$OUT"
