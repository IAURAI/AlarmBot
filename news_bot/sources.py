"""뉴스 수집: RSS 피드 + 네이버 뉴스 검색 API + 오프라인 픽스처."""

from __future__ import annotations

import calendar
import html
import json
import logging
import os
import re
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from .config import NewsBotConfig
from .models import Article

LOGGER = logging.getLogger(__name__)
_TAG = re.compile(r"<[^>]+>")
_NAVER_URL = "https://openapi.naver.com/v1/search/news.json"
# 일부 매체(한국경제 등)는 기본 봇 UA를 403으로 막으므로 브라우저 UA로 요청.
_RSS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


def strip_html(text: str) -> str:
    """HTML 태그를 제거하고 엔티티를 디코드한다."""
    return html.unescape(_TAG.sub("", text or "")).strip()


def _naver_pubdate_ts(pubdate: str | None) -> float | None:
    """네이버 pubDate(RFC822)를 UTC epoch로 파싱한다(실패 시 None). 신선도 필터용."""
    if not pubdate:
        return None
    try:
        return parsedate_to_datetime(pubdate).timestamp()
    except (ValueError, TypeError):
        return None


def fetch_rss(feeds: tuple[str, ...]) -> list[Article]:
    """RSS 피드에서 기사를 수집한다(피드별 실패는 건너뛴다)."""
    import feedparser  # 지연 임포트 — 오프라인 테스트에서 불필요

    articles: list[Article] = []
    for url in feeds:
        try:
            parsed = feedparser.parse(url, agent=_RSS_UA)
        except Exception as exc:  # pragma: no cover - 네트워크 의존
            LOGGER.warning("RSS 실패 %s: %s", url, exc)
            continue
        source = strip_html(getattr(parsed.feed, "title", url)) or url
        for entry in parsed.entries:
            pp = entry.get("published_parsed") or entry.get("updated_parsed")
            articles.append(
                Article(
                    source=source,
                    title=strip_html(entry.get("title", "")),
                    link=entry.get("link", ""),
                    summary=strip_html(entry.get("summary", "")),
                    published=entry.get("published"),
                    published_ts=calendar.timegm(pp) if pp else None,
                )
            )
    LOGGER.info("RSS 수집 %d건", len(articles))
    return articles


def fetch_naver(queries: tuple[str, ...], display: int) -> list[Article]:
    """네이버 뉴스 검색 API로 관심 키워드 기사를 수집한다."""
    cid, secret = os.getenv("NAVER_CLIENT_ID"), os.getenv("NAVER_CLIENT_SECRET")
    if not cid or not secret:
        LOGGER.info("네이버 자격증명 없음 — 네이버 수집 건너뜀")
        return []
    headers = {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret}
    articles: list[Article] = []
    for query in queries:
        try:
            resp = requests.get(
                _NAVER_URL,
                headers=headers,
                params={"query": query, "display": display, "sort": "date"},
                timeout=10,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except Exception as exc:  # pragma: no cover - 네트워크 의존
            LOGGER.warning("네이버 실패 query=%s: %s", query, exc)
            continue
        for item in items:
            articles.append(
                Article(
                    source="naver:" + query,
                    title=strip_html(item.get("title", "")),
                    link=item.get("originallink") or item.get("link", ""),
                    summary=strip_html(item.get("description", "")),
                    published=item.get("pubDate"),
                    published_ts=_naver_pubdate_ts(item.get("pubDate")),
                    matched_terms=[query],
                )
            )
    LOGGER.info("네이버 수집 %d건", len(articles))
    return articles


def load_fixture(path: str | Path) -> list[Article]:
    """오프라인 테스트용 픽스처 JSON을 Article 리스트로 읽는다."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        Article(
            source=r["source"],
            title=r["title"],
            link=r.get("link", ""),
            summary=r.get("summary", ""),
            published=r.get("published"),
        )
        for r in rows
    ]


def apply_scope(articles: list[Article], config: NewsBotConfig) -> list[Article]:
    """범위 설정에 따라 관심종목 키워드 필터를 적용한다."""
    if config.scope in ("market", "all"):
        return articles
    kept: list[Article] = []
    for article in articles:
        hits = [term for term in config.watchlist if term in article.text]
        if hits or article.matched_terms:  # 네이버 검색으로 이미 매칭된 기사는 유지
            article.matched_terms = sorted(set(article.matched_terms) | set(hits))
            kept.append(article)
    LOGGER.info("범위 필터(watchlist) 후 %d건", len(kept))
    return kept


def drop_stale(articles: list[Article], max_age_hours: int, now_ts: float) -> list[Article]:
    """발행 시각이 오래된 기사를 제외한다(속보만; 옛 기사 재발송 방지). 시각 없으면 유지."""
    if max_age_hours <= 0:
        return articles
    cutoff = now_ts - max_age_hours * 3600
    fresh = [a for a in articles if a.published_ts is None or a.published_ts >= cutoff]
    dropped = len(articles) - len(fresh)
    if dropped:
        LOGGER.info("오래된 기사 %d건 제외 (>%dh)", dropped, max_age_hours)
    return fresh


def gather(config: NewsBotConfig, offline: bool) -> list[Article]:
    """설정에 따라 기사를 수집한다(오프라인이면 픽스처)."""
    if offline:
        return apply_scope(load_fixture(config.fixture_path), config)
    articles = fetch_rss(config.rss_feeds)
    if config.use_naver:
        articles += fetch_naver(config.watchlist, config.naver_display)
    return apply_scope(articles, config)


def gather_pool(config: NewsBotConfig, items, offline: bool) -> list[Article]:
    """컨텍스트 모드용 전체 풀 — 관심종목 스코프 필터 없이 넓게 수집한다."""
    if offline:
        return load_fixture(config.context_fixture_path)
    articles = fetch_rss(config.rss_feeds)
    if config.use_naver:
        keywords = sorted({kw for item in items for kw in item.keywords})
        queries = tuple(keywords[: config.naver_max_queries])
        articles += fetch_naver(queries, config.naver_display)
    return articles
