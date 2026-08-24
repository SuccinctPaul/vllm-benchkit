"""组 K —— A4 成本模型（附-8 A4 成本；K1/K2）。

K1 全生命周期成本口径（附-8）：
  - 设备成本 = 资产原值 ÷ 21600 小时 × 生命周期小时；
  - 主机成本 = 同式（资产原值 ÷ 21600 × 生命周期小时）；
  - 电费 = 实测平均功率 kW × 生命周期小时 × 0.60 元/kWh；
  - 人工不计入机器运行成本并单列；
  - 每百万成功输出 token 成本 = 全生命周期成本 ÷ 成功输出 token × 10^6。
  - 各比较关系使用同一资产原值、价格时点、计费周期、功耗采样规则（参数由调用方保证一致）。
K2 利用率与公平性指标：单卡平均利用率≥60%、Jain≥0.90、份额偏差≤10%、
    p99≤隔离 B0-FP16 的 1.25×（公平性复用 fairness.py，此处只做 K2 门禁汇总）。

ARGS 约定：金额单位 元（CNY）；功率 kW；时间 小时；成功输出 token 数由 measure 期回执统计。
"""
# 附-8 固定口径参数
AMORTIZATION_HOURS = 21600.0      # 资产折旧小时
KWH_PRICE_CNY = 0.60              # 元/kWh


def lifecycle_cost(asset_value_cny, lifecycle_hours, avg_power_kw,
                   asset_hours=AMORTIZATION_HOURS,
                   kwh_price=KWH_PRICE_CNY, labor_cny=0.0):
    """K1 全生命周期成本（设备+主机+电费；人工单列）。

    ARGS:
        asset_value_cny   资产原值（设备/主机合计，各比较关系须同一口径）
        lifecycle_hours   生命周期小时（正式测量 30min=0.5h；全生命周期口径由调用方声明）
        avg_power_kw      实测平均功率（kW）
        labor_cny         人工成本（单列，不进入机器运行成本；默认 0）
    RETURN:
        dict {device_cny, host_cny, electricity_cny, machine_runtime_cny,
              labor_cny, total_cny, total_without_labor_cny, params}
    """
    # 附-8：设备和主机成本 = 资产原值 ÷ 21600 × 生命周期小时
    # （原值未拆分设备/主机时，device_cny 承担全部；host_cny=0 并显式记账）
    device_cny = asset_value_cny / asset_hours * lifecycle_hours
    electricity_cny = avg_power_kw * lifecycle_hours * kwh_price
    machine_runtime_cny = device_cny + electricity_cny   # 机器运行成本（不含人工）
    return {
        "device_cny": round(device_cny, 6),
        "host_cny": 0.0,                              # 原值未拆分；显式记账
        "electricity_cny": round(electricity_cny, 6),
        "machine_runtime_cny": round(machine_runtime_cny, 6),
        "labor_cny": round(labor_cny, 6),
        "total_cny": round(machine_runtime_cny + labor_cny, 6),
        "total_without_labor_cny": round(machine_runtime_cny, 6),
        "params": {"asset_value_cny": asset_value_cny,
                   "lifecycle_hours": lifecycle_hours,
                   "avg_power_kw": avg_power_kw,
                   "asset_hours": asset_hours,
                   "kwh_price": kwh_price},
    }


def per_million_token_cost(machine_runtime_cny, successful_output_tokens):
    """每百万成功输出 token 成本 = 机器运行成本 ÷ 成功输出 token × 10^6。"""
    if successful_output_tokens <= 0:
        return None
    return round(machine_runtime_cny / successful_output_tokens * 1e6, 6)


def utilization_gate(avg_utilization_pct, min_util=60.0):
    """K2 利用率门禁：单卡平均利用率 ≥ 60%。"""
    ok = avg_utilization_pct >= min_util
    return {"ok": ok, "avg_utilization_pct": avg_utilization_pct,
            "min_utilization_pct": min_util,
            "reason": "PASS" if ok else f"利用率 {avg_utilization_pct}% < {min_util}%"}


def k2_gate(avg_utilization_pct, fair_report, isolation=None):
    """K2 汇总门禁：利用率≥60% + Jain≥0.90 + 份额偏差≤10% + p99≤1.25×。"""
    reasons = []
    u = utilization_gate(avg_utilization_pct)
    if not u["ok"]:
        reasons.append(u["reason"])
    if not (fair_report.get("share_ok") and fair_report.get("jain_ok")):
        reasons.append(f"公平性不达标: dev={fair_report.get('max_deviation')} "
                       f"jain={fair_report.get('jain')}")
    if isolation is not None and not isolation.get("isolation_ok"):
        reasons.append(f"p99 隔离性超限: {isolation.get('reason', 'ratio>1.25x')}")
    return {"ok": not reasons, "reasons": reasons,
            "utilization": u, "fairness": fair_report, "isolation": isolation}
