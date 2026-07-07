from __future__ import annotations

from kokalim.config import NewsBotConfig
from kokalim.core.dedup import cluster_articles
from kokalim.core.ingest.sources import load_fixture
from kokalim.core.triage.urgency import classify, keyword_score, score_clusters


def test_high_keyword_scores_above_urgent_threshold() -> None:
    cfg = NewsBotConfig()
    score, matched = keyword_score("삼성전자 유상증자 결정 주가 급락", cfg.keyword_weights)
    assert score >= cfg.urgent_high
    assert "유상증자" in matched and "급락" in matched


def test_medium_keyword_lands_in_borderline_band() -> None:
    cfg = NewsBotConfig()
    score, _ = keyword_score("SK하이닉스 3분기 실적 컨센서스 상회", cfg.keyword_weights)
    assert cfg.noise_low <= score < cfg.urgent_high


def test_plain_market_recap_is_noise() -> None:
    cfg = NewsBotConfig()
    score, _ = keyword_score("오늘의 증시 마감 시황 코스피 강보합", cfg.keyword_weights)
    assert score < cfg.noise_low


def test_keyword_only_classify_marks_strong_signals() -> None:
    cfg = NewsBotConfig(urgency_mode="keyword")
    clusters = cluster_articles(load_fixture(cfg.fixture_path), cfg.dedup_threshold)
    classify(clusters, cfg, judge=None)  # LLM 없이 규칙만
    urgent_titles = [c.title for c in clusters if c.urgent]
    assert any("유상증자" in t for t in urgent_titles)      # 삼성 유상증자
    assert any("상한가" in t for t in urgent_titles)        # 네이버 상한가
    # 단순 시황/실적은 긴급이 아니어야 함
    assert not any("강보합" in t for t in urgent_titles)


def test_score_clusters_populates_fields() -> None:
    cfg = NewsBotConfig()
    clusters = cluster_articles(load_fixture(cfg.fixture_path), cfg.dedup_threshold)
    score_clusters(clusters, cfg)
    assert all(isinstance(c.keyword_score, float) for c in clusters)


class _StubJudge:
    """LLM 판정기 대역 — 본 클러스터를 기록하고 지정한 결정만 승인한다."""

    def __init__(self, approve_substrings: list[str]) -> None:
        self.approve = approve_substrings
        self.seen: list = []

    def judge(self, clusters: list):
        self.seen = list(clusters)
        return [
            (any(s in c.title for s in self.approve), "관련 중대 발표" if any(s in c.title for s in self.approve) else "단순 시황")
            for c in clusters
        ]


def test_hybrid_gate_then_llm_has_final_say() -> None:
    cfg = NewsBotConfig(urgency_mode="hybrid")
    clusters = cluster_articles(load_fixture(cfg.fixture_path), cfg.dedup_threshold)
    stub = _StubJudge(approve_substrings=["SK하이닉스"])  # LLM은 SK만 승인, 삼성은 반려
    classify(clusters, cfg, stub)

    # 1) 하드 게이트: 키워드 점수 미달(잡음)은 LLM이 아예 보지 않는다
    assert all(c.keyword_score >= cfg.noise_low for c in stub.seen)
    assert any("카카오" in c.title for c in clusters)          # 존재하지만
    assert not any("카카오" in c.title for c in stub.seen)     # 게이트에서 제외

    # 2) LLM 최종 결정 — 키워드가 강해도 LLM이 반려하면 알림 안 함
    samsung = next(c for c in clusters if "삼성전자" in c.title)
    assert samsung.keyword_score >= cfg.urgent_high            # 키워드는 강하지만
    assert samsung.urgent is False                            # LLM 반려 → 미알림

    # 3) LLM이 승인한 게이트 통과분만 알림
    sk = next(c for c in clusters if "SK하이닉스" in c.title)
    assert sk.urgent is True
