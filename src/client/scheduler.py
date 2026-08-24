"""C5 A4 租户调度器（V4.1 附-7/附-8）。

四租户（dialogue/tool/reasoning/structured）共享单一服务，各 25% 输出 token 份额：
  - 混合到达序列：每租户请求速率 ∝ share / output_cap（output_cap=调度成本默认），
    独立 Random(0) 泊松流合并成单一时间线（seed=0，确定性，不丢弃不重排）；
  - 并发控制：global_concurrency + per_tenant_concurrency 双重信号量；
  - B0 容量扫描：按 b0_capacity_points（输出 token/s）逐点混合负载，
    每点 per_point_s、点间 gap_s；取最大 SLO 合规点的输出率；
  - 正式负载：global_load_factor(=0.70) × B0-FP16 三次中位最大 SLO 合规混合输出率
    （三次中位的生命周期重复由组 Z 编排，本模块提供单生命周期合同）。

判定（附-8 K2）：
  - 逐租户输出 token 份额偏差 ≤ 10%（fairness.share_report）；
  - Jain 公平性指数 ≥ 0.90；
  - 逐租户完成率/错误率门禁 + p99 ≤ 1.25× 隔离 B0-FP16（相对隔离，替代绝对延迟阈值）；
  - 绝对 SLO（slo.evaluate_tenant）仅记录延迟，不作 A4 判定依据。
"""
import asyncio
import json
import math
import random
import time

import httpx

from client import common, executor, fairness, slo

GENERATOR = "vllm-xcheck-mixed-v1"
SEED = 0                 # 附-8：seed=0（与 vllm-xcheck-poisson-v1 一致）
ISOLATION_RATIO = 1.25   # 附-8 K2：p99 ≤ 1.25× 隔离 B0-FP16


def tenant_request_rates(cfg, token_rate, tenants=None, full_load=False):
    """各租户请求速率（req/s）∝ share / cost_len（调度成本=期望输出长度/请求）。

    成本与 max_tokens 上限分离（见 a4-mt.yaml tenants 注释）：cost_len 取实测平均
    输出长度，output_cap 仅作 max_tokens 上限。缺省回退 output_cap（旧口径）。

    ARGS:
        tenants    仅返回这些租户（None=全部）。
        full_load  隔离 B0 基线：该租户独自承担全部 token_rate（share=1.0），
                   即 request_rate = token_rate / cost_len（"隔离 B0-FP16"口径，
                   与混合中 25% 份额对比 p99 ≤ 1.25×，见附-8 K2）。

    RETURN:
        dict {tenant: {"share": float, "output_cap": int, "cost_len": float,
                       "request_rate": float}}
    """
    out = {}
    for name, meta in cfg["workload"]["tenants"].items():
        if tenants is not None and name not in tenants:
            continue
        share = 1.0 if full_load else float(meta["share"])
        cost = float(meta.get("cost_len") or meta.get("output_cap") or 1)
        out[name] = {
            "share": share,
            "output_cap": int(meta.get("output_cap") or 1),
            "cost_len": cost,
            "request_rate": token_rate * share / cost,
        }
    return out


