"""client 通用工具：canonical JSON（RFC 8785-style）+ 稳定 SHA-256 + 固定子集加载。

供 C1（cases.py）/ C2（arrival.py）共用，保证：
  - 同一输入 → 字节一致的输出（request_id / 序列 / 到达时间戳全部确定性生成）；
  - config_hash 只覆盖 workload + sampling（客户端合同），与精度 overlay / B0/B1 角色无关，
    使 B0/B1 复用同一请求清单与到达序列（附-1、附-8）。
"""
import hashlib
import json
import math
import os
import random
import statistics

# RFC 8785（JCS）风格分隔符：紧凑 + 无空格；ensure_ascii 使非 ASCII 全部 \uXXXX 转义，
# 对字符串/数字/布尔/null/数组/对象均确定性可复现。
_JCS_SEPARATORS = (",", ":")


def canonical_json(obj):
    """确定性序列化（JCS-style）。对浮点数依赖 Python 最短往返 repr（与 RFC 8785 一致）。"""
    return json.dumps(obj, sort_keys=True, separators=_JCS_SEPARATORS,
                      ensure_ascii=True, allow_nan=False)


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_hex_bytes(data):
    return hashlib.sha256(data).hexdigest()


def percentile(values, q):
    """线性插值百分位（numpy 'linear' 同语义），确定性。`values` 须已排序；空返回 None。"""
    if not values:
        return None
    n = len(values)
    rank = q / 100.0 * (n - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return values[lo]
    frac = rank - lo
    return values[lo] * (1 - frac) + values[hi] * frac


def percentile_ms(values_s, q=99):
    """秒值列表 → q 分位毫秒；内部排序并跳过 None，空返回 None。"""
    vals = sorted(v * 1000.0 for v in values_s if v is not None)
    return percentile(vals, q)


def median_ms(values_s):
    """秒值列表 → 中位毫秒；跳过 None，空返回 None。"""
    vals = [v * 1000.0 for v in values_s if v is not None]
    return statistics.median(vals) if vals else None


def config_hash(cfg):
    """客户端合同哈希 = SHA-256( canonical_json({workload, sampling}) )。

    只覆盖决定「客户端请求形态」的字段；model/server/env 不参与，
    因此精度（FP16/W8A8）与角色（B0/B1）不影响哈希 → 两侧复用同一合同。
    """
    contract = {"workload": cfg.get("workload", {}), "sampling": cfg.get("sampling", {})}
    return sha256_hex(canonical_json(contract))


# 缺固定子集工件时的确定性合成回退内容（仅 --allow-missing smoke；正式测量必须真实工件）。
_FALLBACK_N = {"sharegpt": 64, "gsm8k": 64, "bfcl": 64, "jsonschema": 64, "long": 32}


def _fallback_content(name, i, rng):
    """按子集语义生成确定性合成回退内容（仅 --allow-missing smoke）。

    各子集改为可回答/可结构化提示，让模型在 output_cap 内产出真实 EOS（否则随机词
    会触发 silent_truncation 伪截断，附-3/8）：
      - sharegpt(dialogue)：自然对话短问 → 简短自然回复（~20-40 token，含 EOS）；
      - bfcl(tool)：工具调用请求 → 触发 tool-call JSON（附-5 显式发 tools）；
      - gsm8k(reasoning)：单步加法 → 仅数字（~2-3 token）；
      - jsonschema(structured)：按 schema 生成 JSON → 短 JSON（~20-30 token）。
    """
    if name == "gsm8k":
        a, b = 3 + (i * 5) % 89, 7 + (i * 13) % 83
        return f"Solve: what is {a} plus {b}? Return only the number."
    if name == "jsonschema":
        return ("Generate a JSON object that conforms to the JSON schema. "
                "Output only valid JSON, nothing else.")
    if name == "bfcl":
        cities = ["Beijing", "Shanghai", "Shenzhen", "Hangzhou", "Chengdu"]
        return (f"Call the get_weather tool for {cities[i % len(cities)]}. "
                "Output the tool call, nothing else.")
    # sharegpt(dialogue)：自然对话短问 → 简短自然回复（EOS 终止，可回答）
    topics = ["how your day is going", "what you think of benchmark testing",
              "whether you prefer tea or coffee", "the weather in autumn",
              "your favorite programming language"]
    return (f"Reply to this message in one short sentence: "
            f"{topics[i % len(topics)]}?")


def load_subset(name, datasets_dir, allow_missing=False):
    """加载 prepare.sh subsets 产出的固定子集（datasets/<name>.jsonl）。

    每行记录：{"id", "content", "sha256"}（normalize 后按 ordered_sha256 稳定排序）。
    缺工件时：
      - allow_missing=False → FileNotFoundError（fail-closed，与 receipt dataset_ready 门禁一致）；
      - allow_missing=True  → 返回确定性合成回退（source=synthetic-fallback，仅供 smoke）。
    """
    path = os.path.join(datasets_dir, f"{name}.jsonl")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    if allow_missing:
        n = _FALLBACK_N.get(name, 64)
        rng = random.Random(0)
        return [{
            "id": str(i),
            "content": _fallback_content(name, i, rng),
            "sha256": "",
            "source": "synthetic-fallback",
        } for i in range(n)]
    raise FileNotFoundError(
        f"缺失固定子集工件 datasets/{name}.jsonl；先 bash scripts/prepare.sh subsets"
        f"（或 --allow-missing 仅 smoke，正式测量必须真实工件）")
