"""Attention event construction."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import PipelineConfig


def add_attention_flags(
    frame: pd.DataFrame,
    config: PipelineConfig,
    news_counts: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """거래대금/극단수익률/선택 뉴스 기반 관심 이벤트 플래그를 추가한다."""
    out = frame.sort_values(["ticker", "date"]).copy()
    grouped = out.groupby("ticker", group_keys=False)
    base_value = grouped["value_gross"].transform(
        lambda s: s.shift(config.attention_shift).rolling(
            config.attention_window,
            min_periods=config.attention_min_periods,
        ).median()
    )
    abs_ret = out["ret"].abs()
    base_absret = abs_ret.groupby(out["ticker"]).transform(
        lambda s: s.shift(config.attention_shift).rolling(
            config.attention_window,
            min_periods=config.attention_min_periods,
        ).quantile(config.extret_quantile)
    )
    out["att_abnvol"] = ((out["value_gross"] / base_value) >= config.abn_vol_multiple).fillna(False).astype(int)
    threshold = pd.concat([base_absret, pd.Series(config.extret_abs_floor, index=out.index)], axis=1).max(axis=1)
    out["att_extret"] = ((abs_ret >= threshold) & threshold.notna()).astype(int)
    out["ext_up"] = ((out["att_extret"].eq(1)) & (out["ret"] > 0)).astype(int)
    out["ext_down"] = ((out["att_extret"].eq(1)) & (out["ret"] < 0)).astype(int)
    if news_counts is not None:
        out = add_news_attention(out, news_counts, config)
    return out


def load_news_counts(csv_path: str | Path | None) -> pd.DataFrame | None:
    """date,ticker,count 컬럼을 가진 뉴스 CSV를 읽는다. 미제공이면 None."""
    if csv_path is None:
        return None
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"news csv not found: {path}")
    frame = pd.read_csv(path, dtype={"ticker": str})
    required = {"date", "ticker", "count"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"news csv missing columns={sorted(missing)}")
    frame["date"] = pd.to_datetime(frame["date"])
    frame["count"] = pd.to_numeric(frame["count"], errors="coerce").fillna(0)
    return frame.loc[:, ["date", "ticker", "count"]]


def add_news_attention(
    frame: pd.DataFrame,
    news_counts: pd.DataFrame,
    config: PipelineConfig,
) -> pd.DataFrame:
    """뉴스 count의 과거 분위수 기준 관심 이벤트를 추가한다."""
    out = frame.merge(news_counts, on=["date", "ticker"], how="left")
    out["count"] = out["count"].fillna(0)
    base_news = out.groupby("ticker")["count"].transform(
        lambda s: s.shift(config.attention_shift).rolling(
            config.attention_window,
            min_periods=config.attention_min_periods,
        ).quantile(config.extret_quantile)
    )
    out["att_news"] = ((out["count"] >= base_news) & (out["count"] >= config.news_count_floor)).astype(int)
    return out.drop(columns=["count"])
