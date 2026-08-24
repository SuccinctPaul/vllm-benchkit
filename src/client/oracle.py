"""组 Q —— 质量 oracle / 判定程序（附-8 判定程序）。

消费 C3 逐请求回执（receipts.jsonl），对每类输出做质量判定：
  Q1 工具判定（BFCL V4）：工具名匹配（回执 expected_tool_name，见 evaluate_one）；
  Q2 推理判定（GSM8K）：数值答案抽取（固定抽取器）与参考答案比对；
  Q3 结构化判定（JSONSchemaBench）：按 JSON Schema Draft 2020-12 校验输出
     （disable_any_whitespace=false / disable_additional_properties=false 语义即不额外收紧）；
  Q4 长文本/多轮判定：dialogue 协议成功 + 多轮连续性；A3 长文本插入可核验目标事实，
     oracle 检查目标事实命中、无截断、输出达标；
  Q5 W8A8 工件资格门禁：相对同源 FP16 成功率下降均≤1pp；Schema 100%；工具/结构化≥95%；
     不合格时 W8A8 交付线不执行、FP16 控制线仍完成。

回执须带：kind（dialogue_round / tool_* / reason / struct / long_* / a3_long）、
verdict、output_text、output_tool_calls、finish_reason、truncated、output_tokens。
判定结果逐条进入质量回执（quality.jsonl），并汇总为质量报告。
"""
import json
import re

import jsonschema

# Q5 资格阈值（附-8）
W8A8_DROP_PP = 1.0          # 相对同源 FP16 成功率下降 ≤ 1pp
TOOL_STRUCT_MIN_PCT = 95.0  # 工具/结构化成功率 ≥ 95%
SCHEMA_MIN_PCT = 100.0      # Schema 合法率 100%


