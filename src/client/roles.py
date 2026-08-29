"""组 I —— B0/B1 角色与配置身份（附-1、附-8 配置身份）。

I1 角色化配置模型：`baseline_role=B0 / candidate_role=B1` 与 `comparison_type`
    （FP16_CONTROL / SYSTEM_DELIVERY / W8A8_MATCHED）。
    SYSTEM_DELIVERY 仅「模型工件 / served_model_name / quantization」三项按角色取值，
    其余硬件/数据/请求/采样/arrival/SLO/质量门槛/服务配置相同。
I2 comparison_id / config_id：RFC 8785 JCS 规范化 + SHA-256。
    - comparison_id = SHA-256(JCS(角色化比较合同))
    - 各角色 config_id = SHA-256(JCS({code_sha, image_digest, config_sha256}))
I3 代码/镜像 SHA 绑定门禁：实际运行环境 commit/digest 与声明值比对，fail-closed。

本模块只依赖通用数据结构，配合 acceptance.expand() 的 effective 树使用；
差分测试（同 comparison_type 各角色展开后除固定角色字段逐项相等）在 run.py 自测里断言。
"""
import copy

from client import jcs, common

# 角色取值字段：SYSTEM_DELIVERY 允许随角色变化的字段（其余字段两侧必须相等）
ROLE_FIELDS = ("model.artifact", "model.served_name", "model.quantization")

# 合法角色与比较类型
ROLES = ("B0", "B1")
COMPARISON_TYPES = ("FP16_CONTROL", "SYSTEM_DELIVERY", "W8A8_MATCHED")

# 配置树中与角色无关、应两侧一致的 section（差分测试范围；不含 meta 等身份字段）
DIFF_SECTIONS = ("model", "server", "env", "sampling", "workload")


def _get_path(cfg, dotted):
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def role_field_delta(cfg):
    """抽取角色字段当前值（按 ROLE_FIELDS 顺序，缺省 None）。"""
    return {f: _get_path(cfg, f) for f in ROLE_FIELDS}


def apply_role(cfg, role, comparison_type, **role_values):
    """对展开配置应用角色 overlay。

    ARGS:
        cfg               acceptance.expand() 的 effective 树（B0 侧角色字段已就位）
        role              B0 / B1
        comparison_type   FP16_CONTROL / SYSTEM_DELIVERY / W8A8_MATCHED
        **role_values     角色字段的实例化值（artifact/served_name/quantization 中任一；
                          未给的沿用 cfg 现值）。实例化方填具体值；本函数只保证「只改角色字段」。
    RETURN:
        dict 深拷贝后的配置树（meta 附 role/comparison_type 身份字段）
    """
    if role not in ROLES:
        raise ValueError(f"非法角色 {role!r}（允许 {ROLES}）")
    if comparison_type not in COMPARISON_TYPES:
        raise ValueError(f"非法 comparison_type {comparison_type!r}（允许 {COMPARISON_TYPES}）")
    unknown = set(role_values) - set(ROLE_FIELDS)
    if unknown:
        raise ValueError(f"角色字段越界: {sorted(unknown)}（仅允许 {ROLE_FIELDS}）")

    out = copy.deepcopy(cfg)
    for field in ROLE_FIELDS:
        if field in role_values and role_values[field] is not None:
            parts = field.split(".")
            cur = out
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = role_values[field]
    out.setdefault("meta", {})["baseline_role"] = role
    out.setdefault("meta", {})["comparison_type"] = comparison_type
    return out


def role_contract(b0_cfg, b1_cfg, comparison_type):
    """角色化比较合同（I2 的 comparison_id 输入）。

    合同 = JCS 规范化后的 {comparison_type, roles: {B0: 角色字段, B1: 角色字段},
            config_sha: 两侧公共配置的 SHA-256}。
    RETURN:
        dict（JCS 可直接 canonicalize）
    """
    b0_delta = role_field_delta(b0_cfg)
    b1_delta = role_field_delta(b1_cfg)
    # 公共配置 = 去掉角色字段后的稳定哈希（两侧用同一份 → comparison_id 只随角色字段变化）
    common_part = {"b0": _config_without_role_fields(b0_cfg),
                   "b1": _config_without_role_fields(b1_cfg)}
    return {
        "schema": "vllm-xcheck-comparison-contract-v1",
        "comparison_type": comparison_type,
        "roles": {"B0": b0_delta, "B1": b1_delta},
        "common_config_sha256": common.sha256_hex(common.canonical_json(common_part)),
    }


