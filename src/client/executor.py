"""C3 请求执行器：流式执行 + 逐请求回执（V4.1 附-5/6/7）。

职责：
  - 按 cases 合同渲染每个请求的 HTTP body（wirerequest，附-5），并记录 body 的 SHA-256；
  - 按到达序列（C2 的 nominal_ts）发送；并发饱和时本地 FIFO 等待（asyncio.Semaphore），
    保留 nominal timestamp、记录 sendlag，不丢弃、不重排、不突发追赶（附-8 到达背压）；
  - 流式（streaming=true）逐 token 计时：TTFT / TPOT / E2EL（附-5/6）；
  - 逐请求回执：输出 token 数、finish_reason / EOS / 截断、错误 / 超时 / 静默截断全部保留
    （不吞错），落 runs 目录 JSONL 供组 Q（oracle）与组 M（指标）消费；
  - 多轮对话 / 多轮工具会话按 session_id 累积历史（round 顺序由合同保证）。

时钟约定：send / ttft / tpot / e2el 全部以 time.monotonic() 计时（与 C2 clock=monotonic 一致）；
nominal_ts 由到达合同给出，sendlag = 实际发送时刻 - nominal。

回执 verdict 分类：
  ok                正常完成（收到 ≥1 个输出 token，且 finish_reason ∈ stop/tool_calls；
                     固定输出形状类 long/synthetic 命中 max_tokens=length 视为完整输出）
  failed            HTTP/传输错误（非 2xx、连接失败等）
  timeout           per-request 超时
  malformed         SSE 解析失败 / 流无 finish_reason / 0 输出 token（无解释的空输出）
  silent_truncation 可变输出场景 finish_reason=length（被 max_tokens 截断，附-3/8 禁止）
"""
import asyncio
import json
import os
import time

import httpx

from client import common

# OpenAI SSE 行前缀 / 终止标记
_SSE_DATA = "data:"
_SSE_DONE = "[DONE]"

# 固定输出形状的 case kind：命中 max_tokens(=length) 即完整输出，不算静默截断
_FIXED_OUTPUT_KINDS = ("synthetic", "a3_long")
_FIXED_OUTPUT_PREFIX = "long_"


def _is_fixed_output(case):
    kind = case.get("kind", "")
    return kind in _FIXED_OUTPUT_KINDS or kind.startswith(_FIXED_OUTPUT_PREFIX)


def percentile(values, q):
    """线性插值百分位（numpy 'linear' 同语义），确定性。values 须为已排序列表。"""
    return common.percentile(values, q)


def build_request_body(cfg, case, sampling, history):
    """渲染单请求的 HTTP body（附-5 wirerequest 约束）。

    ARGS:
        cfg      展开后的 effective 配置（meta/api、meta/endpoint、model/served_name）
        case     cases 合同中的单个 case
        sampling sampling 段（max_tokens 逐请求优先用 case 的）
        history  该会话已累积的 messages（多轮；单轮为空）
    RETURN:
        dict（OpenAI 兼容 /v1/chat/completions 或 /v1/completions）
    """
    api = cfg["meta"]["api"]
    body = {"model": cfg["model"]["served_name"], "stream": True}
    # 流式回执须带 usage（prompt_tokens/completion_tokens，A1-2 按真实 token 计 FLOPs）：
    # chat 与 completions 两个 endpoint 都要 include_usage，否则 vLLM 不回传 usage chunk。
    body["stream_options"] = {"include_usage": True}
    for k in ("temperature", "top_p", "n"):
        if k in sampling and sampling[k] is not None:
            body[k] = sampling[k]
    mt = case.get("max_tokens")
    if mt is None:
        mt = sampling.get("max_tokens")
    if mt is not None:
        body["max_tokens"] = mt
    if sampling.get("ignore_eos") is not None:
        body["ignore_eos"] = bool(sampling["ignore_eos"])
    if sampling.get("logprobs"):
        body["logprobs"] = True
    # smoke 专用：Qwen3 思考模型（如 MODEL_REF 覆盖的 Qwen3-0.6B）默认长 CoT 会触发
    # 可变输出静默截断与输出长度失真；VLLM_BENCHKIT_NO_THINKING=1 时经 chat_template_kwargs
    # 关掉 enable_thinking，让回退/短答在 output_cap 内自然 EOS。env 驱动、不进 config，
    # 因此不影响 config_hash / B0-B1 合同；正式测量（Qwen2.5 无思考）不设置即不生效。
    if os.environ.get("VLLM_BENCHKIT_NO_THINKING") == "1" and api == "chat":
        body["chat_template_kwargs"] = {"enable_thinking": False}

    if api == "chat":
        body["messages"] = list(history) + [{"role": "user", "content": case["content"]}]
        # 工具请求：仅当 case 带 tools 且显式 send_tools 非 false 时发送（A4 非 tool 租户不发送）
        if case.get("tools") and case.get("send_tools", True) is not False:
            body["tools"] = case["tools"]
            body["tool_choice"] = case.get("tool_choice", "auto")
            if case.get("parallel_tool_calls") is not None:
                body["parallel_tool_calls"] = case["parallel_tool_calls"]
        if case.get("schema"):
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": case["schema"], "strict": True},
            }
    else:  # /v1/completions（A1 closed-loop，固定 prompt）
        body["prompt"] = case.get("content") or ""
    return body


