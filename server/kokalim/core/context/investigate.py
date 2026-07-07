"""연관 항목 병렬 조사·평가: 감시 유닛별로 중요도·상황 변화를 판정한다.

백엔드 없음(오프라인/키 없음) → 결정적 휴리스틱. 백엔드 있음 → 유닛별 LLM 판정을
ThreadPoolExecutor로 병렬 실행. 각 유닛은 자기 이전 상황을 컨텍스트로 받아 델타를 판단.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from kokalim.config import NewsBotConfig
from kokalim.core.context.graph import WatchItem
from kokalim.core.models import Cluster

LOGGER = logging.getLogger(__name__)

_ASSESS_SCHEMA = {
    "type": "object",
    "properties": {
        "importance": {"type": "integer"},
        "material": {"type": "boolean"},
        "changed": {"type": "string"},
        "summary": {"type": "string"},
        "stance": {"type": "string", "enum": ["positive", "negative", "neutral"]},
        "alert": {"type": "boolean"},
    },
    "required": ["importance", "material", "changed", "summary", "stance", "alert"],
    "additionalProperties": False,
}


@dataclass
class Assessment:
    """한 감시 유닛에 대한 평가 결과."""

    importance: int      # 0~10 대상 종목에의 중요도
    material: bool       # 직전 상황 대비 실질적 변화인가
    changed: str         # 무엇이 바뀌었는지 한 줄
    summary: str         # 현재 상황 갱신 요약 한 줄
    stance: str          # positive / negative / neutral
    alert: bool          # 지금 알릴 가치가 있는가


def _top_cluster(clusters: list[Cluster]) -> Cluster:
    """키워드 점수·매체 수 기준 대표 클러스터."""
    return max(clusters, key=lambda c: (c.keyword_score, c.source_count))


def _heuristic(item: WatchItem, clusters: list[Cluster], config: NewsBotConfig) -> Assessment:
    """LLM 없이 매체 corroboration + 긴급 키워드로 중요도를 추정한다."""
    top = _top_cluster(clusters)
    importance = int(min(10, round(top.keyword_score + top.source_count)))
    material = top.keyword_score > 0 or top.source_count >= 2
    alert = material and importance >= config.context_alert_min
    return Assessment(importance, material, top.title, top.title, "neutral", alert)


def _llm_assess(backend, item: WatchItem, clusters: list[Cluster], prior: dict) -> Assessment:
    """이전 상황 + 새 헤드라인을 주고 LLM이 중요도·변화·알림 여부를 판정."""
    headlines = "\n".join(
        f"- {c.title}" for c in sorted(clusters, key=lambda c: -c.keyword_score)[:6]
    )
    prior_summary = prior.get("summary") or "없음"
    prompt = (
        f"대상 종목: {item.company}\n"
        f"연관 항목: {item.name} ({item.relation})\n"
        f"이전까지 파악된 상황: {prior_summary}\n"
        f"새로 들어온 관련 헤드라인:\n{headlines}\n\n"
        f"이 새 뉴스가 대상 종목 '{item.company}'에 얼마나 중요한지 판단하라. "
        "importance(0~10 정수), material(직전 상황 대비 실질적 변화 여부), "
        "changed(무엇이 바뀌었는지 한 줄), summary(현재 상황 갱신 한 줄), "
        "stance(positive/negative/neutral), alert(지금 개인 투자자에게 알릴 가치가 있는가)를 JSON으로."
    )
    data = backend.complete_json(prompt, _ASSESS_SCHEMA, max_tokens=512)
    fallback_title = clusters[0].title if clusters else ""
    return Assessment(
        importance=int(data.get("importance", 0)),
        material=bool(data.get("material", False)),
        changed=str(data.get("changed", fallback_title)),
        summary=str(data.get("summary", "")),
        stance=str(data.get("stance", "neutral")),
        alert=bool(data.get("alert", False)),
    )


def assess_items(
    fresh_by_item: dict[WatchItem, list[Cluster]],
    situations: dict,
    config: NewsBotConfig,
    backend,
) -> dict[WatchItem, Assessment]:
    """새 뉴스가 들어온 유닛들을 병렬로 평가한다(백엔드 없으면 휴리스틱)."""
    if not fresh_by_item:
        return {}

    def work(pair: tuple[WatchItem, list[Cluster]]) -> tuple[WatchItem, Assessment]:
        item, clusters = pair
        if backend is None:
            return item, _heuristic(item, clusters, config)
        try:
            return item, _llm_assess(backend, item, clusters, situations.get(item.key, {}))
        except Exception as exc:  # pragma: no cover - 백엔드/네트워크 의존
            LOGGER.warning("유닛 평가 실패 %s: %s — 휴리스틱 폴백", item.key, exc)
            return item, _heuristic(item, clusters, config)

    pairs = list(fresh_by_item.items())
    workers = min(config.context_max_workers, len(pairs))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return dict(executor.map(work, pairs))


_EXPAND_SCHEMA = {
    "type": "object",
    "properties": {
        "related": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "relation": {"type": "string"}},
                "required": ["name", "relation"],
                "additionalProperties": False,
            },
        },
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "keywords": {"type": "array", "items": {"type": "string"}}},
                "required": ["name", "keywords"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["related", "themes"],
    "additionalProperties": False,
}


def discover_expansion(backend, companies: list[str]) -> dict:
    """LLM으로 종목별 추가 연관 엔티티·테마를 발견한다(하이브리드 확장)."""
    expansion: dict = {}
    for company in companies:
        prompt = (
            f"'{company}'의 주가에 영향을 줄 수 있는 '연관 엔티티'(공급사·고객·경쟁사 등)와 "
            "'거시/산업 테마'를 추가로 제안하라. 이미 뻔한 것도 좋지만 놓치기 쉬운 연관을 우선. "
            "related는 {name, relation}, themes는 {name, keywords[]} 형식의 JSON으로."
        )
        try:
            data = backend.complete_json(prompt, _EXPAND_SCHEMA, max_tokens=800)
        except Exception as exc:  # pragma: no cover - 네트워크 의존
            LOGGER.warning("확장 실패 %s: %s", company, exc)
            continue
        expansion[company] = {
            "related": {r["name"]: r["relation"] for r in data.get("related", [])},
            "themes": {t["name"]: list(t["keywords"]) for t in data.get("themes", [])},
        }
    return expansion
