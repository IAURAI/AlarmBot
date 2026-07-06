from __future__ import annotations

from news_bot.dedup import cluster_articles, jaccard, normalize_title, token_set
from news_bot.sources import load_fixture


def test_normalize_strips_punctuation() -> None:
    assert normalize_title("[속보] 삼성전자, 유상증자!") == "속보 삼성전자 유상증자"


def test_jaccard_bounds() -> None:
    assert jaccard(set(), {"a"}) == 0.0
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_duplicate_wire_story_collapses_to_one_cluster() -> None:
    articles = load_fixture("news_bot/fixtures/sample_articles.json")
    clusters = cluster_articles(articles, threshold=0.5)
    samsung = [c for c in clusters if "삼성전자" in c.title and "유상증자" in c.combined_text]
    assert len(samsung) == 1
    assert samsung[0].source_count >= 5  # 5개 매체가 하나로


def test_distinct_stories_stay_separate() -> None:
    articles = load_fixture("news_bot/fixtures/sample_articles.json")
    clusters = cluster_articles(articles, threshold=0.5)
    # 삼성 유상증자와 SK하이닉스 실적은 다른 사건 → 다른 클러스터
    titles = [c.title for c in clusters]
    assert any("삼성전자" in t for t in titles)
    assert any("SK하이닉스" in t for t in titles)
    assert len(clusters) >= 6
