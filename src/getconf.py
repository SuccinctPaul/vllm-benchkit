#!/usr/bin/env python3
"""vllm-benchkit config loader: YAML -> shell export lines.

用法: getconf.py <config.yaml>
把 YAML 拍平为 `export YAML_<KEY>=<value>` 行（顶层键 -> YAML_MODEL 等，
分组键 -> YAML_BENCH_NUM_PROMPTS 等），由 bench.sh/profile.sh eval 载入，
再以 : "${KEY:=${YAML_...:-}}" 实现「环境变量 > YAML 默认」（docs/adr/0005）。
布尔值输出为 JSON 风格小写 true/false；其余值经 shlex 引用。
"""
import os
import shlex
import sys

import yaml


def flatten(prefix: str, node: dict, out: dict) -> None:
    for key, val in node.items():
        name = f"{prefix}_{key}" if prefix else str(key)
        if isinstance(val, dict):
            flatten(name, val, out)
        else:
            out[name.upper()] = val


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    if not os.path.exists(path):
        print(f"[getconf] 找不到配置文件: {path}", file=sys.stderr)
        return 1
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    flat: dict = {}
    flatten("", cfg, flat)
    for key, val in flat.items():
        if val is None:
            continue
        if isinstance(val, bool):
            text = "true" if val else "false"
        else:
            text = str(val)
        print(f"export YAML_{key}={shlex.quote(text)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
