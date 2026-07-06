from __future__ import annotations

from method_b.config import PipelineConfig
from method_b.panel import build_panel
from method_b.regress import run_all_regressions
from method_b.synth import generate


def _coef(results, spec: str, outcome: str, term: str):
    row = results[
        (results["spec"] == spec)
        & (results["subgroup"] == "ALL")
        & (results["outcome"] == outcome)
        & (results["term"] == term)
    ]
    assert not row.empty
    return float(row.iloc[0]["coef"]), float(row.iloc[0]["p"])


def test_synth_pipeline_recovers_seeded_effects() -> None:
    cfg = PipelineConfig(seed=42)
    raw = generate(seed=42)
    panel, _ = build_panel(raw, cfg)
    results = run_all_regressions(panel, cfg)
    short_coef, short_p = _coef(results, "B1", "car_1_5", "att_abnvol_x_share_c")
    mid_coef, mid_p = _coef(results, "B1", "car_3_20", "att_abnvol_x_share_c")
    flow_coef, flow_p = _coef(results, "F1", "netbuy_fwd_0_2", "att_abnvol_x_share_c")
    assert short_coef > 0 and short_p < 0.05
    assert mid_coef < 0 and mid_p < 0.05
    assert flow_coef > 0 and flow_p < 0.05
