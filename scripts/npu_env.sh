#!/usr/bin/env bash
# 加载 NPU 运行时环境（裸机 CANN 9.0.0 / 910B2）。路径默认来自远端 ~/.bashrc，
# 可用环境变量覆盖以适配不同环境/版本（ADR-0005）。故意不用 set -e，source 均容忍失败；且
# source 段临时关闭 nounset（部分 set_env.sh 在 set -u 下引用未定义变量会崩溃，如 ZSH_VERSION），
# 结束后恢复调用侧的 nounset 状态。
if [ "${-//[^u]/}" = u ]; then was_u=1; set +u; else was_u=0; fi
. "${ASCEND_TOOLKIT_SETENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}" 2>/dev/null || true
. "${CANN_NPU_IR_SETENV:-/usr/local/Ascend/cann-9.0.0/share/info/ascendnpu-ir/bin/set_env.sh}" 2>/dev/null || true
. "${NNAL_ATB_SETENV:-/usr/local/Ascend/nnal/atb/set_env.sh}" 2>/dev/null || true
[ "$was_u" = 1 ] && set -u

export LD_PRELOAD="${LD_PRELOAD_EXTRA:-/usr/lib64/libjemalloc.so.2}:${LD_PRELOAD:-}"
