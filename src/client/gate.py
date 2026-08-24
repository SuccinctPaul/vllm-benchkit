"""组 Z3 —— 证据包总门禁（附-9 对齐；每条主线的最终验收门禁）。

receipt 总门禁：config 合法、角色化一致、代码/镜像 SHA 绑定、数据就绪、
质量资格、机制生效、无静默截断 —— 任一不过即拒测（fail-closed）。

本模块是纯判定（无 IO），消费 run.py / acceptance.sh 组装的 evidence dict：
  evidence = {
      "config_ok": bool,                    # receipt.py 各门禁全 PASS（config_valid 等）
      "role_diff": {"equal": bool} | None,  # I1 差分（roles.diff_roles）；None=非角色化 profile
      "sha_binding": {"ok": bool} | None,   # I3 代码/镜像 SHA 绑定；None=未声明
      "dataset_ok": bool,                   # D2 dataset_ready（prepare subsets 工件就绪）
      "quality": {"ok": bool, "reasons": [...]} | None,  # Q oracle 汇总门禁
      "mechanisms": {"ok": bool, "reasons": [...]} | None,  # M 机制生效门禁
      "no_trunc": {"ok": bool, "reasons": [...]},            # 无静默截断（附-3/8 禁止）
      "cost": {"ok": bool} | None,          # K2 成本/利用率门禁（A4；其余 profile 不适用）
  }
"""
from client import metrics, oracle

# 各 check 在证据缺失时的默认判定：fail-closed（缺证据即视为不通过），
# 但「不适用」的项（非角色化 profile 的 role、非 A4 的 cost）允许显式跳过。
GATE_CHECKS = (
    ("config",        "config 合法（receipt 全门禁 PASS）"),
    ("role",          "角色化一致（I1 差分 equal）"),
    ("sha_binding",   "代码/镜像 SHA 绑定（I3）"),
    ("dataset",       "数据就绪（D2）"),
    ("quality",       "质量资格（Q oracle 门禁）"),
    ("mechanisms",    "机制生效（M 门禁）"),
    ("no_trunc",      "无静默截断（附-3/8）"),
    ("cost",          "成本/利用率（K2，A4 适用）"),
)


def _bool_of(value):
    """把证据值归一为 (ok, not_applicable)。

    None → 视为不适用（跳过）；dict → 取其 ok 字段；bool → 原样。
    """
    if value is None:
        return None, True
    if isinstance(value, dict):
        return bool(value.get("ok")), False
    return bool(value), False


def z3_gate(evidence):
    """证据包总门禁（fail-closed；不可用的项按 not-applicable 记账）。

    RETURN:
        dict {
            ok, checks: [{name, label, ok, not_applicable, detail}],
            reasons: [...], gate: "PASS"/"FAIL"
        }
    """
    def detail_of(value):
        if isinstance(value, dict):
            reasons = value.get("reasons") or value.get("reason")
            return " | ".join(map(str, reasons)) if reasons else ""
        return ""

    rows = []
    for key, label in GATE_CHECKS:
        val = evidence.get(key)
        ok, n_a = _bool_of(val)
        rows.append({
            "name": key, "label": label,
            "ok": ok, "not_applicable": n_a, "detail": detail_of(val),
        })

    failed = [r for r in rows if not r["not_applicable"] and not r["ok"]]
    reasons = [f"{r['name']}: {r['label']}（{r['detail'] or '证据缺失'}）" for r in failed]
    return {
        "ok": not failed,
        "checks": rows,
        "reasons": reasons,
        "gate": "PASS" if not failed else "FAIL",
    }


def silent_truncation_ok(records):
    """从逐请求回执统计静默截断数（附-3/8：可变输出禁止静默截断）。

    RETURN:
        (count, ok)
    """
    n = sum(1 for r in records if r.get("verdict") == "silent_truncation")
    return n, n == 0


def assemble_evidence(cfg, records, quality=None, mechanisms=None,
                      config_ok=True, dataset_ok=True,
                      role_diff=None, sha_binding=None, cost=None,
                      server_metrics=None):
    """组装 run.py 用的 evidence dict（Z3 输入）。

    ARGS:
        cfg            展开配置（meta.baseline_role/comparison_type 身份）
        records        C3 逐请求回执
        quality        oracle.evaluate() 的汇总（None=自动对 records 计算）
        mechanisms     metrics.evaluate_mechanisms() 结果（None=用 server_metrics 计算）
        config_ok / dataset_ok    receipt.py / prepare 侧的布尔（由 harness 传入）
        role_diff      roles.diff_roles() 结果；非角色化 profile 传 None
        sha_binding    roles.sha_binding_gate() 结果；未声明传 None
        cost           cost.k2_gate() 结果；非 A4 传 None
        server_metrics M1/M2/M3/M4 解析后的指标 dict（真机由 acceptance.sh 采集）
    RETURN:
        dict 证据包（直接入 z3_gate）
    """
    if quality is None:
        quality = oracle.evaluate(cfg, records)
    if mechanisms is None:
        mechanisms = metrics.evaluate_mechanisms(cfg, server_metrics or {})
    n_trunc, ok_trunc = silent_truncation_ok(records)
    return {
        "config_ok": bool(config_ok),
        "role_diff": role_diff,
        "sha_binding": sha_binding,
        "dataset_ok": bool(dataset_ok),
        "quality": {"ok": bool(quality.get("gate", {}).get("ok")),
                    "reasons": quality.get("gate", {}).get("reasons", [])},
        "mechanisms": {"ok": bool(mechanisms.get("ok")),
                       "reasons": mechanisms.get("reasons", [])},
        "no_trunc": {"ok": ok_trunc, "reasons": [] if ok_trunc else [f"{n_trunc} 条静默截断"]},
        "cost": cost,
    }
