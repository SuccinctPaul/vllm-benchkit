#!/usr/bin/env python3
"""vllm-xcheck acceptance config expander: read -> merge -> validate -> render.

把 config/vllm-xcheck/ 下的分层配置（common 基线 + cells/<cell> 覆盖 + precision/<p> 精度 overlay）
合并成单个 profile 实例，再渲染出：
  - argv      : vllm serve CLI 参数（服务端 / 模型字段的 best-effort 映射）
  - env       : 需导入的环境变量（表附-4）
  - effective : 合并后的生效配置树（含客户端采样 / 工作负载合同）

校验（fail-closed）：
  - meta.required 声明的必填字段缺一即报错；
  - 每个 section 出现 unknown 字段即报错（表外字段拒绝）。

用法：
  python src/acceptance.py --cell a2-dialogue --precision W8A8 --dry-run
  python src/acceptance.py --list                 # 枚举全部 15 个正式 profile
注意：argv 为保守映射（json 序列化 Value 只做事先约定的标志），须在目标机上用
`vllm serve --help` 逐项对账；ignore_eos/logprobs/max_tokens 属客户端请求字段，
「严禁渲染成 serve CLI 参数」（表附-5），只进 effective，不进 argv。
"""
import argparse
import json
import math
import os
import shlex
import sys
from copy import deepcopy

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "..", "config", "vllm-xcheck")


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f) or {}


SCHEMA = load_yaml(os.path.join(BASE, "schema.yaml"))


def deep_merge(base, override):
    """列表整体替换，dict 递归合并（override 优先）。"""
    merged = deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = deep_merge(merged[k], v)
        else:
            merged[k] = deepcopy(v if not isinstance(v, list) else list(v))
    return merged


def validate(schema, merged):
    """返回 (errors, warnings)。必填缺失与 unknown 字段均算 error，fail-closed。"""
    errors, warnings = [], []

    # 1) cell 声明的精度必须合法，且请求的 precision 需在 cell.precisions 内
    meta = merged.get("meta", {})
    if meta.get("precision") not in set(schema["metadata"]["precision"]["allowed"]):
        errors.append(f"meta.precision={meta.get('precision')!r} 不在 legal 精度集内")

    # 2) required 必填校验
    for section, fields in schema.get("required", {}).items():
        bucket = merged.get(section, {})
        for f in fields:
            if bucket.get(f) is None:
                errors.append(f"缺失必填字段 {section}.{f}")

    # 3) 每个 section 的 unknown 字段校验（表外字段拒绝）
    for section, allowed in schema.get("allowed", {}).items():
        if section == "meta":
            continue  # meta.precision/common/cell 由 overlay/cell 各自产生，略过
        bucket = merged.get(section, {})
        if not isinstance(bucket, dict):
            continue
        allowed_set = set(allowed)
        for k in bucket:
            if k not in allowed_set:
                errors.append(f"表外字段 {section}.{k}")

    # 4) precision overlay 字段走单独的允许集
    pmodel = merged.get("model", {})
    for f in ("dtype", "quantization", "served_suffix"):
        if pmodel.get(f) is None and f not in ("quantization",):  # FP16 用 null
            warnings.append(f"precision overlay 建议提供 model.{f}")

    return errors, warnings


def expand_placeholders(cfg):
    """把 {precision} 占位符按 model.served_suffix 替换到 profile / served_name。"""
    suffix = cfg.get("model", {}).get("served_suffix")
    for key, val in list(cfg["meta"].items()):
        if isinstance(val, str) and "{precision}" in val:
            cfg["meta"][key] = val.format(precision=str(suffix).upper())
    for key in ("served_name",):
        val = cfg["model"].get(key)
        if isinstance(val, str) and "{precision}" in val:
            cfg["model"][key] = val.format(precision=suffix)
    return cfg


