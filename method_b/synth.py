"""Synthetic raw data generator for offline validation."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import PipelineConfig
from .ingest import RawData


def generate(n_stocks: int = 60, n_days: int = 750, seed: int = 42) -> RawData:
    """ingest와 동일한 스키마의 합성 원시 데이터 묶음을 생성한다."""
    cfg = PipelineConfig(seed=seed)
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2016-01-04", periods=n_days)
    tickers = _ticker_table(n_stocks)
    market_returns = _market_returns(dates, rng)
    share = _retail_share_paths(n_stocks, n_days, rng)
    events = rng.random((n_stocks, n_days)) < cfg.synth_event_prob
    returns = _stock_returns(tickers, market_returns, share, events, cfg, rng)
    close = 10000 * np.exp(np.cumsum(returns, axis=1))
    shares_out = rng.integers(8_000_000, 90_000_000, size=n_stocks).astype(float)
    mktcap = close * shares_out[:, None]
    gross = _gross_value(mktcap, events, rng)
    netbuy = _netbuy_mktcap(share, events, cfg)
    trading_buy, trading_sell = _trading_tables(tickers, dates, share, gross, netbuy, mktcap)
    ohlcv = _ohlcv_table(tickers, dates, close, returns, gross)
    market_cap = _market_cap_table(tickers, dates, mktcap, gross, shares_out)
    index_ohlcv = _index_table(dates, market_returns)
    market_buy = _aggregate_market_trading(trading_buy)
    market_sell = _aggregate_market_trading(trading_sell)
    return RawData(
        tickers=tickers,
        ohlcv=ohlcv,
        trading_buy=trading_buy,
        trading_sell=trading_sell,
        market_cap=market_cap,
        index_ohlcv=index_ohlcv,
        market_trading_buy=market_buy,
        market_trading_sell=market_sell,
    )


def _ticker_table(n_stocks: int) -> pd.DataFrame:
    """합성 티커와 시장 구분 테이블을 만든다."""
    markets = np.array(["KOSPI"] * (n_stocks // 2) + ["KOSDAQ"] * (n_stocks - n_stocks // 2))
    tickers = [f"{idx:05d}0" for idx in range(1, n_stocks + 1)]
    names = [f"SYNTH{idx:03d}" for idx in range(1, n_stocks + 1)]
    return pd.DataFrame({"ticker": tickers, "market": markets, "name": names})


def _market_returns(dates: pd.DatetimeIndex, rng: np.random.Generator) -> pd.DataFrame:
    """시장별 일수익률 경로를 생성한다."""
    frames = []
    for market in ("KOSPI", "KOSDAQ"):
        ret = rng.normal(0.0003, 0.01, size=len(dates))
        frames.append(pd.DataFrame({"date": dates, "market": market, "mret": ret}))
    return pd.concat(frames, ignore_index=True)


def _retail_share_paths(n_stocks: int, n_days: int, rng: np.random.Generator) -> np.ndarray:
    """종목별 AR(1) 개인 거래비중 경로를 생성한다."""
    means = rng.uniform(0.25, 0.85, size=n_stocks)
    share = np.empty((n_stocks, n_days))
    share[:, 0] = means
    for day in range(1, n_days):
        share[:, day] = means + 0.98 * (share[:, day - 1] - means) + rng.normal(0, 0.01, n_stocks)
    return np.clip(share, 0.1, 0.95)


def _stock_returns(
    tickers: pd.DataFrame,
    market_returns: pd.DataFrame,
    share: np.ndarray,
    events: np.ndarray,
    cfg: PipelineConfig,
    rng: np.random.Generator,
) -> np.ndarray:
    """시장모형 수익률에 이벤트 후 단기/중기 drift를 심는다."""
    n_stocks, n_days = share.shape
    returns = np.empty((n_stocks, n_days))
    beta = rng.uniform(0.5, 1.5, size=n_stocks)
    market_map = {m: g["mret"].to_numpy() for m, g in market_returns.groupby("market")}
    share_c = share - share.mean()
    for idx, row in enumerate(tickers.itertuples(index=False)):
        base = beta[idx] * market_map[row.market] + rng.normal(0, 0.02, size=n_days)
        returns[idx] = base
    for stock_idx, day_idx in np.argwhere(events):
        sc = share_c[stock_idx, day_idx]
        returns[stock_idx, day_idx + 1 : min(day_idx + 6, n_days)] += cfg.synth_k1 * sc
        returns[stock_idx, day_idx + 6 : min(day_idx + 26, n_days)] -= cfg.synth_k2 * sc
    return returns


def _gross_value(mktcap: np.ndarray, events: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """거래대금 gross 경로를 만들고 이벤트일에 6배 spike를 심는다."""
    turnover = rng.lognormal(mean=np.log(0.018), sigma=0.25, size=mktcap.shape)
    gross = mktcap * turnover
    gross[events] *= 6.0
    return gross


def _netbuy_mktcap(share: np.ndarray, events: np.ndarray, cfg: PipelineConfig) -> np.ndarray:
    """개인 순매수/시총 경로에 이벤트 후 수급 효과를 심는다."""
    netbuy = np.random.default_rng(cfg.seed + 999).normal(0, 0.00012, size=share.shape)
    n_stocks, n_days = share.shape
    for stock_idx, day_idx in np.argwhere(events):
        netbuy[stock_idx, day_idx : min(day_idx + 3, n_days)] += cfg.synth_k3 * share[stock_idx, day_idx]
    return netbuy


def _trading_tables(
    tickers: pd.DataFrame,
    dates: pd.DatetimeIndex,
    share: np.ndarray,
    gross: np.ndarray,
    netbuy: np.ndarray,
    mktcap: np.ndarray,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """share와 netbuy에 일관되는 매수/매도 투자자별 테이블을 만든다."""
    buy_rows, sell_rows = [], []
    for stock_idx, row in enumerate(tickers.itertuples(index=False)):
        total_side = gross[stock_idx] / 2.0
        retail_sum = share[stock_idx] * gross[stock_idx]
        retail_net = netbuy[stock_idx] * mktcap[stock_idx]
        retail_buy = np.clip((retail_sum + retail_net) / 2.0, 0, total_side)
        retail_sell = np.clip((retail_sum - retail_net) / 2.0, 0, total_side)
        buy_rows.append(_investor_side_frame(row, dates, total_side, retail_buy))
        sell_rows.append(_investor_side_frame(row, dates, total_side, retail_sell))
    return pd.concat(buy_rows, ignore_index=True), pd.concat(sell_rows, ignore_index=True)


def _investor_side_frame(
    ticker_row: object,
    dates: pd.DatetimeIndex,
    total: np.ndarray,
    retail: np.ndarray,
) -> pd.DataFrame:
    """한 종목 한 side의 투자자별 거래대금 DataFrame을 만든다."""
    residual = np.maximum(total - retail, 0)
    return pd.DataFrame(
        {
            "ticker": ticker_row.ticker,
            "market": ticker_row.market,
            "date": dates,
            "inst": residual * 0.45,
            "other_corp": residual * 0.10,
            "retail": retail,
            "foreign": residual * 0.45,
            "total": total,
        }
    )


def _ohlcv_table(
    tickers: pd.DataFrame,
    dates: pd.DatetimeIndex,
    close: np.ndarray,
    returns: np.ndarray,
    gross: np.ndarray,
) -> pd.DataFrame:
    """합성 OHLCV 테이블을 만든다."""
    frames = []
    for idx, row in enumerate(tickers.itertuples(index=False)):
        open_ = close[idx] / (1 + np.clip(returns[idx], -0.3, 0.3))
        high = np.maximum(open_, close[idx]) * 1.01
        low = np.minimum(open_, close[idx]) * 0.99
        volume = np.maximum(gross[idx] / np.maximum(close[idx], 1), 1)
        frames.append(
            pd.DataFrame(
                {
                    "ticker": row.ticker,
                    "market": row.market,
                    "date": dates,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close[idx],
                    "volume": volume,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def _market_cap_table(
    tickers: pd.DataFrame,
    dates: pd.DatetimeIndex,
    mktcap: np.ndarray,
    gross: np.ndarray,
    shares_out: np.ndarray,
) -> pd.DataFrame:
    """합성 시가총액 테이블을 만든다."""
    frames = []
    for idx, row in enumerate(tickers.itertuples(index=False)):
        frames.append(
            pd.DataFrame(
                {
                    "ticker": row.ticker,
                    "market": row.market,
                    "date": dates,
                    "mktcap": mktcap[idx],
                    "volume": gross[idx] / np.maximum(mktcap[idx] / shares_out[idx], 1),
                    "value": gross[idx] / 2.0,
                    "shares": shares_out[idx],
                    "close": mktcap[idx] / shares_out[idx],
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def _index_table(dates: pd.DatetimeIndex, market_returns: pd.DataFrame) -> pd.DataFrame:
    """시장 수익률에서 합성 지수 OHLCV를 만든다."""
    frames = []
    for market, group in market_returns.groupby("market"):
        close = 1000 * np.exp(np.cumsum(group["mret"].to_numpy()))
        open_ = close / (1 + np.clip(group["mret"].to_numpy(), -0.3, 0.3))
        frames.append(
            pd.DataFrame(
                {
                    "market": market,
                    "date": dates,
                    "open": open_,
                    "high": np.maximum(open_, close) * 1.005,
                    "low": np.minimum(open_, close) * 0.995,
                    "close": close,
                    "volume": 1_000_000_000,
                    "value": 10_000_000_000_000,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def _aggregate_market_trading(frame: pd.DataFrame) -> pd.DataFrame:
    """종목별 수급 테이블을 시장-일 단위로 합산한다."""
    cols = ["inst", "other_corp", "retail", "foreign", "total"]
    return frame.groupby(["market", "date"], as_index=False)[cols].sum()
