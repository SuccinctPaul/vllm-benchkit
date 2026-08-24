#!/usr/bin/env python3
"""vllm-xcheck 客户端执行器 + SLO 判定 CLI（C3 + C4）。

用法：
  python src/client/run.py --cell a2-dialogue --precision FP16 \
      --base-url http://127.0.0.1:8010 --datasets-dir datasets --out runs/accepted/a2d
  python src/client/run.py --cell a2-dialogue --precision FP16 \
      --allow-missing --out /tmp/x --selftest        # 离线自测（MockTransport，不起服务）

流程：
  1. acceptance.expand 展开有效配置（fail-closed）；
  2. cases.generate_cases 生成 canonical 请求清单（C1）；
  3. arrival.capacity_scan 生成 Poisson(seed=0) 到达序列（C2，poisson mode）；
  4. executor.run_scan 流式执行，逐请求回执（C3），写 <out>/receipts.jsonl；
  5. slo.evaluate 判定 SLO 合规（C4），写 <out>/slo.json。

closed-loop 形态：a3-32k 窗口分析在 C6 落地（window.run_closed_loop + evaluate_windows，
写 receipts.jsonl + windows.json + contract.json）；a1 闭环算力测量留待 C7。
"""
import argparse
import asyncio
import copy
import json
import math
import os
import sys
import tempfile

import httpx

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from client import (arrival, cases, closedloop, collect_metrics, cost,
                    executor, fairness, gate, lifecycle, mfu, metrics, oracle, roles,
                    scheduler, slo, window)
import acceptance  # noqa: E402


def _base_url(args):
    """解析独立 server 根（不含 /v1）；未显式给出时按端口推导。"""
    return args.base_url or f"http://127.0.0.1:{args.port}"


def _load_or_generate(args, cfg):
    """生成合同（cases + arrival）；closed-loop 无到达序列（由窗口/闭环执行器调度）。"""
    cases_doc = cases.generate_cases(cfg, args.datasets_dir, repeat=0,
                                     allow_missing=args.allow_missing)
    mode = cfg["workload"].get("mode")
    if mode == "poisson":
        return cases_doc, arrival.capacity_scan(cfg)
    if mode in ("mixed", "closed-loop"):
        # mixed：到达按点由调度器生成（scheduler.scan_segments / mixed_arrival）；
        # closed-loop：串行执行无到达序列（window.run_closed_loop / C7 闭环执行器）。
        return cases_doc, None
    raise SystemExit(f"[run] mode={mode!r} 暂不支持")


# --- Z1/Z2/Z3 集成 ---------------------------------------------------------------

def _load_json(path):
    """读 JSON 文件；空路径/缺文件返回 {}。"""
    if not path:
        return {}
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        try:
            return json.load(f)
        except ValueError:
            return {}


def _role_identity(cfg, args):
    """I 组身份：B0/B1 角色 + comparison_type + config_id + I3 SHA 绑定门禁。

    真机上 acceptance.sh 读实际 commit/digest 后经 --code-sha/--image-digest 传入；
    离线（未传）时绑定门禁按 not-applicable 记账。
    """
    meta = cfg.get("meta", {})
    role = getattr(args, "role", None) or meta.get("baseline_role")
    comparison = getattr(args, "comparison_type", None) or meta.get("comparison_type")
    if not role:
        return None
    code_sha = getattr(args, "code_sha", None) or ""
    image_digest = getattr(args, "image_digest", None)
    cfg_id = roles.config_id(cfg, code_sha, image_digest) if code_sha else None
    binding = roles.sha_binding_gate(
        {"code_sha": code_sha, "image_digest": image_digest},
        {"code_sha": code_sha, "image_digest": image_digest},
    )
    return {"baseline_role": role, "comparison_type": comparison,
            "config_id": cfg_id, "sha_binding": binding}


def _mode_metrics(cfg, quality, mode_verdict):
    """从各模式产物抽 primary_metric 稳定键（Z2 中位数口径）。

    ARGS:
        quality       oracle.evaluate 汇总（按 kind 的 ok_pct）
        mode_verdict  dict：{slo: slo.evaluate, windows: window.evaluate_windows,
                             mfu: mfu.mfu_summary, formal: scheduler.evaluate_formal,
                             cost: cost 相关}
    RETURN:
        dict 稳定键 → 数值
    """
    out = {}
    mv = mode_verdict or {}
    mode = cfg.get("workload", {}).get("mode")

    if mode == "poisson":
        v = mv.get("slo") or {}
        compliant = [b for b in v.get("per_bucket", []) if b.get("slo_pass")]
        if compliant:
            best = max(compliant, key=lambda b: b.get("rate") or 0)
            out["max_compliant_rate"] = best.get("rate")
            out["common_load_ttft_mean_ms"] = (best.get("ttft") or {}).get("mean_ms")
            out["common_load_tpot_mean_ms"] = (best.get("tpot") or {}).get("mean_ms")
            out["error_rate"] = best.get("error_rate")
    elif mode == "mixed":
        f = mv.get("formal") or {}
        out["jain"] = (f.get("fairness") or {}).get("jain")
        out["formal_output_tokens_per_s"] = (mv.get("formal_rate"))
    elif mode == "closed-loop":
        if "mfu" in mv and mv["mfu"] is not None:
            out["main_mfu"] = mv["mfu"].get("main_mfu")
        if "windows" in mv and mv["windows"] is not None:
            w = mv["windows"]
            out["throughput_cv"] = w.get("throughput_cv")
            drift = w.get("drift") or []
            out["max_ttft_drift"] = max((d.get("ttft_median_drift") or 0 for d in drift), default=None)
            out["max_tpot_drift"] = max((d.get("tpot_median_drift") or 0 for d in drift), default=None)

    by_kind = (quality.get("summary", {}).get("by_kind") or {})
    if "reason" in by_kind:
        out["quality_accuracy_pct"] = by_kind["reason"]["ok_pct"]
    if "struct" in by_kind:
        out["schema_valid_pct"] = by_kind["struct"]["ok_pct"]
    if "tool_" in by_kind:
        out["tool_pct"] = by_kind["tool_"]["ok_pct"]
    if "long_" in by_kind or "a3_long" in by_kind:
        k = "a3_long" if "a3_long" in by_kind else "long_"
        out["long_ok_pct"] = by_kind[k]["ok_pct"]
    return out


def _finalize(args, cfg, records, config_hash, mode_verdict=None):
    """Z1 全链路收尾 + Z3 证据包：Q oracle → M 机制 → Z3 总门禁 → 落盘。

    ARGS:
        args          CLI（--server-metrics/--code-sha/--dataset-ok 等）
        cfg           展开配置
        records       C3 逐请求回执
        config_hash   合同 config_hash（cases_doc）
        mode_verdict  各模式产物（见 _mode_metrics）
    RETURN:
        evidence_doc（写盘内容；供 Z2 聚合消费）
    """
    quality = oracle.evaluate(cfg, records)
    server_metrics = _load_json(getattr(args, "server_metrics", ""))
    # 未显式传 --server-metrics 时，若提供了 serve-log/pid（服务应尚存活）则自动聚合：
    # 保证 M1/M2/M4 在服务生命周期内采集（A2—A4 真机验证统一走此路径，避免采集时序缺失）。
    if not server_metrics and getattr(args, "serve_log", "") and getattr(args, "server_pid", ""):
        base = getattr(args, "base_url", "") or f"http://127.0.0.1:{args.port}"
        try:
            doc = collect_metrics.aggregate(
                args.serve_log, base.rstrip("/") + "/metrics", pid=args.server_pid,
                receipt_path=getattr(args, "receipt", ""),
                npu_log_path=getattr(args, "npu_log", ""),
                compile_mode=cfg.get("server", {}).get("compile_mode"))
            if doc and (doc.get("graph", {}).get("found")
                        or doc.get("prefix_cache", {}).get("found")
                        or doc.get("cpu_core_table", {}).get("found")):
                server_metrics = doc
                if args.out:
                    _dump(os.path.join(args.out, "server_metrics.json"), doc)
        except (OSError, ValueError):
            pass
    mechanisms = metrics.evaluate_mechanisms(cfg, server_metrics)
    role_ident = _role_identity(cfg, args)

    # A4 成本（K1/K2）：真机项（功耗/资产原值）由 harness 传入；未传则 not-applicable
    cost_res = None
    if cfg["meta"]["cell"] == "a4-mt" and getattr(args, "asset_value_cny", None):
        avg_power = getattr(args, "power_kw", None) or 0.0
        hours = getattr(args, "lifecycle_hours", None) or 0.5
        records = records or []
        ok_tokens = sum(r.get("completion_tokens") or 0 for r in records)
        c = cost.lifecycle_cost(float(args.asset_value_cny), hours, float(avg_power))
        pm = cost.per_million_token_cost(c["machine_runtime_cny"], ok_tokens)
        cost_res = {"ok": True, "per_1e6_tokens_cny": pm, **c}

    evidence = gate.assemble_evidence(
        cfg, records, quality=quality, mechanisms=mechanisms,
        config_ok=True, dataset_ok=getattr(args, "dataset_ok", True),
        role_diff=None, sha_binding=(role_ident or {}).get("sha_binding"),
        cost=cost_res, server_metrics=server_metrics)
    z3 = gate.z3_gate(evidence)
    evidence_doc = {
        "profile": cfg["meta"]["profile"],
        "cell": cfg["meta"]["cell"],
        "precision": cfg["meta"].get("precision"),
        "primary_metric": cfg.get("workload", {}).get("primary_metric"),
        "config_hash": config_hash,
        "mode": cfg.get("workload", {}).get("mode"),
        "role": role_ident,
        "quality": quality,
        "mechanisms": mechanisms,
        "z3": z3,
        "metrics": _mode_metrics(cfg, quality, mode_verdict),
        "cost": cost_res,
    }
    if args.out:
        _dump(os.path.join(args.out, "quality.json"), quality)
        _dump(os.path.join(args.out, "mechanisms.json"), mechanisms)
        _dump(os.path.join(args.out, "gate.json"), z3)
        _dump(os.path.join(args.out, "evidence.json"), evidence_doc)
    print(f"[run] Q: ok={quality['summary']['ok']}/{quality['summary']['total']} "
          f"gate={'PASS' if quality['gate']['ok'] else 'FAIL'}")
    print(f"[run] M: {'PASS' if mechanisms['ok'] else 'FAIL'} "
          f"{' ; '.join(mechanisms['reasons']) if mechanisms['reasons'] else '(无)'}")
    print(f"[run] Z3 证据包: {z3['gate']}")
    return evidence_doc