def mixed_arrival(cfg, token_rate, duration_s, seed=SEED, tenants=None, full_load=False):
    """生成 token_rate（输出 token/s）下的确定性混合到达序列。

    每租户以 Random(0) 生成独立泊松流（request_rate = token_rate×share/output_cap），
    按 (nominal_ts_s, tenant) 升序合并成单一时间线；本模块不丢弃、不重排。

    ARGS:
        tenants    仅生成这些租户的到达（None=全部）；隔离 B0 基线传单租户列表。
        full_load  隔离 B0 基线：该租户独自承担全部 token_rate（share=1.0，
                   request_rate = token_rate/cost_len），与混合中 25% 份额对比
                   p99 ≤ 1.25×（附-8 K2 "隔离 B0-FP16" 口径）。
    RETURN:
        dict {generator, generator_params, arrivals[], arrival_stream_sha256, generator_hash}
    """
    rates = tenant_request_rates(cfg, token_rate, tenants=tenants, full_load=full_load)
    events = []
    for name, meta in rates.items():
        req_rate = meta["request_rate"]
        cap = math.ceil(req_rate * duration_s)
        if req_rate <= 0 or cap <= 0:
            continue
        rng = random.Random(seed)
        t = 0.0
        for _ in range(cap):
            t += rng.expovariate(req_rate)
            events.append((t, name))
    events.sort(key=lambda e: (e[0], e[1]))
    arrivals = [{"nominal_ts_s": round(ts, 6), "tenant": name} for ts, name in events]

    params = {
        "seed": seed,
        "token_rate": token_rate,
        "duration_s": duration_s,
        "per_tenant": {
            n: {"share": m["share"], "output_cap": m["output_cap"],
                "request_rate": m["request_rate"],
                "request_cap": math.ceil(m["request_rate"] * duration_s)}
            for n, m in rates.items()
        },
    }
    return {
        "generator": GENERATOR,
        "generator_params": params,
        "arrivals": arrivals,
        "arrival_stream_sha256": common.sha256_hex(common.canonical_json(arrivals)),
        "generator_hash": common.sha256_hex(common.canonical_json(
            {"generator": GENERATOR, "params": params})),
    }


def scan_segments(cfg):
    """B0-FP16 混合容量扫描段：每点 = {token_rate, duration_s, gap_s}。"""
    wl = cfg["workload"]
    points = list(wl.get("b0_capacity_points") or [])
    per_point_s = int(wl.get("timeouts", {}).get("per_point_s", 600))
    gap_s = int(wl.get("timeouts", {}).get("point_gap_s", 60))
    return [{"token_rate": float(r), "duration_s": per_point_s, "gap_s": gap_s}
            for r in points]


def formal_token_rate(cfg, b0_median_rate):
    """正式负载输出率 = global_load_factor × B0 中位最大 SLO 合规混合输出率。"""
    factor = float(cfg["workload"].get("global_load_factor", 0.70))
    return round(factor * float(b0_median_rate), 6)


def isolation_token_rate(cfg, tenant, b0_max):
    """租户隔离 B0-FP16 基线的 token_rate（缺省回退 b0_max）。

    基线测量语义（附-8 K2，verify18 复核）：基线 request_rate = token_rate/cost_len。
    cost_len 大的租户（dialogue/structured）在 b0_max 下请求率过低，基线并发达不到
    max_num_seqs 满并发，基线 p99 被低估 → 相对隔离判定失真。允许 per-tenant 配置
    isolation_token_rate 把基线提至该租户 solo 满载并发水平，使四租户基线同为
    worst-case solo p99（见 a4-mt.yaml tenants 注释）。
    """
    meta = cfg["workload"]["tenants"].get(tenant, {})
    return float(meta.get("isolation_token_rate") or b0_max or 0.0)


def _concurrency_limits(cfg, full_load=False):
    """并发上限：full_load（隔离 B0 基线）解除 per-tenant 限制，仅受 global 约束。

    隔离基线测量语义（附-8 K2）：单租户在 b0_max 容量点独自承担全部 token_rate
    （share=1.0），必须能真正达到 full_load 并发——否则 per_tenant_concurrency
    （为 4 租户混合场景调参）会把单租户限流在远低于混合负载的并发，导致基线 p99
    被低估、相对隔离判定失效（verify16/17 均因此误判）。
    """
    wl = cfg["workload"]
    global_conc = int(wl.get("global_concurrency") or 16)
    tenant_conc = global_conc if full_load else int(wl.get("per_tenant_concurrency") or 4)
    return global_conc, tenant_conc


