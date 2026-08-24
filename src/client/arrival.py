"""C2 到达序列生成器（generator=vllm-xcheck-poisson-v1）。

规则（附-8 到达背压）：
  - seed=0；每个 rate 独立 Random(0)，inter-arrival ~ Exp(rate)，逐率生成完整到达序列；
  - request_cap = ceil(rate × per_rate_s)，与 cells 声明的 caps 必须逐项一致（fail-closed）；
  - clock=monotonic：nominal_ts 以单调时钟为基准，rate 段间 drain/cooldown=60s；
  - 并发饱和时的本地 FIFO 等待 / sendlag 由执行器（C3）按 nominal timestamp 保留，
    本模块不丢弃、不重排、不突发追赶——只产出 nominal 序列；
  - 生成器绑定参数哈希（generator_hash）与 ordered inter-arrival 的 SHA-256
    （arrival_stream_sha256），并写入请求清单（供附-8 数据身份核验）。

B0/B1 复用同一序列：本模块是 rate/duration/seed 的纯函数，与角色/精度无关。
"""
import math
import random

from client import common

GENERATOR = "vllm-xcheck-poisson-v1"
SEED = 0                       # 附-8：seed=0
CLOCK = "monotonic"


def _segments(rates, caps, per_rate_s, cooldown_s, seed=SEED):
    """生成各 rate 的完整到达段；返回 segments + 段起始偏移计算用的绝对时钟。

    segments[i] = {
        rate, cap, start_s, nominal_ts_s[], inter_arrival_s[],
    }
    start_s 为「绝对单调时钟」下的段起点；段名义时长 = per_rate_s，段间 + cooldown_s。
    """
    segments = []
    abs_t = 0.0
    for rate, cap in zip(rates, caps):
        rng = random.Random(seed)
        inter = [rng.expovariate(rate) for _ in range(cap)]
        ts = []
        t = 0.0
        for dt in inter:
            t += dt
            ts.append(round(abs_t + t, 6))
        segments.append({
            "rate": rate,
            "cap": cap,
            "start_s": round(abs_t, 3),
            "nominal_ts_s": ts,
            "inter_arrival_s": [round(x, 9) for x in inter],
        })
        abs_t += per_rate_s + cooldown_s
    return segments


def _stream_hash(segments):
    """ordered inter-arrival + nominal 时间戳的 SHA-256（附-8 数据身份 / 进入请求清单）。"""
    payload = [
        {"rate": s["rate"], "inter_arrival_s": s["inter_arrival_s"],
         "nominal_ts_s": s["nominal_ts_s"]}
        for s in segments
    ]
    return common.sha256_hex(common.canonical_json(payload))


def capacity_scan(cfg):
    """按 workload 合同生成完整容量扫描到达序列（poisson mode）。

    RETURN:
        dict：generator / generator_params / segments / arrival_stream_sha256 / generator_hash
        或 None（mode 非 poisson，无到达序列——closed-loop/mixed 由各自闭环调度）。
    """
    wl = cfg.get("workload", {})
    if wl.get("mode") != "poisson":
        return None
    rates = list(wl.get("rates") or [])
    if not rates:
        raise ValueError("poisson mode 必须声明 workload.rates")
    per_rate_s = int(wl.get("timeouts", {}).get("per_rate_s", 600))
    cooldown_s = int(wl.get("timeouts", {}).get("drain_cooldown_s", 60))
    computed_caps = [math.ceil(r * per_rate_s) for r in rates]
    declared = list(wl.get("caps") or [])
    if declared and declared != computed_caps:
        raise ValueError(
            f"workload.caps {declared} 与 request_cap=ceil(rate×{per_rate_s}) {computed_caps} 不一致"
            f"（附-8：只接受预定义 rate 点，request_cap=ceil(rate×600)）")

    segments = _segments(rates, computed_caps, per_rate_s, cooldown_s, SEED)
    params = {
        "seed": SEED,
        "clock": CLOCK,
        "per_rate_s": per_rate_s,
        "cooldown_s": cooldown_s,
        "rates": rates,
        "caps": computed_caps,
    }
    return {
        "generator": GENERATOR,
        "generator_params": params,
        "segments": segments,
        "arrival_stream_sha256": _stream_hash(segments),
        "generator_hash": common.sha256_hex(common.canonical_json({
            "generator": GENERATOR, "params": params})),
    }


def per_rate_stream(rate, per_rate_s, seed=SEED):
    """单 rate 到达序列（供执行器独立消费 / 单测）。"""
    cap = math.ceil(rate * per_rate_s)
    seg = _segments([rate], [cap], per_rate_s, 0, seed)[0]
    return {"rate": rate, "cap": cap, **seg}
