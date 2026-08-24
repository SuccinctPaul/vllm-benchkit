#!/usr/bin/env python3
"""receipt: 验收 run 的生效值快照 + fail-closed 门禁（P3）。

对给定 cell+precision 产出两张输出：
  1) 生效值快照（receipt）：profile/cell/precision/api/endpoint + effective 全树
     + argv（渲染后 serve 命令）+ env + workload 契约。供真机 run 审计与复现。
  2) fail-closed 门禁（gate）：任一不变式不满足即退出非 0，避免在隐患上烧 NPU。

门禁项（全部 fail-closed；唯一豁免项是数据工件缺失，见 --allow-missing-datasets）：
  config_valid        复用 acceptance.validate：必填缺失 / 表外字段 → 拒绝
  precision_overlay   FP16→无量化 & served_suffix=fp16；W8A8→quantization=ascend & 名字带 w8a8
  a1_only_fp16        A1 强制 FP16
  argv_hygiene        客户端请求字段(ignore_eos/logprobs/max_tokens/streaming/repeats/client_seed)
                      严禁渲染进 serve argv（表附-5）
  workload_contract   workload 具备 mode/primary_metric/timeouts；dataset 已知
  dataset_ready       workload.dataset 需要的固定子集工件在 datasets/ 就绪（prepare subsets 产出）；
                      synthetic/mixed 为组合形态无单一工件，仅提示

用法：
  python src/receipt.py --cell a2-dialogue --precision W8A8 --dry-run
  python src/receipt.py --cell a1 --precision FP16 --datasets-dir datasets --out <path>/receipt.json
  python src/receipt.py --cell a2-tool --precision FP16 --allow-missing-datasets   # smoke 豁免
"""
import argparse
import json
import os
import sys

import acceptance  # 同目录模块：expand / model_server_argv / render


# cell workload.dataset -> prepare subsets registry 键；None 表示无单一工件（组合形态）
DATASET_ARTIFACT = {
    "ShareGPT": "sharegpt",
    "GSM8K": "gsm8k",
    "BFCL-V4": "bfcl",
    "JSONSchemaBench": "jsonschema",
    "long": "long",
}
COMPOSED_DATASETS = {"synthetic", "mixed"}

# 客户端请求字段，严禁渲染成 serve argv（表附-5）。只看 flag 名。
CLIENT_REQUEST_FLAGS = {
    "--ignore-eos", "--logprobs", "--max-tokens", "--streaming",
    "--repeats", "--client-seed",
}


def load_registry(datasets_dir):
    path = os.path.join(datasets_dir, ".registry.json")
    if os.path.exists(path):
        with open(path) as f:
            try:
                return json.load(f)
            except ValueError:
                return {}
    return {}


def gate_dataset_ready(merged, datasets_dir, allow_missing):
    ds = merged.get("workload", {}).get("dataset", "")
    if ds in COMPOSED_DATASETS:
        return True, f"composed(no single artifact): {ds}"
    key = DATASET_ARTIFACT.get(ds)
    if key is None:
        return False, f"unknown workload.dataset={ds!r}"
    entry = (load_registry(datasets_dir).get("datasets") or {}).get(key) or {}
    if entry.get("materialized") is True:
        return True, f"ready: {key}"
    if entry.get("status") == "placeholder":
        if allow_missing:
            return True, f"placeholder(declared, url empty), smoke-ok: {key}"
        return False, (f"placeholder({key})：已声明但未 materialize；"
                       f"需在 config.yaml prepare.subsets 填真实 url 或 --allow-missing-datasets")
    if allow_missing:
        return True, f"missing-but-smoke-ok: {key}"
    return False, (f"missing artifact datasets/{key}.jsonl + manifest；"
                   f"先 bash scripts/prepare.sh subsets（或 --allow-missing-datasets）")


