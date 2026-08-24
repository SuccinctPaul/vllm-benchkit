"""组 M —— 指标采集与优化机制生效（附-8 优化机制生效、A3 专项）。

M1 图执行计数器：A2—A4 记录 graph_capture_count / graph_hit_count / graph_fallback_count /
    capture_sizes / compile_time；A1 记录实际图捕获次数（必须为 0）。
M2 前缀缓存与量化生效：dialogue/tool/A4 记录 prefix_cache_queries/hits；W8A8 记录量化生效值。
M3 A3 资源监控：监控间隔=1s；记录 HBM 已用/峰值、KV cache 用量、队列深度、请求 token 数、
    窗口 TTFT/TPOT 与吞吐（随 C6 采集，落证据包）。
M4 运维开销字段：CPU 核表（按所分配 NPU 的 NUMA 节点）、OMP_NUM_THREADS=1、物理 NPU 卡号、
    驱动/固件/torch_npu/容器 digest。

本模块提供三类能力：
  1. `parse_graph_counters` / `parse_prefix_cache` / `parse_quantization_effective`：
     从 vllm serve 日志文本（或结构化指标 JSON）解析机制生效值；
  2. `evaluate_mechanisms(cfg, metrics)`：按 cell 声明的要求做 fail-closed 门禁判定；
  3. `parse_resource_monitor(lines)`：解析 1s 间隔资源监控采样（A3 证据）。

真机数据源（ascend 侧日志 / /metrics 接口 / msprof）在上真机阶段接入；
本模块先固化「解析 + 门禁」逻辑，离线用合成日志自测。
"""
import json
import re


# --- M1 图执行计数器 -----------------------------------------------------------

def parse_graph_counters(log_text, compile_mode=None):
    """从 vllm serve 日志解析图执行计数器。

    RETURN:
        dict {
            graph_capture_count, graph_hit_count, graph_fallback_count,
            capture_sizes, compile_time_s, found: bool（是否从日志采到）
        }
        未采到（eager / 日志无图记录）时 found=False，计数为 None。
    """
    out = {"graph_capture_count": None, "graph_hit_count": None,
           "graph_fallback_count": None, "capture_sizes": None,
           "compile_time_s": None, "found": False}
    capture = re.findall(r"Capturing\s+graph\s+for\s+(\d+)\s+batch", log_text)
    if not capture:
        capture = re.findall(r"graph\s+shape\s+(\d+)", log_text)
    if capture:
        out["graph_capture_count"] = len(capture)
        out["capture_sizes"] = [int(c) for c in capture]
        out["found"] = True
    # ascend/v1 进度行：Capturing CUDA graphs ... | 7/7 [..] → 分母=待捕获尺寸总数
    # （仅在实际执行图捕获时出现；eager 无此行 → 不计为捕获，留给调用方按 0 记账）
    m_prog = re.search(r"Capturing\s+CUDA\s+graphs[^\n]*\|\s*\d+/(\d+)", log_text)
    if m_prog:
        out["graph_capture_count"] = int(m_prog.group(1))
        out["found"] = True
    # compile_time: "Graph capturing finished in 3.14s" / "Compilation took 2.5s"
    m = re.search(r"(?:Graph capturing|Compilation|graph capture)\s+(?:finished in|took)\s+([\d.]+)s",
                  log_text)
    if m:
        out["compile_time_s"] = float(m.group(1))
        out["found"] = True
    # hit/fallback：vllm ascend 日志若有 "graph hit"/"fallback" 计数
    hit = re.search(r"graph\s+hit[s]?[:=]\s*(\d+)", log_text, re.I)
    if hit:
        out["graph_hit_count"] = int(hit.group(1))
    fall = re.search(r"graph\s+fallback[s]?[:=]\s*(\d+)", log_text, re.I)
    if fall:
        out["graph_fallback_count"] = int(fall.group(1))
    return out


# --- M2 前缀缓存 / 量化生效 -----------------------------------------------------

def parse_prefix_cache(log_text=None, metrics_json=None):
    """从日志或 /metrics JSON 解析 prefix cache 查询/命中。未采到返回 None。"""
    if metrics_json is not None:
        for key in ("prefix_cache_queries", "prefix_cache_hits"):
            pass  # 具体键名以真机 /metrics 为准，此处保留扩展点
    out = {"queries": None, "hits": None, "found": False}
    q = re.search(r"prefix\s+cache\s+queries?[:=]\s*(\d+)", log_text or "", re.I)
    h = re.search(r"prefix\s+cache\s+hits?[:=]\s*(\d+)", log_text or "", re.I)
    if q:
        out["queries"] = int(q.group(1))
        out["found"] = True
    if h:
        out["hits"] = int(h.group(1))
        out["found"] = True
    return out


def parse_quantization_effective(log_text):
    """从启动日志解析量化生效值（W8A8：quantization=ascend / weight dtype int8 等）。"""
    m = re.search(r"quantization\s*[:=]\s*['\"]?(\w+)", log_text, re.I)
    if not m:
        m = re.search(r"Quant\s*Algorithm\s*[:=]\s*['\"]?(\w+)", log_text, re.I)
    return m.group(1).lower() if m else None


# --- M3 A3 资源监控 ------------------------------------------------------------

