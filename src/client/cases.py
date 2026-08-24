"""C1 客户端合同模块：按 cell 生成 canonical request case 清单（V4.1 附-5/6/7）。

数据源：prepare.sh subsets 产出的固定子集（datasets/*.jsonl），按 manifest 的
ordered_sha256 顺序循环，不随机重抽。同一 cell 两次生成字节一致：
  - request_id = SHA-256(config_hash | cell | repeat | sequence_no)（附-5 确定性生成）；
  - 顺序 = 各 case 在清单中的固定 index（sequence_no）。

各 cell 合同：
  a2-dialogue : 16 会话 × 4 轮（ShareGPT 64 例）；同会话累积历史（历史由执行器 C3 运行时组装）
  a2-tool     : 32 single + 16 multi/parallel + 16 multi-turn（BFCL-V4；multi/parallel 显式 parallel_tool_calls）
  a2-reason   : 64 例（GSM8K），input≤1024 / output_cap=512
  a2-struct   : 64 例（JSONSchemaBench），input≤2048 / output_cap=512，逐 case 附 JSON Schema
  a2-long     : 32 例（16×8192+512、16×16384+1024）
  a4-mt       : 四租户（dialogue/tool/reasoning/structured）各 25% 份额（附-7），tool 租户发 tools
  a1 / a3-32k : closed-loop 形状合同（合成/长文本）

tools / schema：子集记录携带真实定义时优先（tools_source=dataset）；未接入时
（bfcl/jsonschema url 未填）回退到模块内置固定集并标注 source（正式测量必须 dataset）。
"""
import hashlib

from client import common


def _request_id(config_hash, cell, repeat, seq):
    return common.sha256_hex(f"{config_hash}|{cell}|r{repeat}|{seq:06d}")


# --- 内置固定工具/JSON Schema（BFCL/JSONSchemaBench 子集未接入时的 smoke 回退，标注 source）---
FOLLOW_UP = "Continue the conversation. If a tool call is needed, call it now."

