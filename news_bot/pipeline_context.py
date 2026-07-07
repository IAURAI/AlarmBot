"""연관 컨텍스트 추적 사이클: 유닛별 수집→관련성 게이트→병렬 평가→상황 갱신→알림."""

from __future__ import annotations

import html
import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import NewsBotConfig
from .context import WatchItem, build_watch_items, item_matches, load_expansion
from .dedup import cluster_articles, normalize_title
from .investigate import Assessment, assess_items
from .llm import get_backend
from .notify import get_notifier
from .situation import load_situations, save_situations, update_situation
from .sources import drop_stale, gather_pool
from .state import SeenStore, load_state, save_state
from .supabase_sink import get_sink, rows_from_context_alerts
from .urgency import score_clusters

LOGGER = logging.getLogger(__name__)
_STANCE = {"positive": "▲", "negative": "▼", "neutral": "·"}


@dataclass
class ContextResult:
    """컨텍스트 사이클 실행 결과."""

    fetched: int
    items_with_news: int
    alerts: int
    digest: str
    sent: bool


def run_context_cycle(
    config: NewsBotConfig,
    *,
    offline: bool = False,
    dry_run: bool = False,
    now: datetime | None = None,
) -> ContextResult:
    """연관 컨텍스트 한 사이클을 실행한다."""
    now = now or datetime.now(timezone.utc)
    state = load_state(config.state_path)
    seen = SeenStore(state.get("seen"), config.seen_ttl_hours)
    seen.prune(now)
    situations = load_situations(config.situation_path)

    items = build_watch_items(expansion=load_expansion(config.expansion_path))
    articles = gather_pool(config, items, offline)
    articles = drop_stale(articles, config.max_article_age_hours, now.timestamp())

    fresh_by_item = _partition(items, articles, seen, config)
    LOGGER.info("수집 %d건, 새 뉴스 유닛 %d개", len(articles), len(fresh_by_item))

    backend = None if offline else get_backend(config)
    assessments = assess_items(fresh_by_item, situations, config, backend)
    alerts: list[tuple[WatchItem, Assessment]] = []
    for item, assessment in assessments.items():
        update_situation(situations, item.key, assessment, now)
        if assessment.alert:
            alerts.append((item, assessment))
    alerts = _dedupe_alerts(alerts)

    sink = None if (offline or dry_run) else get_sink(config)
    notifier = get_notifier(config, dry_run)
    digest = format_context_digest(alerts, notifier.uses_html) if alerts else ""
    sent = False
    if alerts:
        if sink is not None:
            sink.insert_rows(rows_from_context_alerts(alerts))
            LOGGER.info("Supabase에 컨텍스트 %d건 적재 → Vercel이 발송", len(alerts))
        else:
            notifier.send(digest)
        sent = True

    _mark_seen(fresh_by_item, seen, now)
    seen.prune(now)
    save_state(config.state_path, seen, now)
    save_situations(config.situation_path, situations)

    return ContextResult(len(articles), len(fresh_by_item), len(alerts), digest, sent)


def _partition(items, articles, seen, config) -> dict[WatchItem, list]:
    """각 감시 유닛에 매칭되는 신규 기사를 모아 클러스터링한다."""
    fresh: dict[WatchItem, list] = {}
    for item in items:
        matched = [a for a in articles if item_matches(item, a) and not seen.contains(a.title)]
        if not matched:
            continue
        clusters = cluster_articles(matched, config.dedup_threshold)
        score_clusters(clusters, config)
        fresh[item] = clusters
    return fresh


def _dedupe_alerts(alerts: list[tuple[WatchItem, Assessment]]) -> list[tuple[WatchItem, Assessment]]:
    """같은 종목에서 같은 사건이 여러 유닛으로 중복 알림되는 것을 합친다."""
    best: dict[tuple[str, str], tuple[WatchItem, Assessment]] = {}
    for item, assessment in alerts:
        key = (item.company, normalize_title(assessment.changed))
        if key not in best or assessment.importance > best[key][1].importance:
            best[key] = (item, assessment)
    ranked = sorted(best.values(), key=lambda x: -x[1].importance)
    return ranked


def _mark_seen(fresh_by_item, seen, now) -> None:
    """이번에 처리한 모든 신규 기사를 seen-set에 기록한다."""
    for clusters in fresh_by_item.values():
        for cluster in clusters:
            for article in cluster.articles:
                seen.add(article.title, now)


def format_context_digest(alerts: list[tuple[WatchItem, Assessment]], use_html: bool) -> str:
    """알림을 종목별로 묶어 다이제스트로 만든다."""
    groups: "OrderedDict[str, list]" = OrderedDict()
    for item, assessment in alerts:
        groups.setdefault(item.company, []).append((item, assessment))
    lines = ["🔔 관심종목 동향"]
    for company, entries in groups.items():
        head = f"<b>[{company}]</b>" if use_html else f"[{company}]"
        lines.append("")
        lines.append(head)
        for item, assessment in entries:
            label = "직접" if item.kind == "self" else f"{item.kind}·{item.name}"
            mark = _STANCE.get(assessment.stance, "·")
            changed = html.escape(assessment.changed) if use_html else assessment.changed
            lines.append(f"• ({label} {mark}) {changed} — 중요도 {assessment.importance}")
    return "\n".join(lines)


def build_situation_report(situations: dict) -> str:
    """추적 중인 상황을 주기 요약으로 만든다(--report)."""
    lines = ["📋 추적 상황 요약"]
    for key in sorted(situations):
        entry = situations[key]
        summary = entry.get("summary")
        if not summary:
            continue
        updated = (entry.get("updated") or "")[:10]
        lines.append(f"• {key}: {summary} ({updated})")
    return "\n".join(lines) if len(lines) > 1 else "📋 추적 중인 상황이 아직 없습니다."
