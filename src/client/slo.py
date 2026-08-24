"""C4 SLO 判定模型（附-8 A2 绝对 SLO）。

把 cells 声明的 workload.slo 阈值与逐请求回执比对，产出合规判定：
  - 完成率门禁：每 rate 完成数（verdict=ok）≥ request_cap × 99%；
  - 错误率门禁：样本 <1000 时 0 失败（failed/timeout/malformed/silent_truncation 均计失败），
                样本 ≥1000 时 error_rate ≤ 0.1%；
  - 延迟门禁：TTFT / TPOT 的 mean / p95 / p99 分别 ≤ 阈值（ms）；
  - 只接受预定义 rate 点（附-8：不插值不外推），记录出现未声明 rate 即 fail-closed。

long 场景（slo.shapes）按输入档分组判定：每个 (rate, input_len) 桶独立比较对应阈值。
"""
from client import executor

# 视为「失败」的 verdict（未完成）：不吞错，全部计入错误率
_FAILED_VERDICTS = {"failed", "timeout", "malformed", "silent_truncation"}
_COMPLETED_VERDICTS = {"ok"}


def _latency_summary(values_s, th):
    """逐请求 TTFT/TPOT（s）→ {count, mean_ms, p95_ms, p99_ms, gate}。"""
    if not values_s:
        return {"count": 0, "mean_ms": None, "p95_ms": None, "p99_ms": None,
                "gate": False, "reason": "no samples"}
    ms = sorted(v * 1000.0 for v in values_s)
    mean = sum(ms) / len(ms)
    p95 = executor.percentile(ms, 95)
    p99 = executor.percentile(ms, 99)
    got = {"mean_ms": mean, "p95_ms": p95, "p99_ms": p99}
    gate = True
    violated = []
    for key in ("mean_ms", "p95_ms", "p99_ms"):
        lim = (th or {}).get(key)
        if lim is None:
            continue
        if got[key] > lim:
            gate = False
            violated.append(f"{key}={got[key]:.1f}>lim={lim}")
    return {
        "count": len(ms),
        "mean_ms": round(mean, 3),
        "p95_ms": round(p95, 3),
        "p99_ms": round(p99, 3),
        "gate": gate,
        "violated": violated,
    }


def _bucket_threshold(slo, input_len):
    """long 按输入档取阈值；普通 slo 直接返回 {ttft, tpot}。"""
    if "shapes" in slo:
        bucket = slo["shapes"].get(str(input_len))
        if bucket is None:
            return None
        return bucket
    return {"ttft": slo.get("ttft"), "tpot": slo.get("tpot")}


def _evaluate_bucket(records, rate, cap, th, latency_gate=True):
    n = len(records)
    completed = sum(1 for r in records if r.get("verdict") in _COMPLETED_VERDICTS)
    failed = sum(1 for r in records if r.get("verdict") in _FAILED_VERDICTS)
    error_rate = failed / n if n else 0.0
    # cap 非 None 时启用完成率门禁（A2 poisson）；A4 租户无 request_cap（cap=None）跳过
    completion_gate = cap is None or (completed >= max(1, int(cap * 0.99)))
    if n < 1000:
        error_gate = failed == 0
        error_rule = "0 failures (<1000 samples)"
    else:
        error_gate = error_rate <= 0.001
        error_rule = "error_rate<=0.1% (>=1000 samples)"

    ok = [r for r in records if r.get("verdict") in _COMPLETED_VERDICTS]
    ttft = _latency_summary([r["ttft_s"] for r in ok if r.get("ttft_s") is not None],
                            (th or {}).get("ttft"))
    tpot = _latency_summary([dt for r in ok for dt in (r.get("tpot_s") or [])],
                            (th or {}).get("tpot"))
    # latency_gate=False 时延迟仅记录不判定（A4：延迟改用相对隔离 ≤1.25×，由 scheduler 接入）
    slo_pass = bool(completion_gate and error_gate
                    and (not latency_gate or (ttft.get("gate") and tpot.get("gate"))))
    return {
        "rate": rate,
        "input_len": records[0].get("input_len") if records else None,
        "request_cap": cap,
        "sent": n,
        "completed": completed,
        "completed_ratio": round(completed / n, 4) if n else 0.0,
        "failed": failed,
        "error_rate": round(error_rate, 6),
        "error_rule": error_rule,
        "completion_gate": bool(completion_gate),
        "error_gate": bool(error_gate),
        "ttft": ttft,
        "tpot": tpot,
        "slo_pass": slo_pass,
    }