def _z2_aggregate(args, cfg):
    """Z2：读取 <z2-dir>/<lifecycle-N>/evidence.json，聚合主指标中位数 + 门禁。

    用法：bash scripts/acceptance.sh 每生命周期跑一次 run.py（server 冷启动—测量—停止），
    产物 evidence.json 放同一 z2-dir 下；本命令聚合 3 次取中位数。
    RETURN: 0/1（Z2 门禁是否通过）
    """
    z2_dir = args.z2_dir
    if not z2_dir or not os.path.isdir(z2_dir):
        print(f"[z2] z2-dir 不存在或为空: {z2_dir!r}", file=sys.stderr)
        return 1
    entries = sorted(os.listdir(z2_dir))
    records = []
    primary = cfg.get("workload", {}).get("primary_metric", "")
    for name in entries:
        ev_path = os.path.join(z2_dir, name, "evidence.json")
        if not os.path.exists(ev_path):
            continue
        with open(ev_path, encoding="utf-8") as f:
            try:
                ev = json.load(f)
            except ValueError:
                continue
        z3 = ev.get("z3") or {}
        records.append({
            "index": len(records) + 1,
            "status": "valid" if z3.get("ok") else "invalid",
            "reason": "evidence gate " + str(z3.get("gate", "FAIL")),
            "evidence": ev,
            "primary_value": lifecycle.primary_metric_value(ev, primary),
        })
    if not records:
        print(f"[z2] {z2_dir} 下无 evidence.json", file=sys.stderr)
        return 1
    result = lifecycle.evaluate_z2(records, primary)
    if args.out:
        _dump(os.path.join(args.out, "z2.json"), result)
    print(f"[z2] primary_metric={primary!r} median={result['aggregate']['median']}")
    for r in result["aggregate"]["per_lifecycle"]:
        print(f"  lc{r['index']} {r['status']:<7} value={r['primary_value']}")
    for r in result["reasons"]:
        print(f"  [FAIL] {r}")
    return 0 if result["ok"] else 1


async def _scan(args):
    cfg = acceptance.expand(args.cell, args.precision, args.model_ref)
    if cfg is None:
        return 1
    cases_doc, arr = _load_or_generate(args, cfg)
    mode = cfg["workload"].get("mode")
    if mode == "mixed":
        return await _scan_mixed(args, cfg, cases_doc)
    if mode == "closed-loop":
        if cfg["workload"].get("window_count") is not None:
            return await _scan_closed_loop(args, cfg, cases_doc)   # A3 窗口
        return await _scan_closed_loop_a1(args, cfg, cases_doc)    # A1 闭环算力
    out = None
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        out = open(os.path.join(args.out, "receipts.jsonl"), "w", encoding="utf-8")
    base_url = _base_url(args)
    try:
        records = await executor.run_scan(cfg, cases_doc["cases"], arr, base_url, out=out)
    finally:
        if out is not None:
            out.close()

    verdict = slo.evaluate(cfg, records)
    if args.out:
        with open(os.path.join(args.out, "slo.json"), "w", encoding="utf-8") as f:
            json.dump(verdict, f, ensure_ascii=False, indent=2, sort_keys=False)
        _dump(os.path.join(args.out, "contract.json"), {
            "config_hash": cases_doc["config_hash"],
            "cell_id": cases_doc["cell_id"],
            "cases_sha256": cases_doc["cases_sha256"],
            "arrival_stream_sha256": arr["arrival_stream_sha256"],
            "generator_hash": arr["generator_hash"],
        })
    _finalize(args, cfg, records, cases_doc["config_hash"], mode_verdict={"slo": verdict})

    print(f"[run] {cfg['meta']['profile']} 发送 {len(records)} 请求")
    print(f"[run] SLO: {'PASS' if verdict['overall']['slo_pass'] else 'FAIL'} "
          f"({verdict['overall'].get('buckets_pass', 0)}/{verdict['overall'].get('buckets_total', 0)} 桶合规)")
    for b in verdict.get("per_bucket", []):
        print(f"  rate={b['rate']} cap={b['request_cap']} sent={b['sent']} "
              f"completed={b['completed']}({b['completed_ratio']}) failed={b['failed']} "
              f"ttft={b['ttft'].get('mean_ms')}/{b['ttft'].get('p95_ms')}/{b['ttft'].get('p99_ms')}ms "
              f"gate={b['slo_pass']}")
    return 0 if verdict["overall"]["slo_pass"] else 1


