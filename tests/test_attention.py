from __future__ import annotations

import pandas as pd

from method_b.attention import add_attention_flags
from method_b.config import PipelineConfig


def test_attention_flags_abnormal_volume_and_extreme_return() -> None:
    cfg = PipelineConfig()
    dates = pd.bdate_range("2020-01-01", periods=150)
    frame = pd.DataFrame(
        {
            "ticker": "000010",
            "date": dates,
            "value_gross": 100.0,
            "ret": 0.001,
        }
    )
    frame.loc[130, "value_gross"] = 500.0
    frame.loc[131, "ret"] = -0.06
    out = add_attention_flags(frame, cfg)
    assert out.loc[130, "att_abnvol"] == 1
    assert out.loc[131, "att_extret"] == 1
    assert out.loc[131, "ext_down"] == 1
    assert out.loc[131, "ext_up"] == 0
