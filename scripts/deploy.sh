#!/usr/bin/env bash
# 配置驱动布置（deploy）：在目标机（服务器）上，依据 config/topology.yaml（ADR-0006）
# 把 vllm / vllm-ascend 布置并可编辑安装进 .venv，vllm-benchkit 是从这里运行的监督者（manage:false）。
# 与 config/config.yaml（运行参数）严格分层；本脚本是 topology.yaml 的唯一消费方。
# 用法: scripts/deploy.sh [install|check]   ; check 只解析+只读核对，不改仓库也不安装
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # scripts/
ROOT="$(cd "$HERE/.." && pwd)"
VENV="${VLLM_BENCHKIT_VENV:-$ROOT/.venv}"
PY="$VENV/bin/python"
CONFIG="${VLLM_BENCHKIT_CONFIG:-$ROOT/config/topology.yaml}"
MIRROR="https://repo.huaweicloud.com/ascend/repos/pypi"   # triton-ascend 等昇腾专属包

MODE="${1:-install}"
case "$MODE" in
  install|check) : ;;
  *) echo "用法: $0 [install|check]" >&2; exit 1 ;;
esac

# --- 0) 载入拓扑（gettopo.py 需要 pyyaml；先确保 venv 内有它） ---
if [ ! -x "$PY" ]; then
  echo "[deploy] 创建 venv: $VENV"
  uv venv --python 3.11 "$VENV"
fi
uv pip install --python "$PY" pyyaml >/dev/null 2>&1 || true
eval "$("$PY" "$ROOT/src/gettopo.py" "$CONFIG")"

# 环境变量优先于 topology.yaml（ADR-0005）
SERVER="${VLLM_BENCHKIT_SERVER:-$TOPO_SERVER}"
DIR="${VLLM_BENCHKIT_DIR:-$TOPO_DIR}"
echo "[deploy] server=$SERVER  dir=$DIR  repos=$TOPO_REPO_COUNT  mode=$MODE"

# --- 1) check 只读核对：report 每个仓库当前 HEAD / 期望 branch-commit ---
if [ "$MODE" = check ]; then
  for ((i=0;i<TOPO_REPO_COUNT;i++)); do
    eval "name=\${TOPO_REPO_${i}_NAME}"
    eval "url=\${TOPO_REPO_${i}_URL}"
    eval "branch=\${TOPO_REPO_${i}_BRANCH}"
    eval "cm=\${TOPO_REPO_${i}_COMMIT}"
    eval "manage=\${TOPO_REPO_${i}_MANAGE}"
    path="$DIR/$name"
    if [ ! -d "$path/.git" ]; then
      echo "  $name  -> 未部署于 $path（manage=$manage）"
      continue
    fi
    echo "  $name -> 期望 branch=$branch commit=$([ -n "$cm" ] && echo "$cm" || echo "<branch 头>") "
    echo "         当前 branch=$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null) HEAD=$(git -C "$path" rev-parse --short HEAD 2>/dev/null)"
  done
  echo "[deploy] --check 完成（未执行 clone/checkout/安装）"
  exit 0
fi

# --- 2) install 模式：clone / checkout 到钉的 branch/commit（manage:false 跳过；DEPLOY_OFFLINE=1 跳过网络） ---
checkout_repo() {
  local name="$1" url="$2" branch="$3" commit="$4"
  local path="$DIR/$name"
  local co=commit
  [ -n "$commit" ] || co=branch
  if [ ! -d "$path/.git" ]; then
    [ "${DEPLOY_OFFLINE:-}" = 1 ] && { echo "[deploy] 离线，跳过 clone $name" >&2; return; }
    echo "[deploy] clone $name ← $url"
    git clone --no-tags "$url" "$path"
  elif [ "${DEPLOY_OFFLINE:-}" != 1 ]; then
    echo "[deploy] fetch $name"
    git -C "$path" fetch --all --prune --tags >/dev/null 2>&1 || git -C "$path" fetch --all --prune
  fi
  local ref="$commit"
  [ -n "$ref" ] || ref="$branch"
  echo "[deploy] checkout $name @ $co=$ref"
  git -C "$path" checkout --quiet --detach "$ref" || git -C "$path" checkout --quiet "$ref"
  echo "  -> $name=$(git -C "$path" rev-parse --short HEAD)  $(git -C "$path" describe --tags 2>/dev/null || true)"
}

for ((i=0;i<TOPO_REPO_COUNT;i++)); do
  eval "name=\${TOPO_REPO_${i}_NAME}"
  eval "url=\${TOPO_REPO_${i}_URL}"
  eval "branch=\${TOPO_REPO_${i}_BRANCH}"
  eval "cm=\${TOPO_REPO_${i}_COMMIT}"
  eval "manage=\${TOPO_REPO_${i}_MANAGE}"
  if [ "$manage" = false ]; then
    echo "[deploy] 跳过监督者 repo: $name"
    continue
  fi
  checkout_repo "$name" "$url" "$branch" "$cm"
done

# --- 3) 安装：把 vllm / vllm-ascend 按清单推导路径 editable 装进 .venv ---
source "$HERE/npu_env.sh"   # CANN 环境，vllm-ascend 构建需要

OVERRIDES="$ROOT/.deploy-overrides.txt"
cat > "$OVERRIDES" <<'EOF'
torch==2.9.0
torch-npu==2.9.0.post2
opencv-python-headless>=4.13.0
# numpy 收敛：vllm 侧 opencv>=4.13 要 >=2；numba（vllm-ascend eplb 依赖）要 <=2.2，故取 [2.0,2.3)
numpy>=2.0.0,<2.3
EOF

echo "[deploy] uv pip install -e $DIR/vllm -e $DIR/vllm-ascend + pyyaml（override: $OVERRIDES）"
uv pip install --python "$PY" \
  -e "$DIR/vllm" -e "$DIR/vllm-ascend" pyyaml \
  --extra-index-url "$MIRROR" --index-strategy unsafe-best-match \
  --overrides "$OVERRIDES"

# --- 4) 验证 ---
if [ ! -x "$VENV/bin/vllm" ]; then
  echo "[deploy] 失败：$VENV/bin/vllm 未生成" >&2
  exit 1
fi
"$PY" - <<'PY'
import torch, torch_npu, vllm, vllm_ascend
from vllm.platforms import current_platform
print("  torch", torch.__version__)
print("  torch_npu", torch_npu.__version__, "npu_avail", torch_npu.npu.is_available())
print("  vllm", vllm.__version__)
print("  platform", type(current_platform).__module__ + "." + type(current_platform).__name__)
print("  vllm_ascend", vllm_ascend.__file__)
PY
echo "[deploy] 完成。下一步: ./scripts/bench.sh throughput"