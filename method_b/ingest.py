"""KRX/pykrx ingest wrappers, cache handling, and raw data schema."""

from __future__ import annotations

import contextlib
import io
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, TypeVar

import numpy as np
import pandas as pd

from .config import PipelineConfig

with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    from pykrx import stock


T = TypeVar("T")
EMPTY_MARKER = "__empty_cache_marker__"
LOGGER = logging.getLogger(__name__)


@dataclass
class RawData:
    """ingest와 synth가 공유하는 원시 테이블 묶음."""

    tickers: pd.DataFrame
    ohlcv: pd.DataFrame
    trading_buy: pd.DataFrame
    trading_sell: pd.DataFrame
    market_cap: pd.DataFrame
    index_ohlcv: pd.DataFrame
    market_trading_buy: pd.DataFrame
    market_trading_sell: pd.DataFrame


class Cache:
    """Parquet cache with an explicit empty-frame marker."""

    def __init__(self, root: Path) -> None:
        """캐시 루트 경로를 설정한다."""
        self.root = Path(root)

    def path(self, endpoint: str, key: str, start: str, end: str) -> Path:
        """엔드포인트/키/기간에 대응하는 캐시 파일 경로."""
        safe = key.replace("/", "_").replace(" ", "_")
        return self.root / endpoint / f"{safe}_{start}_{end}.parquet"

    def read(self, endpoint: str, key: str, start: str, end: str) -> tuple[bool, pd.DataFrame]:
        """캐시가 있으면 hit=True와 DataFrame을 반환한다."""
        path = self.path(endpoint, key, start, end)
        if not path.exists():
            return False, pd.DataFrame()
        frame = pd.read_parquet(path)
        if EMPTY_MARKER in frame.columns:
            return True, pd.DataFrame()
        return True, frame

    def write(self, endpoint: str, key: str, start: str, end: str, frame: pd.DataFrame) -> None:
        """빈 DataFrame도 마커로 구분해 저장한다."""
        path = self.path(endpoint, key, start, end)
        path.parent.mkdir(parents=True, exist_ok=True)
        to_store = frame if not frame.empty else pd.DataFrame({EMPTY_MARKER: [True]})
        to_store.to_parquet(path, index=False)