def comparison_id(b0_cfg, b1_cfg, comparison_type):
    """comparison_id = SHA-256(JCS(角色化比较合同))。"""
    return jcs.sha256_hex(role_contract(b0_cfg, b1_cfg, comparison_type))


def config_id(cfg, code_sha, image_digest=None, config_sha256=None):
    """B0/B1 各自的 config_id = SHA-256(JCS(代码/镜像身份 + 同一配置))。

    ARGS:
        cfg            展开配置（或其 config_sha256）
        code_sha       该角色绑定的 core+plugin 提交（B0 固定 / B1 验收发布提交）
        image_digest   OCI digest（B1 必须；B0 可空）
        config_sha256  配置树身份；缺省对该角色「去掉角色字段后的配置」计算
    RETURN:
        dict {config_id, code_sha, image_digest, config_sha256}
    """
    if config_sha256 is None:
        config_sha256 = common.sha256_hex(common.canonical_json(_config_without_role_fields(cfg)))
    payload = {
        "schema": "vllm-xcheck-config-identity-v1",
        "code_sha": code_sha,
        "image_digest": image_digest,
        "config_sha256": config_sha256,
    }
    return {
        "config_id": jcs.sha256_hex(payload),
        "code_sha": code_sha,
        "image_digest": image_digest,
        "config_sha256": config_sha256,
    }


def diff_roles(b0_cfg, b1_cfg, comparison_type):
    """差分：同 comparison_type 各角色展开后，除角色字段外逐项相等（I1 验收）。

    RETURN:
        dict {equal, role_fields_only, diffs: [{section, field, b0, b1}]}
        任意非角色字段不等 → equal=False（fail-closed）。
    """
    diffs = []
    for section in DIFF_SECTIONS:
        a, b = b0_cfg.get(section, {}), b1_cfg.get(section, {})
        for k in sorted(set(a) | set(b)):
            dotted = f"{section}.{k}"
            if dotted in ROLE_FIELDS:
                continue
            if a.get(k) != b.get(k):
                diffs.append({"section": section, "field": k, "b0": a.get(k), "b1": b.get(k)})
    return {
        "equal": not diffs,
        "role_fields_only": not diffs,
        "diffs": diffs,
        "role_fields": sorted(ROLE_FIELDS),
    }


def _config_without_role_fields(cfg):
    """去掉角色字段后的配置子集（用于公共配置身份，避免角色字段污染）。

    除 ROLE_FIELDS 外，同时剔除 apply_role 写入的 meta 身份字段
    （baseline_role / comparison_type），使 B0/B1 的公共配置身份一致。
    """
    out = copy.deepcopy(cfg)
    for field in ROLE_FIELDS:
        parts = field.split(".")
        cur = out
        for p in parts[:-1]:
            if not isinstance(cur.get(p), dict):
                break
            cur = cur[p]
        cur.pop(parts[-1], None)
    meta = out.get("meta")
    if isinstance(meta, dict):
        meta.pop("baseline_role", None)
        meta.pop("comparison_type", None)
    return out


def sha_binding_gate(declared, actual):
    """I3 代码/镜像 SHA 绑定门禁（fail-closed）。

    ARGS:
        declared  {code_sha, image_digest?}  声明/绑定值（B0 固定；B1 验收发布）
        actual    {code_sha, image_digest?}  运行环境实际读到的值
    RETURN:
        dict {ok, reason, checks: [...]}
    """
    checks = []
    for key in ("code_sha", "image_digest"):
        exp = declared.get(key)
        got = actual.get(key)
        if not exp:
            # 未声明即不适用（B0 仅 code 绑定，image_digest 可空）→ 记账为 ok
            checks.append({"field": key, "ok": True, "reason": "未声明（不适用）"})
            continue
        if not got:
            checks.append({"field": key, "ok": False,
                           "reason": f"实际环境未提供 {key}"})
            continue
        ok = exp == got
        checks.append({"field": key, "ok": ok,
                       "reason": "匹配" if ok else f"不匹配: declared={exp} actual={got}"})
    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "reason": "PASS" if ok else "FAIL", "checks": checks}
