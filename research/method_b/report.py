"""Report and figure generation."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from .config import PipelineConfig


def write_report(results: pd.DataFrame, config: PipelineConfig, out_dir: str | Path) -> Path:
    """results.csv, results.md, interaction figure를 생성한다."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results.to_csv(out / "results.csv", index=False)
    _plot_interactions(results, config, out / "fig_interaction_by_horizon.png")
    md = _markdown_report(results, config)
    path = out / "results.md"
    path.write_text(md, encoding="utf-8")
    return path


def _markdown_report(results: pd.DataFrame, config: PipelineConfig) -> str:
    """해석 요약과 계수표가 포함된 Markdown 문자열을 만든다."""
    b1 = _b1_interactions(results)
    short_ok = _is_sig(b1, "car_1_1", positive=True) or _is_sig(b1, "car_1_5", positive=True)
    mid_ok = _is_sig(b1, "car_3_20", positive=False)
    lines = [
        "# Method B Results",
        "",
        f"- 단기 증폭 여부: {'확인' if short_ok else '미확인'}",
        f"- 중기 반전 심화 여부: {'확인' if mid_ok else '미확인'}",
        "",
        "## B1 ALL 상호작용 계수",
        "",
        _format_table(b1),
        "",
        "## 전체 계수표",
        "",
        _format_table(results),
        "",
    ]
    return "\n".join(lines)


def _b1_interactions(results: pd.DataFrame) -> pd.DataFrame:
    """B1 ALL의 관심 이벤트 x 개인 거래비중 상호작용 행만 추린다."""
    if results.empty:
        return results
    mask = (
        results["spec"].eq("B1")
        & results["subgroup"].eq("ALL")
        & results["term"].eq("att_abnvol_x_share_c")
    )
    cols = ["outcome", "term", "coef", "std_error", "cluster_t", "p", "n_obs", "r2"]
    return results.loc[mask, cols].copy()


def _is_sig(frame: pd.DataFrame, outcome: str, positive: bool) -> bool:
    """지정 outcome이 기대 부호와 p<0.05를 만족하는지 판단한다."""
    row = frame.loc[frame["outcome"].eq(outcome)]
    if row.empty:
        return False
    coef = float(row.iloc[0]["coef"])
    pval = float(row.iloc[0]["p"])
    return pval < 0.05 and (coef > 0 if positive else coef < 0)


def _format_table(frame: pd.DataFrame) -> str:
    """선택 의존성 없이 DataFrame을 GitHub Markdown 표로 바꾼다."""
    if frame.empty:
        return "_No regression results._"
    display = frame.copy()
    for col in ("coef", "std_error", "cluster_t", "p", "r2"):
        if col in display.columns:
            display[col] = display[col].map(lambda x: f"{x:.6g}")
    columns = list(display.columns)
    rows = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for _, row in display.iterrows():
        values = [str(row[col]) for col in columns]
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join(rows)


def _plot_interactions(results: pd.DataFrame, config: PipelineConfig, path: Path) -> None:
    """horizon별 B1 상호작용 계수와 95% CI 그림을 저장한다."""
    frame = _b1_interactions(results)
    fig, ax = plt.subplots(figsize=(7, 4))
    if not frame.empty:
        frame["order"] = frame["outcome"].map({name: idx for idx, name in enumerate(config.outcomes)})
        frame = frame.sort_values("order")
        yerr = 1.96 * frame["std_error"].astype(float)
        ax.errorbar(frame["outcome"], frame["coef"].astype(float), yerr=yerr, fmt="o", color="#1f77b4")
    ax.axhline(0, color="#333333", linewidth=0.8)
    ax.set_xlabel("Horizon")
    ax.set_ylabel("Att x retail share coefficient")
    ax.set_title("B1 interaction by horizon")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
