#!/usr/bin/env bash
# A1-2 msprof 算子对账（V4.1 附-8/组 A1-2；真机项）。
# 子命令:
#   collect --out DIR -- <cmd...>          在 msprof 下运行 <cmd>，采集 AICore 算子数据
#                                           （--ai-core=on --export=on，原始产物全保留）
#   reconcile --dir DIR [--effective-flops N] [--out OUT]
#                                           解析算子汇总 CSV，汇总 MatMul 类算子的
#                                           AICore 时长/Cube FLOPs，与 mfu 有效 FLOPs 对账
#                                           （列名自动探测；误差≤3% 门禁 gate=true）
# 用法示例（在 A1 闭环 measure 段采集后对账）:
#   bash scripts/msprof_reconcile.sh collect --out runs/a1-prof -- \
#     ./.venv/bin/python src/client/run.py --cell a1 --precision FP16 --base-url http://127.0.0.1:8010 ...
#   bash scripts/msprof_reconcile.sh reconcile --dir runs/a1-prof --effective-flops <Σ有效FLOPs>
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PY="${VLLM_NOTES_VENV:-$ROOT/.venv}/bin/python"
source "$HERE/npu_env.sh"

cmd="${1:?用法: msprof_reconcile.sh {collect|reconcile} ...}"; shift
case "$cmd" in
  collect)
    OUT="runs/a1-prof"
    while [ $# -gt 0 ]; do case "$1" in
      --out) OUT="$2"; shift 2;; --) shift; break;; *) echo "未知参数 $1" >&2; exit 2;;
    esac; done
    [ $# -gt 0 ] || { echo "[msprof] 缺被采集命令" >&2; exit 2; }
    mkdir -p "$OUT"
    echo "[msprof] 采集 -> $OUT ：$*"
    msprof --output="$OUT" --ai-core=on --export=on -- "$@"
    echo "[msprof] 原始产物保留于 $OUT"
    ;;
  reconcile)
    DIR="" EFF="" OUT=""
    while [ $# -gt 0 ]; do case "$1" in
      --dir) DIR="$2"; shift 2;; --effective-flops) EFF="$2"; shift 2;;
      --out) OUT="$2"; shift 2;; *) echo "未知参数 $1" >&2; exit 2;; esac; done
    [ -n "$DIR" ] || { echo "[msprof] 缺 --dir" >&2; exit 2; }
    ARGS=(--dir "$DIR")
    [ -n "$EFF" ] && ARGS+=(--effective-flops "$EFF")
    [ -n "$OUT" ] && ARGS+=(--out "$OUT")
    "$PY" "$ROOT/src/client/msprof_reconcile.py" "${ARGS[@]}"
    ;;
  *) echo "未知子命令 $cmd" >&2; exit 2;;
esac
