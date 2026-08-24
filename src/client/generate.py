#!/usr/bin/env python3
"""vllm-xcheck 客户端合同生成 CLI（C1 + C2）。

用法：
  python src/client/generate.py --cell a2-dialogue --precision FP16 --datasets-dir datasets [--repeat 0]
  python src/client/generate.py --cell a2-dialogue --precision FP16 --allow-missing --out runs/accepted/contract
  python src/client/generate.py --cell a2-dialogue --selftest --allow-missing

输出（--out <dir>）：
  contract.json  客户端合同全文：cases + arrival 段 + config_hash / cases_sha256 /
                 arrival_stream_sha256 / generator_hash（附-8 数据身份）
  cases.json     仅 case 清单（C1）
  arrival.json   仅到达序列（C2；closed-loop/mixed 无）

验收（自测 --selftest）：
  - 同一 cell 两次生成字节一致（request_id 与顺序确定性）；
  - B0/B1 复用同一合同：FP16 与 W8A8 生成字节一致（合同与精度无关）。
"""
import argparse
import json
import os
import sys

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from client import arrival, cases, common  # noqa: E402
import acceptance  # noqa: E402


def _build(cell, precision, datasets_dir, repeat, allow_missing, model_ref=""):
    cfg = acceptance.expand(cell, precision, model_ref)
    if cfg is None:
        sys.exit(1)
    cases_doc = cases.generate_cases(cfg, datasets_dir, repeat=repeat, allow_missing=allow_missing)
    arr = arrival.capacity_scan(cfg) if cfg["workload"].get("mode") == "poisson" else None
    return cfg, cases_doc, arr


def _dump(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=False)
    print(f"[client] wrote {path} ({os.path.getsize(path)} bytes)")


def main() -> int:
    ap = argparse.ArgumentParser(description="vllm-xcheck 客户端合同生成（C1+C2）")
    ap.add_argument("--cell", required=True)
    ap.add_argument("--precision", default="FP16")
    ap.add_argument("--datasets-dir", default=os.path.join(_SRC, "..", "datasets"))
    ap.add_argument("--repeat", type=int, default=0)
    ap.add_argument("--model-ref", default="")
    ap.add_argument("--allow-missing", action="store_true",
                    help="缺固定子集时用确定性合成回退（仅 smoke；正式测量必须真实工件）")
    ap.add_argument("--out", default="", help="输出目录；缺省打印到 stdout")
    ap.add_argument("--selftest", action="store_true",
                    help="自测：两次生成字节一致 + FP16/W8A8（B0/B1 复用）合同一致")
    args = ap.parse_args()

    if args.selftest:
        d1 = _build(args.cell, args.precision, args.datasets_dir, args.repeat,
                    args.allow_missing, args.model_ref)
        d2 = _build(args.cell, args.precision, args.datasets_dir, args.repeat,
                    args.allow_missing, args.model_ref)
        if common.canonical_json(d1) != common.canonical_json(d2):
            print("[client] SELFTEST FAIL: 同一 cell 两次生成不一致", file=sys.stderr)
            return 1
        # B0/B1 复用同一合同：合同与精度无关（A1 仅 FP16，跳过精度对比）
        prec_ok = True
        if args.precision.upper() == "FP16" and args.cell != "a1":
            d3 = _build(args.cell, "W8A8", args.datasets_dir, args.repeat,
                        args.allow_missing, args.model_ref)
            # W8A8 的 model 层不同，仅对比「客户端合同」部分（cases+arrival+hash）
            def sub(d):
                return {"cases": d[1], "arrival": d[2]}

            prec_ok = common.canonical_json(sub(d1)) == common.canonical_json(sub(d3))
            if not prec_ok:
                print("[client] SELFTEST FAIL: FP16 与 W8A8 客户端合同不一致（B0/B1 应复用）",
                      file=sys.stderr)
                return 1
        # 到达序列确定性
        cfg = d1[0]
        if cfg["workload"].get("mode") == "poisson":
            a1 = arrival.capacity_scan(cfg)
            a2 = arrival.capacity_scan(cfg)
            if common.canonical_json(a1) != common.canonical_json(a2):
                print("[client] SELFTEST FAIL: 到达序列两次生成不一致", file=sys.stderr)
                return 1
        print(f"[client] SELFTEST PASS: {args.cell}/{args.precision} "
              f"(repeat 字节一致={common.canonical_json(d1)==common.canonical_json(d2)}"
              f", B0/B1 合同一致={prec_ok})")
        return 0

    try:
        cfg, cases_doc, arr = _build(args.cell, args.precision, args.datasets_dir,
                                     args.repeat, args.allow_missing, args.model_ref)
    except FileNotFoundError as e:
        print(f"[client] {e}", file=sys.stderr)
        return 1
    contract = {
        "schema": "vllm-xcheck-client-contract-v1",
        "config_hash": cases_doc["config_hash"],
        "cell_id": cases_doc["cell_id"],
        "precision": args.precision.upper(),      # run 元数据；cases/arrival 与精度无关
        "repeat": cases_doc["repeat"],
        "api": cases_doc["api"],
        "endpoint": cases_doc["endpoint"],
        "mode": cases_doc["mode"],
        "cases": cases_doc["cases"],
        "cases_sha256": cases_doc["cases_sha256"],
        "arrival": arr,
    }

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        _dump(os.path.join(args.out, "contract.json"), contract)
        _dump(os.path.join(args.out, "cases.json"), cases_doc)
        if arr is not None:
            _dump(os.path.join(args.out, "arrival.json"), arr)
        print(f"[client] config_hash={cases_doc['config_hash']}")
        print(f"[client] cases={len(cases_doc['cases'])} "
              f"cases_sha256={cases_doc['cases_sha256']}")
        if arr is not None:
            print(f"[client] arrival_stream_sha256={arr['arrival_stream_sha256']}")
            print(f"[client] generator_hash={arr['generator_hash']}")
    else:
        print(json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