FALLBACK_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的当前天气",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "城市名"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "查询指定时区的当前时间",
            "parameters": {
                "type": "object",
                "properties": {"timezone": {"type": "string", "description": "IANA 时区名"}},
                "required": ["timezone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "搜索关键词并返回结果",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]

FALLBACK_SCHEMAS = [
    {"type": "object", "properties": {"name": {"type": "string"},
                                      "amount": {"type": "number"}},
     "required": ["name"], "additionalProperties": False},
    {"type": "object", "properties": {"city": {"type": "string"},
                                      "days": {"type": "integer"}},
     "required": ["city"], "additionalProperties": False},
    {"type": "object", "properties": {"summary": {"type": "string"},
                                      "tags": {"type": "array", "items": {"type": "string"}}},
     "required": ["summary"], "additionalProperties": False},
]


def _case(config_hash, cell, repeat, seq, kind, content, **extra):
    return {
        "sequence_no": seq,
        "request_id": _request_id(config_hash, cell, repeat, seq),
        "kind": kind,
        "content": content,
        **extra,
    }


def _wrap(sub, i):
    """按 ordered_sha256 顺序循环取 case；不足 N 时取模回绕（防御，D2 保证 n 齐）。"""
    return sub[i % len(sub)]


# --- 各 cell 生成器 -------------------------------------------------------------

def _dialogue(cfg, ch, loader, repeat, n_session=16, n_round=4):
    sub = loader("sharegpt")
    wl = cfg["workload"]
    cases = []
    seq = 0
    for s in range(n_session):
        for r in range(n_round):
            rec = _wrap(sub, s * n_round + r)
            cases.append(_case(ch, cfg["meta"]["cell"], repeat, seq,
                               kind="dialogue_round", content=rec["content"],
                               session_id=s, round=r))
            seq += 1
    return cases


def _tool(cfg, ch, loader, repeat):
    sub = loader("bfcl")
    fixed = cfg["workload"]["dataset_fixed"]
    single, mp, mt = fixed.get("single", 32), fixed.get("multi_parallel", 16), fixed.get("multi_turn", 16)
    kinds = (["tool_single"] * single
             + ["tool_multi_parallel"] * mp
             + ["tool_multi_turn"] * mt)
    cases = []
    seq = 0
    for i, kind in enumerate(kinds):
        rec = _wrap(sub, i)
        tools = rec.get("tools") or FALLBACK_TOOLS
        extra = {
            "tools": tools,
            "tools_source": "dataset" if rec.get("tools") else "builtin-fallback",
            "tool_choice": "auto",
            "parallel_tool_calls": kind == "tool_multi_parallel",
        }
        if kind == "tool_multi_turn":
            # 16 个多轮会话 × 2 轮：round0 原文 + round1 固定跟进语，同会话累积历史
            extra.update({"session_id": i, "round": 0, "rounds": 2})
            cases.append(_case(ch, cfg["meta"]["cell"], repeat, seq, kind,
                               content=rec["content"], **extra))
            seq += 1
            cases.append(_case(ch, cfg["meta"]["cell"], repeat, seq, kind,
                               content=FOLLOW_UP, session_id=i, round=1, rounds=2,
                               tools=extra["tools"], tools_source=extra["tools_source"],
                               tool_choice="auto", parallel_tool_calls=True))
            seq += 1
            continue
        cases.append(_case(ch, cfg["meta"]["cell"], repeat, seq, kind,
                           content=rec["content"], **extra))
        seq += 1
    return cases


def _reason(cfg, ch, loader, repeat, n=64):
    sub = loader("gsm8k")
    shape = cfg["workload"]["request_shape"] or {"input_max": 1024, "output_cap": 512}
    cases = []
    for i in range(n):
        rec = _wrap(sub, i)
        cases.append(_case(ch, cfg["meta"]["cell"], repeat, i, kind="reason",
                           content=rec["content"], shape=shape,
                           max_tokens=shape.get("output_cap")))
    return cases


def _struct(cfg, ch, loader, repeat, n=64):
    sub = loader("jsonschema")
    shape = cfg["workload"]["request_shape"] or {"input_max": 2048, "output_cap": 512}
    cases = []
    for i in range(n):
        rec = _wrap(sub, i)
        schema = rec.get("schema") or FALLBACK_SCHEMAS[i % len(FALLBACK_SCHEMAS)]
        cases.append(_case(ch, cfg["meta"]["cell"], repeat, i, kind="struct",
                           content=rec["content"], shape=shape, schema=schema,
                           schema_source="dataset" if rec.get("schema") else "builtin-fallback",
                           max_tokens=shape.get("output_cap")))
    return cases


def _long(cfg, ch, loader, repeat):
    sub = loader("long")
    buckets = cfg["workload"]["dataset_fixed"] or [{"count": 16, "input_len": 8192, "output_len": 512},
                                                    {"count": 16, "input_len": 16384, "output_len": 1024}]
    cases = []
    seq = 0
    for b in buckets:
        for j in range(b["count"]):
            rec = _wrap(sub, seq)
            cases.append(_case(ch, cfg["meta"]["cell"], repeat, seq,
                               kind=f"long_{b['input_len']}", content=rec["content"],
                               shape={"input_len": b["input_len"], "output_len": b["output_len"]},
                               max_tokens=b["output_len"]))
            seq += 1
    return cases


def _a1(cfg, ch, repeat):
    """A1 closed-loop：每固定形状 1 个合成 case（确定性 prompt），执行器按轮次重复。"""
    cases = []
    for i, s in enumerate(cfg["workload"]["shapes"]):
        cases.append(_case(ch, cfg["meta"]["cell"], repeat, i, kind="synthetic",
                           content=_synthetic_prompt(s.get("input_len", 1024)),
                           shape=s, max_tokens=s.get("output_len", 1)))
    return cases


_SYNTH_WORDS = ("alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu "
                "xi omicron pi rho sigma tau upsilon phi chi psi omega "
                "model tensor attention prefill decode kernel compute latency throughput").split()


def _synthetic_prompt(input_len):
    """确定性合成 prompt（A1）：固定词库按 LCG 循环，字符长度≈input_len×4 token。

    精确 token 数以服务端 usage（prompt_tokens）为准；此处仅提供可复现的固定形状输入。
    """
    out, n, i = [], 0, 0
    words = _SYNTH_WORDS
    while n < int(input_len) * 4:
        w = words[(i * 7 + 3) % len(words)]
        out.append(w)
        n += len(w) + 1
        i += 1
    return " ".join(out)


def _a3(cfg, ch, loader, repeat):
    """A3：长文本 corpus 按 document_id 稳定顺序拼接（D1），渲染后输入 30720 + 输出 2048。"""
    sub = loader("long")
    corpus = "\n\n".join(rec["content"] for rec in sub)
    input_tokens = cfg["workload"].get("input_tokens", 30720)
    output = cfg["sampling"].get("max_tokens", 2048)
    return [_case(ch, cfg["meta"]["cell"], repeat, 0, kind="a3_long",
                  content=corpus, shape={"input_len": input_tokens, "output_len": output},
                  max_tokens=output)]


def _a4(cfg, ch, loader, repeat, per_tenant=16):
    """A4 四租户共享单服务，各 25% 输出 token 份额；tool 租户显式发 tools（附-5/7）。"""
    tenants = cfg["workload"]["tenants"]
    cap_d = tenants.get("dialogue", {}).get("output_cap")
    cap_t = tenants.get("tool", {}).get("output_cap")
    sub_d = loader("sharegpt")
    sub_t = loader("bfcl")
    sub_r = loader("gsm8k")
    sub_s = loader("jsonschema")
    cases = []
    seq = 0
    # dialogue 租户：4 会话 × 4 轮 = 16；max_tokens=output_cap 约束输出长度（调度成本假设成立）
    for s in range(4):
        for r in range(4):
            rec = _wrap(sub_d, s * 4 + r)
            cases.append(_case(ch, cfg["meta"]["cell"], repeat, seq, kind="dialogue_round",
                               tenant="dialogue", content=rec["content"],
                               session_id=s, round=r, max_tokens=cap_d))
            seq += 1
    # tool 租户：8 single + 4 multi/parallel + 4 multi-turn = 16
    for i in range(per_tenant):
        if i < 8:
            kind, pp = "tool_single", False
        elif i < 12:
            kind, pp = "tool_multi_parallel", True
        else:
            kind, pp = "tool_multi_turn", True
        rec = _wrap(sub_t, i)
        tools = rec.get("tools") or FALLBACK_TOOLS
        # tool_choice=required：所有 tool case 都显式要求调用工具。auto 下模型在
        # 长历史/并发时会先输出大段无关前言（多语言），拉高输出 token 破坏 A4 公平性
        # 并可能命中 max_tokens 造成 silent_truncation；required 强制只输出工具调用。
        extra = {"tenant": "tool", "tools": tools,
                 "tools_source": "dataset" if rec.get("tools") else "builtin-fallback",
                 "tool_choice": "required", "parallel_tool_calls": pp,
                 "send_tools": True, "max_tokens": cap_t}
        if kind == "tool_multi_turn":
            extra.update({"session_id": i, "round": 0, "rounds": 2})
        cases.append(_case(ch, cfg["meta"]["cell"], repeat, seq, kind,
                           content=rec["content"], **extra))
        seq += 1
    # reasoning / structured 租户各 16
    shape_r = {"input_max": 1024, "output_cap": 512}
    for i in range(per_tenant):
        rec = _wrap(sub_r, i)
        cases.append(_case(ch, cfg["meta"]["cell"], repeat, seq, kind="reason",
                           tenant="reasoning", content=rec["content"], shape=shape_r,
                           max_tokens=512))
        seq += 1
    shape_s = {"input_max": 2048, "output_cap": 512}
    for i in range(per_tenant):
        rec = _wrap(sub_s, i)
        schema = rec.get("schema") or FALLBACK_SCHEMAS[i % len(FALLBACK_SCHEMAS)]
        cases.append(_case(ch, cfg["meta"]["cell"], repeat, seq, kind="struct",
                           tenant="structured", content=rec["content"], shape=shape_s,
                           schema=schema,
                           schema_source="dataset" if rec.get("schema") else "builtin-fallback",
                           max_tokens=512))
        seq += 1
    return cases


_GENERATORS = {
    "a2-dialogue": _dialogue,
    "a2-tool": _tool,
    "a2-reason": _reason,
    "a2-struct": _struct,
    "a2-long": _long,
    "a4-mt": _a4,
    "a1": _a1,
    "a3-32k": _a3,
}


def generate_cases(cfg, datasets_dir, repeat=0, allow_missing=False):
    """生成 cell 的 canonical request case 清单。

    RETURN:
        dict: {
            schema, config_hash, cell_id, precision, api, endpoint, mode, repeat,
            cases[], cases_sha256, tenant_shares(仅 A4),
        }
        同一 (cfg, datasets_dir, repeat) → 字节一致。
    """
    cell = cfg["meta"]["cell"]
    if cell not in _GENERATORS:
        raise ValueError(f"未知 cell={cell!r}，客户端合同生成器未注册")
    ch = common.config_hash(cfg)
    loader = lambda name: common.load_subset(name, datasets_dir, allow_missing)
    fn = _GENERATORS[cell]
    if cell == "a1":
        cases = fn(cfg, ch, repeat)
    else:
        cases = fn(cfg, ch, loader, repeat)

    doc = {
        "schema": "vllm-xcheck-client-cases-v1",
        "config_hash": ch,
        "cell_id": cell,
        # 注意：不嵌 precision —— 客户端合同与精度/角色无关，B0/B1 须字节一致（附-1）。
        "api": cfg["meta"].get("api"),
        "endpoint": cfg["meta"]["endpoint"],
        "mode": cfg["workload"].get("mode"),
        "repeat": repeat,
        "cases": cases,
        "cases_sha256": common.sha256_hex(common.canonical_json(cases)),
    }
    if cell == "a4-mt":
        doc["tenant_shares"] = cfg["workload"].get("tenants")
    return doc
