"""Supabase(PostgREST)로 알림을 구조화 행으로 적재하는 sink.

로컬 봇이 판정한 긴급/컨텍스트 알림을 Supabase `alerts` 테이블에 INSERT 한다.
그 INSERT를 트리거로 Supabase Database Webhook이 Vercel `/api/notify`를 호출 →
Vercel이 텔레그램으로 발송한다. 따라서 sink가 활성이면 **로컬은 직접 발송하지 않는다**
(발송 주체를 클라우드로 이관 → 중복 발송 방지).

자격증명(`SUPABASE_URL` / `SUPABASE_SERVICE_KEY`)이 없으면 `get_sink`가 None을 돌려주고
파이프라인은 기존 콘솔/텔레그램 발송으로 조용히 폴백한다(기존 자격증명 철학과 동일).

보안: SUPABASE_SERVICE_KEY(service_role)는 RLS를 우회하는 쓰기 키다. **로컬 `.env`에만**
두고 절대 커밋하지 않는다(코드는 값을 로깅하지 않음).
"""

from __future__ import annotations

import logging
import os
from urllib.parse import quote

import requests

from .config import NewsBotConfig
from .models import Cluster

LOGGER = logging.getLogger(__name__)

_HEADLINE_MAX = 500
_REASON_MAX = 1000


def _ticker_of(cluster: Cluster, watchlist: tuple[str, ...]) -> str | None:
    """클러스터에서 대표 관심종목명을 찾는다(매칭 term 우선, 없으면 본문 스캔)."""
    for article in cluster.articles:
        for term in article.matched_terms:
            if term in watchlist:
                return term
    text = cluster.combined_text
    for name in watchlist:
        if name in text:
            return name
    return None


def _urgency_label(score: float, config: NewsBotConfig) -> str:
    """키워드 점수를 상/중/하 라벨로."""
    if score >= config.high_weight:
        return "high"
    if score >= config.medium_weight:
        return "medium"
    return "low"


def _importance_label(importance: int) -> str:
    """컨텍스트 중요도(0~10)를 상/중/하 라벨로."""
    if importance >= 7:
        return "high"
    if importance >= 4:
        return "medium"
    return "low"


def rows_from_clusters(clusters: list[Cluster], config: NewsBotConfig) -> list[dict]:
    """기본 모드 긴급 클러스터를 Supabase 행으로 변환한다."""
    rows: list[dict] = []
    for cluster in clusters:
        rows.append(
            {
                "ticker": _ticker_of(cluster, config.watchlist),
                "headline": (cluster.summary or cluster.title)[:_HEADLINE_MAX],
                "reason": (cluster.reason or "")[:_REASON_MAX],
                "url": cluster.representative.link or None,
                "urgency": _urgency_label(cluster.keyword_score, config),
                "score": round(float(cluster.keyword_score), 3),
                "source_count": cluster.source_count,
                "kind": "urgent",
            }
        )
    return rows


def rows_from_context_alerts(alerts: list[tuple]) -> list[dict]:
    """컨텍스트 모드 알림((WatchItem, Assessment))을 Supabase 행으로 변환한다."""
    rows: list[dict] = []
    for item, assessment in alerts:
        rows.append(
            {
                "ticker": item.company,
                "headline": assessment.changed[:_HEADLINE_MAX],
                "reason": assessment.summary[:_REASON_MAX],
                "url": None,
                "urgency": _importance_label(assessment.importance),
                "score": float(assessment.importance),
                "source_count": 1,
                "kind": "context",
            }
        )
    return rows


class SupabaseSink:
    """PostgREST로 `alerts` 테이블에 행을 삽입한다."""

    def __init__(self, url: str, key: str, table: str) -> None:
        """Supabase URL/서비스키/테이블명을 설정한다."""
        self._base = url.rstrip("/")
        self._endpoint = f"{self._base}/rest/v1/{table}"
        self._headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }

    def insert_rows(self, rows: list[dict]) -> None:
        """행 목록을 한 번의 요청으로 삽입한다(빈 목록이면 무시)."""
        if not rows:
            return
        resp = requests.post(self._endpoint, headers=self._headers, json=rows, timeout=10)
        resp.raise_for_status()

    def upsert_supported(self, tickers) -> None:
        """지원 종목 동기화: 현재 목록=active로 upsert하고 빠진 종목은 비활성화(단일 소스 유지)."""
        tickers = list(tickers)
        if not tickers:
            return
        endpoint = f"{self._base}/rest/v1/supported_tickers"
        headers = {**self._headers, "Prefer": "return=minimal,resolution=merge-duplicates"}
        rows = [{"ticker": t, "active": True} for t in tickers]
        resp = requests.post(endpoint, headers=headers, json=rows, timeout=10)
        resp.raise_for_status()
        # config에서 빠진 종목은 비활성화. 값만 URL 인코딩(in.() 구조·콤마는 리터럴).
        vals = ",".join(quote(t, safe="") for t in tickers)
        resp2 = requests.patch(f"{endpoint}?ticker=not.in.({vals})", headers=self._headers,
                               json={"active": False}, timeout=10)
        resp2.raise_for_status()


def get_sink(config: NewsBotConfig):
    """설정이 supabase이고 자격증명이 있으면 SupabaseSink를, 아니면 None을 반환한다."""
    if config.sink != "supabase":
        return None
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        LOGGER.warning("SUPABASE_URL/SUPABASE_SERVICE_KEY 없음 — Supabase sink 비활성(발송기 폴백)")
        return None
    return SupabaseSink(url, key, config.supabase_table)


def sync_supported(config: NewsBotConfig) -> None:
    """지원 종목을 Supabase에 동기화한다(sink 활성 시). 자격증명/설정 없으면 무동작."""
    sink = get_sink(config)
    if sink is None:
        return
    sink.upsert_supported(config.watchlist)
