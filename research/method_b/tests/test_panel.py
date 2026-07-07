from __future__ import annotations

from method_b.config import PipelineConfig
from method_b.panel import build_panel
from method_b.synth import generate


def test_build_panel_schema_from_synth() -> None:
    cfg = PipelineConfig(seed=7)
    raw = generate(n_stocks=8, n_days=220, seed=7)
    panel, meta = build_panel(raw, cfg)
    expected = {
        "ret",
        "AR",
        "car_1_1",
        "car_1_5",
        "car_3_20",
        "car_21_60",
        "retail_share",
        "retail_share_mkt",
        "att_abnvol",
        "netbuy_fwd_0_2",
    }
    assert expected.issubset(panel.columns)
    assert panel.index.names == ["ticker", "date"]
    assert meta["n_tickers"] == 8
