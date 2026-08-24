#!/usr/bin/env python3
"""A1-1 峰值 FLOPs 分母采集（附-8/组 A1-1）。

用途：真机上独占 910B2 测量 FP16 峰值算力，供 mfu.mfu_summary 的峰值分母
（经 `VLLM_BENCHKIT_PEAK_FLOPS` 环境变量注入，见 mfu.peak_flops_per_s）。

来源分级（honest，不冒充 ascend-dmi）：
  - 若检测到 ascend-dmi 可执行文件，则打印指引由人工执行并回填（保留原始输出，
    本模块不回退冒充）；本模块默认走 torch_npu matmul 实测回退（同 ascend-dmi
    的 matmul benchmark 原理，910B2 单机一般不带 ascend-dmi）。
  - matmul 实测：大矩阵乘（fp16），先热身若干次，再测 n 次取中位数，
    全部原始采样落盘，可复现（同输入同尺寸同卡同运行）。

输出 JSON（runs/a1-peak.json）：
  {
    "source": "torch-npu-matmul-fp16" | "ascend-dmi",
    "device": npu-smi 芯片型号,
    "shape": {n, dtype},
    "samples_flops_per_s": [...],      # 原始采样（保留）
    "median_flops_per_s": ...,
    "note": "...",
  }

CLI:
  python src/client/a1_peak.py [--n 8192] [--iters 7] [--warmup 3]
                               [--devices 0] [--out runs/a1-peak.json]
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import time

_910B2_SPEC_FP16 = 320e12   # 910B2 FP16 理论峰值（TFLOPS→FLOPs/s），仅作参考注释


def _npu_model():
    """npu-smi 读芯片型号（如 910B2）；失败返回 None。"""
    try:
        out = subprocess.run(["npu-smi", "info"], capture_output=True,
                             text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[a1_peak] npu-smi 不可用，无法探测芯片型号: {e}",
              file=sys.stderr)
        return None
    for line in out.splitlines():
        if "910" in line:
            toks = line.split()
            for t in toks:
                if "910" in t:
                    return t
            return toks[1] if len(toks) > 1 else None
    return None


def measure_matmul_peak(n, iters, warmup, devices):
    """torch_npu 大矩阵乘实测 FP16 峰值（FLOPs/s 采样列表）。"""
    os.environ["ASCEND_RT_VISIBLE_DEVICES"] = devices
    try:
        import torch
        import torch_npu  # noqa: F401  确保 NPU backend 注册
    except ImportError as e:
        print(f"[a1_peak] 缺少 torch_npu: {e}（须在真机 CANN 环境运行）", file=sys.stderr)
        raise SystemExit(2)
    a = torch.randn(n, n, dtype=torch.float16, device="npu")
    b = torch.randn(n, n, dtype=torch.float16, device="npu")
    flops = 2.0 * n * n * n

    def one():
        torch.npu.synchronize()
        t0 = time.monotonic()
        c = a @ b
        torch.npu.synchronize()
        dt = time.monotonic() - t0
        return flops / dt

    for _ in range(warmup):          # 热身（编译/调度开销不计入）
        one()
    return [one() for _ in range(iters)]


def main():
    ap = argparse.ArgumentParser(description="A1-1 峰值 FLOPs 分母采集")
    ap.add_argument("--n", type=int, default=8192)
    ap.add_argument("--iters", type=int, default=7)
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--devices", default="0")
    ap.add_argument("--out", default="runs/a1-peak.json")
    args = ap.parse_args()

    if shutil_which("ascend-dmi"):
        print("[a1_peak] 检测到 ascend-dmi：请人工执行并回填原始输出，"
              "再以 VLLM_BENCHKIT_PEAK_FLOPS=<值> 覆盖峰值分母；"
              "本脚本不代为执行 ascend-dmi。", file=sys.stderr)

    samples = measure_matmul_peak(args.n, args.iters, args.warmup, args.devices)
    median = statistics.median(samples)
    doc = {
        "source": "torch-npu-matmul-fp16",
        "device": _npu_model(),
        "shape": {"n": args.n, "dtype": "float16"},
        "samples_flops_per_s": [round(s, 3) for s in samples],
        "median_flops_per_s": round(median, 3),
        "spec_fp16_flops_per_s": _910B2_SPEC_FP16,
        "note": "ascend-dmi 不存在；torch_npu fp16 matmul 3 次以上中位（原始采样全保留）。",
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    print(f"[a1_peak] 写 {args.out}；用以下环境变量注入峰值分母：")
    print(f"  export VLLM_BENCHKIT_PEAK_FLOPS={median:.0f}")
    return 0


def shutil_which(name):
    from shutil import which
    return which(name)


if __name__ == "__main__":
    sys.exit(main())
