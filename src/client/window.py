"""C6 A3 窗口分析器（附-8 A3 专项）。

closed-loop、concurrency=1、总上下文 32768 = 渲染后输入 30720 + 输出 2048：
  - run_closed_loop：warmup_s 预热（不计量）→ measure_s 计量，串行执行同一业务长文本
    case（max_requests 上限 / min_completed_requests 最少完成数门禁）；
  - split_windows：按 measure 相对时间切 window_count×window_s 窗口；
  - evaluate_windows：判定（附-8 A3）：
      窗口吞吐 CV ≤ 5%；
      各窗口 TTFT/TPOT 中位数相对首个稳定窗口漂移 ≤ 10%、p99 ≤ 20%；
      0 失败（failed/timeout/malformed/silent_truncation 全计）、无 OOM/死锁/退出；
      输入 token 数以 chat template 渲染后 tokenIDs 计（prompt_tokens 校验 ±5%），
      输出完整（completion_tokens == 2048，不得截断）。

数据身份：不得用随机 token 替代业务长文本 —— case 来自 prepare subsets 的 long 子集
（cases._a3 按 document_id 稳定顺序拼接，附-8 D1）。
"""
import json
import statistics
import time

import httpx

from client import common, executor

_FAILED = {"failed", "timeout", "malformed", "silent_truncation"}
_CV_LIMIT = 0.05          # 窗口吞吐 CV ≤ 5%
_DRIFT_MEDIAN = 0.10      # 中位数漂移 ≤ 10%
_DRIFT_P99 = 0.20         # p99 漂移 ≤ 20%
_INPUT_TOL = 0.05         # 输入 token 数（渲染后 tokenIDs）容差 ±5%


def _median_ms(values_s):
    return common.median_ms(values_s)


def _p99_ms(values_s):
    return common.percentile_ms(values_s, 99)


def _summ(values_s):
    """TTFT/TPOT 样本 → {count, median_ms, p99_ms}。"""
    return {"count": len(values_s),
            "median_ms": _median_ms(values_s),
            "p99_ms": _p99_ms(values_s)}


async def run_closed_loop(cfg, cases, base_url, client=None, out=None,
                          timeout_s=None, warmup_s=None, measure_s=None,
                          max_requests=None):
    """closed-loop 串行执行（concurrency=1）：warmup 不计量 + measure 计量。

    ARGS:
        cfg / cases    acceptance.expand / cases.generate_cases（A3 单 case 循环）
        base_url       server 根（不含 /v1）
        client         httpx.AsyncClient（None 自建；测试注入 MockTransport）
        out            measure 段逐请求回执写对象（JSON Lines）；None=不落盘
        timeout_s      per-request 超时（缺省 workload.timeouts.per_request_s）
        warmup_s / measure_s / max_requests   覆盖 workload 的 timeouts / max_requests
    RETURN:
        (warm_records, measure_records)  warm 不计量，measure 带 measure_ts_s
    """
    wl = cfg["workload"]
    if warmup_s is None:
        warmup_s = float(wl.get("timeouts", {}).get("warmup_s", 300))
    if measure_s is None:
        measure_s = float(wl.get("timeouts", {}).get("measure_s", 1800))
    if max_requests is None:
        max_requests = int(wl.get("max_requests") or 0)
    if timeout_s is None:
        timeout_s = float(wl.get("timeouts", {}).get("per_request_s") or 1800)
    sampling = cfg.get("sampling", {})
    case = cases[0]                      # A3：单一业务长文本 case 循环
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout_s, trust_env=False)
    start = time.monotonic()
    warm, meas = [], []

    async def fire(measure_phase):
        rec = await executor.execute_one(cfg, case, sampling, base_url, client,
                                         timeout_s, history=None)
        if measure_phase:
            rec["measure_ts_s"] = round(time.monotonic() - mstart, 6)
            meas.append(rec)
            if out is not None:
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
        else:
            warm.append(rec)

    try:
        # warmup：到 warmup_s 结束（不计量）
        while time.monotonic() - start < warmup_s:
            await fire(False)
        # measure：measure_s 内串行，max_requests 封顶
        mstart = time.monotonic()
        while time.monotonic() - mstart < measure_s:
            if max_requests and len(meas) >= max_requests:
                break
            await fire(True)
    finally:
        if own_client:
            await client.aclose()
    return warm, meas


def split_windows(receipts, window_count, window_s):
    """按 measure 相对时间切窗口；空窗口也保留（stall 时吞吐=0，CV 判定捕获）。"""
    out = []
    for i in range(window_count):
        lo, hi = i * window_s, (i + 1) * window_s
        recs = [r for r in receipts if lo <= (r.get("measure_ts_s") or 0) < hi]
        out.append({"index": i, "start_s": lo, "end_s": hi, "records": recs})
    return out


def _drift(got, base):
    if got is None or base is None or base == 0:
        return None
    return (got - base) / base


def _token_gates(receipts, input_tokens, output_len):
    """输入以渲染后 tokenIDs 计（prompt_tokens ±5%）；输出完整（completion_tokens==output_len）。"""
    input_ok, output_ok = True, True
    for r in receipts:
        if r.get("verdict") != "ok":
            continue
        pt = r.get("prompt_tokens")
        if pt is not None and input_tokens and abs(pt - input_tokens) / input_tokens > _INPUT_TOL:
            input_ok = False
        ct = r.get("completion_tokens")
        if ct is not None and ct != output_len:
            output_ok = False
    return input_ok, output_ok