def build_receipt(merged):
    argv_list = acceptance.model_server_argv(merged, merged["model"]["family"])
    flags = {f for f, _ in argv_list if f is not None}
    return dict(
        profile=merged["meta"]["profile"],
        cell=merged["meta"]["cell"],
        precision=merged["meta"].get("precision"),
        api=merged["meta"].get("api"),
        endpoint=merged["meta"]["endpoint"],
        workload=dict(
            mode=merged.get("workload", {}).get("mode"),
            dataset=merged.get("workload", {}).get("dataset"),
            primary_metric=merged.get("workload", {}).get("primary_metric"),
        ),
        argv="vllm serve " + acceptance.render(argv_list),
        argv_flags=sorted(flags),
        env=merged.get("env", {}),
        effective=merged,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="vllm-xcheck acceptance receipt + fail-closed gate")
    ap.add_argument("--cell", required=True)
    ap.add_argument("--precision", default="FP16")
    ap.add_argument("--datasets-dir", default="datasets", help="prepare subsets registry 目录")
    ap.add_argument("--allow-missing-datasets", action="store_true",
                    help="豁免 dataset_ready 门禁（smoke 用）；其余门禁仍 fail-closed")
    ap.add_argument("--out", default="", help="写 receipt 到文件；空=仅打印")
    ap.add_argument("--dry-run", action="store_true", help="打印 receipt 不校验数据集工件")
    args = ap.parse_args()

    merged = acceptance.expand(args.cell, args.precision)
    if merged is None:
        return 1

    gates = []  # (name, ok, detail)

    def add(name, ok, detail=""):
        gates.append({"name": name, "ok": bool(ok), "detail": detail})

    # config_valid：expand 已 fail-closed（有 error 会返回 None），此处恒真，仍记账
    add("config_valid", True, "schema required/allowed 通过")

    # precision_overlay
    q = (merged.get("model") or {}).get("quantization")
    suffix = (merged.get("model") or {}).get("served_suffix")
    served_name = (merged.get("model") or {}).get("served_name", "")
    pname = args.precision.upper()
    if pname == "FP16":
        add("precision_overlay", q is None and suffix == "fp16" and "fp16" in served_name,
            f"quantization={q!r}, served_suffix={suffix!r}, served_name={served_name!r}")
    else:
        add("precision_overlay", q == "ascend" and suffix == "w8a8" and "w8a8" in served_name,
            f"quantization={q!r}, served_suffix={suffix!r}, served_name={served_name!r}")

    # a1_only_fp16
    if merged["meta"]["cell"] == "a1":
        add("a1_only_fp16", pname == "FP16",
            f"cell=a1 仅允许 FP16（请求 {pname}）")
    else:
        add("a1_only_fp16", True, "非 A1，不适用")

    # argv_hygiene：客户端请求字段不得泄入 serve argv
    argv_list = acceptance.model_server_argv(merged, merged["model"]["family"])
    leaked = sorted(CLIENT_REQUEST_FLAGS & {f for f, _ in argv_list if f is not None})
    add("argv_hygiene", not leaked,
        "无泄入" if not leaked else f"客户端字段泄入 argv: {leaked}")

    # frozen_server：附-2 共同服务端固定值（配置级自检，fail-closed）
    sv = merged.get("server", {})
    mo = merged.get("model", {})
    frozen_ok = all([
        mo.get("block_size") == 128,
        mo.get("kv_cache_dtype") == "auto",       # requested
        mo.get("kv_cache_effective") == "float16",  # effective
        sv.get("serving_mode") == "unified",
        sv.get("tool_call_parser") == "hermes",
        sv.get("structured_outputs_backend") == "xgrammar",  # 禁止 backend=auto
        sv.get("speculative") is False,
        sv.get("scheduling_policy") == "fcfs",
    ])
    add("frozen_server", frozen_ok,
        f"block_size={mo.get('block_size')}, kv_cache_dtype={mo.get('kv_cache_dtype')}, "
        f"kv_cache_effective={mo.get('kv_cache_effective')}, serving_mode={sv.get('serving_mode')}, "
        f"tool_call_parser={sv.get('tool_call_parser')}, struct_backend={sv.get('structured_outputs_backend')}, "
        f"speculative={sv.get('speculative')}, policy={sv.get('scheduling_policy')}")

    # workload_contract
    wl = merged.get("workload", {})
    ds = wl.get("dataset", "")
    ds_known = ds in DATASET_ARTIFACT or ds in COMPOSED_DATASETS
    wl_ok = all(wl.get(k) for k in ("mode", "primary_metric")) and wl.get("timeouts")
    add("workload_contract", wl_ok and ds_known,
        f"mode={wl.get('mode')!r}, dataset={ds!r}(known={ds_known})")

    # dataset_ready（唯一可豁免项）
    if args.dry_run:
        add("dataset_ready", True, "dry-run 跳过工件校验")
    else:
        ok, detail = gate_dataset_ready(merged, args.datasets_dir, args.allow_missing_datasets)
        add("dataset_ready", ok, detail)

    gate_ok = all(g["ok"] for g in gates)

    receipt = build_receipt(merged)
    receipt["gates"] = gates
    receipt["gate"] = "PASS" if gate_ok else "FAIL"

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(receipt, f, ensure_ascii=False, indent=2, sort_keys=False)
        print(f"[receipt] 已写出 {args.out}")

    print(f"gate={receipt['gate']}  profile={receipt['profile']} ({receipt['cell']}/{receipt['precision']})"
          f"  api={receipt['api']}  endpoint={receipt['endpoint']}")
    for g in gates:
        print(f"   [{('ok ' if g['ok'] else 'FAIL')}] {g['name']:<20} {g['detail']}")

    return 0 if gate_ok else 1


if __name__ == "__main__":
    sys.exit(main())