async def run_mixed(cfg, cases, contract, base_url, client=None, out=None,
                    timeout_s=None, point_rate=None, start_offset=0.0, no_wait=False,
                    full_load=False):
    """执行混合到达序列：global + per-tenant 双重并发控制（附-7/附-8 背压）。

    ARGS:
        cfg / cases / contract    acceptance.expand / cases.generate_cases / scheduler.mixed_arrival
        base_url                  server 根（不含 /v1）
        client                    httpx.AsyncClient（None 自建；测试注入 MockTransport）
        out                       逐请求回执写对象（JSON Lines）；None=不落盘
        point_rate                该段的名义输出 token/s（写入回执 rate 字段）
        start_offset              该段在全局时钟上的起点偏移（s）
        no_wait                   测试专用：跳过 nominal 等待（False=严格按 nominal 发送）
        full_load                 隔离 B0 基线（单租户独占）：解除 per-tenant 并发上限，
                                  仅受 global_concurrency 约束（见 _concurrency_limits）。
    RETURN:
        list[dict] 逐请求回执（含 tenant / rate / nominal_ts_s / sendlag_s）
    """
    wl = cfg["workload"]
    global_conc, tenant_conc = _concurrency_limits(cfg, full_load=full_load)
    if timeout_s is None:
        timeout_s = float(wl.get("timeouts", {}).get("per_request_s") or 600)
    sampling = cfg.get("sampling", {})
    pools = {}
    for c in cases:
        t = c.get("tenant")
        if t:
            pools.setdefault(t, []).append(c)
    sem_g = asyncio.Semaphore(global_conc)
    sem_t = {t: asyncio.Semaphore(tenant_conc) for t in pools}
    counts = {t: 0 for t in pools}
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout_s, trust_env=False)
    start = time.monotonic()
    records, sessions = [], {}

    async def fire(ev):
        t = ev["tenant"]
        async with sem_t[t]:                 # 租户内并发 ≤ per_tenant_concurrency
            async with sem_g:                # 全局并发 ≤ global_concurrency
                nominal = start + start_offset + ev["nominal_ts_s"]
                now = time.monotonic()
                if now < nominal and not no_wait:
                    await asyncio.sleep(nominal - now)
                pool = pools.get(t) or cases
                case = pool[counts.get(t, 0) % len(pool)]
                counts[t] = counts.get(t, 0) + 1
                rec = await executor.execute_one(
                    cfg, case, sampling, base_url, client, timeout_s,
                    history=sessions.get(executor._history_key(case), []))
                rec["tenant"] = t
                rec["rate"] = point_rate
                rec["nominal_ts_s"] = round(ev["nominal_ts_s"], 6)
                rec["sendlag_s"] = round(time.monotonic() - nominal, 6)
                executor._update_history(sessions, case, rec)
                records.append(rec)
                if out is not None:
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    out.flush()

    tasks = [asyncio.create_task(fire(ev)) for ev in contract["arrivals"]]
    await asyncio.gather(*tasks)
    if own_client:
        await client.aclose()
    return records


def _tenant_records(records, tenant):
    return [r for r in records if r.get("tenant") == tenant]


def _tpot_p99(records):
    vals = [dt for r in records if r.get("verdict") == "ok"
            for dt in (r.get("tpot_s") or [])]
    return common.percentile_ms(vals, 99)


