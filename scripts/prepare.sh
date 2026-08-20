#!/usr/bin/env bash
# 准备基准资源（prepare）：下载模型权重 / 官方数据集 / 生成本地自定义工作负载。
# 参数默认值来自 config/config.yaml，同名环境变量优先（docs/adr/0005）。
# 子命令: model | dataset | workload | all（默认 all）；FORCE=1 可重下/重新生成。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # scripts/
ROOT="$(cd "$HERE/.." && pwd)"
VENV="${VLLM_NOTES_VENV:-$ROOT/.venv}"
PY="$VENV/bin/python"
[ -x "$PY" ] || { echo "[prepare] 未找到 $VENV/bin/python，先 uv sync" >&2; exit 2; }

MODE="${1:-all}"

# 载入 config.yaml 默认值（YAML_* 命名空间）；环境变量优先于 YAML，见 ADR-0005
YAML_ENV="$("$PY" "$ROOT/src/getconf.py" "${VLLM_NOTES_CONFIG:-$ROOT/config/config.yaml}")"
eval "$YAML_ENV"

: "${MODEL:=${YAML_MODEL:-}}"
: "${PREPARE_MODEL_DIR:=${YAML_PREPARE_MODEL_DIR:-}}"
: "${PREPARE_DATASET_DIR:=$ROOT/${YAML_PREPARE_DATASET_DIR:-datasets}}"
: "${PREPARE_SHAREGPT_URL:=${YAML_PREPARE_SHAREGPT_URL:-}}"
: "${PREPARE_SHAREGPT_FILE:=${YAML_PREPARE_SHAREGPT_FILE:-}}"
: "${PREPARE_WORKLOAD_FILE:=${YAML_PREPARE_WORKLOAD_FILE:-}}"
: "${PREPARE_WORKLOAD_NUM:=${YAML_PREPARE_WORKLOAD_NUM:-}}"
: "${PREPARE_WORKLOAD_LEN:=${YAML_PREPARE_WORKLOAD_LEN:-}}"
: "${PREPARE_WORKLOAD_SEED:=${YAML_PREPARE_WORKLOAD_SEED:-}}"

mkdir -p "$PREPARE_DATASET_DIR"

model() {
  # 权重已下载则跳过（FORCE=1 重下；snapshot_download 本身断点续传、幂等）
  if [ -n "$PREPARE_MODEL_DIR" ] && [ -n "$(ls -A "$PREPARE_MODEL_DIR" 2>/dev/null)" ] && [ "${FORCE:-}" != 1 ]; then
    echo "[prepare] 模型已存在: $PREPARE_MODEL_DIR（跳过；FORCE=1 重下）"
    return 0
  fi
  echo "[prepare] 下载模型 $MODEL -> ${PREPARE_MODEL_DIR:-<HF 缓存>}"
  "$PY" - "$MODEL" "$PREPARE_MODEL_DIR" <<'EOF'
import sys
from huggingface_hub import snapshot_download
model, local_dir = sys.argv[1], sys.argv[2] or None
print(snapshot_download(repo_id=model, local_dir=local_dir))
EOF
}

dataset() {
  local dst="$PREPARE_DATASET_DIR/$PREPARE_SHAREGPT_FILE"
  if [ -f "$dst" ] && [ "${FORCE:-}" != 1 ]; then
    echo "[prepare] 数据集已存在: $dst（跳过；FORCE=1 重下）"
    return 0
  fi
  [ -n "$PREPARE_SHAREGPT_URL" ] || { echo "[prepare] 未配置 PREPARE_SHAREGPT_URL，跳过 dataset" >&2; return 0; }
  echo "[prepare] 下载数据集 -> $dst"
  curl -fL --retry 3 -o "$dst" "$PREPARE_SHAREGPT_URL"
}

workload() {
  local dst="$PREPARE_DATASET_DIR/$PREPARE_WORKLOAD_FILE"
  if [ -f "$dst" ] && [ "${FORCE:-}" != 1 ]; then
    echo "[prepare] 工作负载已存在: $dst（跳过；FORCE=1 重新生成）"
    return 0
  fi
  echo "[prepare] 生成工作负载 $dst（$PREPARE_WORKLOAD_NUM 条 / 每条 ~$PREPARE_WORKLOAD_LEN 词，seed=$PREPARE_WORKLOAD_SEED）"
  "$PY" - "$dst" "$PREPARE_WORKLOAD_NUM" "$PREPARE_WORKLOAD_LEN" "$PREPARE_WORKLOAD_SEED" <<'EOF'
import json
import random
import sys

dst, num, length, seed = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
rng = random.Random(seed)
words = ["vllm", "ascend", "npu", "benchmark", "throughput", "latency", "profile",
         "attention", "kv-cache", "scheduler", "prefill", "decode", "tensor-parallel"]
with open(dst, "w") as f:
    for _ in range(num):
        text = " ".join(rng.choice(words) for _ in range(length))
        f.write(json.dumps({"prompt": text}) + "\n")
print(f"  -> {num} 条写入 {dst}")
EOF
}

case "$MODE" in
  model|dataset|workload) "$MODE" ;;
  all) model; dataset; workload ;;
  *) echo "用法: $0 {model|dataset|workload|all}" >&2; exit 1 ;;
esac
