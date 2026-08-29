#!/usr/bin/env python3
"""验收报告生成：把 accepted/<profile>/ 的产物渲染成一页 markdown。

职责边界（守"单一事实来源"）：
- 判定逻辑（通过/不通过）已经算好并落在产物 JSON 里（gate.json / vllm-xcheck 验收），
  本脚本只做"呈现"，绝不重复实现判定，避免与 src/acceptance 漂移。
- 输入：一个或多个 profile 目录（或 `runs/accepted` 根目录，此时批量）。
- 输出：每个 profile 目录写 report.md；传根目录时额外写 index.md 汇总。

用法：
    python src/report.py runs/accepted/A4-MT-FP16-PC-verify19
    python src/report.py runs/accepted            # 批量 + 汇总 index.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 产物文件名（与 vllm-xcheck 验收一致）
F_GATE = "gate.json"
F_SLO = "slo.json"
F_EVIDENCE = "evidence.json"
F_CONTRACT = "contract.json"
F_QUALITY = "quality.json"
F_MECHANISMS = "mechanisms.json"


def _load(d: Path, name: str) -> dict | None:
    """读取一个产物 JSON，缺失或损坏返回 None（报告对应栏目标 —）。"""
    p = d / name
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _ok_symbol(v) -> str:
    """把产物里的三态判定(ok: true/false/null)映射成符号。"""
    if v is True:
        return "✅"
    if v is False:
        return "❌"
    return "—"  # None / N/A


def _mk(val) -> str:
    """兜底：把字段值安全地转成展示字符串。"""
    if val is None:
        return "—"
    if isinstance(val, bool):
        return "是" if val else "否"
    if isinstance(val, float):
        return f"{val:g}"
    return str(val)


def _meta(profile: dict) -> dict:
    ev = _load(profile, F_EVIDENCE) or {}
    ctl = _load(profile, F_CONTRACT) or {}
    return {
        "profile": ev.get("profile") or ctl.get("profile") or profile.name,
        "cell": ev.get("cell") or ctl.get("cell_id") or "—",
        "precision": ev.get("precision") or "—",
        "mode": ev.get("mode") or "—",
        "primary_metric": ev.get("primary_metric") or "—",
        "config_hash": ctl.get("config_hash") or ev.get("config_hash") or "—",
    }


def _gate_section(profile: dict) -> str:
    gate = _load(profile, F_GATE)
    ev = _load(profile, F_EVIDENCE)
    # 判定权威：gate.json 优先；缺失时回退 evidence.z3（同一份检验结果）
    if gate is None and ev and ev.get("z3"):
        gate = ev["z3"]
    if gate is None:
        return "> ⭕ 无 gate.json / evidence.z3，缺判定依据。"
    lines = [
        "## 门禁判定",
        "",
        f"- 总体 gate：`{gate.get('gate', '—')}`　整体 ok：`{_mk(gate.get('ok'))}`",
        "",
        "| 检查项 | 状态 | 说明 |",
        "|--------|------|------|",
    ]
    checks = gate.get("checks") or []
    if checks:
        for c in checks:
            name = c.get("name", "—")
            if c.get("not_applicable"):
                state = "N/A"
            else:
                state = _ok_symbol(c.get("ok"))
            lines.append(f"| `{name}` | {state} | {_mk(c.get('detail'))} |")
    else:
        lines.append("| — | — | 无 checks 明细 |")
    return "\n".join(lines)


def _mechanisms_section(profile: dict) -> str:
    ev = _load(profile, F_EVIDENCE)
    mech = None
    if ev and ev.get("mechanisms"):
        mech = ev["mechanisms"]
    else:
        raw = _load(profile, F_MECHANISMS)
        if raw:
            mech = raw if "mechanisms" in raw else {"mechanisms": raw}
    if not mech or not mech.get("mechanisms"):
        return ""
    lines = [
        "## 机制生效（M 门禁）",
        "",
        f"- 整体：{_ok_symbol(mech.get('ok'))}",
        "",
        "| 机制 | 必需 | 状态 | 依据 |",
        "|------|------|------|------|",
    ]
    for m in mech["mechanisms"]:
        name = m.get("name", "—")
        req = "是" if m.get("required") else "否"
        lines.append(
            f"| `{name}` | {req} | {_ok_symbol(m.get('ok'))} | {_mk(m.get('detail'))} |"
        )
    return "\n".join(lines)


def _slo_section(profile: dict) -> str:
    slo = _load(profile, F_SLO)
    if slo is None:
        return "> ⭕ 无 slo.json，缺 SLO/隔离数据。"
    out = ["## SLO 与隔离"]
    # 基线（隔离对比对象）
    base = slo.get("isolation_baseline") or {}
    if base:
        out += [
            "",
            "**隔离基线（B0，非混合负载）**",
            "",
            "| tenant | B0 TTFT p99 (ms) | B0 TPOT p99 (ms) |",
            "|--------|-------------------|-------------------|",
        ]
        for tenant, v in base.items():
            out.append(f"| {tenant} | {_mk(v.get('ttft_p99_ms'))} | {_mk(v.get('tpot_p99_ms'))} |")
    # 扫描矩阵：各 token_rate 点的达成吞吐 + 公平（隔离在 formal.isolation 层）
    scan = slo.get("scan") or {}
    points = scan.get("per_point") or []
    if points:
        out += [
            "",
            "**负载扫描（scan）**",
            "",
            "| 请求率 (tok/s) | 达成吞吐 (tok/s) | 公平指数 | 公平达标 | 全项合规 |",
            "|----------------|------------------|----------|----------|----------|",
        ]
        for p in points:
            fair = p.get("fairness") or {}
            out.append(
                f"| {_mk(p.get('token_rate'))} | {_mk(p.get('achieved_output_tokens_per_s'))} "
                f"| {_mk(fair.get('jain'))} | {_ok_symbol(fair.get('jain_ok'))} "
                f"| {_ok_symbol(p.get('slo_compliant'))} |"
            )
    formal = slo.get("formal") or {}
    iso = formal.get("isolation") or {}
    tenants = iso.get("tenants") or []
    if tenants:
        out += [
            "",
            "**正式隔离（formal，与 B0 基线对比）**",
            "",
            "| tenant | TTFT p99 (ms) | B0 TTFT p99 (ms) | TTFT 达标 | TPOT p99 (ms) | B0 TPOT p99 (ms) | TPOT 达标 |",
            "|--------|---------------|------------------|-----------|---------------|------------------|-----------|",
        ]
        for t in tenants:
            out.append(
                f"| {t.get('tenant')} | {_mk(t.get('ttft_p99_ms'))} | {_mk(t.get('b0_ttft_p99_ms'))} "
                f"| {_ok_symbol(t.get('ttft_ok'))} | {_mk(t.get('tpot_p99_ms'))} | {_mk(t.get('b0_tpot_p99_ms'))} "
                f"| {_ok_symbol(t.get('tpot_ok'))} |"
            )
        out.append("")
        out.append(
            f"- 隔离比（B0 基线相对最坏 ratio）：`{_mk(iso.get('ratio'))}`　隔离达标：{_ok_symbol(iso.get('isolation_ok'))}"
        )
    overall = formal.get("overall") or {}
    if overall:
        out += [
            "",
            f"- 正式吞吐判定：slo_pass={_ok_symbol(overall.get('slo_pass'))}　"
            f"tenants {overall.get('tenants_pass')}/{overall.get('tenants_total')}"
        ]
    return "\n".join(out)


def _render(profile: Path) -> str:
    meta = _meta(profile)
    text = [
        f"# 验收报告 · {meta['profile']}",
        "",
        "> 由 `src/report.py` 从验收产物渲染，判定依据为产物 JSON，非本报告重算。",
        "",
        "## 概览",
        "",
        "| 项 | 值 |",
        "|----|----|",
        f"| cell | {meta['cell']} |",
        f"| precision | {meta['precision']} |",
        f"| mode | {meta['mode']} |",
        f"| 主指标 | {meta['primary_metric']} |",
        f"| config_hash | `{meta['config_hash'][:12]}` |",
    ]
    text.append("")
    text.append(_gate_section(profile))
    mech = _mechanisms_section(profile)
    if mech:
        text += ["", mech]
    text += ["", _slo_section(profile)]
    text += ["", "---", "", f"_报告生成于 {Path(profile).resolve()}_"]
    return "\n".join(text)


def _is_profile_dir(d: Path) -> bool:
    """识别是否为单个 profile 目录：含任一验收产物文件即视为。"""
    return any((d / n).is_file() for n in (F_GATE, F_SLO, F_QUALITY, F_MECHANISMS, F_EVIDENCE))


def _collect(dirs: list[Path]) -> tuple[list[Path], list[Path]]:
    """返回 (profiles, roots)。

    目录有子 profile 时当"根"：渲染其下各 profile 并生成汇总 index.md；
    否则若自身是 profile 目录，当作单个 profile 渲染。
    """
    profiles: list[Path] = []
    roots: list[Path] = []
    for d in dirs:
        d = d.resolve()
        subs = (
            [p for p in sorted(d.iterdir()) if p.is_dir() and _is_profile_dir(p)]
            if d.is_dir()
            else []
        )
        if subs:
            profiles.extend(subs)
            roots.append(d)
        elif _is_profile_dir(d):
            profiles.append(d)
    return profiles, roots


def _index(profiles: list[Path], out_dir: Path) -> Path:
    rows = ["# 验收报告汇总", "", "| profile | cell | 主指标 | gate |", "|---------|------|--------|------|"]
    for p in profiles:
        meta = _meta(p)
        gate = _load(p, F_GATE)
        g = gate.get("gate", "—") if gate else "—"
        link = f"[{meta['profile']}]({p.name}/report.md)"
        rows.append(f"| {link} | {meta['cell']} | {meta['primary_metric']} | `{g}` |")
    rows += ["", "> 判定依据为各 profile 的 gate.json，与验收产物一致。"]
    dest = out_dir / "index.md"
    dest.write_text("\n".join(rows), encoding="utf-8")
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="验收报告生成（渲染，不重算判定）")
    ap.add_argument("dirs", nargs="+", help="profile 目录，或 accepted 根目录（向下批扫一层）")
    args = ap.parse_args(argv)

    profiles, roots = _collect([Path(d) for d in args.dirs])
    written: list[Path] = []
    for p in profiles:
        dest = p / "report.md"
        dest.write_text(_render(p), encoding="utf-8")
        written.append(dest)
        print(f"[report] wrote {dest.relative_to(Path.cwd())}")

    # 对每个根目录生成一份汇总 index.md
    for root in roots:
        subs = [p for p in profiles if p.parent == root]
        if subs:
            idx = _index(subs, root)
            print(f"[report] wrote {idx.relative_to(Path.cwd())}")

    if not written:
        print("[report] 未找到验收产物目录（缺 gate.json/slo.json 等）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())