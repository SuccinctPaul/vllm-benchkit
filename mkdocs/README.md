# vllm-notes 文档站点 —— 构建与本地运行手册

> 这篇是**站点构建运行**的唯一说明，写给想在本机预览或动手改站点的人。
> 一句话：`docs/` 是内容，`mkdocs.yml` 是配置，`mkdocs/` 是本地插件，`site/` 是构建产物，`.github/workflows/pages.yml` 是 CI 发布。

---

## 1. 这条目录里有什么

| 路径 | 作用 |
|------|------|
| `mkdocs.yml`（仓库根） | 站点配置：Material 主题、中文界面、`nav` 导航树、搜索、深浅色 |
| `docs/` | **站点唯一发布的内容**（`.md` + `docs/assets/` 翻译挂件） |
| `mkdocs/`（本目录） | 本地微插件包：`link_rewrite.py` 把走出 `docs/` 的链接改写成 GitHub blob 链 |
| `.docs-venv/` | 本地构建用的独立虚拟环境（gitignored，不进仓库） |
| `site/` | `mkdocs build` 的输出目录（gitignored，交给 Pages/CI 用） |
| `.github/workflows/pages.yml` | GitHub Actions：装依赖 → `mkdocs build` → 发布 Pages |

`docs/` 之外的这些（`mkdocs.yml`、`mkdocs/`、`.github/`）**只进 git、不进站点**——站点内容就是 `docs/`，保证轻量。

---

## 2. 为什么不用项目自带的 uv 环境

`pyproject.toml` 钉死了 `torch-npu` 等 **NPU 专用依赖**（只有 Linux wheel），macOS 上 `uv run mkdocs` 根本解析不出来。所以文档工具链**独立**出来，放 `.docs-venv/`，不污染 `uv.lock`。

## 3. 第一次：搭好本地构建环境

```bash
# 在仓库根执行一次即可
uv venv .docs-venv                                                # 建独立虚拟环境
uv pip install -p .docs-venv mkdocs-material                       # Material 主题 + mkdocs + 搜索
uv pip install -p .docs-venv -e ./mkdocs                           # 本地插件（link_rewrite，可编辑安装）
```

## 4. 本地预览（跑起来看）

```bash
.docs-venv/bin/mkdocs serve
# 浏览器打开 http://127.0.0.1:8000
```

- 改了 `docs/*.md` 或 `mkdocs.yml` 会自动热更新。
- 停止：在终端按 `Ctrl-C`。

## 5. 本地构建（出静态站）

```bash
.docs-venv/bin/mkdocs build            # 产物在 site/
.docs-venv/bin/mkdocs build --clean    # 先清空再重建，避免残留旧页面
```

## 6. 想让「本地预览」也显示 GitHub 链接（可选）

`link_rewrite` 插件只在拿到 `repo/branch` 时才把 `src/`、`config/`、根 `README`/`CONTEXT` 的引用改写成 GitHub blob 链。默认（不配置）时保持相对路径。

- **CI**：GitHub 自动注入 `GITHUB_REPOSITORY` + `GITHUB_REF_NAME`，无需手动。
- **本地**：在仓库根建一个 gitignored 文件 `.mkdocs-repo`，两行即可：

```
repo=你的owner/仓库名
branch=feat/vllm-xcheck
```

配好后 `mkdocs serve` 的页面里，代码/配置引用就会跳转到对应 GitHub 文件。不配也行，站点照常构建。

## 7. 更新依赖 / 换分支发布

- 改站点插件逻辑：改 `mkdocs/link_rewrite.py`，本地用 `mkdocs serve` 即看效果（可编辑安装，自动生效）。
- 换发布分支：改 `.github/workflows/pages.yml` 里 `on.push.branches` 即可。
- 发布到 Pages 的前置动作（一次性）：仓库 **Settings → Pages → Source 选 "GitHub Actions"**。

## 8. 常见问题

| 现象 | 原因 / 处理 |
|------|-------------|
| `mkdocs serve` 提示 `plugin ... not installed` | 没装本地插件：重跑 §3 第三步；插件是通过 **entry-point** 注册的，只把文件放进 `mkdocs/` 目录不行，必须 `pip install -e ./mkdocs` |
| 构建出现大量 `#L行号` 的 INFO 提示 | 已知限制：静态站不按行跳转 GitHub 锚点，仅提示、不影响构建 |
| 构建时 WARNING 说 `../CONTEXT.md` 找不到 | 走出 `docs/` 的相对链接未配 repo（见 §6），CI 里会自动消失 |
| 想在别处跑但没装 `.docs-venv` | 重跑 §3 |

---

相关：站点主题/导航配置见根 `mkdocs.yml`；发布流水线见 `.github/workflows/pages.yml`。