def model_server_argv(cfg, model_ref):
    server, model = cfg.get("server", {}), cfg.get("model", {})
    # smoke 覆盖：VLLM_BENCHKIT_GPU_MEM_UTIL 临时降显存利用率（缺省用配置值，不污染 common.yaml）
    gpu_util = float(os.environ.get("VLLM_BENCHKIT_GPU_MEM_UTIL", server.get("gpu_memory_utilization")))
    # value 型 flag 只在配置给值时发射；未指定（None）则不发射该 flag（保守映射，
    # 交由 serve 默认。曾因 a2 未声明 max_model_len 而渲染出孤悬的 --max-model-len）。
    pairs = [
        ("--model", model_ref),
        ("--served-model-name", model.get("served_name")),
        ("--dtype", model.get("dtype")),
        ("--kv-cache-dtype", model.get("kv_cache_dtype")),   # requested（附-2：auto）
        ("--load-format", model.get("load_format")),
        ("--block-size", model.get("block_size")),
        ("--max-model-len", server.get("max_model_len")),
        ("--gpu-memory-utilization", gpu_util),
        ("--max-num-seqs", server.get("max_num_seqs")),
        ("--max-num-batched-tokens", server.get("max_num_batched_tokens")),
        ("--scheduling-policy", server.get("scheduling_policy")),
        ("--tensor-parallel-size", server.get("tensor_parallel_size")),
        ("--pipeline-parallel-size", server.get("pipeline_parallel_size")),
        ("--distributed-executor-backend", server.get("distributed_executor_backend")),
        ("--tokenizer-mode", server.get("tokenizer_mode")),
        ("--uvicorn-log-level", server.get("log_level")),
        ("--host", server.get("host")),
        # engine_seed 必须经 CLI 显式下发（表附-2：engine_seed=0；receipt 侧已记账），
        # 不依赖 serve 默认，保证复现锚点见表附-8。
        ("--seed", server.get("engine_seed")),
    ]
    flags = [(f, v) for f, v in pairs if v is not None]
    # 结构化输出 backend（表附-3：禁止 backend=auto）。engine 级参数，v0.18.0 serve 以
    # --structured-outputs-config <JSON> 下发；用紧凑分隔符去空格，避免 acceptance.sh
    # `nohup vllm serve $args` 未加引号拆分时把 JSON 拆成多个 token（forge in CLI）。
    struct_backend = server.get("structured_outputs_backend")
    if struct_backend:
        flags.append(("--structured-outputs-config",
                      json.dumps({"backend": struct_backend}, separators=(",", ":"))))
    if model.get("quantization"):
        flags.append(("--quantization", model.get("quantization")))

    # 布尔开关：true → --enable-*；false → --no-enable-*（真机 --help=all 对账：本 build
    # 全部为 BooleanOptionalAction 形态，关闭用 --no- 前缀，无 --disable- 变体）。
    # A1 闭环算力依赖 prefix-caching / chunked-prefill 显式关闭，否则 prefill 被缓存跳过、
    # 墙钟时间虚低 → effective FLOPs/s 高估、MFU>100%。
    booleans = [
        ("--enable-prefix-caching", "--no-enable-prefix-caching", "enable_prefix_caching"),
        ("--enable-chunked-prefill", "--no-enable-chunked-prefill", "enable_chunked_prefill"),
        ("--enforce-eager", "--no-enforce-eager", "enforce_eager"),
        ("--trust-remote-code", "--no-trust-remote-code", "trust_remote_code"),
    ]
    for enable_flag, disable_flag, key in booleans:
        val = server.get(key)
        if val is True:
            flags.append((enable_flag, None))
        elif val is False:
            flags.append((disable_flag, None))
    # 图执行：compile_mode 不为 none 时给出 compile 相关保守标志（目标机需 --help 对账）。
    # smoke 豁免（VLLM_BENCHKIT_SKIP_GRAPH_ARGS=1）：真机 ascend build 的 serve 不接受
    # --compile-mode/--cudagraph-mode（--help=all 无此 flag），smoke 时跳过仅验证连通。
    skip_graph = os.environ.get("VLLM_BENCHKIT_SKIP_GRAPH_ARGS") == "1"
    if server.get("compile_mode") and server.get("compile_mode") not in ("none", None) and not skip_graph:
        flags.append(("--compile-mode", server.get("compile_mode")))
        flags.append(("--cudagraph-mode", server.get("cudagraph_mode")))
        if server.get("capture_sizes"):
            flags.append(("--cudagraph-capture-sizes", " ".join(map(str, server["capture_sizes"]))))
    # 前端/工具/结构化（附-3/附-8；目标机需 --help 对账）。structured flag 亦随 smoke 豁免
    if server.get("tool_call_parser"):
        flags.append(("--tool-call-parser", server.get("tool_call_parser")))
    if server.get("auto_tool_choice") is True:
        flags.append(("--enable-auto-tool-choice", None))   # vLLM v0.18；tool 类 cell 显式启用
    # 注：structured_outputs_backend 是 engine 级参数，v0.18.0 serve CLI 未暴露该 flag
    # （--help 对账确认），故不渲染进 argv；该值保留在 effective 配置身份字段（附-8 配置身份）。
    return flags


