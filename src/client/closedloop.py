"""C7 A1 闭环算力测量执行器（附-8/组 A）。

closed-loop、固定形状（主 4096×8→1；辅 1024×32→1、8192×4→1）：
  - run_rounds：按「轮」执行，非按秒。每轮顺序遍历全部 shapes，每形状按 batch 并发
    （asyncio.gather）发齐请求；warmup_rounds 轮不计量，measure_rounds 轮计量并记录
    该形状该轮的墙钟与聚合 token（A1 的 warmup/measure 语义为轮数，见 cells/a1.yaml
    timeouts.warmup_s/measure_s 注释）；
  - 回执逐请求带 shape/round/measure 标记；token 数以服务端 usage（prompt_tokens /
    completion_tokens）为准，供 mfu.effective_flops 按真实 token 计算（A1-2 口径）。

与 C6 window.run_closed_loop 的区别：A3 按秒切窗且单 case 串行；A1 按轮 + 多形状 + batch 并发。
"""
import asyncio
import json
import time

import httpx

from client import executor

_CONCURRENT = 4   # 形状内并发信号量上限（防御；batch 一般 ≤32）


async def _fire_batch(cfg, case, sampling, batch, base_url, client, timeout_s):
    """单形状一轮：batch 个请求并发发齐，返回回执列表。"""
    sem = asyncio.Semaphore(_CONCURRENT)

    async def one():
        async with sem:
            return await executor.execute_one(cfg, case, sampling, base_url,
                                              client, timeout_s, history=None)

    return await asyncio.gather(*[one() for _ in range(batch)])


async def run_rounds(cfg, cases, base_url, client=None, out=None, timeout_s=None,
                     warmup_rounds=None, measure_rounds=None):
    """A1 closed-loop：逐轮执行全部固定形状，每轮每形状 batch 并发。

    ARGS:
        cfg / cases    acceptance.expand / cases.generate_cases（a1：每形状 1 个 synthetic case）
        base_url       server 根（不含 /v1）
        client         httpx.AsyncClient（None 自建；测试注入 MockTransport）
        out            measure 段逐请求回执写对象（JSON Lines）；None=不落盘
        timeout_s      per-request 超时（缺省 workload.timeouts.per_request_s）
        warmup_rounds / measure_rounds   轮数（缺省取 timeouts.warmup_s/measure_s，
                       A1 语义为该字段即轮数）
    RETURN:
        (warm_records, measure_records, rounds_stats)
        rounds_stats: measure 段 list[{round, shape, requests, completed, failed,
                      output_tokens, prompt_tokens, completion_tokens, wall_s, tokens_per_s}]
    """
    wl = cfg["workload"]
    shapes = wl["shapes"]
    if warmup_rounds is None:
        warmup_rounds = int(wl.get("timeouts", {}).get("warmup_s", 10))
    if measure_rounds is None:
        measure_rounds = int(wl.get("timeouts", {}).get("measure_s", 30))
    if timeout_s is None:
        timeout_s = float(wl.get("timeouts", {}).get("per_request_s") or 300)
    sampling = cfg.get("sampling", {})
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout_s, trust_env=False)
    warm, meas, stats = [], [], []
    try:
        for rnd in range(warmup_rounds + measure_rounds):
            measure_phase = rnd >= warmup_rounds
            for si, shape in enumerate(shapes):
                case = cases[si % len(cases)]
                batch = int(shape.get("batch") or 1)
                t0 = time.monotonic()
                recs = await _fire_batch(cfg, case, sampling, batch, base_url,
                                         client, timeout_s)
                wall = time.monotonic() - t0
                for rec in recs:
                    rec["shape"] = dict(shape)
                    rec["round"] = rnd + 1
                    rec["measure"] = measure_phase
                if measure_phase:
                    meas.extend(recs)
                    if out is not None:
                        for rec in recs:
                            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        out.flush()
                    pt = max((r.get("prompt_tokens") or 0) for r in recs) or int(shape["input_len"])
                    ct = max((r.get("completion_tokens") or 0) for r in recs) or int(shape["output_len"])
                    stats.append({
                        "round": rnd + 1,
                        "shape": dict(shape),
                        "requests": batch,
                        "completed": sum(1 for r in recs if r.get("verdict") == "ok"),
                        "failed": sum(1 for r in recs if r.get("verdict") in
                                      {"failed", "timeout", "malformed", "silent_truncation"}),
                        "output_tokens": sum(int(r.get("output_tokens") or 0) for r in recs),
                        "prompt_tokens": pt,
                        "completion_tokens": ct,
                        "wall_s": round(wall, 6),
                        "tokens_per_s": round(sum(int(r.get("output_tokens") or 0) for r in recs) / wall, 3)
                        if wall > 0 else 0.0,
                    })
                else:
                    warm.extend(recs)
    finally:
        if own_client:
            await client.aclose()
    return warm, meas, stats
