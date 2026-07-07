from __future__ import annotations

import dataclasses

from kokalim.config import NewsBotConfig
from kokalim.core.context.graph import build_watch_items, item_matches
from kokalim.core.models import Article
from kokalim.core.pipeline_context import run_context_cycle
from kokalim.core.context.situation import load_situations


def _cfg(tmp_path) -> NewsBotConfig:
    """오프라인·휴리스틱(키 없음) 컨텍스트 설정."""
    return dataclasses.replace(
        NewsBotConfig(),
        urgency_mode="keyword",  # make_client → None → 결정적 휴리스틱
        platform="console",
        state_path=str(tmp_path / "state.json"),
        situation_path=str(tmp_path / "situations.json"),
    )


def test_build_watch_items_includes_self_related_theme() -> None:
    items = build_watch_items()
    keys = {it.key for it in items}
    assert "삼성전자::삼성전자" in keys        # self
    assert "삼성전자::엔비디아" in keys        # related
    assert "삼성전자::HBM" in keys            # theme
    kinds = {it.kind for it in items}
    assert kinds == {"self", "related", "theme"}


def test_item_matches_by_keyword() -> None:
    items = {it.name: it for it in build_watch_items() if it.company == "삼성전자"}
    art = Article(source="x", title="엔비디아 HBM 공급 재편", link="")
    assert item_matches(items["엔비디아"], art)
    assert item_matches(items["HBM"], art)
    assert not item_matches(items["감산"], art)


def test_context_cycle_surfaces_direct_and_related(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    result = run_context_cycle(cfg, offline=True, dry_run=True)
    assert result.fetched == 15
    assert result.items_with_news >= 3
    assert result.alerts >= 2               # 삼성 직접(유상증자) + 연관(엔비디아/HBM 등)
    assert "삼성전자" in result.digest
    # 단일 매체·무키워드 소식(D램)은 알림에 오르지 않는다
    assert "D램" not in result.digest


def test_situation_state_persisted_and_second_run_quiet(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    first = run_context_cycle(cfg, offline=True, dry_run=True)
    assert first.alerts >= 2

    situations = load_situations(cfg.situation_path)
    assert "삼성전자::삼성전자" in situations
    assert situations["삼성전자::삼성전자"]["timeline"]     # 타임라인에 변화 기록됨

    second = run_context_cycle(cfg, offline=True, dry_run=True)
    assert second.items_with_news == 0     # 모두 seen 처리 → 조용
    assert second.alerts == 0
    assert second.sent is False


def test_related_story_alerts_under_target_company(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    result = run_context_cycle(cfg, offline=True, dry_run=True)
    # 엔비디아 HBM 재편 소식이 삼성전자 동향에 잡혀야 한다
    assert "엔비디아" in result.digest or "HBM" in result.digest or "SK하이닉스" in result.digest
