"""Panel construction: returns, retail share, AR/CAR, and controls."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .attention import add_attention_flags
from .config import PipelineConfig
from .ingest import RawData


def build_panel(
    raw: RawData,
    config: PipelineConfig,
    out_dir: str | Path | None = None,
    news_counts: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """원시 테이블 묶음에서 종목-일 회귀 패널을 생성한다."""
    panel = _merge_security_tables(raw)
    panel = _add_returns_and_flow(panel, config)
    panel = _add_market_share(panel, raw)
    panel, fallback = _add_market_return(panel, raw)
    panel = _add_market_model_ar(panel, config)
    panel = _add_forward_outcomes(panel, config)
    panel = _add_controls(panel, config)
    panel = add_attention_flags(panel, config, news_counts)
    panel = _winsorize(panel, config)
    panel = panel.sort_values(["ticker", "date"]).set_index(["ticker", "date"])
    meta = {"mret_fallback": fallback, "n_obs": int(len(panel)), "n_tickers": int(panel.index.get_level_values(0).nunique())}
    if out_dir is not None:
        _write_outputs(panel, meta, Path(out_dir))
    return panel, meta


def _merge_security_tables(raw: RawData) -> pd.DataFrame:
    """종목별 가격, 시가총액, 매수/매도 수급 테이블을 병합한다."""
    prices = raw.ohlcv.copy()
    prices["date"] = pd.to_datetime(prices["date"])
    cap = raw.market_cap.loc[:, ["ticker", "date", "mktcap", "shares"]].copy()
    buy = _rename_side(raw.trading_buy, "buy")
    sell = _rename_side(raw.trading_sell, "sell")
    out = prices.merge(cap, on=["ticker", "date"], how="left")
    out = out.merge(buy, on=["ticker", "date"], how="left")
    out = out.merge(sell, on=["ticker", "date"], how="left")
    return out


def _rename_side(frame: pd.DataFrame, side: str) -> pd.DataFrame:
    """종목 수급 테이블의 투자자 컬럼에 매수/매도 접미사를 붙인다."""
    cols = ["ticker", "date", "inst", "other_corp", "retail", "foreign", "total"]
    out = frame.loc[:, cols].copy()
    rename = {col: f"{col}_{side}" for col in cols if col not in {"ticker", "date"}}
    return out.rename(columns=rename)


def _add_returns_and_flow(frame: pd.DataFrame, config: PipelineConfig) -> pd.DataFrame:
    """수익률, 개인 거래비중, 순매수/시총 변수를 계산한다."""
    out = frame.sort_values(["ticker", "date"]).copy()
    out["ret"] = out.groupby("ticker")["close"].pct_change()
    out["value_gross"] = out["total_buy"] + out["total_sell"]
    out["retail_share_raw"] = ((out["retail_buy"] + out["retail_sell"]) / out["value_gross"]).clip(0, 1)
    out["retail_share"] = out.groupby("ticker")["retail_share_raw"].transform(
        lambda s: s.shift(1).rolling(config.retail_window, min_periods=config.retail_min_periods).mean()
    )
    for investor in ("retail", "inst", "foreign"):
        out[f"{investor}_netbuy"] = out[f"{investor}_buy"] - out[f"{investor}_sell"]
    out["netbuy_mktcap"] = out["retail_netbuy"] / out["mktcap"]
    out["inst_netbuy_mktcap"] = out["inst_netbuy"] / out["mktcap"]
    out["frgn_netbuy_mktcap"] = out["foreign_netbuy"] / out["mktcap"]
    return out


def _add_market_share(frame: pd.DataFrame, raw: RawData) -> pd.DataFrame:
    """시장 월간 개인 거래비중을 1개월 래그해 병합한다."""
    if raw.market_trading_buy.empty or raw.market_trading_sell.empty:
        frame["retail_share_mkt"] = np.nan
        return frame
    buy = _rename_market_side(raw.market_trading_buy, "buy")
    sell = _rename_market_side(raw.market_trading_sell, "sell")
    market = buy.merge(sell, on=["market", "date"], how="inner")
    market["month"] = pd.to_datetime(market["date"]).dt.to_period("M")
    market["retail_sum"] = market["retail_buy"] + market["retail_sell"]
    market["total_sum"] = market["total_buy"] + market["total_sell"]
    monthly = market.groupby(["market", "month"], as_index=False)[["retail_sum", "total_sum"]].sum()
    monthly["retail_share_mkt"] = monthly["retail_sum"] / monthly["total_sum"]
    monthly["month"] = monthly["month"] + 1
    out = frame.copy()
    out["month"] = pd.to_datetime(out["date"]).dt.to_period("M")
    out = out.merge(monthly[["market", "month", "retail_share_mkt"]], on=["market", "month"], how="left")
    return out.drop(columns=["month"])


def _rename_market_side(frame: pd.DataFrame, side: str) -> pd.DataFrame:
    """시장 수급 테이블의 투자자 컬럼에 매수/매도 접미사를 붙인다."""
    cols = ["market", "date", "inst", "other_corp", "retail", "foreign", "total"]
    out = frame.loc[:, cols].copy()
    rename = {col: f"{col}_{side}" for col in cols if col not in {"market", "date"}}
    return out.rename(columns=rename)


def _add_market_return(frame: pd.DataFrame, raw: RawData) -> tuple[pd.DataFrame, str]:
    """시장 지수 수익률을 병합하고 누락 시 단면 평균으로 대체한다."""
    out = frame.copy()
    if raw.index_ohlcv.empty:
        out["mret"] = out.groupby("date")["ret"].transform("mean")
        return out, "all"
    idx = raw.index_ohlcv.sort_values(["market", "date"]).copy()
    idx["date"] = pd.to_datetime(idx["date"])
    idx["mret"] = idx.groupby("market")["close"].pct_change()
    out = out.merge(idx[["market", "date", "mret"]], on=["market", "date"], how="left")
    missing = out["mret"].isna()
    if missing.any():
        out.loc[missing, "mret"] = out.loc[missing].groupby("date")["ret"].transform("mean")
        return out, "partial"
    return out, "none"


def _add_market_model_ar(frame: pd.DataFrame, config: PipelineConfig) -> pd.DataFrame:
    """종목별 rolling market model abnormal return을 추가한다."""
    out = frame.sort_values(["ticker", "date"]).copy()
    pieces = []
    for _, group in out.groupby("ticker", sort=False):
        pieces.append(_ticker_ar(group, config))
    return pd.concat(pieces, ignore_index=True)


def _ticker_ar(group: pd.DataFrame, config: PipelineConfig) -> pd.DataFrame:
    """단일 종목의 rolling alpha/beta 기반 AR을 계산한다."""
    out = group.copy()
    ret = out["ret"].to_numpy(float)
    mret = out["mret"].to_numpy(float)
    ar = np.full(len(out), np.nan)
    for idx in range(len(out)):
        start = max(0, idx - config.market_model_start)
        end = idx - config.market_model_end + 1
        if end <= start:
            continue
        valid = np.isfinite(ret[start:end]) & np.isfinite(mret[start:end])
        if valid.sum() < config.market_model_min_periods:
            continue
        x = mret[start:end][valid]
        y = ret[start:end][valid]
        var_x = np.var(x)
        beta = np.cov(x, y, ddof=0)[0, 1] / var_x if var_x > 0 else 0.0
        alpha = y.mean() - beta * x.mean()
        if np.isfinite(ret[idx]) and np.isfinite(mret[idx]):
            ar[idx] = ret[idx] - (alpha + beta * mret[idx])
    out["AR"] = ar
    return out


def _add_forward_outcomes(frame: pd.DataFrame, config: PipelineConfig) -> pd.DataFrame:
    """전방 CAR와 전방 개인 순매수 outcome을 계산한다."""
    out = frame.sort_values(["ticker", "date"]).copy()
    for name, (start, end) in config.car_windows.items():
        out[name] = out.groupby("ticker")["AR"].transform(lambda s: _forward_sum(s, start, end, config.car_min_fraction))
    out["netbuy_fwd_0_2"] = out.groupby("ticker")["netbuy_mktcap"].transform(
        lambda s: _forward_sum(s, 0, 2, config.car_min_fraction)
    )
    return out


def _forward_sum(series: pd.Series, start: int, end: int, min_fraction: float) -> pd.Series:
    """전방 윈도우 합을 계산하되 관측 비율이 낮으면 NaN을 반환한다."""
    values = series.to_numpy(float)
    out = np.full(len(values), np.nan)
    width = end - start + 1
    required = int(np.ceil(width * min_fraction))
    for idx in range(len(values)):
        window = values[idx + start : idx + end + 1]
        if len(window) != width:
            continue
        valid = np.isfinite(window)
        if valid.sum() >= required:
            out[idx] = np.nansum(window)
    return pd.Series(out, index=series.index)


def _add_controls(frame: pd.DataFrame, config: PipelineConfig) -> pd.DataFrame:
    """시가총액, 모멘텀, 변동성 control 변수를 추가한다."""
    out = frame.sort_values(["ticker", "date"]).copy()
    grouped = out.groupby("ticker", group_keys=False)
    out["log_mktcap"] = np.log(out.groupby("ticker")["mktcap"].shift(1).where(lambda s: s > 0))
    shifted_ret = grouped["ret"].transform(lambda s: s.shift(1))
    out["mom_20"] = shifted_ret.groupby(out["ticker"]).transform(
        lambda s: (1 + s).rolling(config.control_window, min_periods=config.control_min_periods).apply(np.prod, raw=True) - 1
    )
    out["vol_20"] = shifted_ret.groupby(out["ticker"]).transform(
        lambda s: s.rolling(config.control_window, min_periods=config.control_min_periods).std()
    )
    return out


def _winsorize(frame: pd.DataFrame, config: PipelineConfig) -> pd.DataFrame:
    """연속형 숫자 변수를 전표본 분위수 기준으로 윈저라이즈한다."""
    out = frame.copy()
    skip = {"att_abnvol", "att_extret", "ext_up", "ext_down", "att_news"}
    numeric = out.select_dtypes(include=[np.number]).columns.difference(list(skip))
    for col in numeric:
        low, high = out[col].quantile([config.winsor_low, config.winsor_high])
        if pd.notna(low) and pd.notna(high) and low < high:
            out[col] = out[col].clip(low, high)
    return out


def _write_outputs(panel: pd.DataFrame, meta: dict[str, object], out_dir: Path) -> None:
    """패널 parquet와 메타데이터 JSON을 저장한다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(out_dir / "panel.parquet")
    (out_dir / "panel_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