async def _scan_mixed(args, cfg, cases_doc):
    """A4 mixed：B0-FP16 混合容量扫描 → 隔离基线 → 70% 正式负载。

    流程：
      1. 按 b0_capacity_points 逐点跑混合负载（per_point_s 每点，点间 gap_s）；
         容量发现：b0_max = 最大 SLO 合规点（完成/错误门禁 + 份额≤10% + Jain≥0.90）；
      2. 隔离 B0-FP16 基线：在 b0_max 容量点，每租户单租户独自承担全部 token_rate
         （share=1.0，request_rate = b0_max/cost_len，附-8 K2），采 p99 TTFT/TPOT；
      3. 正式负载 = global_load_factor × b0_max（warmup_s 预热不计量，formal_s 计量）；
      4. 判定：正式负载逐租户 SLO + 公平性 + 隔离性（p99≤1.25× 隔离 B0-FP16）入 slo.json；
         receipts-isolation/scan/formal.jsonl 分别落盘。
    返回 0/1（正式负载 SLO 是否通过）。
    """
    base_url = _base_url(args)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
    wl = cfg["workload"]
    warmup_s = int(wl["timeouts"].get("warmup_s", 300))
    formal_s = int(wl["timeouts"].get("formal_s", 3600))
    iso_s = int(wl["timeouts"].get("isolation_s", 300))
    scan_out = formal_out = iso_out = None
    if args.out:
        scan_out = open(os.path.join(args.out, "receipts-scan.jsonl"), "w", encoding="utf-8")
        formal_out = open(os.path.join(args.out, "receipts-formal.jsonl"), "w", encoding="utf-8")
        iso_out = open(os.path.join(args.out, "receipts-isolation.jsonl"), "w", encoding="utf-8")
    try:
        points = scheduler.scan_segments(cfg)
        # 1) B0 混合容量扫描（每点一个确定性混合合同）——容量发现：
        #    slo_compliant = 全租户完成/错误门禁 + 份额偏差≤10% + Jain≥0.90。
        #    隔离性不在扫描点判定（K2 p99≤1.25× 隔离 B0-FP16 在正式负载阶段执行，
        #    隔离基线在 b0_max 容量点测量，见步骤 2）。
        scan_contracts, scan_results = [], []
        for pt in points:
            contract = scheduler.mixed_arrival(cfg, pt["token_rate"], pt["duration_s"])
            scan_contracts.append({"token_rate": pt["token_rate"], **contract})
            records = await scheduler.run_mixed(cfg, cases_doc["cases"], contract, base_url,
                                                out=scan_out, point_rate=pt["token_rate"])
            v = scheduler.evaluate_scan_point(cfg, records, pt["token_rate"], pt["duration_s"])
            scan_results.append(v)
            print(f"[run] scan {pt['token_rate']} tok/s: sent={len(records)} "
                  f"achieved={v['achieved_output_tokens_per_s']} "
                  f"slo={'PASS' if v['slo_compliant'] else 'FAIL'}")

        compliant = [r["token_rate"] for r in scan_results if r["slo_compliant"]]
        b0_max = max(compliant) if compliant else 0.0
        formal_rate = scheduler.formal_token_rate(cfg, b0_max)
        print(f"[run] B0-FP16 最大 SLO 合规混合输出率={b0_max} tok/s -> "
              f"正式负载 0.70×={formal_rate} tok/s")

        # 2) 隔离 B0-FP16 基线：每租户单租户独自承担全部 token_rate（share=1.0，
        #    request_rate = token_rate/cost_len，附-8 K2 "隔离 B0-FP16" 口径），
        #    采 p99 TTFT/TPOT 作为正式负载的相对隔离基准。token_rate 缺省 = b0_max，
        #    可 per-tenant 配置 isolation_token_rate 把基线提至该租户 solo 满载并发
        #    （verify18 复核：cost_len 大的租户在 b0_max 下基线并发不足，p99 被低估）。
        b0_isolated, iso_contracts = {}, []
        if b0_max > 0:
            for t in list(wl["tenants"]):
                cases_t = [c for c in cases_doc["cases"] if c.get("tenant") == t]
                iso_rate = scheduler.isolation_token_rate(cfg, t, b0_max)
                iso_contract = scheduler.mixed_arrival(cfg, iso_rate, iso_s, tenants=[t],
                                                       full_load=True)
                iso_contracts.append({"tenant": t, "token_rate": iso_rate, **iso_contract})
                iso_records = await scheduler.run_mixed(cfg, cases_t, iso_contract, base_url,
                                                        out=iso_out, point_rate=iso_rate,
                                                        full_load=True)
                b0_isolated[t] = {
                    "ttft_p99_ms": scheduler._p99_ms(iso_records),
                    "tpot_p99_ms": scheduler._tpot_p99(iso_records),
                    "sent": len(iso_records),
                }
                print(f"[run] isolation {t}@{iso_rate}: sent={len(iso_records)} "
                      f"ttft_p99={b0_isolated[t]['ttft_p99_ms']}ms "
                      f"tpot_p99={b0_isolated[t]['tpot_p99_ms']}ms")
        else:
            print("[run] 无 SLO 合规扫描点，跳过隔离基线与正式负载")

        # 3) 正式混合负载：warmup 不计量 + measure 计量
        if warmup_s > 0 and formal_rate > 0:
            warm_contract = scheduler.mixed_arrival(cfg, formal_rate, warmup_s)
            await scheduler.run_mixed(cfg, cases_doc["cases"], warm_contract, base_url,
                                      out=None, point_rate=formal_rate)
        formal_contract = scheduler.mixed_arrival(cfg, formal_rate, formal_s)
        formal_records = await scheduler.run_mixed(cfg, cases_doc["cases"], formal_contract,
                                                   base_url, out=formal_out,
                                                   point_rate=formal_rate)
        formal_verdict = scheduler.evaluate_formal(cfg, formal_records, b0_isolated=b0_isolated)
    finally:
        if scan_out is not None:
            scan_out.close()
        if formal_out is not None:
            formal_out.close()
        if iso_out is not None:
            iso_out.close()

    if args.out:
        with open(os.path.join(args.out, "slo.json"), "w", encoding="utf-8") as f:
            json.dump({
                "mode": "mixed",
                "isolation_baseline": b0_isolated,
                "scan": {
                    "points": [r["token_rate"] for r in scan_results],
                    "slo_compliant_points": compliant,
                    "b0_max_slo_compliant_output_tokens_per_s": b0_max,
                    "formal_output_tokens_per_s": formal_rate,
                    "per_point": scan_results,
                },
                "formal": formal_verdict,
            }, f, ensure_ascii=False, indent=2, sort_keys=False)
        _dump(os.path.join(args.out, "contract.json"), {
            "config_hash": cases_doc["config_hash"],
            "cell_id": cases_doc["cell_id"],
            "cases_sha256": cases_doc["cases_sha256"],
            "isolation": [{"tenant": c["tenant"], "token_rate": c["token_rate"],
                           "full_load": True,   # 隔离 B0-FP16：单租户独自承担全部 token_rate
                           "arrival_stream_sha256": c["arrival_stream_sha256"],
                           "generator_hash": c["generator_hash"]} for c in iso_contracts],
            "scan": [{"token_rate": c["token_rate"],
                      "arrival_stream_sha256": c["arrival_stream_sha256"],
                      "generator_hash": c["generator_hash"]} for c in scan_contracts],
            "formal": {"arrival_stream_sha256": formal_contract["arrival_stream_sha256"],
                       "generator_hash": formal_contract["generator_hash"]},
        })
    _finalize(args, cfg, formal_records, cases_doc["config_hash"],
              mode_verdict={"formal": formal_verdict, "formal_rate": formal_rate})

    print(f"[run] {cfg['meta']['profile']} 正式负载发送 {len(formal_records)} 请求")
    print(f"[run] 公平性: Jain={formal_verdict['fairness']['jain']} "
          f"share_ok={formal_verdict['fairness']['share_ok']} "
          f"jain_ok={formal_verdict['fairness']['jain_ok']} "
          f"max_deviation={formal_verdict['fairness']['max_deviation']}")
    print(f"[run] 逐租户 SLO: "
          f"{formal_verdict['overall']['tenants_pass']}/{formal_verdict['overall']['tenants_total']} "
          f"PASS")
    for t, tv in formal_verdict["tenants"].items():
        print(f"  {t:<10} sent={tv['sent']} completed={tv['completed']} "
              f"failed={tv['failed']} ttft={tv['ttft'].get('mean_ms')}/{tv['ttft'].get('p99_ms')}ms "
              f"tpot={tv['tpot'].get('mean_ms')}/{tv['tpot'].get('p99_ms')}ms "
              f"slo={'PASS' if tv['slo_pass'] else 'FAIL'}")
    return 0 if formal_verdict["overall"]["slo_pass"] else 1


async def _scan_closed_loop(args, cfg, cases_doc):
    """A3 closed-loop：warmup 不计量 → measure 计量 → 窗口切分与判定（C6）。

    流程：
      1. window.run_closed_loop 串行执行同一业务长文本 case
         （warmup_s 预热不计量，measure_s 计量，max_requests 封顶）；
      2. window.evaluate_windows 按 window_count×window_s 切窗判定
         （窗口吞吐 CV≤5%、TTFT/TPOT 漂移中位≤10%/p99≤20%、0 失败、
         输入渲染后 tokenIDs ±5%、输出 completion_tokens==output_len）；
      3. 落 receipts.jsonl + windows.json + contract.json。
    返回 0/1（窗口判定是否通过）。
    """
    base_url = _base_url(args)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
    wl = cfg["workload"]
    out = None
    if args.out:
        out = open(os.path.join(args.out, "receipts.jsonl"), "w", encoding="utf-8")
    try:
        warm, meas = await window.run_closed_loop(cfg, cases_doc["cases"], base_url, out=out)
    finally:
        if out is not None:
            out.close()

    verdict = window.evaluate_windows(cfg, meas)
    if args.out:
        with open(os.path.join(args.out, "windows.json"), "w", encoding="utf-8") as f:
            json.dump(verdict, f, ensure_ascii=False, indent=2, sort_keys=False)
        _dump(os.path.join(args.out, "contract.json"), {
            "config_hash": cases_doc["config_hash"],
            "cell_id": cases_doc["cell_id"],
            "cases_sha256": cases_doc["cases_sha256"],
            "mode": "closed-loop",
            "warmup_s": wl["timeouts"].get("warmup_s"),
            "measure_s": wl["timeouts"].get("measure_s"),
            "window_count": wl.get("window_count"),
            "window_s": wl.get("window_s"),
        })
    _finalize(args, cfg, meas, cases_doc["config_hash"],
              mode_verdict={"windows": verdict})

    print(f"[run] {cfg['meta']['profile']} closed-loop warmup={len(warm)} measure={len(meas)}")
    print(f"[run] 窗口判定: {'PASS' if verdict['overall']['pass'] else 'FAIL'} "
          f"CV={verdict['throughput_cv']} 完成={verdict['completed_total']} "
          f"(min={verdict['min_completed_requests']})")
    for w in verdict["windows"]:
        print(f"  w{w['index']} req={w['requests']} ok={w['completed']} failed={w['failed']} "
              f"tps={w['throughput_tokens_per_s']} "
              f"ttft_med={w['ttft']['median_ms']}ms tpot_med={w['tpot']['median_ms']}ms")
    for r in verdict["overall"]["reasons"]:
        print(f"  [FAIL] {r}")
    return 0 if verdict["overall"]["pass"] else 1


