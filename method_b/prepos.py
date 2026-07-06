"""사전 매집(밑밥) 가설 이벤트 스터디.

관심 이벤트(이상 거래대금) 전후로 투자자별 순매수 흐름을 분해해
"기관이 미리 사두고(t-20~-1) 이벤트에 개인에게 판다(t0~+2)"는
매집→점화→분산 패턴을 검정한다. 언론 개입 자체는 뉴스 데이터 없이
검정 불가하며, 여기서는 그 전제인 수급 패턴만 다룬다.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, replace
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm

from .config import PipelineConfig
from .report import _format_table

LOGGER = logging.getLogger(__name__)

FLOW_COLS = {
    "개인": "netbuy_mktcap",
    "기관": "inst_netbuy_mktcap",
    "외국인": "frgn_netbuy_mktcap",
    "기타법인": "other_netbuy_mktcap",
}
WINDOWS = {"pre_20_1": (-20, -1), "ign_0_2": (0, 2), "unw_3_20": (3, 20)}
CAR_WINDOWS = {"car_pre_20_1": (-20, -1), "car_0_2": (0, 2), "car_3_20": (3, 20)}
TAU_LO, TAU_HI = -20, 20

SERIES_COLORS = {"개인": "#2a78d6", "기관": "#1baf7a", "외국인": "#eda100", "기타법인": "#008300"}
INK = {"primary": "#0b0b0b", "secondary": "#52514e", "muted": "#898781",
       "grid": "#e1e0d9", "baseline": "#c3c2b7", "surface": "#fcfcfb"}


@dataclass(frozen=True)
class PreposParams:
    """이벤트 정의/윈도우 파라미터."""

    abn_vol_multiple: float = 8.0
    min_gap_days: int = 20
    min_valid_fraction: float = 0.8


def run(
    panel_path: Path,
    out_dir: Path,
    params: PreposParams,
    config: PipelineConfig,
    event_start: str | None = None,
    event_end: str | None = None,
    tickers: set[str] | None = None,
) -> pd.DataFrame:
    """패널을 읽어 이벤트 통계·분할검정·그림을 생성하고 이벤트 테이블을 반환한다.

    event_start/event_end는 이벤트 '발생일' 필터이며 윈도우 데이터는 경계 밖도 사용한다.
    """
    frame = _prepare_frame(panel_path, params, config)
    if event_start:
        frame.loc[frame["date"] < pd.Timestamp(event_start), "is_event"] = False
    if event_end:
        frame.loc[frame["date"] > pd.Timestamp(event_end), "is_event"] = False
    if tickers:
        frame.loc[~frame["ticker"].isin(tickers), "is_event"] = False
    events = _extract_events(frame, params)
    if events.empty:
        raise RuntimeError("조건에 맞는 이벤트가 없습니다.")
    LOGGER.info("events: total=%d, up=%d", len(events), int(events["is_up"].sum()))
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = _window_stats(events)
    stats.to_csv(out_dir / "prepos_stats.csv", index=False)
    split = _prepos_split(events)
    split.to_csv(out_dir / "prepos_split.csv", index=False)
    daily = _daily_sequence(events)
    daily.to_csv(out_dir / "daily_sequence.csv", index=False)
    events.drop(columns=["traj"]).to_parquet(out_dir / "prepos_events.parquet")
    _plot(events, out_dir / "fig_prepos_flows.png")
    _write_summary(stats, split, events, out_dir / "summary.md")
    return events


def _prepare_frame(panel_path: Path, params: PreposParams, config: PipelineConfig) -> pd.DataFrame:
    """패널을 로드하고 엄격 관심 플래그와 기타법인 순매수를 추가한다."""
    frame = pd.read_parquet(panel_path).reset_index().sort_values(["ticker", "date"]).reset_index(drop=True)
    base = frame.groupby("ticker")["value_gross"].transform(
        lambda s: s.shift(config.attention_shift).rolling(
            config.attention_window, min_periods=config.attention_min_periods
        ).median()
    )
    frame["att_strict"] = ((frame["value_gross"] / base) >= params.abn_vol_multiple).fillna(False).astype(int)
    frame["other_netbuy_mktcap"] = (frame["other_corp_buy"] - frame["other_corp_sell"]) / frame["mktcap"]
    prior = frame.groupby("ticker")["att_strict"].transform(
        lambda s: s.shift(1).rolling(params.min_gap_days, min_periods=1).sum()
    )
    frame["is_event"] = (frame["att_strict"].eq(1)) & (prior.fillna(0).eq(0))
    return frame


def _extract_events(frame: pd.DataFrame, params: PreposParams) -> pd.DataFrame:
    """디클러스터된 이벤트별 윈도우 합계와 τ-궤적을 추출한다."""
    rows: list[dict[str, object]] = []
    width = TAU_HI - TAU_LO + 1
    for ticker, group in frame.groupby("ticker", sort=False):
        arrays = {name: group[col].to_numpy(float) for name, col in FLOW_COLS.items()}
        ar = group["AR"].to_numpy(float)
        ret = group["ret"].to_numpy(float)
        share = group["retail_share"].to_numpy(float)
        dates = group["date"].to_numpy()
        market = group["market"].iloc[0]
        n = len(group)
        for pos in np.flatnonzero(group["is_event"].to_numpy()):
            if pos + TAU_LO < 0 or pos + TAU_HI >= n:
                continue
            row: dict[str, object] = {
                "ticker": ticker, "market": market, "date": dates[pos],
                "share": share[pos], "ret0": ret[pos], "is_up": bool(ret[pos] > 0),
            }
            ok = True
            for wname, (lo, hi) in WINDOWS.items():
                for fname in FLOW_COLS:
                    row[f"{fname}_{wname}"] = _win_sum(arrays[fname], pos, lo, hi, params.min_valid_fraction)
            for wname, (lo, hi) in CAR_WINDOWS.items():
                row[wname] = _win_sum(ar, pos, lo, hi, params.min_valid_fraction)
            if any(pd.isna(row[f"{f}_pre_20_1"]) for f in FLOW_COLS) or pd.isna(row["car_0_2"]):
                ok = False
            if not ok:
                continue
            traj = np.vstack([
                arrays[f][pos + TAU_LO: pos + TAU_HI + 1] for f in FLOW_COLS
            ] + [ar[pos + TAU_LO: pos + TAU_HI + 1]])
            if traj.shape[1] == width:
                row["traj"] = traj
                rows.append(row)
    return pd.DataFrame(rows)


def _win_sum(arr: np.ndarray, pos: int, lo: int, hi: int, min_frac: float) -> float:
    """τ∈[lo,hi] 합계. 유효 관측 비율이 낮으면 NaN."""
    seg = arr[pos + lo: pos + hi + 1]
    valid = np.isfinite(seg)
    if valid.sum() < np.ceil(len(seg) * min_frac):
        return float("nan")
    return float(np.nansum(seg))


def _clustered_mean(y: pd.Series, groups: pd.Series) -> tuple[float, float, float]:
    """날짜 클러스터 SE 기반 평균/t/p."""
    mask = y.notna()
    yv = y[mask].to_numpy(float)
    if len(yv) < 10:
        return float("nan"), float("nan"), float("nan")
    g = pd.factorize(groups[mask])[0]
    res = sm.OLS(yv, np.ones((len(yv), 1))).fit(cov_type="cluster", cov_kwds={"groups": g})
    return float(res.params[0]), float(res.tvalues[0]), float(res.pvalues[0])


def _clustered_diff(y: pd.Series, indicator: pd.Series, groups: pd.Series) -> tuple[float, float, float]:
    """상위군-하위군 평균 차이(indicator 계수)의 날짜 클러스터 검정."""
    mask = y.notna() & indicator.notna()
    if mask.sum() < 60:
        return float("nan"), float("nan"), float("nan")
    x = sm.add_constant(indicator[mask].astype(float).to_numpy())
    g = pd.factorize(groups[mask])[0]
    res = sm.OLS(y[mask].to_numpy(float), x).fit(cov_type="cluster", cov_kwds={"groups": g})
    return float(res.params[1]), float(res.tvalues[1]), float(res.pvalues[1])


def _window_stats(events: pd.DataFrame) -> pd.DataFrame:
    """이벤트 전/중/후 윈도우별 투자자 수급과 CAR의 평균·유의성 표."""
    rows = []
    for subset_name, sub in (("ALL", events), ("UP", events[events["is_up"]])):
        for wname in WINDOWS:
            for fname in FLOW_COLS:
                mean, t, p = _clustered_mean(sub[f"{fname}_{wname}"] * 1e4, sub["date"])
                rows.append({"subset": subset_name, "window": wname, "measure": fname,
                             "mean_bp": mean, "t": t, "p": p, "n": int(sub[f"{fname}_{wname}"].notna().sum())})
        for wname in CAR_WINDOWS:
            mean, t, p = _clustered_mean(sub[wname] * 100, sub["date"])
            rows.append({"subset": subset_name, "window": wname, "measure": "CAR(%)",
                         "mean_bp": mean, "t": t, "p": p, "n": int(sub[wname].notna().sum())})
    return pd.DataFrame(rows)


def _prepos_split(events: pd.DataFrame) -> pd.DataFrame:
    """급등형 이벤트를 사전 스마트머니(기관+외국인) 매집 상/하위 3분위로 갈라 비교한다."""
    up = events[events["is_up"]].copy()
    up["smart_pre"] = up["기관_pre_20_1"] + up["외국인_pre_20_1"]
    rows = []
    for scope_name, scope in (("UP_ALL", up), ("UP_HIGH_SHARE", up[up["share"] >= up["share"].quantile(2 / 3)])):
        if len(scope) < 90:
            continue
        lo_q, hi_q = scope["smart_pre"].quantile([1 / 3, 2 / 3])
        tri = scope[(scope["smart_pre"] <= lo_q) | (scope["smart_pre"] >= hi_q)].copy()
        tri["top"] = (tri["smart_pre"] >= hi_q).astype(int)
        for measure, scale in (("개인_ign_0_2", 1e4), ("기관_ign_0_2", 1e4), ("외국인_ign_0_2", 1e4),
                               ("기타법인_ign_0_2", 1e4), ("car_0_2", 100), ("car_3_20", 100)):
            top_mean = tri.loc[tri["top"].eq(1), measure].mean() * scale
            bot_mean = tri.loc[tri["top"].eq(0), measure].mean() * scale
            diff, t, p = _clustered_diff(tri[measure] * scale, tri["top"], tri["date"])
            rows.append({"scope": scope_name, "measure": measure, "top_mean": top_mean,
                         "bottom_mean": bot_mean, "diff": diff, "t": t, "p": p,
                         "n_top": int(tri["top"].sum()), "n_bot": int((1 - tri["top"]).sum())})
    return pd.DataFrame(rows)


def _daily_sequence(events: pd.DataFrame) -> pd.DataFrame:
    """급등형 이벤트의 τ별 일평균 순매수(bp)/AR(%)과 날짜 클러스터 t 표."""
    up = events[events["is_up"]].reset_index(drop=True)
    stack = np.stack(up["traj"].to_list())  # (n, 5, 41), NaN 보존
    names = list(FLOW_COLS) + ["AR"]
    rows = []
    for j, tau in enumerate(range(TAU_LO, TAU_HI + 1)):
        row: dict[str, object] = {"tau": tau}
        for i, name in enumerate(names):
            scale = 100.0 if name == "AR" else 1e4
            y = pd.Series(stack[:, i, j] * scale, index=up.index)
            mean, t, _ = _clustered_mean(y, up["date"])
            row[f"{name}_mean"] = mean
            row[f"{name}_t"] = t
        rows.append(row)
    return pd.DataFrame(rows)


def _plot(events: pd.DataFrame, path: Path) -> None:
    """이벤트 주변 평균 누적 수급(투자자별)과 누적 AR 궤적 그림."""
    plt.rcParams["font.family"] = "Noto Sans CJK HK"
    plt.rcParams["axes.unicode_minus"] = False
    up = events[events["is_up"]]
    taus = np.arange(TAU_LO, TAU_HI + 1)
    stack = np.nan_to_num(np.stack(up["traj"].to_list()), nan=0.0)  # (n_events, 5, 41)
    cum = stack.cumsum(axis=2).mean(axis=0)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8.2, 7.2), sharex=True, dpi=160,
                                   gridspec_kw={"height_ratios": [3, 2]})
    fig.patch.set_facecolor(INK["surface"])
    for ax in (ax1, ax2):
        ax.set_facecolor(INK["surface"])
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(INK["baseline"])
        ax.grid(axis="y", color=INK["grid"], linewidth=0.8)
        ax.tick_params(colors=INK["muted"], labelsize=9)
        ax.axhline(0, color=INK["baseline"], linewidth=0.9)
        ax.axvline(0, color=INK["muted"], linewidth=0.9, linestyle=(0, (3, 3)))
    for i, name in enumerate(FLOW_COLS):
        y = cum[i] * 1e4
        ax1.plot(taus, y, color=SERIES_COLORS[name], linewidth=2.0, label=name)
        ax1.annotate(name, (taus[-1], y[-1]), xytext=(4, 0), textcoords="offset points",
                     color=SERIES_COLORS[name], fontsize=9, fontweight="bold", va="center")
    ax1.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=INK["secondary"])
    ax1.set_title("급등형 관심 이벤트 전후 평균 누적 순매수 (시총 대비 bp)",
                  color=INK["primary"], fontsize=11, loc="left")
    ax1.set_xlim(TAU_LO, TAU_HI + 6)
    ax2.plot(taus, cum[4] * 100, color=INK["primary"], linewidth=2.0)
    ax2.set_title("평균 누적 초과수익 CAR (%)", color=INK["primary"], fontsize=11, loc="left")
    ax2.set_xlabel("이벤트 기준 영업일 (τ)", color=INK["secondary"], fontsize=9)
    fig.tight_layout()
    fig.savefig(path, facecolor=INK["surface"])
    plt.close(fig)


def _write_summary(stats: pd.DataFrame, split: pd.DataFrame, events: pd.DataFrame, path: Path) -> None:
    """요약 markdown을 저장한다."""
    lines = ["# 사전 매집(밑밥) 이벤트 스터디", "",
             f"- 이벤트: {len(events)}개 (급등형 {int(events['is_up'].sum())}개), "
             f"디클러스터 {TAU_HI}일, 임계 8x", "",
             "## 윈도우 통계 (mean_bp: 시총 대비 bp, CAR은 %)", "",
             _format_table(stats.round(4)), "",
             "## 사전 스마트머니 상/하위 3분위 비교 (급등형)", "",
             _format_table(split.round(4)), ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    """CLI 진입점."""
    parser = argparse.ArgumentParser(description="pre-positioning event study")
    parser.add_argument("--panel", default="output/real/panel.parquet")
    parser.add_argument("--out", default="output/real/prepos")
    parser.add_argument("--multiple", type=float, default=8.0)
    parser.add_argument("--event-start", default=None)
    parser.add_argument("--event-end", default=None)
    parser.add_argument("--tickers-file", default=None, help="이벤트를 제한할 티커 목록(줄 단위)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    params = PreposParams(abn_vol_multiple=args.multiple)
    tickers = None
    if args.tickers_file:
        tickers = {line.strip() for line in Path(args.tickers_file).read_text().splitlines() if line.strip()}
    run(Path(args.panel), Path(args.out), params, PipelineConfig(),
        event_start=args.event_start, event_end=args.event_end, tickers=tickers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
