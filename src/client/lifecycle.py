"""组 Z2 —— 正式生命周期重复与中位数（V4.1 附-8 正式测量重复）。

每个正式测试阶段至少 3 次独立服务生命周期（冷启动—预热—测量—停止）：
  - 异常仅可追加 2 次复测（retry），不替换原结果；
  - 按预声明 primary_metric 取中位数（对照中位数依赖组 I 的 B0/B1 双角色）。

本模块是纯函数 + 编排骨架：
  - primary_metric_value(evidence, primary_metric)  从证据包抽主指标数值；
  - run_lifecycles(run_once, ...)                   跑 N 次独立生命周期（retry 追加）；
  - aggregate(records, primary_metric)              取有效生命周期的主指标中位数；
  - evaluate_z2(records, ...)                        门禁：≥3 有效 + retry≤2 且不替换。
"""
import statistics

# 默认重复口径（附-8）
REQUIRED_LIFECYCLES = 3      # 至少 3 次独立服务生命周期
MAX_RETRIES = 2              # 异常最多追加 2 次复测（不替换原结果）

# primary_metric（cells 声明串）→ 证据包 metrics 稳定键 的映射。
# 新增 cell 时在此登记；未登记则以 evidence["metrics"] 中与串同名的键兜底。
PRIMARY_METRIC_KEY = {
    "main-shape MFU": "main_mfu",
    "max compliant output token/s + common-load TTFT/TPOT": "max_compliant_output_tokens_per_s",
    "common-load TTFT/TPOT + tool quality gate": "common_load_ttft_mean_ms",
    "quality accuracy + error rate (perf full report)": "quality_accuracy_pct",
    "schema-valid rate + task success rate (perf full report)": "schema_valid_pct",
    "capacity boundary + error rate + HBM/KV usage": "capacity_boundary_tokens_per_s",
    "window throughput CV + TTFT/TPOT drift + 0 failure": "throughput_cv",
    "cost per 1e6 successful output tokens (full-lifecycle)": "cost_per_1e6_tokens_cny",
}


def median(values):
    """数值中位数（确定性；空序列返回 None）。"""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return statistics.median(vals)


def primary_metric_value(evidence, primary_metric):
    """从证据包抽 primary_metric 数值。

    ARGS:
        evidence        run.py 写的证据包 dict（含 "metrics": {稳定键: 数值}）
        primary_metric  cells workload.primary_metric 声明串
    RETURN:
        float | None（未登记且 metrics 无同名键 → None）
    """
    metrics_map = (evidence or {}).get("metrics") or {}
    key = PRIMARY_METRIC_KEY.get(primary_metric, primary_metric)
    v = metrics_map.get(key)
    return float(v) if isinstance(v, (int, float)) else None


def run_lifecycles(run_once, primary_metric, required_lifecycles=REQUIRED_LIFECYCLES,
                   max_retries=MAX_RETRIES):
    """编排 N 次独立生命周期。

    ARGS:
        run_once            无参可调用，返回 (evidence, ok)：
                            evidence  该生命周期的证据包（写盘内容，含 metrics）
                            ok        bool：该生命周期是否有效（失败→ 计入 retry）
        primary_metric      cells workload.primary_metric 声明串（中位数口径）
        required_lifecycles 需要的有效生命周期数（≥3）
        max_retries         异常最多追加复测次数（≤2）
    RETURN:
        list[dict] 生命周期记录（有效 + 无效全部保留）：
            {index, attempt, status: "valid"|"invalid", reason, evidence, primary_value}
    """
    records = []
    valid = 0
    attempt = 0
    index = 0
    max_attempts = required_lifecycles + max_retries   # 无效生命周期最多追加 max_retries 次
    # 先凑满 required 个有效；无效生命周期按追加复测记账（不替换原结果）
    while valid < required_lifecycles and attempt < max_attempts:
        attempt += 1
        index += 1
        evidence, ok = run_once()
        if ok:
            valid += 1
            records.append({
                "index": index, "attempt": attempt, "status": "valid",
                "reason": "", "evidence": evidence,
                "primary_value": primary_metric_value(evidence, primary_metric),
            })
        else:
            records.append({
                "index": index, "attempt": attempt, "status": "invalid",
                "reason": ((evidence or {}).get("z3") or {}).get("gate", "invalid"),
                "evidence": evidence, "primary_value": None,
            })
    return records


def aggregate(records, primary_metric):
    """对生命周期记录取主指标中位数。

    RETURN:
        dict {valid, total, median, values, per_lifecycle}
    """
    rows = []
    values = []
    for r in records:
        row = {"index": r["index"], "attempt": r["attempt"], "status": r["status"],
               "primary_value": r["primary_value"]}
        rows.append(row)
        if r["status"] == "valid" and r["primary_value"] is not None:
            values.append(r["primary_value"])
    valid = [r for r in rows if r["status"] == "valid"]
    return {
        "primary_metric": primary_metric,
        "total": len(records),
        "valid": len(valid),
        "values": values,
        "median": median(values),
        "per_lifecycle": rows,
    }


def evaluate_z2(records, primary_metric, required_lifecycles=REQUIRED_LIFECYCLES,
                max_retries=MAX_RETRIES):
    """Z2 门禁：≥required 有效生命周期 + retry≤max_retries + 中位数可算。

    RETURN:
        dict {ok, reasons, aggregate, primary_metric}
    """
    agg = aggregate(records, primary_metric)
    reasons = []
    if agg["valid"] < required_lifecycles:
        reasons.append(f"有效生命周期 {agg['valid']} < {required_lifecycles}")
    invalid_after = [r for r in records if r["status"] == "invalid"]
    if len(invalid_after) > max_retries:
        reasons.append(f"无效生命周期 {len(invalid_after)} > 允许复测 {max_retries} 次（不替换原结果）")
    if agg["median"] is None:
        reasons.append("primary_metric 中位数不可计算（无有效数值）")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "aggregate": agg,
        "primary_metric": primary_metric,
        "max_retries": max_retries,
        "required_lifecycles": required_lifecycles,
    }
