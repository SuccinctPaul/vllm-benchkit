"""C7 A1 闭环算力口径：effective_compute_spec v1 有效 FLOPs + MFU（附-8/组 A）。

口径（组 A1-2 v1）：
  - MatMul 按 2MNK；BatchMatMul 逐批求和；
  - Attention 计入 QK^T 与 PV 两组 MatMul（软激活/掩码为逐元素算子，不计）；
  - 逐元素算子（RMSNorm、GELU/SiLU 激活、残差、LayerNorm、位置编码）不计入；
  - Embedding 为查表，不计入。
  - decode 阶段 attention 与既有 kv_len 交互（QK^T/PV 均 2×b×d×kv_len）。

MFU = 实际有效 FLOPs/s ÷ 硬件峰值 FLOPs/s。
  - 分子 = 逐算子 effective FLOPs（基于回执的真实 prompt/completion token 数）÷ measure 墙钟；
  - 分母 = 峰值 FLOPs/s：组 A1-1 由 ascend-dmi FP16 实测（3 次中位）提供；
    本模块内置 Qwen2.5-14B 结构常量 + 峰值占位，真机值经 config/环境覆盖。

数据身份：token 数一律取自逐请求回执的 prompt_tokens/completion_tokens（服务端 usage），
不使用声明值，保证两次同设备测量可复现。
"""
import os

# Qwen2.5-14B-Instruct 结构常量（官方 config.json 固化；可用 config.model 字段覆盖）
ARCH_QWEN25_14B = {
    "hidden": 5120,            # hidden_size
    "layers": 48,              # num_hidden_layers
    "heads": 40,               # num_attention_heads
    "kv_heads": 8,             # num_key_value_heads
    "head_dim": 128,           # head_dim
    "intermediate": 13824,     # intermediate_size（SwiGLU，Qwen2.5-14B 实际 config.json 值）
    "vocab": 152064,           # vocab_size
}
_DEFAULT_ARCH_NAME = "Qwen2.5-14B-Instruct"

# 峰值占位：910B2 FP16 理论峰值估计（TFLOPS→FLOPs/s）。真机 ascend-dmi 结果应覆盖此值。
_PEAK_FLOPS_FALLBACK = 320e12
_PEAK_ENV = "VLLM_BENCHKIT_PEAK_FLOPS"


def arch_for(cfg):
    """取模型结构：config.model 显式字段优先，缺省回退 Qwen2.5-14B 常量。"""
    arch = dict(ARCH_QWEN25_14B)
    m = cfg.get("model", {}) or {}
    for k, v in m.get("arch", {}).items():
        if v is not None:
            arch[k] = int(v)
    return arch


def peak_flops_per_s(cfg=None, env_value=None):
    """硬件峰值 FLOPs/s 分母（组 A1-1 真机 ascend-dmi 应提供并覆盖）。

    Priority: env_value 参数 > VLLM_BENCHKIT_PEAK_FLOPS 环境变量 > 内置占位。
    返回 (value, source)，source ∈ {"env", "builtin-fallback"}。
    """
    if env_value is not None and float(env_value) > 0:
        return float(env_value), "env"
    ev = os.environ.get(_PEAK_ENV)
    if ev:
        return float(ev), "env"
    return _PEAK_FLOPS_FALLBACK, "builtin-fallback"


def _layer_flops(arch, b, p, kv_len=None):
    """单层有效 FLOPs。p=prefill token 数；decode 时 p 为每 token 1、kv_len 为前缀长度。"""
    d = arch["hidden"]
    d_kv = arch["kv_heads"] * arch["head_dim"]
    i = arch["intermediate"]
    d_qkv = d + 2 * d_kv
    if p > 1:                          # prefill：attention 为全序列 s² 二次项
        qkv = 2 * b * p * d * d_qkv
        attn = 4 * b * p * p * d       # QK^T + PV
        o = 2 * b * p * d * d
        mlp = 6 * b * p * d * i        # SwiGLU up + gate + down
    else:                              # decode：每 token，attention 与 kv_len 交互
        kl = (kv_len if kv_len is not None else max(1, p))
        qkv = 2 * b * d * d_qkv
        attn = 4 * b * kl * d          # QK^T + PV（2×b×kl×d 各）
        o = 2 * b * d * d
        mlp = 6 * b * d * i
    return qkv + attn + o + mlp


