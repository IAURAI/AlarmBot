from __future__ import annotations

from method_b.config import PipelineConfig
from method_b.panel import build_panel
from method_b.regress import run_all_regressions
from method_b.synth import generate


def test_regression_outputs_core_terms() -> None:
    cfg = PipelineConfig(seed=11)
    raw = generate(n_stocks=20, n_days=360, seed=11)
    panel, _ = build_panel(raw, cfg)
    results = run_all_regressions(panel, cfg)
    b1 = results[(results["spec"] == "B1") & (results["subgroup"] == "ALL")]
    assert {"att_abnvol", "att_abnvol_x_share_c"}.issubset(set(b1["term"]))
    assert "car_1_5" in set(b1["outcome"])