def render(argv_list):
    # 布尔开关以 (flag, None) 出现 → 无值仅渲染 flag 本身；不得被过滤（曾把 --enforce-eager 等丢弃）
    return " \\\n  ".join(
        (f"{f} {v}" if v is not None else f)
        for f, v in argv_list
    )


def expand(cell_name, pname, model_ref=""):
    """合并 common + cells/<cell> + precision/<p>，展开占位并校验（fail-closed）。

    ARGS:
        cell_name    cells/<cell>.yaml 的名字（不带扩展名）
        pname        精度名：FP16 | W8A8（大小写不敏感，内部统一大写）
    RETURN:
        merged       校验通过的有效配置树；任一 ERROR 则打印到 stderr 并返回 None
    """
    pname = pname.upper()
    common = load_yaml(os.path.join(BASE, "common.yaml"))
    cell = load_yaml(os.path.join(BASE, "cells", f"{cell_name}.yaml"))
    prec = load_yaml(os.path.join(BASE, "precision", f"{pname.lower()}.yaml"))

    if pname not in cell["meta"].get("precisions", []):
        print(f"[acceptance] 错误: cell '{cell_name}' 不声明精度 {pname}（允许 {cell['meta']['precisions']}）",
              file=sys.stderr)
        return None

    merged = deep_merge(common, cell)
    merged = deep_merge(merged, prec)   # precision overlay 最后叠（只改 dtype/quant/served_suffix/precision 名）
    expand_placeholders(merged)
    if os.environ.get("VLLM_BENCHKIT_SHORT") == "1":
        _apply_smoke(merged)            # smoke 覆盖：把 workload 时长/规模缩到分钟级

    errors, warnings = validate(SCHEMA, merged)
    for w in warnings:
        print(f"[acceptance] 警告: {w}", file=sys.stderr)
    for e in errors:
        print(f"[acceptance] ERROR: {e}", file=sys.stderr)
    if errors:
        return None
    return merged


def _apply_smoke(merged):
    """VLLM_BENCHKIT_SHORT=1 真机 smoke：把 workload 时长/规模缩到分钟级。

    与 VLLM_BENCHKIT_GPU_MEM_UTIL 同一设计（smoke 临时覆盖、不污染 cell 配置）。
    仅影响 run.py 客户端 workload；server argv 不受影响。
    A1 的 timeouts.warmup_s/measure_s 语义为轮数（见 cells/a1.yaml 注释），故 A1 收 1/2 轮。
    """
    wl = merged.get("workload") or {}
    to = wl.get("timeouts")
    if isinstance(to, dict):
        for k, v in {
            "warmup_s": 1, "capacity_s": 30, "common_load_s": 20, "per_rate_s": 15,
            "drain_cooldown_s": 3, "measure_s": 20, "per_request_s": 120,
            "lifecycle_s": 300, "per_point_s": 15, "point_gap_s": 3, "formal_s": 20,
        }.items():
            if k in to:
                to[k] = v
    if wl.get("window_count") is not None:       # A3 窗口：2×10s，极少请求
        wl["window_count"] = 2
        wl["window_s"] = 10
        wl["max_requests"] = min(wl.get("max_requests", 64), 6)
        wl["min_completed_requests"] = 1
    if isinstance(to, dict) and merged.get("meta", {}).get("cell") == "a1":
        to["warmup_s"] = 1                        # A1 语义：预热轮数
        to["measure_s"] = 2                       # A1 语义：计量轮数
    if wl.get("b0_capacity_points"):             # A4 扫描点收缩
        # 通用默认（每点 15s）对 A4 样本过少：低负载点 request_rate = token_rate×share/output_cap
        # 仅 0.03~0.4 req/s，15s 内每租户 1 个请求 → 公平性统计无意义。A4 专用：
        #   扫描点取高负载点（样本随 token_rate 线性增长）+ per_point_s=60s（每租户 8~15 请求）
        #   + formal_s=120s（formal_rate=0.7×b0_max → dialogue ~31 请求），总时长 ~5min。
        if merged.get("meta", {}).get("cell") == "a4-mt" and isinstance(to, dict):
            # cost_len 校准后低负载点样本已充足（64 tok/s → dialogue 1.2 / tool 3.2 /
            # reasoning 8 req/s），不再需要只取 ≥192 高负载点；tool 租户语法解码单请求
            # 成本高，256/384 超出服务可持续容量（verify9 实测隔离失败），扫低位找真实 B0。
            points = [r for r in wl["b0_capacity_points"] if r <= 192]
            wl["b0_capacity_points"] = points[-4:] or wl["b0_capacity_points"][-3:]
            to["per_point_s"] = 60
            to["point_gap_s"] = 3
            to["formal_s"] = 120
            to["warmup_s"] = 30
            to["isolation_s"] = 60           # 隔离 B0 基线：每租户单租户独自承担全部 token_rate 60s
        else:
            wl["b0_capacity_points"] = wl["b0_capacity_points"][:3]
    if wl.get("rates"):                          # A2 容量扫描点收缩；caps 须与 per_rate_s 同步重算
        wl["rates"] = wl["rates"][:2]
        if isinstance(wl.get("caps"), list):
            per = to.get("per_rate_s", 600) if isinstance(to, dict) else 600
            wl["caps"] = [math.ceil(r * per) for r in wl["rates"]]