def effective_flops(arch, batch, prompt_tokens, completion_tokens):
    """单形状一「轮」（batch 并发请求）的逐算子有效 FLOPs（A1-2 v1）。

    ARGS:
        arch              模型结构 dict（arch_for 返回）
        batch             该形状每轮并发请求数
        prompt_tokens     每请求 prefill token 数（真实 usage）
        completion_tokens 每请求 decode token 数（真实 usage）
    RETURN:
        int 有效 FLOPs（prefill + decode 全层 + LM head）
    """
    d, V, L = arch["hidden"], arch["vocab"], arch["layers"]
    p, q = int(prompt_tokens), int(completion_tokens)
    # 总层算力：prefill 一次（seq=p）+ decode q 步；第 step 个输出 token 的 kv 前缀
    # 为 p + (step-1) 个已缓存 token（附-8 A1-2 v1 口径，避免 off-by-one 多计 1 步）。
    prefill = L * _layer_flops(arch, batch, p) + 2 * batch * p * d * V
    decode = 0
    for step in range(1, q + 1):
        decode += L * _layer_flops(arch, batch, 1, kv_len=p + step - 1) + 2 * batch * d * V
    return prefill + decode


def mfu_summary(rounds_stats, arch=None, peak_flops=None, shape_order=None):
    """逐形状 MFU 汇总（measure 轮统计）。

    ARGS:
        rounds_stats  list[dict]：{shape{input_len,batch,output_len}, completed, failed,
                      prompt_tokens, completion_tokens, wall_s}（wall_s 为该形状该轮墙钟）
        arch          模型结构（缺省 Qwen2.5-14B）
        peak_flops    FLOPs/s 分母；None 用内置占位（真机 ascend-dmi 应覆盖）
        shape_order   声明的主→辅形状顺序（list[{input_len,batch,output_len}]）；
                      控制 per_shape/main_* 的选择（主形状全失败时落到下一个有数据形状）
    RETURN:
        dict {arch, peak_flops, per_shape[], main_mfu, main_shape, included_rounds}
    """
    arch = arch or ARCH_QWEN25_14B
    peak, peak_source = peak_flops_per_s(env_value=peak_flops)
    # 声明顺序键表（供 main_* 取「主形状优先」）；None/空则按首次出现顺序。
    declared = []
    if shape_order:
        for sh in shape_order:
            declared.append((int(sh.get("input_len")), int(sh.get("batch") or 1),
                             int(sh.get("output_len") or 0)))

    grouped = {}
    order = []
    included = 0
    for r in rounds_stats:
        s = r["shape"]
        key = (int(s.get("input_len")), int(s.get("batch") or 1),
               int(s.get("output_len") or 0))
        completed = int(r.get("completed") or 0)
        # 全失败轮不计量：failed 请求未做真实推理，其声明 token 数不能计为有效 FLOPs，
        # 否则墙钟被失败路径（如 HTTP 502 秒失败）压低 → effective FLOPs/s 虚高 → MFU>100%。
        if completed <= 0:
            continue
        if key not in grouped:
            grouped[key] = {"shape": s, "flops": 0, "wall_s": 0.0, "rounds": 0}
            order.append(key)
        g = grouped[key]
        # 分子按成功请求数计（completed ≤ batch），分母为该轮墙钟（含并发批次整体耗时）。
        g["flops"] += effective_flops(arch, completed,
                                      r["prompt_tokens"], r["completion_tokens"])
        g["wall_s"] += r.get("wall_s") or 0.0
        g["rounds"] += 1
        included += 1

    # 展示顺序：声明序中已有数据的形状在前，其余按首次出现顺序（主形状优先）。
    order = [k for k in declared if k in grouped] + [k for k in order if k not in declared]

    per_shape = []
    for key in order:
        g = grouped[key]
        eff_flops_per_s = (g["flops"] / g["wall_s"]) if g["wall_s"] > 0 else 0.0
        mfu = (eff_flops_per_s / peak) if peak > 0 else None
        per_shape.append({
            "shape": g["shape"],
            "rounds": g["rounds"],
            "effective_flops": g["flops"],
            "wall_s": round(g["wall_s"], 6),
            "effective_flops_per_s": round(eff_flops_per_s, 3),
            "mfu": None if mfu is None else round(mfu, 6),
        })
    main = per_shape[0] if per_shape else None
    return {
        "arch": {k: arch[k] for k in ("hidden", "layers", "heads", "kv_heads",
                                      "head_dim", "intermediate", "vocab")},
        "peak_flops_per_s": peak,
        "peak_source": peak_source,
        "per_shape": per_shape,
        "main_shape": main["shape"] if main else None,
        "main_mfu": main["mfu"] if main else None,
        "included_rounds": included,
    }
