#!/usr/bin/env python3
"""vllm-benchkit topology loader: config/topology.yaml -> shell export lines (ADR-0006).

用法: gettopo.py <config/topology.yaml>   # 输出 `export TOPO_*` 行，由 deploy.sh eval 载入。
仓库名可能含连字符（如 vllm-benchkit）当不成 shell 变量名，故按索引展开为
`TOPO_REPO_<i>_<FIELD>`（FIELD ∈ NAME/URL/BRANCH/COMMIT/MANAGE）。布尔 manage 输出为
JSON 小写 true/false。环境变量优先于 YAML（ADR-0005）由 deploy.sh 侧实现。
"""
import os
import shlex
import sys

import yaml


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "config/topology.yaml"
    if not os.path.exists(path):
        print(f"[gettopo] 找不到配置文件: {path}", file=sys.stderr)
        return 1
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}

    print(f"export TOPO_SERVER={shlex.quote(str(cfg.get('server', '') or ''))}")
    print(f"export TOPO_DIR={shlex.quote(str(cfg.get('dir', '') or ''))}")

    repos: dict = cfg.get("repos") or {}
    print(f"export TOPO_REPO_COUNT={len(repos)}")
    for i, (name, meta) in enumerate(repos.items()):
        meta = meta or {}
        print(f"export TOPO_REPO_{i}_NAME={shlex.quote(str(name))}")
        for field in ("url", "branch", "commit"):
            print(f"export TOPO_REPO_{i}_{field.upper()}={shlex.quote(str(meta.get(field, '') or ''))}")
        manage = bool(meta.get("manage", True))  # 缺省 manage: true（ADR-0006）
        print(f"export TOPO_REPO_{i}_MANAGE={'true' if manage else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())