def main() -> int:
    ap = argparse.ArgumentParser(description="vllm-xcheck acceptance config expander")
    ap.add_argument("--cell", help="cell 名，如 a2-dialogue（也接受 cells/<cell>.yaml 路径）")
    ap.add_argument("--precision", default="FP16", help="精度 overlay：FP16 或 W8A8")
    ap.add_argument("--model-ref", default="", help="模型参考（默认 model.family；实例化方可给 revision 路径）")
    ap.add_argument("--dry-run", action="store_true", help="打印 argv/env/effective")
    ap.add_argument("--list", action="store_true", help="枚举全部正式 profile 实例")
    ap.add_argument("--argv", action="store_true", help="仅打印 vllm serve 单行命令（供 acceptance.sh 消费）")
    ap.add_argument("--env", action="store_true", help="仅打印 export 环境行（供 acceptance.sh 消费）")
    args = ap.parse_args()

    if args.list:
        import glob
        for cell_path in sorted(glob.glob(os.path.join(BASE, "cells", "*.yaml"))):
            cell = load_yaml(cell_path)
            cell_name = os.path.basename(cell_path)[:-5]
            seq = ", ".join(
                (cell["meta"]["profile"]).replace("{precision}", p) for p in cell["meta"]["precisions"]
            )
            print(f"{cell_name:<16} -> {seq}")
        return 0

    if not args.cell:
        ap.error("--cell 必填（或 --list）")

    cell_name = os.path.basename(args.cell.rstrip("/"))
    if cell_name.endswith(".yaml"):
        cell_name = cell_name[:-5]

    pname = args.precision.upper()
    merged = expand(cell_name, pname, args.model_ref)
    if merged is None:
        return 1

    argv_list = model_server_argv(merged, args.model_ref or merged["model"]["family"])
    profile_id = merged["meta"]["profile"]

    # 机器可读模式：只输出载荷（供 acceptance.sh 规则化消费），不带 info 行
    if args.argv:
        tokens = [f"{f} {v}" if v is not None else f for f, v in argv_list]
        print("vllm serve " + " ".join(tokens))
        return 0
    if args.env:
        for k, v in merged.get("env", {}).items():
            print(f"export {k}={shlex.quote(str(v))}")
        return 0

    print(f"profile        : {profile_id}")
    print(f"cell           : {merged['meta']['cell']} / precision={merged['meta'].get('precision')}")
    print(f"endpoint       : {merged['meta']['endpoint']}")
    print(f"api            : {merged['meta'].get('api')}")

    if args.dry_run:
        print("\n--- argv (vllm serve, best-effort) ---")
        print("vllm serve", render(argv_list))
        print("\n--- env (表附-4) ---")
        for k, v in merged.get("env", {}).items():
            print(f"export {k}={v!r}")
        print("\n--- effective (merged) ---")
        print(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), end="")

    return 0


if __name__ == "__main__":
    sys.exit(main())