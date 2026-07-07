"""한 사이클 오케스트레이션: 수집→중복제거→긴급판정→요약→발송→상태저장."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from kokalim.config import NewsBotConfig
from kokalim.core.dedup import cluster_articles
from kokalim.notify import format_digest, get_notifier
from kokalim.core.ingest.sources import drop_stale, gather
from kokalim.state import SeenStore, load_state, save_state
from kokalim.core.summarize import make_summarizer, summarize_clusters
from kokalim.core.triage.urgency import classify, make_judge

LOGGER = logging.getLogger(__name__)


@dataclass
class CycleResult:
    """한 사이클 실행 결과 요약."""

    fetched: int
    fresh: int
    clusters: int
    urgent: int
    digest: str
    sent: bool


def run_cycle(
    config: NewsBotConfig,
    *,
    offline: bool = False,
    dry_run: bool = False,
    now: datetime | None = None,
) -> CycleResult:
    """뉴스 한 사이클을 실행하고 결과를 반환한다."""
    now = now or datetime.now(timezone.utc)
    state = load_state(config.state_path)
    seen = SeenStore(state.get("seen"), config.seen_ttl_hours)
    seen.prune(now)

    articles = gather(config, offline)
    articles = drop_stale(articles, config.max_article_age_hours, now.timestamp())
    fresh = [a for a in articles if not seen.contains(a.title)]
    LOGGER.info("수집 %d건 중 신규 %d건", len(articles), len(fresh))

    clusters = cluster_articles(fresh, config.dedup_threshold)
    classify(clusters, config, None if offline else make_judge(config))
    urgent = [c for c in clusters if c.urgent]
    urgent.sort(key=lambda c: c.keyword_score, reverse=True)

    summarize_clusters(urgent, None if offline else make_summarizer(config))

    sent = False
    notifier = get_notifier(config, dry_run)
    digest = format_digest(urgent, config, notifier.uses_html) if urgent else ""
    if urgent:
        notifier.send(digest)
        sent = True

    for article in fresh:
        seen.add(article.title, now)
    seen.prune(now)
    save_state(config.state_path, seen, now)

    return CycleResult(
        fetched=len(articles),
        fresh=len(fresh),
        clusters=len(clusters),
        urgent=len(urgent),
        digest=digest,
        sent=sent,
    )
