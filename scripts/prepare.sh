#!/usr/bin/env bash
# 准备基准资源（prepare）：下载模型权重 / 官方数据集 / 生成本地自定义工作负载。
# 参数默认值来自 config/config.yaml，同名环境变量优先（docs/adr/0005）。
# 子命令: model | dataset | workload | subsets | all（默认 all）；FORCE=1 可重下/重新生成。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # scripts/
ROOT="$(cd "$HERE/.." && pwd)"
VENV="${VLLM_BENCHKIT_VENV:-$ROOT/.venv}"
PY="$VENV/bin/python"
[ -x "$PY" ] || { echo "[prepare] 未找到 $VENV/bin/python，先 uv sync" >&2; exit 2; }

MODE="${1:-all}"

# 载入 config.yaml 默认值（YAML_* 命名空间）；环境变量优先于 YAML，见 ADR-0005
YAML_ENV="$("$PY" "$ROOT/src/getconf.py" "${VLLM_BENCHKIT_CONFIG:-$ROOT/config/config.yaml}")"
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

# P4 数据/工件接入：下载 -> 规范化 -> 稳定排序取前 N -> hash manifest + 统一 registry(.registry.json)。
# 每个子集都写 <name>.manifest.json；空 url 记 status=placeholder。registry 供 src/receipt.py 门禁消费。
subsets() {
  echo "[prepare] 整理固定数据子集（sha256(id||content) 稳定排序取前 N，表附-8）"
  "$PY" - "$ROOT/config/config.yaml" "$PREPARE_DATASET_DIR" <<'EOF'
import hashlib
import json
import os
import sys
import urllib.request

import yaml

cfg_path, ddir = sys.argv[1], sys.argv[2]
cfg = yaml.safe_load(open(cfg_path)) or {}
subs = cfg.get("prepare", {}).get("subsets") or []
os.makedirs(ddir, exist_ok=True)

def pick(key_id, content):
    return hashlib.sha256((str(key_id) + "||" + str(content)).encode()).hexdigest()

def write_manifest(name, payload):
    with open(os.path.join(ddir, f"{name}.manifest.json"), "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

registry = []

for s in subs:
    name = s["name"]
    n = int(s.get("n", 64))
    url = str(s.get("url", "")).strip()
    if not url:   # 已声明未接入：写占位 manifest，供 receipt 识别为 placeholder
        write_manifest(name, {"name": name, "status": "placeholder", "fixed_n": n,
                              "src_url": None, "materialized": False,
                              "rule": "固定子集占位：url 待填"})
        registry.append({"key": name, "status": "placeholder", "materialized": False, "fixed_n": n})
        print(f"[prepare] {name}: 未配置真实数据源（url 空），记录占位 manifest")
        continue
    ext = "jsonl" if s.get("format") == "jsonl" else "json"
    src = os.path.join(ddir, f".{name}.src.{ext}")
    if not (os.path.exists(src) and os.environ.get("FORCE") != "1"):
        print(f"[prepare] {name}: 下载 {url}")
        urllib.request.urlretrieve(url, src)
    with open(src) as f:
        if ext == "jsonl":
            recs = [json.loads(l) for l in f if l.strip()]
        else:
            recs = json.load(f)
    id_key = s.get("id_key")
    content_key = s.get("content_key")
    norm = []
    for i, r in enumerate(recs):
        cid = r.get(id_key, i) if id_key else i
        if content_key:
            content = r.get(content_key, "")
        elif "content" in r:
            content = r["content"]
        elif "text" in r:
            content = r["text"]
        else:
            content = json.dumps(r, ensure_ascii=False)
        norm.append((cid, content))
    # case_id 升序后按 sha256(id||content) 稳定排序，取前 N（表附-8）
    picked = sorted(norm, key=lambda t: pick(*t))[:n]
    out = os.path.join(ddir, f"{name}.jsonl")
    hashes = []
    with open(out, "w") as f:
        for cid, content in picked:
            h = pick(cid, content)
            hashes.append(h)
            f.write(json.dumps({"id": str(cid), "content": content,
                                "sha256": h}, ensure_ascii=False) + "\n")
    write_manifest(name, {"name": name, "status": "ready", "fixed_n": n,
                          "total": len(norm), "src_url": url, "materialized": True,
                          "ordered_sha256": hashes,
                          "rule": "case_id asc + sha256(id||content) stable sort, take first N (表附-8)"})
    registry.append({"key": name, "status": "ready", "materialized": True, "fixed_n": n, "total": len(norm)})
    print(f"  -> datasets/{name}.jsonl（{len(picked)}/{len(norm)}）+ {name}.manifest.json")

# 统一工件 registry（receipt.py dataset_ready 门禁据此判断）
reg_doc = {"version": 1, "dataset_dir": ddir,
           "generated": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
           "datasets": {e["key"]: e for e in registry}}
with open(os.path.join(ddir, ".registry.json"), "w") as f:
    json.dump(reg_doc, f, ensure_ascii=False, indent=2)
ready = sum(1 for e in registry if e.get("materialized"))
print(f"[prepare] 工件 registry 已写: {ddir}/.registry.json（{ready}/{len(registry)} 已 materialize）")
EOF
}

case "$MODE" in
  model|dataset|workload|subsets) "$MODE" ;;
  all) model; dataset; workload; subsets ;;
  *) echo "用法: $0 {model|dataset|workload|subsets|all}" >&2; exit 1 ;;
esac
