from __future__ import annotations

import dataclasses

from kokalim.config import NewsBotConfig
from kokalim.core.pipeline import run_cycle


def _cfg(tmp_path) -> NewsBotConfig:
    """네트워크·LLM 없이 도는 오프라인 키워드 설정."""
    return dataclasses.replace(
        NewsBotConfig(),
        urgency_mode="keyword",
        scope="all",
        platform="console",
        state_path=str(tmp_path / "state.json"),
    )


def test_offline_cycle_surfaces_urgent_and_dedups(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    result = run_cycle(cfg, offline=True, dry_run=True)
    assert result.fetched == 12          # 픽스처 전체(scope=all)
    assert result.clusters < result.fresh  # 중복이 실제로 합쳐졌다
    assert result.urgent >= 2            # 삼성 유상증자 + 네이버 상한가
    assert result.sent is True
    assert "긴급" in result.digest


def test_second_cycle_suppresses_already_seen(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    first = run_cycle(cfg, offline=True, dry_run=True)
    assert first.fresh > 0
    second = run_cycle(cfg, offline=True, dry_run=True)
    assert second.fresh == 0             # seen-set이 재발송을 막는다
    assert second.urgent == 0
    assert second.sent is False


def test_watchlist_scope_filters_out_unrelated(tmp_path) -> None:
    cfg = dataclasses.replace(_cfg(tmp_path), scope="watchlist")
    result = run_cycle(cfg, offline=True, dry_run=True)
    # 스팩/시황 등 관심종목 무관 기사는 제외되므로 전체보다 적게 수집
    assert result.fetched < 12
    assert result.urgent >= 1
