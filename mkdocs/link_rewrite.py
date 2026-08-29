"""link_rewrite —— MkDocs 微插件：把「走出 docs/」的相对链接改写为 GitHub blob 链接。

背景：
  站点只发布 docs/ 子树，而 docs 内很多链接指向仓库根 / src / config（MkDocs 无法渲染成页面）。
  为满足「本地与 blog 地址兼容」：
    - 本地（未配置 repo 信息) → 保持原相对链接（在 git 仓库里点击可用）。
    - blog / CI（拿到 repo 信息）→ 改写成 `https://github.com/<repo>/blob/<branch>/<path>`。

repo 信息解析顺序：
  1. 环境变量 GITHUB_REPOSITORY + GITHUB_REF_NAME（GitHub Actions 自动注入）；
  2. 本地可选文件 .mkdocs-repo（gited-ignored），每行 `key=value`：repo=owner/name、branch=xxx（或 file 首行 repo、次行 branch）。

仅改写下列「走出 docs 的相对链接」前缀；docs 内部相对链接不动。
  ../CONTEXT.md -> CONTEXT.md
  ../README.md  -> README.md
  ../config/..  -> config/..
  ../../config/. -> config/..
  ../src/..     -> src/..          (guide/ 层级)
  ../../src/..  -> src/..          (acceptance/, adr/ 层级)
特殊：../config/topology.yaml 真文件被 gitignore，改指已提交的 config/topology.example.yaml。
保留 #L行号 fragment（GitHub blob 支持）。
"""

from __future__ import annotations

import os
from pathlib import Path

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.plugins import BasePlugin

# (匹配前缀, 目标仓库内路径) —— 按最长前缀先匹配
_PREFIX_MAP = [
    ("../../config/topology.yaml", "config/topology.example.yaml"),
    ("../config/topology.yaml", "config/topology.example.yaml"),
    ("../../CONTEXT.md", "CONTEXT.md"),
    ("../CONTEXT.md", "CONTEXT.md"),
    ("../../README.md", "README.md"),
    ("../README.md", "README.md"),
    ("../../config/", "config/"),
    ("../config/", "config/"),
    ("../../src/", "src/"),
    ("../src/", "src/"),
]

_BRANCH_ENVS = ("GITHUB_HEAD_REF", "GITHUB_REF_NAME", "BRANCH")


def _resolve_repo_info() -> tuple[str | None, str | None]:
    """返回 (repo, branch)，无则 (None, None)。"""
    repo = os.environ.get("GITHUB_REPOSITORY")
    branch = next((os.environ.get(k) for k in _BRANCH_ENVS if os.environ.get(k)), None)
    if repo and branch:
        return repo, branch

    # 本地可选文件 .mkdocs-repo
    f = Path(".mkdocs-repo")
    if f.is_file():
        kv = {}
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                kv[k.strip()] = v.strip()
            else:
                kv.setdefault("branch", line)
        repo = repo or kv.get("repo")
        branch = branch or kv.get("branch")
    return repo, branch


def _rewrite_md(src: str, repo: str, branch: str) -> str:
    if not repo or not branch:
        return src
    out_lines = []
    for line in src.splitlines():
        if "](" not in line:
            out_lines.append(line)
            continue
        new = line
        for prefix, target in _PREFIX_MAP:
            # 匹配形如 ](prefix 的链接开头
            marker = "](" + prefix
            if marker in new:
                new = new.replace(
                    marker,
                    f"](https://github.com/{repo}/blob/{branch}/{target}",
                    1,
                )
        out_lines.append(new)
    return "\n".join(out_lines)


class LinkRewritePlugin(BasePlugin):
    def on_config(self, config: MkDocsConfig) -> MkDocsConfig:
        self._repo, self._branch = _resolve_repo_info()
        return config

    def on_page_markdown(self, markdown: str, config: MkDocsConfig, **kwargs) -> str:
        return _rewrite_md(markdown, self._repo, self._branch)