async def _scan_closed_loop_a1(args, cfg, cases_doc):
    """A1 closed-loop：按轮跑固定形状（warmup 10 / measure 30 轮）→ 逐形状吞吐 + MFU。

    流程：
      1. closedloop.run_rounds 逐轮执行全部固定形状（每轮每形状 batch 并发）；
      2. mfu.mfu_summary 按回执真实 token 计逐算子有效 FLOPs（A1-2 v1），
         算主/辅形状 MFU（分母 = ascend-dmi 峰值，缺省内置占位）；
      3. 落 receipts.jsonl + rounds.json + mfu.json + contract.json。
    返回 0（执行成功；MFU 达标判定属组 A，待真机 ascend-dmi 峰值后判定）。
    """
    base_url = _base_url(args)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
    wl = cfg["workload"]
    out = None
    if args.out:
        out = open(os.path.join(args.out, "receipts.jsonl"), "w", encoding="utf-8")
    try:
        warm, meas, stats = await closedloop.run_rounds(cfg, cases_doc["cases"],
                                                        base_url, out=out)
    finally:
        if out is not None:
            out.close()

    summary = mfu.mfu_summary(stats, arch=mfu.arch_for(cfg),
                              shape_order=wl["shapes"])
    if args.out:
        with open(os.path.join(args.out, "rounds.json"), "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2, sort_keys=False)
        with open(os.path.join(args.out, "mfu.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, sort_keys=False)
        _dump(os.path.join(args.out, "contract.json"), {
            "config_hash": cases_doc["config_hash"],
            "cell_id": cases_doc["cell_id"],
            "cases_sha256": cases_doc["cases_sha256"],
            "mode": "closed-loop",
            "shapes": wl["shapes"],
            "warmup_rounds": wl["timeouts"].get("warmup_s"),
            "measure_rounds": wl["timeouts"].get("measure_s"),
        })
    _finalize(args, cfg, meas, cases_doc["config_hash"],
              mode_verdict={"mfu": summary})

    print(f"[run] {cfg['meta']['profile']} closed-loop warmup={len(warm)} measure={len(meas)} "
          f"rounds={len(stats)}")
    for s in summary["per_shape"]:
        sh = s["shape"]
        m = "n/a" if s["mfu"] is None else f"{s['mfu'] * 100:.2f}%"
        print(f"  {sh['input_len']}x{sh['batch']}->{sh['output_len']} rounds={s['rounds']} "
              f"eff={s['effective_flops_per_s']:.3e} FLOPs/s mfu={m}")
    main_m = ("n/a" if summary["main_mfu"] is None
              else f"{summary['main_mfu'] * 100:.2f}%")
    print(f"[run] 主形状 MFU={main_m}（峰值分母 source={summary['peak_source']}，"
          f"真机 ascend-dmi 应覆盖）")
    return 0


# --- 离线自测（httpx.MockTransport，不起服务） ------------------------------------

_CHAT_SSE = (
    'data: {"id":"x","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}\n\n'
    'data: {"id":"x","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}\n\n'
    'data: {"id":"x","choices":[{"index":0,"delta":{"content":" world"},"finish_reason":null}]}\n\n'
    'data: {"id":"x","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    'data: [DONE]\n\n'
)
_COMP_SSE = (
    'data: {"id":"x","choices":[{"index":0,"text":"a"}]}\n\n'
    'data: {"id":"x","choices":[{"index":0,"text":"b"}]}\n\n'
    'data: {"id":"x","choices":[{"index":0,"finish_reason":"stop"}]}\n\n'
    'data: [DONE]\n\n'
)
_LENGTH_SSE = (
    'data: {"id":"x","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}\n\n'
    'data: {"id":"x","choices":[{"index":0,"delta":{"content":"x"},"finish_reason":null}]}\n\n'
    'data: {"id":"x","choices":[{"index":0,"delta":{},"finish_reason":"length"}]}\n\n'
    'data: [DONE]\n\n'
)
_EMPTY_SSE = (
    'data: {"id":"x","choices":[{"index":0,"delta":{},"finish_reason":null}]}\n\n'
    'data: [DONE]\n\n'
)
# 工具调用流：首片带 id/name，后续片仅 arguments 增量，finish=tool_calls
_TOOL_SSE = (
    'data: {"id":"x","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}\n\n'
    'data: {"id":"x","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_abc","type":"function","function":{"name":"get_weather","arguments":""}}]},"finish_reason":null}]}\n\n'
    'data: {"id":"x","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"city\\": \\"北京\\"}"}}]},"finish_reason":null}]}\n\n'
    'data: {"id":"x","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\n'
    'data: [DONE]\n\n'
)


def _mock_client(responses, error_on=()):
    async def handler(request):
        # 命中 error_on 的请求返回非 2xx（错误注入）
        if request.url.path in error_on:
            return httpx.Response(500, text="boom")
        kind = request.url.path
        body = responses.get(kind, _CHAT_SSE)
        return httpx.Response(200, content=body.encode("utf-8"),
                              headers={"content-type": "text/event-stream"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


async def _selftest(args):
    cfg = acceptance.expand(args.cell, args.precision, args.model_ref)
    if cfg is None:
        return 1
    # 缩小到可快速完成的规模：自建迷你到达段（不经 capacity_scan，避免大 cap）
    cases_doc = cases.generate_cases(cfg, args.datasets_dir, repeat=0,
                                     allow_missing=args.allow_missing)
    base = "http://test"
    failures = []

    def check(name, ok, detail=""):
        print(f"  [{'ok ' if ok else 'FAIL'}] {name} {detail}")
        if not ok:
            failures.append(name)

    # 1) 正常 chat 流 → verdict=ok，TTFT/TPOT/E2EL/body_sha256 齐全
    arr = {"segments": [{"rate": 0.1, "cap": 2, "nominal_ts_s": [0.0, 0.1]}]}
    cases_sub = cases_doc["cases"][:2]
    client = _mock_client({"/v1/chat/completions": _CHAT_SSE})
    recs = await executor.run_scan(cfg, cases_sub, arr, base, client=client)
    ok = all(r["verdict"] == "ok" for r in recs)
    r0 = recs[0]
    check("ok-verdict", ok, f"verdicts={[r['verdict'] for r in recs]}")
    check("ok-fields", (r0["ttft_s"] is not None and len(r0["tpot_s"]) >= 1
                        and r0["e2el_s"] is not None and r0["body_sha256"]),
          f"ttft={r0['ttft_s']} tpot={r0['tpot_s']} e2el={r0['e2el_s']}")
    check("ok-output", r0["output_tokens"] == 2 and r0["output_text"] == "Hello world"
          and r0["finish_reason"] == "stop", f"tokens={r0['output_tokens']} text={r0['output_text']!r}")

    # 2) 超时 → verdict=timeout（MockTransport 无真实 socket，无法用 sleep 触发；
    #    以 handler 抛 httpx.ReadTimeout 模拟读超时路径）
    async def timeout_handler(request):
        raise httpx.ReadTimeout("simulated read timeout")
    slow = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler), timeout=0.05)
    rec = (await executor.run_scan(cfg, cases_sub, arr, base, client=slow))[0]
    check("timeout-verdict", rec["verdict"] == "timeout", f"verdict={rec['verdict']} err={rec['error']}")

    # 3) 非 2xx → verdict=failed（保留错误不吞错）
    err_client = _mock_client({}, error_on=("/v1/chat/completions",))
    rec = (await executor.run_scan(cfg, cases_sub, arr, base, client=err_client))[0]
    check("failed-verdict", rec["verdict"] == "failed" and "500" in rec["error"],
          f"verdict={rec['verdict']} err={rec['error']}")

    # 4) 可变输出 finish=length → silent_truncation
    len_client = _mock_client({"/v1/chat/completions": _LENGTH_SSE})
    rec = (await executor.run_scan(cfg, cases_sub, arr, base, client=len_client))[0]
    check("silent-truncation", rec["verdict"] == "silent_truncation" and rec["truncated"],
          f"verdict={rec['verdict']} truncated={rec['truncated']}")

    # 5) 0 输出 token → malformed（空输出）
    emp_client = _mock_client({"/v1/chat/completions": _EMPTY_SSE})
    rec = (await executor.run_scan(cfg, cases_sub, arr, base, client=emp_client))[0]
    check("empty-malformed", rec["verdict"] == "malformed" and "empty output" in rec["error"],
          f"verdict={rec['verdict']} err={rec['error']}")

    # 6) 固定输出形状 case（kind=long_8192）finish=length → ok（完整输出）
    fixed_case = dict(cases_sub[0], kind="long_8192",
                      shape={"input_len": 8192, "output_len": 512}, max_tokens=512)
    rec = (await executor.run_scan(cfg, [fixed_case],
                                   {"segments": [{"rate": 0.1, "cap": 1, "nominal_ts_s": [0.0]}]},
                                   base, client=len_client))[0]
    check("fixed-length-ok", rec["verdict"] == "ok" and rec["truncated"],
          f"verdict={rec['verdict']} truncated={rec['truncated']}")

    # 6a) 工具调用 SSE 解析：id/name/arguments 按 index 累积 → output_tool_calls（Q 门禁前置）
    tool_case = {"request_id": "t1", "sequence_no": 0, "kind": "tool_single", "tenant": "tool",
                 "content": "Call the get_weather tool for Beijing.",
                 "tools": [{"type": "function", "function": {
                     "name": "get_weather", "description": "weather",
                     "parameters": {"type": "object",
                                    "properties": {"city": {"type": "string"}},
                                    "required": ["city"]}}}],
                 "send_tools": True, "tool_choice": "auto", "max_tokens": 256}
    tool_client = _mock_client({"/v1/chat/completions": _TOOL_SSE})
    trec = await executor.execute_one(cfg, tool_case, cfg["sampling"], base,
                                      tool_client, 5.0)
    exp_calls = [{"id": "call_abc", "type": "function",
                  "function": {"name": "get_weather", "arguments": '{"city": "北京"}'}}]
    check("tool-sse-parsed", trec["verdict"] == "ok"
          and trec["finish_reason"] == "tool_calls"
          and trec["output_tool_calls"] == exp_calls
          and trec["output_text"] == "" and trec["output_tokens"] >= 2,
          f"verdict={trec['verdict']} calls={trec['output_tool_calls']} "
          f"text={trec['output_text']!r} tokens={trec['output_tokens']}")
    # 6b) 回执透传 schema（struct Q 校验依赖）
    struct_case = {"request_id": "s1", "sequence_no": 1, "kind": "struct", "tenant": "structured",
                   "content": "Generate JSON per schema",
                   "schema": {"type": "object",
                              "properties": {"name": {"type": "string"}},
                              "required": ["name"]}, "max_tokens": 512}
    srec = await executor.execute_one(cfg, struct_case, cfg["sampling"], base,
                                      _mock_client({"/v1/chat/completions": _CHAT_SSE}), 5.0)
    check("schema-propagated", srec["schema"] == struct_case["schema"],
          f"schema={srec.get('schema')}")

    # 7) SLO 判定：构造迷你 cfg（声明 rate=0.1 / cap=2），全部 ok → 通过；注入失败 → 不通过
    mini = copy.deepcopy(cfg)
    mini["workload"]["rates"] = [0.1]
    mini["workload"]["caps"] = [2]
    good = [{"verdict": "ok", "rate": 0.1, "input_len": None, "ttft_s": 0.05,
             "tpot_s": [0.01, 0.01]},
            {"verdict": "ok", "rate": 0.1, "input_len": None, "ttft_s": 0.04,
             "tpot_s": [0.01, 0.01]}]
    v = slo.evaluate(mini, good)
    check("slo-pass", v["overall"]["slo_pass"] and v["overall"]["buckets_pass"] == 1,
          f"pass={v['overall']['slo_pass']} buckets_pass={v['overall'].get('buckets_pass')}")
    bad = [{"verdict": "timeout", "rate": 0.1, "input_len": None},
           {"verdict": "ok", "rate": 0.1, "input_len": None, "ttft_s": 0.05, "tpot_s": []}]
    v = slo.evaluate(mini, bad)
    check("slo-fail-error-gate",
          (not v["overall"]["slo_pass"]) and v["undeclared_rates"] == [],
          f"pass={v['overall']['slo_pass']}")
    # 未声明 rate 点 → fail-closed
    rogue = [{"verdict": "ok", "rate": 9.9, "input_len": None, "ttft_s": 0.05, "tpot_s": []}]
    v = slo.evaluate(mini, rogue)
    check("slo-undeclared-rate", (not v["overall"]["slo_pass"]) and v["undeclared_rates"] == [9.9],
          f"undeclared={v['undeclared_rates']}")

    # 8) 普通 chat cell 用 completions SSE 走一遍 completions 分支（A1 走 /v1/completions）
    cfg1 = acceptance.expand("a1", "FP16")
    cases_a1 = cases.generate_cases(cfg1, args.datasets_dir, allow_missing=args.allow_missing)
    rec = (await executor.run_scan(cfg1, cases_a1["cases"][:1],
                                   {"segments": [{"rate": 0.1, "cap": 1, "nominal_ts_s": [0.0]}]},
                                   base, client=_mock_client({"/v1/completions": _COMP_SSE})))[0]
    check("completions-branch", rec["verdict"] == "ok" and rec["output_tokens"] == 2,
          f"verdict={rec['verdict']} tokens={rec['output_tokens']}")

    # 9) long 场景按输入档（slo.shapes）判定
    mini_long = copy.deepcopy(cfg)
    mini_long["workload"]["rates"] = [0.05]
    mini_long["workload"]["caps"] = [2]
    mini_long["workload"]["slo"] = {
        "shapes": {
            "8192": {"ttft": {"mean_ms": 8000, "p95_ms": 12000, "p99_ms": 16000},
                     "tpot": {"mean_ms": 40, "p95_ms": 60, "p99_ms": 80}},
            "16384": {"ttft": {"mean_ms": 16000, "p95_ms": 24000, "p99_ms": 32000},
                      "tpot": {"mean_ms": 40, "p95_ms": 60, "p99_ms": 80}},
        }
    }
    good_long = [{"verdict": "ok", "rate": 0.05, "input_len": 8192, "ttft_s": 1.0, "tpot_s": [0.03]},
                 {"verdict": "ok", "rate": 0.05, "input_len": 16384, "ttft_s": 2.0, "tpot_s": [0.03]}]
    v = slo.evaluate(mini_long, good_long)
    check("slo-long-buckets", v["overall"]["slo_pass"] and v["overall"]["buckets_pass"] == 2,
          f"pass={v['overall']['slo_pass']} buckets_pass={v['overall'].get('buckets_pass')}")
    bad_long = [{"verdict": "ok", "rate": 0.05, "input_len": 8192, "ttft_s": 20.0, "tpot_s": [0.03]}]
    v = slo.evaluate(mini_long, bad_long)
    violated = (v["per_bucket"][0]["ttft"].get("violated") or []) if v["per_bucket"] else []
    check("slo-long-fail", not v["overall"]["slo_pass"] and any("mean_ms" in x for x in violated),
          f"violated={violated}")

    # 10) C5 A4 租户调度：混合到达 + 并发 + 份额/公平性 + 逐租户 SLO + 隔离性
    cfg_a4 = acceptance.expand("a4-mt", "FP16")
    cases_a4 = cases.generate_cases(cfg_a4, args.datasets_dir, allow_missing=args.allow_missing)
    wl_a4 = cfg_a4["workload"]["tenants"]
    # 10a) 请求速率 ∝ share/cost_len（cost_len=调度成本，与 max_tokens 上限 output_cap 分离）
    rates = scheduler.tenant_request_rates(cfg_a4, 4096)
    expect_rate = {n: 4096 * float(m["share"]) / float(m.get("cost_len") or m.get("output_cap") or 1)
                   for n, m in wl_a4.items()}
    check("mixed-rate-ratio",
          all(abs(rates[n]["request_rate"] - expect_rate[n]) < 1e-9 for n in wl_a4)
          and all(rates[n]["cost_len"] == float(wl_a4[n].get("cost_len") or wl_a4[n]["output_cap"])
                  for n in wl_a4),
          f"rates={ {n: rates[n]['request_rate'] for n in wl_a4} }")
    # 10b) mixed_arrival 确定性：同输入同 sha；计数=ceil(request_rate×duration)；时间线升序
    c1 = scheduler.mixed_arrival(cfg_a4, 4096, 1.0)
    c2 = scheduler.mixed_arrival(cfg_a4, 4096, 1.0)
    check("mixed-arrival-deterministic",
          c1["arrival_stream_sha256"] == c2["arrival_stream_sha256"]
          and c1["generator_hash"] == c2["generator_hash"], "sha256")
    # 10b-1) 单租户过滤（隔离 B0 基线）：只生成该租户到达，计数=ceil(request_rate×duration)
    iso_c = scheduler.mixed_arrival(cfg_a4, 4096, 1.0, tenants=["dialogue"])
    iso_tenants = {ev["tenant"] for ev in iso_c["arrivals"]}
    check("mixed-arrival-tenant-filter",
          iso_tenants == {"dialogue"}
          and len(iso_c["arrivals"]) == math.ceil(expect_rate["dialogue"] * 1.0),
          f"tenants={iso_tenants} n={len(iso_c['arrivals'])} "
          f"exp={math.ceil(expect_rate['dialogue'] * 1.0)}")
    cnt = {}
    for ev in c1["arrivals"]:
        cnt[ev["tenant"]] = cnt.get(ev["tenant"], 0) + 1
    expect_cnt = {n: math.ceil(expect_rate[n] * 1.0) for n in wl_a4}
    ts_sorted = all(c1["arrivals"][i]["nominal_ts_s"] <= c1["arrivals"][i + 1]["nominal_ts_s"]
                    for i in range(len(c1["arrivals"]) - 1))
    check("mixed-arrival-counts", cnt == expect_cnt and ts_sorted,
          f"counts={cnt} expect={expect_cnt} sorted={ts_sorted}")
    # 10c) run_mixed + MockTransport：回执租户分配正确、计数符合到达合同、rate 字段正确
    recs = await scheduler.run_mixed(cfg_a4, cases_a4["cases"], c1, base,
                                     client=_mock_client({"/v1/chat/completions": _CHAT_SSE}),
                                     point_rate=4096, no_wait=True)
    rec_cnt = {}
    for r in recs:
        rec_cnt[r["tenant"]] = rec_cnt.get(r["tenant"], 0) + 1
    check("mixed-run-tenants",
          all(r.get("tenant") in wl_a4 for r in recs)
          and rec_cnt == expect_cnt and all(r["rate"] == 4096 for r in recs),
          f"n={len(recs)} counts={rec_cnt}")
    # 10c-1) 隔离 B0 基线 full_load：解除 per-tenant 上限（仅 global 约束），
    #        使单租户能真正独占 full_load 并发（附-8 K2 基线测量语义）。
    wl_a4_cfg = cfg_a4["workload"]
    g_lo, t_lo = scheduler._concurrency_limits(cfg_a4, full_load=False)
    g_hi, t_hi = scheduler._concurrency_limits(cfg_a4, full_load=True)
    check("mixed-fullload-concurrency",
          t_lo == int(wl_a4_cfg["per_tenant_concurrency"]) and g_hi == g_lo
          and t_hi == g_hi,
          f"normal=({g_lo},{t_lo}) full_load=({g_hi},{t_hi}) "
          f"per_tenant={wl_a4_cfg['per_tenant_concurrency']}")
    # 10c-3) per-tenant isolation_token_rate：配置键生效，缺省/未知租户回退 b0_max
    iso_rates = {t: scheduler.isolation_token_rate(cfg_a4, t, 192) for t in wl_a4}
    check("mixed-isolation-token-rate",
          iso_rates["dialogue"] == 405.0 and iso_rates["tool"] == 192.0
          and iso_rates["reasoning"] == 192.0 and iso_rates["structured"] == 504.0
          and scheduler.isolation_token_rate(cfg_a4, "no-such-tenant", 192) == 192.0,
          f"rates={iso_rates} fallback=192")
    # 10c-2) cost_len 校准公平性：实际输出≈cost_len 时 dispatch ∝ share/cost_len → 份额≈25%
    syn = []
    for t, n in cnt.items():
        for _ in range(n):
            syn.append({"tenant": t, "verdict": "ok", "input_len": None,
                        "ttft_s": 0.05, "tpot_s": [0.01, 0.01],
                        "output_tokens": rates[t]["cost_len"]})
    fair = fairness.share_report(syn, cfg_a4)
    check("mixed-fairness-costlen", fair["share_ok"] and fair["jain_ok"]
          and fair["jain"] is not None and fair["jain"] >= 0.90,
          f"dev={fair['max_deviation']} jain={fair['jain']}")
    # 10d) 逐租户 SLO（evaluate_formal）：全租户 ok → PASS
    fv = scheduler.evaluate_formal(cfg_a4, syn)
    check("mixed-formal-slo", fv["overall"]["slo_pass"]
          and fv["overall"]["tenants_pass"] == fv["overall"]["tenants_total"] == 4,
          f"tenants_pass={fv['overall']['tenants_pass']}")
    # 10e) p99 ≤ 1.25× 隔离 B0-FP16（K2 隔离性证据）
    b0 = {t: {"ttft_p99_ms": 1000, "tpot_p99_ms": 40} for t in wl_a4}
    iso = scheduler.p99_isolation_check(cfg_a4, syn, b0)
    check("mixed-isolation", iso["isolation_ok"] and iso["ratio"] == 1.25,
          f"rows={iso['tenants']}")
    # 10f) 容量点判定：slo_compliant（全租户 SLO + 份额 + Jain）；achieved=Σ(count×cost_len)/duration
    exp_achieved = round(sum(n * rates[t]["cost_len"] for t, n in cnt.items()) / 1.0, 4)
    sp = scheduler.evaluate_scan_point(cfg_a4, syn, 4096, 1.0)
    check("mixed-scan-point", sp["slo_compliant"]
          and abs(sp["achieved_output_tokens_per_s"] - exp_achieved) < 1e-9,
          f"achieved={sp['achieved_output_tokens_per_s']} exp={exp_achieved} compliant={sp['slo_compliant']}")

    # 11) C6 A3 窗口分析：窗口切分 + 判定（CV/漂移/失败/token 门禁）+ closed-loop 执行
    cfg_a3 = acceptance.expand("a3-32k", "FP16")
    # 11a) 稳定序列（每窗 6 条均匀分布）：CV=0、漂移 0、0 失败、token 全合规 → PASS
    stable = [{"verdict": "ok", "measure_ts_s": i * 50.0, "ttft_s": 0.5, "tpot_s": [0.02],
               "output_tokens": 2048, "prompt_tokens": 30720, "completion_tokens": 2048}
              for i in range(40)]
    v = window.evaluate_windows(cfg_a3, stable)
    check("window-stable-pass", v["overall"]["pass"]
          and v["throughput_cv"] == 0.0 and v["throughput_cv_ok"]
          and v["drift_ok"] and v["zero_failure_ok"] and v["min_completed_ok"]
          and v["input_ok"] and v["output_ok"],
          f"cv={v['throughput_cv']} reasons={v['overall']['reasons']}")
    # 11b) 窗口切分：6×300s，空窗口保留；1 条 + 5 空窗 → 吞吐 CV 捕获
    sparse = [dict(stable[0], measure_ts_s=10.0)]
    wins = window.split_windows(sparse, 6, 300)
    check("window-split-empty-kept",
          len(wins) == 6 and len(wins[0]["records"]) == 1
          and all(len(w["records"]) == 0 for w in wins[1:]),
          f"lens={[len(w['records']) for w in wins]}")
    v = window.evaluate_windows(cfg_a3, sparse)
    check("window-stall-cv", (not v["throughput_cv_ok"]) and not v["overall"]["pass"],
          f"cv={v['throughput_cv']} reasons={v['overall']['reasons']}")
    # 11c) 静默截断 → zero_failure_ok=False
    v = window.evaluate_windows(cfg_a3, [dict(stable[0], verdict="silent_truncation")])
    check("window-silent-fail", (not v["zero_failure_ok"]) and not v["overall"]["pass"],
          f"reasons={v['overall']['reasons']}")
    # 11d) 第 2 窗 TTFT 中位数 +20% → 漂移超限
    drift_recs = [dict(r, ttft_s=0.6 if 6 <= i < 12 else r["ttft_s"])
                  for i, r in enumerate(stable)]
    v = window.evaluate_windows(cfg_a3, drift_recs)
    check("window-drift-fail", (not v["drift_ok"]) and not v["overall"]["pass"]
          and abs(v["drift"][1]["ttft_median_drift"]) > 0.10,
          f"d={v['drift'][1]['ttft_median_drift']} reasons={v['overall']['reasons']}")
    # 11e) 输出不完整（completion_tokens != 2048）→ output 门禁
    v = window.evaluate_windows(cfg_a3, [dict(stable[0], completion_tokens=1024)])
    check("window-output-gate", (not v["output_ok"]) and not v["overall"]["pass"],
          f"reasons={v['overall']['reasons']}")
    # 11f) closed-loop 串行执行（MockTransport）：warmup 不计量、measure 计量带 measure_ts_s
    mini_a3 = copy.deepcopy(cfg_a3)
    mini_a3["workload"]["timeouts"]["warmup_s"] = 0
    mini_a3["workload"]["timeouts"]["measure_s"] = 0.5
    mini_a3["workload"]["max_requests"] = 8
    cases_a3 = cases.generate_cases(cfg_a3, args.datasets_dir, allow_missing=args.allow_missing)
    warm, meas = await window.run_closed_loop(
        mini_a3, cases_a3["cases"], base,
        client=_mock_client({"/v1/chat/completions": _CHAT_SSE}), timeout_s=1.0)
    check("window-closedloop",
          len(warm) == 0 and 1 <= len(meas) <= 8
          and all(r["verdict"] == "ok" for r in meas)
          and all(r.get("measure_ts_s") is not None for r in meas),
          f"warm={len(warm)} meas={len(meas)}")

    # 12) C7 A1 闭环算力：逐算子有效 FLOPs + MFU + 按轮 batch 并发执行
    cfg_a1 = acceptance.expand("a1", "FP16")
    # 12a) 逐算子有效 FLOPs 确定性 + 单调（更多 token → 更多 FLOPs）
    fl1 = mfu.effective_flops(mfu.ARCH_QWEN25_14B, 8, 4096, 1)
    fl2 = mfu.effective_flops(mfu.ARCH_QWEN25_14B, 8, 4096, 1)
    fl_big = mfu.effective_flops(mfu.ARCH_QWEN25_14B, 8, 8192, 1)
    check("mfu-deterministic-monotonic", fl1 == fl2 and fl_big > fl1,
          f"flops={fl1} big={fl_big}")
    # 12b) 量级：prefill 每 token ≈ 2×N 量级（N≈14.77e9）
    pref1 = mfu.effective_flops(mfu.ARCH_QWEN25_14B, 1, 4096, 0)
    per_tok = pref1 / 4096.0
    check("mfu-prefill-magnitude", 1.5e10 < per_tok < 8e10,
          f"per_token_flops={per_tok:.3e}")
    # 12c) mfu_summary 公式：MFU = Σeff_flops / wall / peak（显式峰值）
    stats_c = [{"shape": {"input_len": 4096, "batch": 8, "output_len": 1},
                "completed": 8, "failed": 0,
                "prompt_tokens": 4096, "completion_tokens": 1, "wall_s": 2.0},
               {"shape": {"input_len": 4096, "batch": 8, "output_len": 1},
                "completed": 8, "failed": 0,
                "prompt_tokens": 4096, "completion_tokens": 1, "wall_s": 2.0}]
    sm = mfu.mfu_summary(stats_c, peak_flops=1e15)
    expect = fl1 * 2 / 4.0 / 1e15
    check("mfu-formula", sm["peak_source"] == "env" and sm["main_mfu"] is not None
          and abs(sm["main_mfu"] - round(expect, 6)) < 1e-9,
          f"mfu={sm['main_mfu']} expect={expect}")
    # 12d) run_rounds：warmup=1 / measure=2 轮 × 1 形状 × batch 2 并发
    mini_a1 = copy.deepcopy(cfg_a1)
    mini_a1["workload"]["shapes"] = [{"input_len": 8, "batch": 2, "output_len": 1}]
    mini_a1["workload"]["timeouts"]["per_request_s"] = 2
    cases_a1 = cases.generate_cases(mini_a1, args.datasets_dir, allow_missing=args.allow_missing)
    warm, meas, stats = await closedloop.run_rounds(
        mini_a1, cases_a1["cases"], base,
        client=_mock_client({"/v1/completions": _COMP_SSE}),
        warmup_rounds=1, measure_rounds=2)
    check("closedloop-rounds",
          len(warm) == 2 and len(meas) == 4 and len(stats) == 2
          and all(s["completed"] == 2 for s in stats)
          and all(r["measure"] for r in meas) and not any(r["measure"] for r in warm),
          f"warm={len(warm)} meas={len(meas)} stats={len(stats)}")
    # 12e) 合成 prompt 确定性：同 input_len 两次字节一致
    p1 = cases._synthetic_prompt(64)
    p2 = cases._synthetic_prompt(64)
    check("synthetic-prompt-deterministic", p1 == p2 and len(p1) >= 64 * 4,
          f"len={len(p1)}")
    # 12f) 全失败轮不计量：HTTP 502 秒失败不虚高 MFU（复现 204500 的 1772% 场景）
    stats_bad = [{"shape": {"input_len": 4096, "batch": 8, "output_len": 1},
                  "completed": 0, "failed": 8,
                  "prompt_tokens": 4096, "completion_tokens": 1, "wall_s": 0.3}]
    sm_bad = mfu.mfu_summary(stats_bad, peak_flops=1e15)
    check("mfu-failed-round-excluded", sm_bad["per_shape"] == []
          and sm_bad["main_mfu"] is None and sm_bad["included_rounds"] == 0,
          f"per_shape={sm_bad['per_shape']} main={sm_bad['main_mfu']}")
    # 12g) 部分成功轮：只按 completed 计 FLOPs（8 中 3 成功 → 3 请求的 FLOPs / 全轮墙钟）
    stats_part = [{"shape": {"input_len": 4096, "batch": 8, "output_len": 1},
                   "completed": 3, "failed": 5,
                   "prompt_tokens": 4096, "completion_tokens": 1, "wall_s": 2.0}]
    sm_part = mfu.mfu_summary(stats_part, peak_flops=1e15)
    eff3 = mfu.effective_flops(mfu.ARCH_QWEN25_14B, 3, 4096, 1)
    expect_part = eff3 / 2.0 / 1e15
    check("mfu-partial-completed", sm_part["main_mfu"] is not None
          and abs(sm_part["main_mfu"] - round(expect_part, 6)) < 1e-9,
          f"mfu={sm_part['main_mfu']} expect={expect_part}")
    # 12h) shape_order：声明主形状优先；主形状无成功轮时落到下一个有数据形状
    sm_ord = mfu.mfu_summary(stats_c + [{"shape": {"input_len": 8192, "batch": 4,
                                                   "output_len": 1},
                                         "completed": 0, "failed": 4,
                                         "prompt_tokens": 8192, "completion_tokens": 1,
                                         "wall_s": 0.1}],
                             shape_order=[{"input_len": 8192, "batch": 4, "output_len": 1},
                                          {"input_len": 4096, "batch": 8, "output_len": 1}])
    check("mfu-shape-order", sm_ord["main_shape"]["input_len"] == 4096
          and len(sm_ord["per_shape"]) == 1,
          f"main={sm_ord['main_shape']} n={len(sm_ord['per_shape'])}")

    # 13) I1-I3：角色化差分 + comparison_id/config_id + SHA 绑定门禁
    cfg_a2 = acceptance.expand("a2-dialogue", "FP16")
    b0 = roles.apply_role(cfg_a2, "B0", "SYSTEM_DELIVERY",
                          **{"model.served_name": "qwen-b0"})
    b1 = roles.apply_role(cfg_a2, "B1", "SYSTEM_DELIVERY",
                          **{"model.served_name": "qwen-b1",
                             "model.quantization": "ascend"})
    d = roles.diff_roles(b0, b1, "SYSTEM_DELIVERY")
    check("roles-diff", d["equal"], f"diffs={d['diffs']}")
    cid1 = roles.comparison_id(b0, b1, "SYSTEM_DELIVERY")
    cid2 = roles.comparison_id(b0, b1, "SYSTEM_DELIVERY")
    check("roles-comparison-id", cid1 == cid2 and len(cid1) == 64, f"id={cid1[:12]}...")
    # 公共配置 sha：去掉角色字段后两侧一致
    sha_b0 = roles.config_id(b0, "deadbeef")["config_sha256"]
    sha_b1 = roles.config_id(b1, "deadbeef")["config_sha256"]
    check("roles-common-config-sha", sha_b0 == sha_b1, f"{sha_b0[:8]} vs {sha_b1[:8]}")
    ok_b = roles.sha_binding_gate({"code_sha": "abc"}, {"code_sha": "abc"})
    bad_b = roles.sha_binding_gate({"code_sha": "abc", "image_digest": "sha256:x"},
                                   {"code_sha": "abc", "image_digest": "sha256:y"})
    check("roles-sha-binding", ok_b["ok"] and not bad_b["ok"],
          f"ok={ok_b['ok']} bad={bad_b['ok']}")

    # 14) Q1-Q5：质量 oracle 逐条判定 + W8A8 资格门禁
    rec_reason = {"request_id": "r1", "kind": "reason", "verdict": "ok",
                  "output_text": "The answer is #### 42", "reference_answer": "42"}
    r = oracle.evaluate_one(cfg_a2, rec_reason)
    check("q-reason-ok", r["q_ok"] and r["q_rule"] == "gsm8k-answer", f"errs={r['q_errors']}")
    rec_tool = {"request_id": "r2", "kind": "tool_single", "verdict": "ok",
                "output_tool_calls": [{"name": "get_weather",
                                       "arguments": {"city": "bj", "units": "c"}}],
                "expected_tool_name": "get_weather"}
    r = oracle.evaluate_one(cfg_a2, rec_tool)
    check("q-tool-ok", r["q_ok"], f"errs={r['q_errors']}")
    rec_struct = {"request_id": "r3", "kind": "struct", "verdict": "ok",
                  "output_text": '{"a": 1}', "schema": {"type": "object",
                                                        "properties": {"a": {"type": "integer"}},
                                                        "required": ["a"]}}
    r = oracle.evaluate_one(cfg_a2, rec_struct)
    check("q-struct-ok", r["q_ok"], f"errs={r['q_errors']}")
    rec_struct_bad = dict(rec_struct, output_text="not json")
    r = oracle.evaluate_one(cfg_a2, rec_struct_bad)
    check("q-struct-fail", not r["q_ok"] and r["q_errors"], f"errs={r['q_errors']}")
    # Q5 W8A8 资格：下降 2pp > 1pp → 拒绝
    fp16_q = {"summary": {"by_kind": {"reason": {"ok_pct": 100.0}}},
              "gate": {"ok": True, "reasons": []}}
    w8_q = {"summary": {"by_kind": {"reason": {"ok_pct": 98.0}}},
            "gate": {"ok": True, "reasons": []}}
    q5 = oracle.w8a8_qualification(fp16_q, w8_q)
    check("q-w8a8-drop", (not q5["ok"]) and abs(q5["per_metric"][0]["drop_pp"] - 2.0) < 0.01,
          f"drop={q5['per_metric']}")

    # 15) M1-M4：机制解析 + fail-closed 门禁
    g = metrics.parse_graph_counters(
        "Capturing graph for 2 batch ... Graph capturing finished in 3.5s", "vllm_compile")
    check("m1-parse-graph", g["found"] and g["graph_capture_count"] == 1
          and abs(g["compile_time_s"] - 3.5) < 1e-9, f"g={g}")
    mini_m = copy.deepcopy(cfg_a2)
    mini_m["server"]["compile_mode"] = "vllm_compile"
    mini_m["server"]["enable_prefix_caching"] = True
    mini_m["model"]["quantization"] = "ascend"
    m_ok = metrics.evaluate_mechanisms(mini_m, {
        "graph": {"found": True, "graph_capture_count": 1, "graph_fallback_count": 0},
        "prefix_cache": {"found": True, "queries": 100, "hits": 80},
        "quantization_effective": "ascend",
        "cpu_core_table": {"found": True, "allowed": "0-3"}})
    check("m-gate-ok", m_ok["ok"], f"reasons={m_ok['reasons']}")
    m_bad = metrics.evaluate_mechanisms(mini_m, {"graph": {"found": True, "graph_capture_count": 0}})
    check("m-gate-fail-closed", not m_bad["ok"], f"reasons={m_bad['reasons']}")
    mini_a1_m = copy.deepcopy(cfg_a1)
    mini_a1_m["server"]["compile_mode"] = "none"
    m_a1 = metrics.evaluate_mechanisms(mini_a1_m,
                                       {"graph": {"found": True, "graph_capture_count": 0},
                                        "cpu_core_table": {"found": True}})
    check("m-a1-no-capture", m_a1["ok"], f"reasons={m_a1['reasons']}")

    # 16) K1-K2：A4 成本模型（÷21600h、0.60 元/kWh、每百万 token）
    c = cost.lifecycle_cost(1_000_000, 0.5, 1.5)
    expect_machine = 1_000_000 / 21600.0 * 0.5 + 1.5 * 0.5 * 0.60
    pm = cost.per_million_token_cost(c["machine_runtime_cny"], 100_000)
    check("k1-cost", abs(c["machine_runtime_cny"] - expect_machine) < 1e-6
          and pm is not None and pm > 0, f"machine={c['machine_runtime_cny']} pm={pm}")
    k2 = cost.k2_gate(70.0, {"share_ok": True, "jain_ok": True, "jain": 0.95},
                      {"isolation_ok": True})
    check("k2-gate-ok", k2["ok"], f"reasons={k2['reasons']}")
    k2_bad = cost.k2_gate(50.0, {"share_ok": True, "jain_ok": True, "jain": 0.95})
    check("k2-gate-fail", not k2_bad["ok"], f"reasons={k2_bad['reasons']}")

    # 17) Z3 证据包总门禁（fail-closed；不适用项跳过）
    good_ev = {"config_ok": True, "role_diff": {"equal": True},
               "sha_binding": {"ok": True}, "dataset_ok": True,
               "quality": {"ok": True}, "mechanisms": {"ok": True},
               "no_trunc": {"ok": True, "reasons": []}, "cost": None}
    z3 = gate.z3_gate(good_ev)
    check("z3-pass", z3["ok"] and z3["gate"] == "PASS", f"reasons={z3['reasons']}")
    bad_ev = dict(good_ev, mechanisms={"ok": False, "reasons": ["M1 fail"]})
    z3 = gate.z3_gate(bad_ev)
    check("z3-fail", not z3["ok"] and any("mechanisms" in r for r in z3["reasons"]),
          f"reasons={z3['reasons']}")
    n_trunc, ok_trunc = gate.silent_truncation_ok(
        [{"verdict": "ok"}, {"verdict": "silent_truncation"}])
    check("z3-trunc-count", n_trunc == 1 and not ok_trunc, f"n={n_trunc}")

    # 18) Z2 生命周期重复与中位数（3 次有效 → 中位数；无效 → retry 追加不替换）
    recs_lc = lifecycle.run_lifecycles(
        lambda: ({"metrics": {"main_mfu": 0.9}, "z3": {"gate": "PASS"}}, True),
        "main-shape MFU", required_lifecycles=3, max_retries=2)
    agg = lifecycle.aggregate(recs_lc, "main-shape MFU")
    check("z2-valid-median", len(recs_lc) == 3 and agg["valid"] == 3
          and abs(agg["median"] - 0.9) < 1e-9, f"agg={agg}")
    state = {"n": 0, "vals": [None, 0.8, 0.9, 0.85]}

    def once():
        v = state["vals"][state["n"]]
        state["n"] += 1
        return ({"metrics": {"main_mfu": v}, "z3": {"gate": "PASS" if v else "FAIL"}}, bool(v))
    recs_lc2 = lifecycle.run_lifecycles(once, "main-shape MFU",
                                        required_lifecycles=3, max_retries=2)
    agg2 = lifecycle.aggregate(recs_lc2, "main-shape MFU")
    check("z2-retry-append",
          len(recs_lc2) == 4 and agg2["valid"] == 3
          and len([r for r in recs_lc2 if r["status"] == "invalid"]) == 1
          and abs(agg2["median"] - 0.85) < 1e-9,
          f"agg={agg2}")
    z2 = lifecycle.evaluate_z2(recs_lc2, "main-shape MFU")
    check("z2-gate-ok", z2["ok"], f"reasons={z2['reasons']}")

    # 19) Z1 全链路：_finalize 产出证据包（Q+M+Z3+metrics 结构齐全）
    import types
    fargs = types.SimpleNamespace(out="", server_metrics="", dataset_ok=True,
                                  code_sha="", image_digest="", role="",
                                  comparison_type="", asset_value_cny=None,
                                  power_kw=None, lifecycle_hours=0.5)
    ev = _finalize(fargs, cfg_a2, [rec_reason], "hash123",
                   mode_verdict={"slo": slo.evaluate(cfg_a2, [rec_reason])})
    check("z1-evidence-structure",
          ev["config_hash"] == "hash123" and ev["z3"]["gate"] in ("PASS", "FAIL")
          and "quality" in ev and "mechanisms" in ev and "metrics" in ev
          and ev["primary_metric"] == cfg_a2["workload"]["primary_metric"],
          f"z3={ev['z3']['gate']} metrics={ev['metrics']}")

    # 20) M 自动聚合：提供 serve-log/pid 时 _finalize 自动采集（M1 从 serve.log 解析）
    _m_log = os.path.join(tempfile.gettempdir(), "vllm-benchkit-mtest-serve.log")
    with open(_m_log, "w", encoding="utf-8") as f:
        f.write("INFO Capturing CUDA graphs (mixed prefill-decode, PIECEWISE): "
                "100%|#| 5/5 [00:00<00:00]\n"
                "INFO Graph capturing finished in 2 secs\n"
                "INFO Engine 000: Avg generation throughput: 100.0 tokens/s, "
                "Prefix cache hit rate: 75.0%\n")
    fargs_m = types.SimpleNamespace(out="", server_metrics="", dataset_ok=True,
                                    code_sha="", image_digest="", role="",
                                    comparison_type="", asset_value_cny=None,
                                    power_kw=None, lifecycle_hours=0.5,
                                    serve_log=_m_log, server_pid="999999",
                                    receipt="", npu_log="", base_url="",
                                    port=8010)
    ev_m = _finalize(fargs_m, cfg_a2, [rec_reason], "hash123",
                     mode_verdict={"slo": slo.evaluate(cfg_a2, [rec_reason])})
    # 自测目的=验证 M1 从 serve.log 解析（合成 log 仅含图捕获行，无 M2/M4 数据源；
    # M2/M4 由真机 acceptance.sh metrics 路径 + m-gate-ok 用例覆盖）
    m1 = next((c for c in ev_m["mechanisms"]["mechanisms"]
               if c["name"] == "M1-graph-captured"), None)
    check("m-auto-aggregate",
          m1 is not None and m1["ok"],
          f"m1={m1} reasons={ev_m['mechanisms']['reasons']}")
    os.remove(_m_log)

    if failures:
        print(f"[selftest] FAIL: {len(failures)} 项未通过 -> {failures}", file=sys.stderr)
        return 1
    print(f"[selftest] PASS: {args.cell}/{args.precision} C3-C7+I+Q+M+K+Z1-Z3 全项通过")
    return 0


async def main_async(args):
    if args.selftest:
        return await _selftest(args)
    if args.z2_dir:
        cfg = acceptance.expand(args.cell, args.precision, args.model_ref)
        if cfg is None:
            return 1
        return _z2_aggregate(args, cfg)
    return await _scan(args)


def main() -> int:
    ap = argparse.ArgumentParser(description="vllm-xcheck 客户端执行器 + SLO/质量/机制判定（C3+C4+Z1-Z3）")
    ap.add_argument("--cell", required=True)
    ap.add_argument("--precision", default="FP16")
    ap.add_argument("--datasets-dir", default=os.path.join(_SRC, "..", "datasets"))
    ap.add_argument("--model-ref", default="")
    ap.add_argument("--allow-missing", action="store_true")
    ap.add_argument("--base-url", default="",
                    help="独立 server 根（不含 /v1）；缺省 http://127.0.0.1:{port}")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--out", default="", help="输出目录：receipts.jsonl + slo.json + contract.json + 证据包")
    ap.add_argument("--selftest", action="store_true",
                    help="离线自测（MockTransport 模拟 SSE，不起服务）")
    # Z1/Z2/Z3 / I 组 证据参数
    ap.add_argument("--server-metrics", default="",
                    help="真机采集的 M1/M2/M3/M4 指标 JSON（acceptance.sh 产出；离线可缺）")
    ap.add_argument("--serve-log", default="",
                    help="vllm serve.log 路径（--server-metrics 缺省时自动聚合 M1 用；服务须存活）")
    ap.add_argument("--server-pid", default="",
                    help="vllm serve 进程 pid（自动聚合 M4 CPU 核表用）")
    ap.add_argument("--receipt", default="",
                    help="receipt.json 路径（自动聚合取 compile_mode；可缺）")
    ap.add_argument("--npu-log", default="",
                    help="npu-smi 采样 JSONL 路径（自动聚合 M3 用；可缺）")
    ap.add_argument("--dataset-ok", action="store_true", default=True,
                    help="数据工件就绪（Z3 dataset 门禁；由 harness 按 receipt 门禁传入）")
    ap.add_argument("--code-sha", default="", help="运行环境 core+plugin 提交（I3 绑定；真机读实际值）")
    ap.add_argument("--image-digest", default="", help="OCI digest（I3；B1 必填）")
    ap.add_argument("--role", default="", choices=["", "B0", "B1"], help="基线/候选角色（I1）")
    ap.add_argument("--comparison-type", default="",
                    choices=["", "FP16_CONTROL", "SYSTEM_DELIVERY", "W8A8_MATCHED"],
                    help="比较类型（I1；缺省取 cell meta）")
    # Z2
    ap.add_argument("--z2-dir", default="",
                    help="Z2 聚合：目录含 <lifecycle-N>/evidence.json，取主指标中位数 + 门禁")
    # K1 A4 成本真机项（harness 传入；未传则 cost 门禁 not-applicable）
    ap.add_argument("--asset-value-cny", type=float, default=None, help="A4 资产原值（元）")
    ap.add_argument("--power-kw", type=float, default=None, help="A4 实测平均功率（kW）")
    ap.add_argument("--lifecycle-hours", type=float, default=0.5, help="A4 生命周期小时（默认 0.5h）")
    args = ap.parse_args()
    try:
        return asyncio.run(main_async(args))
    except FileNotFoundError as e:
        print(f"[run] {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def _dump(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=False)
    print(f"[run] wrote {path}")


if __name__ == "__main__":
    sys.exit(main())