def evaluate_windows(cfg, receipts, window_s=None, output_len=None):
    """A3 窗口判定（附-8 A3 专项）。

    RETURN:
        dict {window_count, window_s, completed_total, windows[], throughput_cv,
              throughput_cv_ok, drift[], drift_ok, zero_failure_ok,
              min_completed_ok, input_ok, output_ok, overall{pass, reasons}}
    """
    wl = cfg["workload"]
    window_s = float(window_s or wl.get("window_s") or 300)
    window_count = int(wl.get("window_count") or 6)
    min_completed = int(wl.get("min_completed_requests") or 24)
    input_tokens = int(wl.get("input_tokens") or 0)
    if output_len is None:
        output_len = int((cfg.get("sampling") or {}).get("max_tokens") or 2048)

    rows = []
    for w in split_windows(receipts, window_count, window_s):
        recs = w["records"]
        ok = [r for r in recs if r.get("verdict") == "ok"]
        out_tokens = sum(int(r.get("output_tokens") or 0) for r in recs)
        rows.append({
            "index": w["index"],
            "start_s": w["start_s"],
            "requests": len(recs),
            "completed": len(ok),
            "failed": sum(1 for r in recs if r.get("verdict") in _FAILED),
            "throughput_tokens_per_s": round(out_tokens / window_s, 3),
            "ttft": _summ([r["ttft_s"] for r in ok if r.get("ttft_s") is not None]),
            "tpot": _summ([dt for r in ok for dt in (r.get("tpot_s") or [])]),
        })

    # 窗口吞吐 CV（空窗口计 0 吞吐 → stall 时 CV 超标）
    tps = [r["throughput_tokens_per_s"] for r in rows]
    mean_tps = statistics.mean(tps)
    cv = (statistics.pstdev(tps) / mean_tps) if mean_tps else None
    cv_ok = cv is not None and cv <= _CV_LIMIT

    # 漂移：相对首个稳定窗口（第一个有 ok 样本的窗口）
    base_i = next((i for i, r in enumerate(rows) if r["ttft"]["count"] > 0), None)
    drift, drift_ok = [], base_i is not None
    if base_i is not None:
        b = rows[base_i]
        for r in rows:
            if r["index"] == b["index"]:
                drift.append({"index": r["index"], "baseline": True, "drift_ok": True,
                              "ttft_median_drift": None, "ttft_p99_drift": None,
                              "tpot_median_drift": None, "tpot_p99_drift": None})
                continue
            d = {
                "index": r["index"],
                "baseline": False,
                "ttft_median_drift": _drift(r["ttft"]["median_ms"], b["ttft"]["median_ms"]),
                "ttft_p99_drift": _drift(r["ttft"]["p99_ms"], b["ttft"]["p99_ms"]),
                "tpot_median_drift": _drift(r["tpot"]["median_ms"], b["tpot"]["median_ms"]),
                "tpot_p99_drift": _drift(r["tpot"]["p99_ms"], b["tpot"]["p99_ms"]),
            }
            wok = (
                (d["ttft_median_drift"] is None or abs(d["ttft_median_drift"]) <= _DRIFT_MEDIAN)
                and (d["tpot_median_drift"] is None or abs(d["tpot_median_drift"]) <= _DRIFT_MEDIAN)
                and (d["ttft_p99_drift"] is None or abs(d["ttft_p99_drift"]) <= _DRIFT_P99)
                and (d["tpot_p99_drift"] is None or abs(d["tpot_p99_drift"]) <= _DRIFT_P99)
            )
            d["drift_ok"] = wok
            drift_ok = drift_ok and wok
            drift.append(d)

    # 0 失败 / 静默截断 / 完成数 / token 校验
    failed_count = sum(1 for r in receipts if r.get("verdict") in _FAILED)
    silent_count = sum(1 for r in receipts if r.get("verdict") == "silent_truncation")
    completed_total = sum(1 for r in receipts if r.get("verdict") == "ok")
    zero_failure_ok = failed_count == 0 and silent_count == 0
    min_completed_ok = completed_total >= min_completed
    input_ok, output_ok = _token_gates(receipts, input_tokens, output_len)

    reasons = []
    if not cv_ok:
        reasons.append(f"窗口吞吐 CV={cv} > 5%")
    if not drift_ok:
        reasons.append("TTFT/TPOT 窗口漂移超限（中位数>10% 或 p99>20%）")
    if not zero_failure_ok:
        reasons.append(f"存在失败/静默截断（failed={failed_count}, silent={silent_count}）")
    if not min_completed_ok:
        reasons.append(f"完成数 {completed_total} < min_completed_requests={min_completed}")
    if not input_ok:
        reasons.append("输入 token 数（渲染后 tokenIDs）偏离声明值 >5%")
    if not output_ok:
        reasons.append(f"存在输出未完整（completion_tokens != {output_len}）")
    overall_pass = not reasons

    return {
        "window_count": window_count,
        "window_s": window_s,
        "completed_total": completed_total,
        "min_completed_requests": min_completed,
        "windows": rows,
        "throughput_cv": None if cv is None else round(cv, 6),
        "throughput_cv_ok": bool(cv_ok),
        "drift": drift,
        "drift_ok": bool(drift_ok),
        "zero_failure_ok": bool(zero_failure_ok),
        "min_completed_ok": bool(min_completed_ok),
        "input_ok": bool(input_ok),
        "output_ok": bool(output_ok),
        "overall": {"pass": bool(overall_pass), "reasons": reasons},
    }
