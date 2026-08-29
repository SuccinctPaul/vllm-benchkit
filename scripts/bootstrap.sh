#!/usr/bin/env bash
# 基础工具链布置（bootstrap）：在目标机（服务器）上补齐「机器层面的地基」——
# git / curl / 编译工具链 / uv / python3.11，并**检测**（不安装）CANN 与 NPU 运行时。
# 它只负责 deploy.sh（装 vllm/vllm-ascend 到 .venv）之前的裸机前置：
#   bootstrap.sh  →  deploy.sh install  →  uv sync
# 用法: scripts/bootstrap.sh [check]   ; check 只只读检测，不做任何安装/改动
#
# 边界说明（为什么有的东西本脚本不装）：
#   1. CANN toolkit / NPU 驱动需要 root + 华为官方安装包，不同机型/版本各不相同，
#      本脚本只做「检测 + 给出手工指引」，把环境变量放 npu_env.sh 统一管（ADR-0005）。
#   2. 系统镜像（apt/yum 源）不带连通性假设，本脚本用 `--no-install-recommends` 最小安装。
set -eu

# --- 0) 平台与身份检测 ---
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
IS_ROOT=0
if [ "$(id -u)" = 0 ]; then IS_ROOT=1; fi
if [ "$(uname)" != "Linux" ]; then
  echo "[bootstrap] 注意：本机非 Linux（$(uname)）。以下步骤面向 Linux/Ascend 目标机。" >&2
fi

MODE="${1:-install}"
case "$MODE" in
  install|check) : ;;
  *) echo "用法: $0 [install|check]" >&2; exit 1 ;;
esac
echo "[bootstrap] 目标机构建地基（mode=${MODE}, root=${IS_ROOT}）"

echo "[bootstrap] --- 1/4 基础工具链检测 ---"
have()  { command -v "$1" >/dev/null 2>&1; }
NEED=()
for t in git curl; do
  if have "$t"; then echo "  $t: 已装"
  else echo "  $t: 缺失"; NEED+=("$t"); fi
done

# 编译工具链（vllm-ascend / numba 构建需要 gcc/g++/make）
for t in gcc g++ make; do
  if have "$t"; then echo "  $t: 已装"; else echo "  $t: 缺失"; NEED+=("$t"); fi
done

echo "[bootstrap] --- 2/4 python3.11 检测 ---"
PY311="$(command -v python3.11 || true)"
if [ -n "$PY311" ] && "$PY311" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then
  echo "  python3.11: $PY311"
else
  echo "  python3.11: 缺失（deploy.sh 的 uv venv --python 3.11 需要它）"
  NEED+=("python3.11")
fi

echo "[bootstrap] --- 3/4 uv 检测 ---"
if have uv; then
  echo "  uv: $(uv --version)"
else
  echo "  uv: 缺失"
  NEED+=("uv")
fi

echo "[bootstrap] --- 4/4 CANN / NPU 运行时的检测（只读，不安装） ---"
. "$HERE/npu_env.sh" || true   # 复用同一套 CANN 环境路径默认值（ADR-0005），临时关 set -u 由内部处理
for p in \
  "$(command -v npu-smi 2>/dev/null)" \
  /usr/local/Ascend/ascend-toolkit/set_env.sh \
  /usr/local/Ascend/cann-9.0.0/share/info/ascendnpu-ir/bin/set_env.sh; do
  if [ -n "$p" ] && [ -e "$p" ]; then echo "  找到 CANN/NPU 件: $p"; fi
done
if ! command -v npu-smi >/dev/null 2>&1 && [ ! -e /usr/local/Ascend/ascend-toolkit/set_env.sh ]; then
  echo "  ⚠️  未检测到 CANN/NPU 运行时。请手工安装华为 CANN 9.0.0 + NPU 驱动后重跑（不由本脚本安装）。"
fi

if [ "$MODE" = check ]; then
  if [ "${#NEED[@]}" -gt 0 ]; then
    echo "[bootstrap] --check 结论：以下待装（可运行 scripts/bootstrap.sh 自动安装）：${NEED[*]}"
    exit 1
  fi
  echo "[bootstrap] --check 结论：基础工具链齐备；CANN 请按上面标记确认。"
  exit 0
fi

# --- 5) 系统包安装（仅当有缺失或未 root 时给指引） ---
if [ "${#NEED[@]}" -gt 0 ]; then
  if [ "$IS_ROOT" = 1 ]; then
    echo "[bootstrap] 以 root 安装系统包: ${NEED[*]}"
    if [ -x /usr/bin/apt-get ]; then
      apt-get update -y >/dev/null
      # python3.11 已在较新 Ubuntu 默认仓库；旧发行版需 ppa/deadsnakes，这里先尝试默认
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ${NEED[*]} >/dev/null
    elif [ -x /usr/bin/yum ]; then
      yum install -y "${NEED[@]}" >/dev/null 2>&1
    else
      echo "[bootstrap] 未知包管理器，请手工安装: ${NEED[*]}"
    fi
  else
    echo "[bootstrap] 检测到缺失却非 root：请以 root 运行，或用包管理器手工安装 ${NEED[*]}"
  fi
fi

# --- 6) uv 安装（未安装时，用官方安装器到用户目录，不需 root） ---
if ! have uv; then
  echo "[bootstrap] 安装 uv（官方脚本 → ~/.local/bin）"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || \
    { echo "[bootstrap] uv 官方脚本失败，请手工安装: https://docs.astral.sh/uv/" >&2; exit 1; }
  export PATH="$HOME/.local/bin:$PATH"
fi

# --- 7) 结束语 ---
echo ""
echo "[bootstrap] 完成。基础工具链就绪：git curl gcc g++ make python3.11 uv"
echo "  下一步："
echo "    ./scripts/deploy.sh install      # 按 topology 拉/装 vllm + vllm-ascend 到 .venv"
echo "    ./scripts/bench.sh throughput    # 冒烟"
echo "  若上面有 ⚠️ 未检测到 CANN，请先手工装 CANN 9.0.0 + NPU 驱动再走下一步。"