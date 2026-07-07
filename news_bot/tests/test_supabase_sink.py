from __future__ import annotations

import dataclasses
import types

from news_bot import supabase_sink as ss
from news_bot.config import NewsBotConfig
from news_bot.investigate import Assessment
from news_bot.models import Article, Cluster
from news_bot.pipeline import run_cycle


def _cluster(title, summary="", reason="", score=3.0, matched=("삼성전자",),
             link="http://x/1", sources=("yna",)) -> Cluster:
    """테스트용 클러스터."""
    arts = [
        Article(source=s, title=title, link=link, summary=summary, matched_terms=list(matched))
        for s in sources
    ]
    return Cluster(articles=arts, keyword_score=score, reason=reason, summary=summary)


def test_rows_from_clusters_shape() -> None:
    cfg = NewsBotConfig()
    c = _cluster("삼성전자 유상증자 결정", summary="삼성전자 유상증자 8조", reason="희석 우려",
                 score=3.0, sources=("yna", "mk"))
    rows = ss.rows_from_clusters([c], cfg)
    assert len(rows) == 1
    row = rows[0]
    assert row["ticker"] == "삼성전자"
    assert row["kind"] == "urgent"
    assert row["urgency"] == "high"            # 3.0 >= high_weight
    assert row["url"] == "http://x/1"
    assert row["source_count"] == 2            # 서로 다른 매체 2곳
    assert row["headline"].startswith("삼성전자 유상증자")  # summary 우선
    assert row["reason"] == "희석 우려"
    assert isinstance(row["score"], float)


def test_ticker_detected_from_body_when_no_matched_terms() -> None:
    c = _cluster("반도체 업황 반등", summary="SK하이닉스 신고가", matched=(), score=1.0)
    row = ss.rows_from_clusters([c], NewsBotConfig())[0]
    assert row["ticker"] == "SK하이닉스"        # 본문 스캔으로 탐지
    assert row["urgency"] == "medium"          # 1.0 >= medium_weight


def test_rows_from_context_alerts_shape() -> None:
    item = types.SimpleNamespace(company="삼성전기")
    assessment = Assessment(importance=8, material=True, changed="MLCC 증설 발표",
                            summary="전장용 확대", stance="positive", alert=True)
    row = ss.rows_from_context_alerts([(item, assessment)])[0]
    assert row["ticker"] == "삼성전기"
    assert row["kind"] == "context"
    assert row["urgency"] == "high"            # 8 >= 7
    assert row["headline"] == "MLCC 증설 발표"
    assert row["reason"] == "전장용 확대"
    assert row["score"] == 8.0
    assert row["url"] is None


def test_get_sink_disabled_by_default() -> None:
    assert ss.get_sink(NewsBotConfig()) is None   # 기본 sink=notifier


def test_get_sink_none_without_creds(monkeypatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    cfg = dataclasses.replace(NewsBotConfig(), sink="supabase")
    assert ss.get_sink(cfg) is None               # 자격증명 없으면 폴백


def test_get_sink_builds_endpoint_with_creds(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://demo.supabase.co/")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
    cfg = dataclasses.replace(NewsBotConfig(), sink="supabase")
    sink = ss.get_sink(cfg)
    assert isinstance(sink, ss.SupabaseSink)
    assert sink._endpoint == "https://demo.supabase.co/rest/v1/alerts"


def test_insert_rows_posts_expected_request(monkeypatch) -> None:
    captured: dict = {}

    class FakeResp:
        def raise_for_status(self):
            pass

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return FakeResp()

    monkeypatch.setattr(ss.requests, "post", fake_post)
    ss.SupabaseSink("https://demo.supabase.co", "svc", "alerts").insert_rows([{"headline": "x"}])
    assert captured["url"] == "https://demo.supabase.co/rest/v1/alerts"
    assert captured["headers"]["apikey"] == "svc"
    assert captured["headers"]["Authorization"] == "Bearer svc"
    assert captured["headers"]["Prefer"] == "return=minimal"
    assert captured["json"] == [{"headline": "x"}]


def test_insert_rows_empty_is_noop(monkeypatch) -> None:
    called = {"n": 0}
    monkeypatch.setattr(ss.requests, "post", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    ss.SupabaseSink("u", "k", "alerts").insert_rows([])
    assert called["n"] == 0


def test_upsert_supported_posts(monkeypatch) -> None:
    captured: dict = {}

    class FakeResp:
        def raise_for_status(self):
            pass

    patched: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json)
        return FakeResp()

    def fake_patch(url, headers=None, json=None, timeout=None):
        patched.update(url=url, json=json)
        return FakeResp()

    monkeypatch.setattr(ss.requests, "post", fake_post)
    monkeypatch.setattr(ss.requests, "patch", fake_patch)
    ss.SupabaseSink("https://demo.supabase.co", "svc", "alerts").upsert_supported(["삼성전자", "네이버"])
    assert captured["url"].endswith("/rest/v1/supported_tickers")
    assert "merge-duplicates" in captured["headers"]["Prefer"]
    assert captured["json"] == [
        {"ticker": "삼성전자", "active": True},
        {"ticker": "네이버", "active": True},
    ]
    # config에서 빠진 종목 비활성화(not.in)
    assert "supported_tickers?ticker=not.in.(" in patched["url"]
    assert patched["json"] == {"active": False}


def test_sync_supported_noop_without_creds(monkeypatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)

    def boom(*a, **k):
        raise AssertionError("자격증명 없는데 네트워크 호출됨")

    monkeypatch.setattr(ss.requests, "post", boom)
    ss.sync_supported(dataclasses.replace(NewsBotConfig(), sink="supabase"))  # 조용히 무동작


def test_offline_pipeline_never_calls_supabase(tmp_path, monkeypatch) -> None:
    """sink=supabase여도 offline/dry-run이면 네트워크를 타지 않고 콘솔로 폴백한다."""
    def boom(*a, **k):
        raise AssertionError("오프라인인데 Supabase로 POST가 나갔다")

    monkeypatch.setattr(ss.requests, "post", boom)
    cfg = dataclasses.replace(
        NewsBotConfig(), sink="supabase", urgency_mode="keyword", scope="all",
        state_path=str(tmp_path / "state.json"),
    )
    result = run_cycle(cfg, offline=True, dry_run=True)
    assert result.sent is True     # 콘솔 폴백으로 발송