def evaluate_tenant(cfg, records, tenant, latency_gate=True):
    """A4 逐租户 SLO 判定（附-8：复用绝对 SLO 阈值；无 request_cap，跳过完成率门禁）。

    latency_gate=False 时延迟仅记录不判定（A4 延迟改用相对隔离 ≤1.25×，
    由 scheduler.p99_isolation_check 接入；此处只做完成率/错误率门禁）。

    RETURN:
        dict：tenant / sent / completed / failed / error_rate / ttft / tpot / slo_pass
        只统计该租户 measure 期回执（调用方已过滤）。
    """
    slo = (cfg.get("workload") or {}).get("slo")
    if not slo:
        return {"tenant": tenant, "sent": len(records), "slo_pass": True,
                "note": "cell 未声明 workload.slo，跳过延迟判定"}
    return _evaluate_bucket(records, tenant, None,
                            {"ttft": slo.get("ttft"), "tpot": slo.get("tpot")},
                            latency_gate=latency_gate)


def evaluate(cfg, records):
    """对一批逐请求回执做 SLO 判定。

    RETURN:
        dict: {
            mode, per_bucket[]（poisson 按 rate（long 再按 input_len）分桶；closed-loop 单桶）,
            overall{slo_pass, ...}, declared_rates, undeclared_rates,
        }
    """
    slo = (cfg.get("workload") or {}).get("slo")
    mode = (cfg.get("workload") or {}).get("mode")
    if not slo:
        return {"mode": mode, "per_bucket": [],
                "overall": {"slo_pass": True, "buckets_pass": 0, "buckets_total": 0,
                            "note": "cell 未声明 workload.slo，跳过延迟判定"},
                "declared_rates": [], "undeclared_rates": []}

    declared_rates = list((cfg.get("workload") or {}).get("rates") or [])
    caps = list((cfg.get("workload") or {}).get("caps") or [])
    cap_of = {r: (caps[i] if i < len(caps) else None) for i, r in enumerate(declared_rates)}
    found_rates = sorted({r for r in (x.get("rate") for x in records) if r is not None})
    undeclared = [r for r in found_rates if r not in declared_rates]
    if undeclared:
        # 附-8：只接受预定义 rate 点，不插值不外推 → fail-closed
        return {"mode": mode, "per_bucket": [],
                "overall": {"slo_pass": False, "buckets_pass": 0, "buckets_total": 0,
                            "note": f"出现未声明 rate 点: {undeclared}（附-8 不插值不外推）"},
                "declared_rates": declared_rates, "undeclared_rates": undeclared}

    per_bucket = []
    if "shapes" in slo:                      # long：按 (rate, input_len) 分组
        groups = {}
        for r in records:
            groups.setdefault((r.get("rate"), r.get("input_len")), []).append(r)
        for (rate, ilen), rr in sorted(groups.items()):
            th = _bucket_threshold(slo, ilen)
            if th is None:
                return {"mode": mode, "per_bucket": [],
                        "overall": {"slo_pass": False, "buckets_pass": 0, "buckets_total": 0,
                                    "note": f"slo.shapes 未声明 input_len={ilen} 档（slo.shapes 键: {list(slo['shapes'])}）"},
                        "declared_rates": declared_rates, "undeclared_rates": undeclared}
            per_bucket.append(_evaluate_bucket(rr, rate, cap_of.get(rate), th))
    else:                                    # 普通 A2：按 rate 分组
        groups = {}
        for r in records:
            groups.setdefault(r.get("rate"), []).append(r)
        for rate in declared_rates:
            rr = groups.get(rate, [])
            per_bucket.append(_evaluate_bucket(rr, rate, cap_of.get(rate),
                                               {"ttft": slo.get("ttft"), "tpot": slo.get("tpot")}))

    pass_buckets = [b for b in per_bucket if b["slo_pass"]]
    overall_pass = len(pass_buckets) == len(per_bucket) if per_bucket else False
    return {
        "mode": mode,
        "per_bucket": per_bucket,
        "overall": {
            "slo_pass": bool(overall_pass),
            "buckets_total": len(per_bucket),
            "buckets_pass": len(pass_buckets),
        },
        "declared_rates": declared_rates,
        "undeclared_rates": undeclared,
    }