def parse_resource_monitor(lines, interval_s=1.0):
    """解析 1s 间隔资源监控采样行。

    采样行格式（JSON Lines，真机采集脚本产出）：
      {"ts_s": 0.0, "hbm_used_gib": 60.1, "hbm_peak_gib": 62.0,
       "kv_cache_gib": 20.0, "queue_depth": 3, "req_tokens": 4096}
    RETURN:
        dict {samples: [...], hbm_peak_gib, interval_s, count}
    """
    samples = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            s = json.loads(line)
        except ValueError:
            continue
        if isinstance(s, dict) and ("hbm_used_gib" in s or "kv_cache_gib" in s):
            samples.append(s)
    peak = max((s.get("hbm_used_gib") or 0 for s in samples), default=0.0)
    return {"samples": samples, "hbm_peak_gib": peak,
            "interval_s": interval_s, "count": len(samples)}


# --- M4 CPU 核表 / 运维字段 ----------------------------------------------------

def parse_cpu_core_table(lines):
    """从 `taskset -pc $$` / /proc/self/status Cpus_allowed_list 解析核表。"""
    out = {"allowed": None, "affinity_line": None, "found": False}
    for line in lines:
        m = re.search(r"Cpus_allowed_list:\s*([0-9,\-]+)", line)
        if m:
            out["allowed"] = m.group(1)
            out["found"] = True
        m2 = re.search(r"taskset -pc .*?([0-9,\-]+)", line)
        if m2:
            out["affinity_line"] = m2.group(1)
            out["found"] = True
    return out


# --- 门禁判定 -------------------------------------------------------------------

def evaluate_mechanisms(cfg, metrics):
    """按 cell 声明做优化机制生效门禁（fail-closed，组 M 验收）。

    ARGS:
        cfg      展开配置（server.compile_mode / enable_prefix_caching / model.quantization /
                 workload.mode / meta.cell）
        metrics  聚合指标 dict（M1/M2/M3/M4 解析结果）
    RETURN:
        dict {ok, mechanisms: [{name, required, ok, detail}], reasons}
    """
    server = cfg.get("server", {})
    model = cfg.get("model", {})
    cell = cfg.get("meta", {}).get("cell")
    wl = cfg.get("workload", {})
    checks, reasons = [], []

    # M1 图执行：compile_mode != none → 必须采到图捕获（A2—A4）；A1（eager）→ 捕获必须为 0
    compile_mode = server.get("compile_mode")
    graph = metrics.get("graph") or {}
    if compile_mode and compile_mode not in ("none", None):
        ok = graph.get("found") and (graph.get("graph_capture_count") or 0) > 0 \
            and not graph.get("graph_fallback_count")
        checks.append({"name": "M1-graph-captured", "required": True, "ok": bool(ok),
                       "detail": f"compile_mode={compile_mode} counters={graph}"})
        if not ok:
            reasons.append(f"M1: 图执行未生效/有回退（compile_mode={compile_mode}）")
    else:
        captures = graph.get("graph_capture_count")
        ok = captures == 0
        checks.append({"name": "M1-a1-no-capture", "required": True, "ok": bool(ok),
                       "detail": f"A1 eager 应 0 图捕获，实际 {captures}"})
        if not ok:
            reasons.append(f"M1: A1 应 eager 0 捕获，实际 {captures}")

    # M2 前缀缓存：enable_prefix_caching → 需采到 queries/hits
    if server.get("enable_prefix_caching"):
        pc = metrics.get("prefix_cache") or {}
        ok = pc.get("found") and pc.get("queries") is not None
        checks.append({"name": "M2-prefix-cache", "required": True, "ok": bool(ok),
                       "detail": f"prefix_cache={pc}"})
        if not ok:
            reasons.append("M2: 启用 prefix caching 但未采到 queries/hits 计数器")

    # M2 量化生效：W8A8（quantization=ascend）→ 启动日志须确认生效
    if model.get("quantization"):
        qeff = metrics.get("quantization_effective")
        ok = qeff is not None
        checks.append({"name": "M2-quant-effective", "required": True, "ok": bool(ok),
                       "detail": f"quantization={model.get('quantization')} effective={qeff}"})
        if not ok:
            reasons.append("M2: W8A8 未确认量化生效（启动日志无量化记录）")

    # M3 A3 资源监控：closed-loop 窗口模式 → 必须有 1s 间隔采样
    if wl.get("mode") == "closed-loop" and cell == "a3-32k":
        rs = metrics.get("resource_monitor") or {}
        ok = rs.get("count", 0) > 0 and rs.get("hbm_peak_gib") is not None
        checks.append({"name": "M3-resource-monitor", "required": True, "ok": bool(ok),
                       "detail": f"samples={rs.get('count')} hbm_peak={rs.get('hbm_peak_gib')}"})
        if not ok:
            reasons.append("M3: A3 缺少 1s 间隔资源监控采样")

    # M4 运维字段：startup receipt 应含核表
    m4 = metrics.get("cpu_core_table") or {}
    ok = m4.get("found")
    checks.append({"name": "M4-cpu-core-table", "required": True, "ok": bool(ok),
                   "detail": f"allowed={m4.get('allowed')}"})
    if not ok:
        reasons.append("M4: startup receipt 缺 CPU 核表")

    return {"ok": not reasons, "mechanisms": checks, "reasons": reasons}