def _json_loads(text):
    """从输出文本提取 JSON（容忍 ```json 围栏 / 前导说明）。失败返回 None。"""
    if not text:
        return None
    t = text.strip()
    fence = re.match(r"```(?:json)?\s*(.*?)\s*```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    try:
        return json.loads(t)
    except ValueError:
        # 尝试从文本中截取首个 {...} / [...] 平衡块
        for open_ch, close_ch in (("{", "}"), ("[", "]")):
            i = t.find(open_ch)
            if i < 0:
                continue
            depth = 0
            for j in range(i, len(t)):
                if t[j] == open_ch:
                    depth += 1
                elif t[j] == close_ch:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(t[i:j + 1])
                        except ValueError:
                            break
    return None


def extract_gsm8k_answer(text):
    """Q2 GSM8K 答案抽取（固定抽取器）：取最后出现的数字答案。

    规则（确定性）：
      1. 匹配 '#### <num>' 格式（GSM8K 标准答案标记），取其后数字；
      2. 否则匹配最后一行/句中的数字（含负数、小数、逗号分组）；
      3. 无数字 → None（判定失败）。
    """
    if not text:
        return None
    m = re.search(r"####\s*([-+]?\d+(?:[.,]\d+)?)", text)
    if m:
        return _norm_num(m.group(1))
    nums = re.findall(r"[-+]?\d+(?:[.,]\d+)?", text)
    return _norm_num(nums[-1]) if nums else None


def _norm_num(s):
    return s.replace(",", "").replace("。", ".")


def validate_json_schema(output, schema):
    """Q3 结构化判定：按 JSON Schema Draft 2020-12 校验。

    RETURN:
        dict {ok, errors: [...]}
    """
    data = output if isinstance(output, (dict, list)) else _json_loads(output)
    if data is None:
        return {"ok": False, "errors": ["输出不是合法 JSON"]}
    validator = jsonschema.Draft202012Validator(schema)
    errors = [f"schema: {e.message}" for e in sorted(validator.iter_errors(data), key=str)]
    return {"ok": not errors, "errors": errors}


def check_long_fact(output, facts):
    """Q4 长文本目标事实检查：任一目标事实命中 + 无截断（A3）。"""
    if not output:
        return {"ok": False, "errors": ["空输出"]}
    missing = [f for f in facts if f not in output]
    return {
        "ok": not missing,
        "errors": [f"目标事实未命中: {f}" for f in missing],
    }


def evaluate_one(cfg, rec):
    """对单条 C3 回执做质量判定（按 kind 路由）。

    RETURN:
        dict 质量回执：request_id / kind / tenant / verdict / q_ok / q_errors / q_rule
        非质量类 kind（dialogue_round 等）只做协议/完整性检查。
    """
    kind = rec.get("kind", "")
    verdict = rec.get("verdict")
    base = {
        "request_id": rec.get("request_id"),
        "kind": kind,
        "tenant": rec.get("tenant"),
        "verdict": verdict,
        "q_ok": True,
        "q_errors": [],
        "q_rule": None,
    }
    if verdict != "ok":
        return {**base, "q_ok": False,
                "q_errors": [f"verdict={verdict}（非 ok，质量不判定）"]}

    text = rec.get("output_text") or ""
    q_errors = []

    if kind == "reason":
        base["q_rule"] = "gsm8k-answer"
        # 参考答案：优先用回执携带的 reference_answer（正式测量由数据集注入）
        exp = rec.get("reference_answer")
        got = extract_gsm8k_answer(text)
        if got is None:
            q_errors.append("抽取不到数字答案")
        elif exp is not None and _norm_num(str(exp)) != got:
            q_errors.append(f"答案不匹配: got={got} exp={exp}")

    elif kind.startswith("struct"):
        base["q_rule"] = "jsonschema-draft2020-12"
        schema = rec.get("schema")
        if not schema:
            q_errors.append("回执缺 schema，无法校验")
        else:
            r = validate_json_schema(text, schema)
            if not r["ok"]:
                q_errors.extend(r["errors"])

    elif kind.startswith("tool_"):
        base["q_rule"] = "bfcl-name-match"
        calls = rec.get("output_tool_calls") or []
        if not calls:
            q_errors.append("无工具调用")
        # 期望工具名：回执携带 expected_tool_name（正式测量由数据集注入）；
        # 参数级比较需要数据集提供 expected_tool_args，本期回执不携带（见 acceptance-tasks 组 Q1）
        exp = rec.get("expected_tool_name")
        if exp is not None:
            got_names = [c.get("name") for c in calls if isinstance(c, dict)]
            if exp not in got_names:
                q_errors.append(f"工具名不匹配: got={got_names} exp={exp}")

    elif kind.startswith("long_") or kind == "a3_long":
        base["q_rule"] = "target-fact-hit+no-truncation"
        facts = rec.get("target_facts") or []
        if kind == "a3_long":
            r = check_long_fact(text, facts)
            if not r["ok"]:
                q_errors.extend(r["errors"])
        if rec.get("truncated"):
            q_errors.append("输出被截断（finish=length）")
        out_len = rec.get("output_tokens") or 0
        expected = rec.get("shape", {}).get("output_len") if rec.get("shape") else None
        if expected and out_len < expected:
            q_errors.append(f"输出 token 不足: got={out_len} expected={expected}")

    elif kind == "dialogue_round":
        base["q_rule"] = "protocol-ok+continuity"
        # 多轮连续性：round>0 时服务端收到历史（回执侧已由执行器保证）；此处校验输出非空
        if rec.get("round", 0) > 0 and not text:
            q_errors.append("多轮轮次输出为空")

    return {**base, "q_ok": not q_errors, "q_errors": q_errors}


def _kind_pct(per_request, by_kind, kind_prefix):
    """按 kind 前缀聚合成功率（%）；无样本返回 None。

    优先用 per_request 明细逐条统计（kind 为完整字符串，如 tool_single）；
    缺省（如自测传最小 summary）回退 by_kind 汇总，按样本数加权平均。
    """
    if per_request:
        rows = [r for r in per_request if (r.get("kind") or "").startswith(kind_prefix)]
        if not rows:
            return None
        return round(100.0 * sum(1 for r in rows if r.get("q_ok")) / len(rows), 2)
    rows = [b for k, b in (by_kind or {}).items()
            if k.startswith(kind_prefix) and b.get("total")]
    if not rows:
        return None
    return round(100.0 * sum(b.get("ok", 0) for b in rows) / sum(b["total"] for b in rows), 2)


def evaluate(cfg, records):
    """对一批回执做质量判定并汇总。

    RETURN:
        dict {
            summary: {total, ok, by_kind: {kind: {total, ok, ok_pct}}},
            per_request: [质量回执...], schema_valid_pct, tool_pct, struct_pct,
            gate: {ok, reasons}（Q5 结构化/工具 ≥95%、Schema 100%）
        }
    """
    per_request = [evaluate_one(cfg, r) for r in records]
    total = len(per_request)
    ok = sum(1 for r in per_request if r["q_ok"])
    by_kind = {}
    for r in per_request:
        k = r["kind"] or "unknown"
        b = by_kind.setdefault(k, {"total": 0, "ok": 0})
        b["total"] += 1
        b["ok"] += 1 if r["q_ok"] else 0
    for b in by_kind.values():
        b["ok_pct"] = round(100.0 * b["ok"] / b["total"], 2) if b["total"] else 100.0

    def pct(kind_prefix):
        return _kind_pct(per_request, by_kind, kind_prefix)

    struct_pct = pct("struct")
    tool_pct = pct("tool_")
    schema_rows = [r for r in per_request if (r["kind"] or "").startswith("struct")]
    schema_valid_pct = round(100.0 * sum(r["q_ok"] for r in schema_rows) / len(schema_rows), 2) \
        if schema_rows else None

    reasons = []
    if tool_pct is not None and tool_pct < TOOL_STRUCT_MIN_PCT:
        reasons.append(f"工具成功率 {tool_pct}% < {TOOL_STRUCT_MIN_PCT}%")
    if struct_pct is not None and struct_pct < TOOL_STRUCT_MIN_PCT:
        reasons.append(f"结构化成功率 {struct_pct}% < {TOOL_STRUCT_MIN_PCT}%")
    if schema_valid_pct is not None and schema_valid_pct < SCHEMA_MIN_PCT:
        reasons.append(f"Schema 合法率 {schema_valid_pct}% < {SCHEMA_MIN_PCT}%")

    return {
        "summary": {"total": total, "ok": ok, "by_kind": by_kind},
        "per_request": per_request,
        "tool_pct": tool_pct,
        "struct_pct": struct_pct,
        "schema_valid_pct": schema_valid_pct,
        "gate": {"ok": not reasons, "reasons": reasons},
    }


def w8a8_qualification(fp16_report, w8a8_report):
    """Q5 W8A8 工件资格门禁：相对同源 FP16 成功率下降均 ≤1pp；Schema 100%；工具/结构化 ≥95%。

    ARGS:
        fp16_report / w8a8_report  evaluate() 的汇总 dict（同 cell 的 FP16 与 W8A8 两侧）
    RETURN:
        dict {ok, reasons, per_metric: [...]}
    """
    metrics = ("reason", "tool_", "struct", "long_")
    per_metric = []
    reasons = []
    for m in metrics:
        f16_pct = _kind_pct((fp16_report or {}).get("per_request"),
                            ((fp16_report or {}).get("summary") or {}).get("by_kind"), m)
        w8_pct = _kind_pct((w8a8_report or {}).get("per_request"),
                           ((w8a8_report or {}).get("summary") or {}).get("by_kind"), m)
        if f16_pct is None and w8_pct is None:
            continue
        drop = (f16_pct or 0.0) - (w8_pct or 0.0)
        row = {"metric": m, "fp16_pct": f16_pct, "w8a8_pct": w8_pct, "drop_pp": round(drop, 2)}
        per_metric.append(row)
        if drop > W8A8_DROP_PP:
            reasons.append(f"{m}: FP16={f16_pct}% → W8A8={w8_pct}%（下降 {drop:.2f}pp > {W8A8_DROP_PP}pp）")

    w8_gate = w8a8_report.get("gate") or {}
    if not w8_gate.get("ok"):
        reasons.extend(w8_gate.get("reasons") or [])
    return {"ok": not reasons, "per_metric": per_metric, "reasons": reasons}