def evaluate_scan_point(cfg, records, token_rate, duration_s):
    """单个 B0 容量点的判定：逐租户 SLO(完成/错误) + 公平性 + 合规输出率。

    扫描点只做容量发现（在 b0_capacity_points 上找最大可持续混合输出率）：
    slo_compliant = 全部租户完成/错误门禁 且 份额偏差≤10% 且 Jain≥0.90。
    隔离性（p99≤1.25× 隔离 B0-FP16，附-8 K2）不在扫描点判定——隔离基线的测量点
    是 b0_max（run.py 先扫描、再在 b0_max 容量点采隔离基线），K2 隔离门禁
    在正式负载阶段执行。绝对 SLO 阈值仅作记录（latency_gate=False）。
    """
    tenants = list(cfg["workload"]["tenants"])
    per_tenant = {t: slo.evaluate_tenant(cfg, _tenant_records(records, t), t,
                                         latency_gate=False) for t in tenants}
    fair = fairness.share_report(records, cfg)
    all_slo = all(per_tenant[t].get("slo_pass") for t in tenants)
    return {
        "token_rate": token_rate,
        "achieved_output_tokens_per_s": (round(fair["total_output_tokens"] / duration_s, 4)
                                         if duration_s else None),
        "total_output_tokens": fair["total_output_tokens"],
        "tenants": per_tenant,
        "fairness": fair,
        "slo_compliant": bool(all_slo and fair["share_ok"] and fair["jain_ok"]),
    }


def evaluate_formal(cfg, records, b0_isolated=None):
    """正式混合负载判定：逐租户 SLO + 公平性 + 隔离性（附-8 K2）。

    b0_isolated 缺省（离线自测）时退回绝对延迟门禁；真机验证传隔离基线。
    """
    tenants = list(cfg["workload"]["tenants"])
    per_tenant = {t: slo.evaluate_tenant(cfg, _tenant_records(records, t), t,
                                         latency_gate=b0_isolated is None) for t in tenants}
    fair = fairness.share_report(records, cfg)
    iso = p99_isolation_check(cfg, records, b0_isolated or {})
    pass_n = sum(1 for t in tenants if per_tenant[t].get("slo_pass"))
    overall_pass = (pass_n == len(tenants) and fair["share_ok"] and fair["jain_ok"]
                    and iso["isolation_ok"])
    return {
        "mode": "mixed",
        "tenants": per_tenant,
        "fairness": fair,
        "isolation": iso,
        "overall": {
            "slo_pass": bool(overall_pass),
            "tenants_pass": pass_n,
            "tenants_total": len(tenants),
        },
    }


def p99_isolation_check(cfg, records, b0_isolated):
    """p99 ≤ 1.25× 隔离 B0-FP16（附-8 K2 隔离性证据）。

    ARGS:
        b0_isolated   {tenant: {"ttft_p99_ms": float, "tpot_p99_ms": float}}（B0 隔离运行证据）
    RETURN:
        dict {ratio, tenants[], isolation_ok}
        任一侧缺 B0 基准或 A4 样本时该侧跳过（不判失败）。
    """
    rows = []
    ok_all = True
    for t in cfg["workload"]["tenants"]:
        tr = _tenant_records(records, t)
        ttft_p99 = _p99_ms(tr)
        tpot_p99 = _tpot_p99(tr)
        base = b0_isolated.get(t) or {}
        b_ttft, b_tpot = base.get("ttft_p99_ms"), base.get("tpot_p99_ms")
        ttft_ok = ttft_p99 is None or b_ttft is None or ttft_p99 <= ISOLATION_RATIO * b_ttft
        tpot_ok = tpot_p99 is None or b_tpot is None or tpot_p99 <= ISOLATION_RATIO * b_tpot
        ok_all = ok_all and ttft_ok and tpot_ok
        rows.append({
            "tenant": t,
            "ttft_p99_ms": None if ttft_p99 is None else round(ttft_p99, 3),
            "b0_ttft_p99_ms": b_ttft,
            "ttft_ok": bool(ttft_ok),
            "tpot_p99_ms": None if tpot_p99 is None else round(tpot_p99, 3),
            "b0_tpot_p99_ms": b_tpot,
            "tpot_ok": bool(tpot_ok),
        })
    return {"ratio": ISOLATION_RATIO, "tenants": rows, "isolation_ok": bool(ok_all)}


def _p99_ms(records):
    ttfts = [r["ttft_s"] for r in records
             if r.get("verdict") == "ok" and r.get("ttft_s") is not None]
    return common.percentile_ms(ttfts, 99)
