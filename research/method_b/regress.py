"""PanelOLS regression specifications for Method B."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from linearmodels.panel import PanelOLS

from .config import PipelineConfig

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Spec:
    """회귀식 구성 정보."""

    name: str
    outcomes: tuple[str, ...]
    terms: tuple[str, ...]
    controls: tuple[str, ...]


def run_all_regressions(panel: pd.DataFrame, config: PipelineConfig) -> pd.DataFrame:
    """B1/B2/F1/극단수익률 강건성 회귀를 실행하고 long table로 반환한다."""
    data = _prepare_regression_data(panel)
    rows: list[dict[str, object]] = []
    specs = _specs(config)
    for subgroup, subgroup_data in _subgroups(data):
        for spec in specs:
            for outcome in spec.outcomes:
                rows.extend(_run_one(subgroup_data, spec, outcome, subgroup))
    return pd.DataFrame(rows)


def _prepare_regression_data(panel: pd.DataFrame) -> pd.DataFrame:
    """센터링 share와 상호작용 변수를 만든다."""
    data = panel.reset_index() if isinstance(panel.index, pd.MultiIndex) else panel.copy()
    data["share_c"] = data["retail_share"] - data["retail_share"].mean(skipna=True)
    data["share_mkt_c"] = data["retail_share_mkt"] - data["retail_share_mkt"].mean(skipna=True)
    data["att_abnvol_x_share_c"] = data["att_abnvol"] * data["share_c"]
    data["att_abnvol_x_share_mkt_c"] = data["att_abnvol"] * data["share_mkt_c"]
    data["ext_up_x_share_c"] = data["ext_up"] * data["share_c"]
    data["ext_down_x_share_c"] = data["ext_down"] * data["share_c"]
    return data.set_index(["ticker", "date"]).sort_index()


def _specs(config: PipelineConfig) -> list[Spec]:
    """실행할 회귀 specification 목록을 반환한다."""
    controls = ("log_mktcap", "mom_20", "vol_20", "retail_share")
    return [
        Spec("B1", config.outcomes, ("att_abnvol", "att_abnvol_x_share_c"), controls),
        Spec("B2", config.outcomes, ("att_abnvol", "share_mkt_c", "att_abnvol_x_share_mkt_c"), controls),
        Spec("F1", ("netbuy_fwd_0_2",), ("att_abnvol", "att_abnvol_x_share_c"), controls),
        Spec("R1_extret", config.outcomes, ("ext_up", "ext_up_x_share_c", "ext_down", "ext_down_x_share_c"), controls),
    ]


def _subgroups(data: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    """ALL/KOSPI/KOSDAQ 서브그룹 데이터를 만든다."""
    reset = data.reset_index()
    groups = [("ALL", reset)]
    for market in ("KOSPI", "KOSDAQ"):
        groups.append((market, reset.loc[reset["market"].eq(market)]))
    return [(name, group.set_index(["ticker", "date"]).sort_index()) for name, group in groups if not group.empty]


def _run_one(data: pd.DataFrame, spec: Spec, outcome: str, subgroup: str) -> list[dict[str, object]]:
    """단일 specification/outcome/subgroup 회귀를 실행한다."""
    columns = [outcome, *spec.terms, *spec.controls]
    model_data = data.loc[:, [col for col in columns if col in data.columns]].replace([np.inf, -np.inf], np.nan).dropna()
    if model_data.empty or model_data.index.get_level_values(0).nunique() < 2:
        return []
    formula = f"{outcome} ~ {' + '.join([*spec.terms, *spec.controls])} + EntityEffects + TimeEffects"
    try:
        result = PanelOLS.from_formula(formula, data=model_data, drop_absorbed=True, check_rank=False).fit(
            cov_type="clustered",
            cluster_entity=True,
            cluster_time=True,
        )
    except Exception as exc:
        LOGGER.warning("Regression failed spec=%s outcome=%s subgroup=%s: %s", spec.name, outcome, subgroup, exc)
        return []
    return [_result_row(result, spec.name, outcome, subgroup, term) for term in spec.terms if term in result.params.index]


def _result_row(result: object, spec: str, outcome: str, subgroup: str, term: str) -> dict[str, object]:
    """PanelOLS 결과에서 한 term의 요약 행을 만든다."""
    coef = float(result.params[term])
    std_error = float(result.std_errors[term])
    return {
        "spec": spec,
        "outcome": outcome,
        "subgroup": subgroup,
        "term": term,
        "coef": coef,
        "std_error": std_error,
        "cluster_t": float(result.tstats[term]),
        "p": float(result.pvalues[term]),
        "n_obs": int(result.nobs),
        "r2": float(result.rsquared),
    }
