"""Pipeline configuration for Method B."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PipelineConfig:
    """Method B에서 사용하는 모든 파라미터와 상수."""

    start: str = "20160104"
    end: str = "20260630"
    markets: tuple[str, ...] = ("KOSPI", "KOSDAQ")
    sample_tickers: int = 200
    seed: int = 42
    cache_dir: Path = Path("method_b/data/cache")
    out_dir: Path = Path("output")

    krx_rate_limit_seconds: float = 0.4
    krx_max_retries: int = 3
    krx_backoff_seconds: float = 0.8
    min_ticker_observations: int = 120

    anchor_start_year: int = 2016
    anchor_end_year: int = 2026
    anchor_start_day: int = 2
    anchor_search_days: int = 10

    attention_baseline_start: int = 120
    attention_baseline_end: int = 21
    attention_min_periods: int = 60
    abn_vol_multiple: float = 4.0
    extret_abs_floor: float = 0.05
    extret_quantile: float = 0.95
    news_count_floor: int = 3

    retail_window: int = 20
    retail_min_periods: int = 10
    market_model_start: int = 120
    market_model_end: int = 21
    market_model_min_periods: int = 60
    car_min_fraction: float = 0.8

    winsor_low: float = 0.01
    winsor_high: float = 0.99
    control_window: int = 20
    control_min_periods: int = 10

    outcomes: tuple[str, ...] = (
        "car_1_1",
        "car_1_5",
        "car_3_20",
        "car_21_60",
    )
    car_windows: dict[str, tuple[int, int]] = field(
        default_factory=lambda: {
            "car_1_1": (1, 1),
            "car_1_5": (1, 5),
            "car_3_20": (3, 20),
            "car_21_60": (21, 60),
        }
    )
    index_tickers: dict[str, str] = field(
        default_factory=lambda: {"KOSPI": "1001", "KOSDAQ": "2001"}
    )

    synth_n_stocks: int = 60
    synth_n_days: int = 750
    synth_event_prob: float = 0.015
    synth_k1: float = 0.008
    synth_k2: float = 0.006
    synth_k3: float = 0.001

    @property
    def attention_window(self) -> int:
        """관심 이벤트 베이스라인의 영업일 수."""
        return self.attention_baseline_start - self.attention_baseline_end + 1

    @property
    def attention_shift(self) -> int:
        """오늘 기준 베이스라인 종료일까지의 시프트."""
        return self.attention_baseline_end

    @property
    def market_model_window(self) -> int:
        """마켓 모델 추정 베이스라인의 영업일 수."""
        return self.market_model_start - self.market_model_end + 1

    @property
    def market_model_shift(self) -> int:
        """오늘 기준 마켓 모델 추정 종료일까지의 시프트."""
        return self.market_model_end
