"""A4 公平性判定（附-7/附-8 K2）。

- Jain 公平性指数（按租户 measure 期输出 token 计算）≥ 0.90；
- 逐租户输出 token 份额偏差 ≤ 10%（|actual_share - target_share| ≤ 0.10）。

与 slo.py（逐租户 SLO）配合构成 A4 判定证据。
"""
# 份额偏差容忍度（附-7：≤10%）
SHARE_TOLERANCE = 0.10
# Jain 公平性阈值（附-8 K2：≥0.90）
JAIN_THRESHOLD = 0.90


def jain_index(values):
    """Jain 公平性指数 = (Σx)² / (n·Σx²)，x ≥ 0；全部为 0 时视为 1.0（完全公平）。"""
    n = len(values)
    if n == 0:
        return None
    if any(v < 0 for v in values):
        return None
    s = sum(values)
    if s == 0:
        return 1.0
    return s * s / (n * sum(v * v for v in values))


def share_report(records, cfg):
    """逐租户输出 token 份额判定。

    records: A4 measure 期逐请求回执（含 tenant 字段与 output_tokens）
    cfg:     展开配置（workload.tenants 声明 share）

    RETURN:
        dict {
            tenants: [{tenant, target_share, output_tokens, actual_share,
                       deviation, deviation_ok}],
            max_deviation, share_ok（全部 |dev| ≤ 10%）,
            jain, jain_ok（≥0.90）,
        }
    """
    tenants = cfg["workload"]["tenants"]
    total = 0
    per_tenant = {}
    for r in records:
        t = r.get("tenant")
        if t not in tenants:
            continue
        tok = int(r.get("output_tokens") or 0)
        per_tenant[t] = per_tenant.get(t, 0) + tok
        total += tok

    rows, max_dev = [], 0.0
    for name, meta in tenants.items():
        target = float(meta["share"])
        got = per_tenant.get(name, 0)
        actual = (got / total) if total else 0.0
        dev = actual - target
        max_dev = max(max_dev, abs(dev))
        rows.append({
            "tenant": name,
            "target_share": target,
            "output_tokens": got,
            "actual_share": round(actual, 4),
            "deviation": round(dev, 4),
            "deviation_ok": abs(dev) <= SHARE_TOLERANCE,
        })

    jain = jain_index([per_tenant.get(t, 0) for t in tenants])
    return {
        "tenants": rows,
        "total_output_tokens": total,
        "max_deviation": round(max_dev, 4),
        "share_ok": max_dev <= SHARE_TOLERANCE,
        "jain": None if jain is None else round(jain, 4),
        "jain_ok": jain is not None and jain >= JAIN_THRESHOLD,
    }