def _extract_chunk(cfg, chunk):
    """从单个 SSE chunk 提取 (content, tool_delta, finish_reason, usage)。

    chat: choices[0].delta.content / delta.tool_calls（tool call 片段计 1 token）
    completions: choices[0].text

    RETURN:
        content     str|None  delta.content 文本片段（含补全文本）
        tool_delta  list|None delta.tool_calls 片段（由调用侧按 index 累积 id/name/arguments）
        fr, usage   chunk 的 finish_reason / usage
    """
    usage = chunk.get("usage")
    choices = chunk.get("choices") or []
    content, fr, tool_delta = None, None, None
    if choices:
        ch = choices[0]
        fr = ch.get("finish_reason")
        if cfg["meta"]["api"] == "chat":
            delta = ch.get("delta") or {}
            content = delta.get("content")
            if not content:
                tool_delta = delta.get("tool_calls") or None
        else:
            content = ch.get("text") or ""
    return content, tool_delta, fr, usage


async def execute_one(cfg, case, sampling, base_url, client, timeout_s, history=None):
    """执行单个流式请求并产出逐请求回执（纯函数，不触碰会话历史）。

    RETURN:
        dict 回执：request_id / sequence_no / kind / tenant / session_id / round /
        body + body_sha256 / send_ts_monotonic / ttft_s / tpot_s[] / e2el_s /
        output_tokens / output_text / output_tool_calls / prompt_tokens /
        finish_reason / truncated / verdict / error
    """
    history = list(history or [])
    body = build_request_body(cfg, case, sampling, history)
    url = base_url.rstrip("/") + cfg["meta"]["endpoint"]
    rec = {
        "request_id": case["request_id"],
        "sequence_no": case["sequence_no"],
        "kind": case["kind"],
        "tenant": case.get("tenant"),
        "session_id": case.get("session_id"),
        "round": case.get("round"),
        "shape": case.get("shape"),
        "input_len": (case.get("shape") or {}).get("input_len")
                     or (case.get("shape") or {}).get("input_max"),
        "body_sha256": common.sha256_hex(common.canonical_json(body)),
        "body": body,
        "verdict": None,
        "error": None,
        "finish_reason": None,
        "truncated": False,
        "output_tokens": 0,
        "output_text": "",
        "output_tool_calls": [],
        "ttft_s": None,
        "tpot_s": [],
        "e2el_s": None,
        "prompt_tokens": None,
        "completion_tokens": None,
    }
    # 质量校验所需字段（oracle）：schema / expected_tool_name / reference_answer /
    # target_facts 由 case 生成时注入，逐请求透传回执，正式测量由数据集提供
    for key in ("schema", "expected_tool_name", "reference_answer", "target_facts"):
        if case.get(key) is not None:
            rec[key] = case[key]
    t_send = time.monotonic()
    rec["send_ts_monotonic"] = round(t_send, 6)

    try:
        async with client.stream("POST", url, json=body, timeout=timeout_s) as resp:
            if resp.status_code != 200:
                await resp.aread()
                rec["verdict"] = "failed"
                rec["error"] = f"HTTP {resp.status_code}"
                rec["e2el_s"] = round(time.monotonic() - t_send, 6)
                return rec
            first_ts, prev_ts, tokens = None, None, 0
            content_parts, tool_fragments = [], {}
            finish = None

            def _arrival(now):
                """记录一次输出 token 到达（TTFT/TPOT/计数；content 与 tool 片段共用）。"""
                nonlocal first_ts, prev_ts, tokens
                if first_ts is None:
                    first_ts = now
                    rec["ttft_s"] = round(now - t_send, 6)
                if prev_ts is not None:
                    rec["tpot_s"].append(round(now - prev_ts, 6))
                prev_ts = now
                tokens += 1

            async for raw in resp.aiter_lines():
                line = raw.strip()
                if not line.startswith(_SSE_DATA):
                    continue
                payload = line[len(_SSE_DATA):].strip()
                if payload == _SSE_DONE:
                    break
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    rec["verdict"] = "malformed"
                    rec["error"] = "invalid SSE JSON payload"
                    rec["e2el_s"] = round(time.monotonic() - t_send, 6)
                    return rec
                content, tool_delta, fr, usage = _extract_chunk(cfg, chunk)
                if content:
                    _arrival(time.monotonic())
                    content_parts.append(content)
                if tool_delta:
                    # 按 index 累积 tool call 片段（首片带 id/name，后续片仅 arguments 增量）
                    for tc in tool_delta:
                        idx = tc.get("index", 0)
                        frag = tool_fragments.setdefault(
                            idx, {"id": None, "name": "", "arguments": ""})
                        if tc.get("id"):
                            frag["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            frag["name"] = fn["name"]
                        if fn.get("arguments"):
                            frag["arguments"] += fn["arguments"]
                        _arrival(time.monotonic())
                if fr is not None:
                    finish = fr
                if usage:
                    rec["prompt_tokens"] = usage.get("prompt_tokens")
                    rec["completion_tokens"] = usage.get("completion_tokens")
            rec["output_tokens"] = tokens
            rec["output_text"] = "".join(content_parts)
            rec["finish_reason"] = finish
            rec["truncated"] = finish == "length"
            if rec["kind"].startswith("tool_") and tool_fragments:
                rec["output_tool_calls"] = [
                    {"id": frag["id"], "type": "function",
                     "function": {"name": frag["name"], "arguments": frag["arguments"]}}
                    for frag in tool_fragments.values()
                ]
            rec["e2el_s"] = round(time.monotonic() - t_send, 6)
    except httpx.TimeoutException:
        rec["verdict"] = "timeout"
        rec["error"] = f"timeout>{timeout_s}s"
        rec["e2el_s"] = round(time.monotonic() - t_send, 6)
        return rec
    except httpx.HTTPError as e:
        rec["verdict"] = "failed"
        rec["error"] = f"{type(e).__name__}: {e}"
        rec["e2el_s"] = round(time.monotonic() - t_send, 6)
        return rec

    # verdict 判定（附-3/8：可变输出禁止静默截断；固定输出命中 max_tokens 视为完整）
    if rec["output_tokens"] == 0:
        rec["verdict"] = "malformed"
        rec["error"] = "empty output (no token received)"
    elif rec["finish_reason"] == "length" and not _is_fixed_output(case):
        rec["verdict"] = "silent_truncation"
        rec["error"] = f"hit max_tokens without EOS (finish_reason=length, {rec['output_tokens']} tokens)"
    elif rec["finish_reason"] not in ("stop", "tool_calls", "length"):
        rec["verdict"] = "malformed"
        rec["error"] = f"stream ended without valid finish_reason (got {rec['finish_reason']!r})"
    else:
        rec["verdict"] = "ok"
    return rec


def _history_key(case):
    return (case.get("tenant") or "", case.get("session_id"))


def _update_history(sessions, case, rec):
    """多轮会话历史累积：成功后追加 user + assistant（含 tool_calls）两段。"""
    key = _history_key(case)
    if key[1] is None:
        return
    hist = sessions.setdefault(key, [])
    hist.append({"role": "user", "content": case["content"]})
    if rec["verdict"] == "ok":
        assistant = {"role": "assistant", "content": rec["output_text"]}
        if rec["output_tool_calls"]:
            assistant["tool_calls"] = rec["output_tool_calls"]
        hist.append(assistant)


async def run_scan(cfg, cases, arrival, base_url, client=None, out=None, timeout_s=None):
    """执行 poisson 容量扫描：按到达序列逐请求发送（附-8 到达背压）。

    ARGS:
        cfg / cases / arrival   来自 acceptance.expand / cases.generate_cases / arrival.capacity_scan
        base_url                server 根（不含 /v1）
        client                  httpx.AsyncClient（None 时自建；测试可注入 MockTransport）
        out                     逐请求回执的写对象（file-like，写入 JSON Lines）；None=不落盘
        timeout_s               per-request 超时（缺省用 workload.timeouts.per_request_s）
    RETURN:
        list[dict] 逐请求回执（每条附带 rate/cap/nominal_ts_s/sendlag_s）
    """
    wl = cfg["workload"]
    max_conc = int(wl.get("max_concurrency") or 16)
    if timeout_s is None:
        timeout_s = float(wl.get("timeouts", {}).get("per_request_s") or 600)
    sampling = cfg.get("sampling", {})
    sem = asyncio.Semaphore(max_conc)
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout_s, trust_env=False)
    start = time.monotonic()
    records = []
    sessions = {}

    async def fire(case, ts, rate, cap):
        async with sem:                    # 饱和时本地 FIFO 等待（不丢弃/不重排）
            nominal = start + ts
            now = time.monotonic()
            if now < nominal:
                await asyncio.sleep(nominal - now)
            rec = await execute_one(cfg, case, sampling, base_url, client, timeout_s,
                                    history=sessions.get(_history_key(case), []))
            rec["rate"] = rate
            rec["cap"] = cap
            rec["nominal_ts_s"] = round(ts, 6)
            rec["sendlag_s"] = round(time.monotonic() - nominal, 6)
            _update_history(sessions, case, rec)
            records.append(rec)
            if out is not None:
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()

    tasks = []
    for seg in arrival["segments"]:
        rate, cap = seg["rate"], seg["cap"]
        for i, ts in enumerate(seg["nominal_ts_s"]):
            case = cases[i % len(cases)]
            tasks.append(asyncio.create_task(fire(case, ts, rate, cap)))
    await asyncio.gather(*tasks)

    if own_client:
        await client.aclose()
    return records