def check_krx_login(config: PipelineConfig | None = None) -> bool:
    """KRX 로그인이 가능한지 최근 삼성전자 투자자별 매수 데이터로 확인한다."""
    if not os.getenv("KRX_ID") or not os.getenv("KRX_PW"):
        return False
    cfg = config or PipelineConfig()
    today = pd.Timestamp.today().normalize()
    start = (today - pd.Timedelta(days=10)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    try:
        frame = _krx_call(
            stock.get_market_trading_value_by_date,
            cfg,
            start,
            end,
            "005930",
            on="매수",
        )
    except Exception as exc:  # pragma: no cover - network/login dependent
        LOGGER.debug("KRX login probe failed: %s", exc)
        return False
    return not frame.empty


def fetch_raw_data(config: PipelineConfig) -> RawData:
    """실데이터 모드에서 유니버스를 만들고 종목/시장 원시 데이터를 수집한다."""
    cache = Cache(config.cache_dir)
    universe = build_universe(config, cache)
    tickers = stratified_sample(universe, config.sample_tickers, config.seed)
    LOGGER.info("Fetch universe size=%d, sampled=%d", len(universe), len(tickers))
    market_frames = _fetch_market_tables(config, cache)
    ticker_frames = _fetch_ticker_tables(tickers, config, cache)
    return RawData(tickers=ticker_frames[0], **market_frames, **ticker_frames[1])


def build_universe(config: PipelineConfig, cache: Cache | None = None) -> pd.DataFrame:
    """2016~2026 앵커일의 KOSPI+KOSDAQ 합집합 유니버스를 생성한다."""
    cache = cache or Cache(config.cache_dir)
    records: list[dict[str, str]] = []
    for year in range(config.anchor_start_year, config.anchor_end_year + 1):
        for market in config.markets:
            anchor = _find_anchor_day(year, market, config, cache)
            tickers = fetch_ticker_list(anchor, market, config, cache)
            for ticker in tickers:
                records.append({"ticker": ticker, "market": market})
    frame = pd.DataFrame(records).drop_duplicates("ticker")
    frame["name"] = [fetch_ticker_name(t, config, cache) for t in frame["ticker"]]
    keep = frame["ticker"].str[-1].eq("0") & ~frame["name"].str.contains("스팩", na=False)
    return frame.loc[keep].sort_values(["market", "ticker"]).reset_index(drop=True)


def stratified_sample(universe: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    """시장별 절반 층화 샘플을 고정 seed로 추출한다."""
    if n <= 0 or len(universe) <= n:
        return universe.reset_index(drop=True)
    rng = np.random.default_rng(seed)
    markets = list(universe["market"].drop_duplicates())
    base = n // len(markets)
    remainder = n % len(markets)
    sampled: list[pd.DataFrame] = []
    for idx, market in enumerate(markets):
        part = universe.loc[universe["market"].eq(market)]
        take = min(len(part), base + int(idx < remainder))
        chosen = rng.choice(part.index.to_numpy(), size=take, replace=False)
        sampled.append(part.loc[np.sort(chosen)])
    return pd.concat(sampled, ignore_index=True).sort_values(["market", "ticker"])


def fetch_ticker_list(date: str, market: str, config: PipelineConfig, cache: Cache) -> list[str]:
    """특정 일자/시장 상장 티커 리스트를 캐시와 함께 조회한다."""
    hit, cached = cache.read("ticker_list", market, date, date)
    if hit:
        return cached["ticker"].astype(str).to_list()
    values = _krx_call(stock.get_market_ticker_list, config, date, market=market)
    frame = pd.DataFrame({"ticker": [str(x) for x in values]})
    cache.write("ticker_list", market, date, date, frame)
    return frame["ticker"].to_list()


def fetch_ticker_name(ticker: str, config: PipelineConfig, cache: Cache) -> str:
    """종목명을 캐시와 함께 조회한다."""
    hit, cached = cache.read("ticker_name", ticker, "name", "name")
    if hit and not cached.empty:
        return str(cached.loc[0, "name"])
    name = str(_krx_call(stock.get_market_ticker_name, config, ticker))
    cache.write("ticker_name", ticker, "name", "name", pd.DataFrame({"name": [name]}))
    return name


def fetch_ohlcv(ticker: str, market: str, config: PipelineConfig, cache: Cache) -> pd.DataFrame:
    """수정 OHLCV를 조회하고 표준 컬럼으로 정규화한다."""
    hit, cached = cache.read("ohlcv", ticker, config.start, config.end)
    if hit:
        return cached
    raw = _krx_call(
        stock.get_market_ohlcv,
        config,
        config.start,
        config.end,
        ticker,
        adjusted=True,
    )
    frame = normalize_ohlcv(raw, "ohlcv")
    frame.insert(0, "market", market)
    frame.insert(0, "ticker", ticker)
    cache.write("ohlcv", ticker, config.start, config.end, frame)
    return frame


def fetch_trading_value(
    ticker: str,
    market: str,
    side: str,
    config: PipelineConfig,
    cache: Cache,
) -> pd.DataFrame:
    """투자자별 거래대금 매수/매도 데이터를 조회한다."""
    endpoint = f"trading_{side}"
    hit, cached = cache.read(endpoint, ticker, config.start, config.end)
    if hit:
        return cached
    raw = _krx_call(
        stock.get_market_trading_value_by_date,
        config,
        config.start,
        config.end,
        ticker,
        on=side,
    )
    frame = normalize_trading_value(raw, endpoint)
    frame.insert(0, "market", market)
    frame.insert(0, "ticker", ticker)
    cache.write(endpoint, ticker, config.start, config.end, frame)
    return frame


def fetch_market_cap(ticker: str, market: str, config: PipelineConfig, cache: Cache) -> pd.DataFrame:
    """일자별 시가총액을 조회하고 표준 컬럼으로 정규화한다."""
    hit, cached = cache.read("market_cap", ticker, config.start, config.end)
    if hit:
        return cached
    raw = _krx_call(stock.get_market_cap, config, config.start, config.end, ticker)
    frame = normalize_market_cap(raw, "market_cap")
    frame.insert(0, "market", market)
    frame.insert(0, "ticker", ticker)
    cache.write("market_cap", ticker, config.start, config.end, frame)
    return frame


def fetch_index_ohlcv(market: str, config: PipelineConfig, cache: Cache) -> pd.DataFrame:
    """시장 지수 OHLCV를 조회한다."""
    index_ticker = config.index_tickers[market]
    hit, cached = cache.read("index_ohlcv", market, config.start, config.end)
    if hit:
        return cached
    raw = _krx_call(stock.get_index_ohlcv, config, config.start, config.end, index_ticker)
    frame = normalize_index_ohlcv(raw, "index_ohlcv")
    frame.insert(0, "market", market)
    cache.write("index_ohlcv", market, config.start, config.end, frame)
    return frame


def fetch_market_trading_value(
    market: str,
    side: str,
    config: PipelineConfig,
    cache: Cache,
) -> pd.DataFrame:
    """시장 전체 투자자별 거래대금 매수/매도 데이터를 조회한다."""
    endpoint = f"market_trading_{side}"
    hit, cached = cache.read(endpoint, market, config.start, config.end)
    if hit:
        return cached
    raw = _krx_call(
        stock.get_market_trading_value_by_date,
        config,
        config.start,
        config.end,
        market,
        on=side,
    )
    frame = normalize_trading_value(raw, endpoint)
    frame.insert(0, "market", market)
    cache.write(endpoint, market, config.start, config.end, frame)
    return frame


def normalize_ohlcv(frame: pd.DataFrame, endpoint: str) -> pd.DataFrame:
    """pykrx OHLCV 컬럼을 영문 표준 컬럼으로 변환한다."""
    mapping = {"시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume"}
    out = _normalize_date_frame(frame, mapping, list(mapping), endpoint)
    optional = {"거래대금": "value", "등락률": "change"}
    return _add_optional_columns(frame, out, optional)


def normalize_trading_value(frame: pd.DataFrame, endpoint: str) -> pd.DataFrame:
    """투자자별 거래대금 컬럼을 영문 표준 컬럼으로 변환한다."""
    mapping = {
        "기관합계": "inst",
        "기타법인": "other_corp",
        "개인": "retail",
        "외국인합계": "foreign",
        "전체": "total",
    }
    return _normalize_date_frame(frame, mapping, list(mapping), endpoint)


def normalize_market_cap(frame: pd.DataFrame, endpoint: str) -> pd.DataFrame:
    """시가총액 반환 컬럼을 영문 표준 컬럼으로 변환한다."""
    mapping = {"시가총액": "mktcap", "거래량": "volume", "거래대금": "value", "상장주식수": "shares"}
    out = _normalize_date_frame(frame, mapping, list(mapping), endpoint)
    return _add_optional_columns(frame, out, {"종가": "close"})


def normalize_index_ohlcv(frame: pd.DataFrame, endpoint: str) -> pd.DataFrame:
    """지수 OHLCV 반환 컬럼을 영문 표준 컬럼으로 변환한다."""
    mapping = {"시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume"}
    out = _normalize_date_frame(frame, mapping, list(mapping), endpoint)
    out = _add_optional_columns(frame, out, {"거래대금": "value", "상장시가총액": "index_mktcap"})
    if "value" not in out.columns:
        out["value"] = np.nan
    return out


def _fetch_market_tables(config: PipelineConfig, cache: Cache) -> dict[str, pd.DataFrame]:
    """시장 단위 지수와 투자자별 거래대금 테이블을 가져온다."""
    index, buy, sell = [], [], []
    for market in config.markets:
        index.append(fetch_index_ohlcv(market, config, cache))
        buy.append(fetch_market_trading_value(market, "매수", config, cache))
        sell.append(fetch_market_trading_value(market, "매도", config, cache))
    return {
        "index_ohlcv": _concat(index),
        "market_trading_buy": _concat(buy),
        "market_trading_sell": _concat(sell),
    }


def _fetch_ticker_tables(
    tickers: pd.DataFrame,
    config: PipelineConfig,
    cache: Cache,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """샘플 종목별 OHLCV, 수급, 시가총액 테이블을 가져온다."""
    kept, ohlcv, buy, sell, cap = [], [], [], [], []
    total = len(tickers)
    for pos, row in enumerate(tickers.itertuples(index=False), start=1):
        LOGGER.info("Fetch ticker %d/%d %s", pos, total, row.ticker)
        price = fetch_ohlcv(row.ticker, row.market, config, cache)
        if len(price) < config.min_ticker_observations:
            LOGGER.warning("Skip %s: observations=%d", row.ticker, len(price))
            continue
        kept.append({"ticker": row.ticker, "market": row.market, "name": row.name})
        ohlcv.append(price)
        buy.append(fetch_trading_value(row.ticker, row.market, "매수", config, cache))
        sell.append(fetch_trading_value(row.ticker, row.market, "매도", config, cache))
        cap.append(fetch_market_cap(row.ticker, row.market, config, cache))
    return pd.DataFrame(kept), {
        "ohlcv": _concat(ohlcv),
        "trading_buy": _concat(buy),
        "trading_sell": _concat(sell),
        "market_cap": _concat(cap),
    }


def _find_anchor_day(year: int, market: str, config: PipelineConfig, cache: Cache) -> str:
    """각 연도 1월 초에서 상장 리스트가 존재하는 첫 영업일을 찾는다."""
    for offset in range(config.anchor_search_days):
        day = config.anchor_start_day + offset
        date = f"{year}01{day:02d}"
        try:
            tickers = fetch_ticker_list(date, market, config, cache)
        except Exception as exc:
            LOGGER.debug("Anchor probe failed %s %s: %s", market, date, exc)
            continue
        if tickers:
            return date
    raise RuntimeError(f"{year} {market} 1월 첫 영업일을 찾지 못했습니다.")


def _krx_call(func: Callable[..., T], config: PipelineConfig, *args: object, **kwargs: object) -> T:
    """rate limit과 지수 백오프 재시도를 적용해 pykrx 함수를 호출한다."""
    last_exc: Exception | None = None
    for attempt in range(config.krx_max_retries):
        time.sleep(config.krx_rate_limit_seconds)
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # pragma: no cover - network dependent
            last_exc = exc
            if attempt + 1 < config.krx_max_retries:
                time.sleep(config.krx_backoff_seconds * (2**attempt))
    assert last_exc is not None
    raise last_exc


def _normalize_date_frame(
    frame: pd.DataFrame,
    mapping: dict[str, str],
    required: Iterable[str],
    endpoint: str,
) -> pd.DataFrame:
    """날짜 index를 date 컬럼으로 내리고 필수 컬럼을 표준명으로 바꾼다."""
    if frame.empty:
        return pd.DataFrame(columns=["date", *mapping.values()])
    missing = [col for col in required if col not in frame.columns]
    if missing:
        raise ValueError(f"{endpoint} expected columns missing={missing}; raw columns={list(frame.columns)}")
    out = frame.rename(columns=mapping).loc[:, list(mapping.values())].copy()
    out.insert(0, "date", pd.to_datetime(frame.index))
    return out.reset_index(drop=True)


def _add_optional_columns(
    raw: pd.DataFrame,
    out: pd.DataFrame,
    optional: dict[str, str],
) -> pd.DataFrame:
    """존재하는 선택 컬럼만 표준명으로 추가한다."""
    for kor, eng in optional.items():
        if kor in raw.columns and eng not in out.columns:
            out[eng] = raw[kor].to_numpy()
    return out


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """빈 리스트를 안전하게 처리하는 concat helper."""
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
