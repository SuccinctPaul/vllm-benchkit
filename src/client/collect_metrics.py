#!/usr/bin/env python3
"""组 M 真机指标采集（M1/M2/M3/M4 → server_metrics.json，供 run.py --server-metrics 消费）。

两条子命令：
  monitor   --out npu.jsonl [--interval 1] [--seconds N] [--devices 0]
            按 interval 轮询 npu-smi，写 JSONL 采样（M3 A3 资源监控证据；1s 间隔最佳努力）。
  aggregate --serve-log serve.log --metrics-url http://127.0.0.1:P/metrics \
            [--pid PID] [--receipt receipt.json] [--npu-log npu.jsonl] [--out server_metrics.json]
            聚合 M1（图计数）/M2（prefix cache / 量化生效）/M3（npu 采样）/M4（CPU 核表）。

与 metrics.evaluate_mechanisms 的契约：
  server_metrics.json = {
    "graph":        {found, graph_capture_count, graph_fallback_count, capture_sizes, compile_time_s},
    "prefix_cache": {found, queries, hits},
    "quantization_effective": "ascend" | "None" | null,
    "resource_monitor": {samples, hbm_peak_gib, interval_s, count},
    "cpu_core_table": {found, allowed},
  }
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)          # src/（client 包的父目录）
if SRC not in sys.path:
    sys.path.insert(0, SRC)
from client import metrics  # noqa: E402

_HBM_RE = re.compile(r"(\d+)\s*/\s*65536")   # 910B2 HBM 总量 65536 MB（单芯片）


def _npu_smi_chip_rows():
    """调用 npu-smi info，返回 [(chip_idx, raw_line)]——仅 HBM-Usage 明细行，绑定所在芯片。

    910B2 每芯片两行：概要行（`| N 910B2 | OK | ...`，N 是芯片号）+ 明细行
    （`| 0 | 0000:... | ... | x/ 65536`，其首列是 AICore 编号恒为 0，不能当芯片号）。
    故以概要行确定当前 chip，明细行含 HBM 总量口径（x/ 65536）时配对输出。
    """
    try:
        out = subprocess.run(["npu-smi", "info"], capture_output=True, text=True,
                             timeout=30).stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return []
    rows, chip = [], None
    for line in out.splitlines():
        m = re.match(r"^\s*\|\s*(\d+)\s+\S+\s*\|\s*OK\b", line)
        if m:
            chip = m.group(1)
            continue
        if chip is not None and _HBM_RE.search(line):
            rows.append((chip, line))
    return rows


def _hbm_used_gib(chip_row):
    m = _HBM_RE.search(chip_row)
    return round(int(m.group(1)) / 1024.0, 3) if m else None


def monitor(out_path, interval=1.0, seconds=0, devices="0"):
    """轮询 npu-smi，写 JSONL：{"ts_s","hbm_used_gib","device"}。"""
    devs = [d.strip() for d in devices.split(",") if d.strip()]
    t0 = time.monotonic()
    last = t0
    count = 0
    with open(out_path, "w", encoding="utf-8") as f:
        while seconds <= 0 or (time.monotonic() - t0) < seconds:
            rows = _npu_smi_chip_rows()
            for idx, row in rows:
                if devs and idx not in devs:
                    continue
                hbm = _hbm_used_gib(row)
                if hbm is None:
                    continue            # 概要行（HBM 列为 hugepages 0/0）无总量口径，跳过
                rec = {"ts_s": round(time.monotonic() - t0, 2),
                       "device": idx, "hbm_used_gib": hbm}
                f.write(json.dumps(rec) + "\n")
                f.flush()
                count += 1
            now = time.monotonic()
            if now - last < interval:
                time.sleep(interval - (now - last))
            last = time.monotonic()
    print(f"[collect_metrics] monitor 写 {count} 采样 -> {out_path}")
    return 0


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _graph_from_log(serve_log, compile_mode):
    g = metrics.parse_graph_counters(serve_log, compile_mode)
    if not g["found"] and (not compile_mode or compile_mode in ("none", None)):
        # A1 eager：无捕获即 0 捕获（found=True 显式记账，供 M1 门禁消费）
        g["found"] = True
        g["graph_capture_count"] = 0
    return g


def _prefix_cache_from_metrics(text):
    """从 /metrics（prometheus 文本）解析 prefix cache 查询/命中。"""
    out = {"queries": None, "hits": None, "found": False}
    q = re.search(r"prefix_cache_queries_total[^{]*\{[^}]*\}\s+(\d+)", text, re.I) or \
        re.search(r"prefix_cache_queries\s+(\d+)", text, re.I)
    h = re.search(r"prefix_cache_hits_total[^{]*\{[^}]*\}\s+(\d+)", text, re.I) or \
        re.search(r"prefix_cache_hits\s+(\d+)", text, re.I)
    if q:
        out["queries"] = int(q.group(1))
        out["found"] = True
    if h:
        out["hits"] = int(h.group(1))
        out["found"] = True
    return out


def aggregate(serve_log_path, metrics_url, pid=None, receipt_path=None,
              npu_log_path=None, out_path=None, compile_mode=None):
    serve_log = _read(serve_log_path) if serve_log_path and os.path.exists(serve_log_path) else ""
    if compile_mode is None:
        if receipt_path and os.path.exists(receipt_path):
            try:
                with open(receipt_path) as f:
                    eff = json.load(f)["effective"]
                compile_mode = eff.get("server", {}).get("compile_mode")
            except (KeyError, ValueError, OSError):
                pass

    graph = _graph_from_log(serve_log, compile_mode)

    prefix_cache = {"queries": None, "hits": None, "found": False}
    if metrics_url:
        try:
            text = subprocess.run(["curl", "-sf", "--noproxy", "*", metrics_url],
                                  capture_output=True, text=True, timeout=30).stdout
            prefix_cache = _prefix_cache_from_metrics(text)
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

    quantization_effective = metrics.parse_quantization_effective(serve_log)

    resource_monitor = {"samples": [], "hbm_peak_gib": None, "interval_s": 1.0, "count": 0}
    if npu_log_path and os.path.exists(npu_log_path):
        with open(npu_log_path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        resource_monitor = metrics.parse_resource_monitor(lines, interval_s=1.0)

    cpu_core_table = {"allowed": None, "affinity_line": None, "found": False}
    if pid:
        try:
            status = _read(f"/proc/{pid}/status")
            cpu_core_table = metrics.parse_cpu_core_table(status.splitlines())
        except OSError:
            pass

    doc = {
        "graph": graph,
        "prefix_cache": prefix_cache,
        "quantization_effective": quantization_effective,
        "resource_monitor": resource_monitor,
        "cpu_core_table": cpu_core_table,
    }
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        print(f"[collect_metrics] 聚合 -> {out_path}")
    else:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    return doc


def main():
    ap = argparse.ArgumentParser(description="组 M 真机指标采集")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("monitor", help="npu-smi 轮询采样")
    p.add_argument("--out", required=True)
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--seconds", type=float, default=0.0)
    p.add_argument("--devices", default="0")

    p = sub.add_parser("aggregate", help="聚合 M1/M2/M3/M4")
    p.add_argument("--serve-log", default="")
    p.add_argument("--metrics-url", default="")
    p.add_argument("--pid", default="")
    p.add_argument("--receipt", default="")
    p.add_argument("--npu-log", default="")
    p.add_argument("--out", default="")

    args = ap.parse_args()
    if args.cmd == "monitor":
        return monitor(args.out, args.interval, args.seconds, args.devices)
    aggregate(args.serve_log, args.metrics_url, args.pid,
              args.receipt, args.npu_log, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
