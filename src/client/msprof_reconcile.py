#!/usr/bin/env python3
"""A1-2 msprof 对账（附-8/组 A1-2；真机项）。

目标：把 mfu.py 的 effective_compute_spec v1 有效 FLOPs 与 msprof 采集的算子
执行数据对账，验证误差≤3%（验收；真机 msprof 采集在 scripts/msprof_reconcile.sh）。

数据流：
  msprof --output=DIR --ai-core=on --export=on [--aic-metrics=ArithmeticUtilization] -- <app>
    → DIR 下产出算子汇总 CSV（常见名 op_summary.csv / kernel_details.csv 等）；
  reconcile --dir DIR [--effective-flops N]：
    1. 递归定位算子汇总 CSV；
    2. 按算子类型归类：MatMul 类（MatMul/MatMulV2/MatMulV3/BatchMatMul/BatchMatMulV2/
       Mm/MV2 等）计为「有效 FLOPs 集合」，逐元素算子（RMSNorm/LayerNorm/Gelu/Silu/Add 等）
       不计（与 mfu.py 口径一致，A1-2 v1）；
    3. 汇总 MatMul 类算子的 AICore 时长与 Cube FLOPs（列名自动探测，缺列记 None）；
    4. 若给 --effective-flops N（mfu.mfu_summary 的 Σ有效 FLOPs），输出误差%。

列名探测规则（不同 CANN 版本列名有差异，容错）：
  时长列：包含 "aicore_time" / "AICore Time" / "Time(us)" 的列；
  FLOPs 列：包含 "cube_flops" / "Cube FLOPs" / "MAC FLOPs" / "flops"（忽略 vector 的时间列）；
  算子名列：包含 "op_type" / "Op Type" / "name" 的列。
"""
import argparse
import csv
import glob
import json
import os
import re

# 计入 effective FLOPs 集合的算子类型（A1-2 v1：MatMul 2MNK、Attention QK^T+PV）
_MATMUL_PAT = re.compile(r"(MatMul|BatchMatMul|Mm|MV2|Gemv|BMM)", re.I)
# 明确不计入的逐元素/激活/归一化算子（防御：防止把元素算子当 MatMul）
_ELEMENTWISE_PAT = re.compile(
    r"(RMSNorm|LayerNorm|Norm|Gelu|Silu|Swish|ReLU|Add|Mul|Softmax|Dropout|Reshape|"
    r"Transpose|Cast|Mask|Erf|Pow|Sub|Div|Concat|Split|Pad|Scatter)", re.I)


def _find_op_csv(prof_dir):
    """递归定位算子汇总 CSV；返回 (path, cols) 或 (None, None)。"""
    hits = []
    for root, _dirs, files in os.walk(prof_dir):
        for fn in files:
            if fn.endswith(".csv") and any(k in fn.lower() for k in
                                           ("op_summary", "kernel", "operator", "op_statistic")):
                hits.append(os.path.join(root, fn))
    if not hits:
        return None, None
    path = sorted(hits)[0]
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        cols = [c.strip() for c in next(csv.reader(f))]
    return path, cols


def _pick_col(cols, *patterns):
    for p in patterns:
        for c in cols:
            if p in c.lower():
                return c
    return None


def _num(s):
    s = (s or "").strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def reconcile(prof_dir, effective_flops=None):
    path, cols = _find_op_csv(prof_dir)
    if path is None:
        return {"found": False, "error": "未找到算子汇总 CSV（op_summary/kernel_details 等）"}
    name_col = _pick_col(cols, "op_type", "op type", "operator type", "kernel name")
    time_col = _pick_col(cols, "aicore_time", "aicore time", "time(us)")
    flops_col = _pick_col(cols, "cube_flops", "cube flops", "mac flops", "total flops")
    rows = []
    total_time_us = 0.0
    matmul_time_us = 0.0
    matmul_flops = 0.0
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            r = {k.strip(): v for k, v in r.items()}
            name = (r.get(name_col) or "") if name_col else ""
            t = _num(r.get(time_col)) if time_col else 0.0
            fl = _num(r.get(flops_col)) if flops_col else 0.0
            # MatMul 类算子直接计入（A1-2 v1 口径）；元素/激活/归一化仅用于排除
            # 非 MatMul 算子（注意 "Mul" 是 MatMul 子串，故排除逻辑只在非 MatMul 时生效）
            is_mm = bool(_MATMUL_PAT.search(name))
            if is_mm:
                is_mm = not bool(_ELEMENTWISE_PAT.search(name.replace("MatMul", "").replace("matmul", "")))
            total_time_us += t
            if is_mm:
                matmul_time_us += t
                matmul_flops += fl
            rows.append({"op": name, "time_us": t, "flops": fl, "matmul": is_mm})
    ratio = (matmul_time_us / total_time_us) if total_time_us else None
    doc = {
        "found": True,
        "csv": os.path.relpath(path, os.path.abspath(os.path.dirname(prof_dir))),
        "cols": {"name": name_col, "time": time_col, "flops": flops_col},
        "op_count": len(rows),
        "total_aicore_time_us": round(total_time_us, 3),
        "matmul_time_us": round(matmul_time_us, 3),
        "matmul_time_ratio": None if ratio is None else round(ratio, 6),
        "matmul_flops": round(matmul_flops, 3),
        "effective_flops": effective_flops,
    }
    if effective_flops and matmul_flops > 0:
        err = abs(matmul_flops - effective_flops) / effective_flops * 100.0
        doc["error_pct"] = round(err, 4)
        doc["gate"] = err <= 3.0
    else:
        doc["error_pct"] = None
        doc["gate"] = None
    return doc


def main():
    ap = argparse.ArgumentParser(description="A1-2 msprof 算子对账")
    ap.add_argument("--dir", required=True, help="msprof --output 目录")
    ap.add_argument("--effective-flops", type=float, default=None,
                    help="mfu.mfu_summary 的 Σ有效 FLOPs（可选，给则输出误差%）")
    ap.add_argument("--out", default="", help="输出 JSON 路径（缺省打印 stdout）")
    a = ap.parse_args()
    doc = reconcile(a.dir, a.effective_flops)
    text = json.dumps(doc, ensure_ascii=False, indent=2)
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 0 if doc.get("gate", True) is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
