"""긴급 클러스터 요약: 추출식 폴백 + (선택) LLM 한 줄 요약."""

from __future__ import annotations

import logging

from kokalim.config import NewsBotConfig
from kokalim.core.triage.llm import get_backend
from kokalim.core.models import Cluster

LOGGER = logging.getLogger(__name__)

_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summaries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "summary": {"type": "string"},
                },
                "required": ["index", "summary"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["summaries"],
    "additionalProperties": False,
}


def extractive_summary(cluster: Cluster) -> str:
    """LLM 없이 대표 제목 기반 한 줄 요약을 만든다."""
    return cluster.title.strip()


class LLMSummarizer:
    """긴급 클러스터를 LLM 백엔드로 배치 요약한다."""

    def __init__(self, backend) -> None:
        """LLM 백엔드(codex/anthropic)를 받는다."""
        self._backend = backend

    def summarize(self, clusters: list[Cluster]) -> list[str]:
        """각 클러스터의 한 줄 요약을 순서대로 반환한다."""
        if not clusters:
            return []
        listing = "\n".join(
            f"[{i}] {c.title} — {c.representative.summary[:200]}"
            for i, c in enumerate(clusters)
        )
        prompt = (
            "다음 각 뉴스 사건을 개인 투자자용으로 핵심만 한 문장(40자 내외)으로 요약하라. "
            "종목명과 무슨 일이 일어났는지를 명확히. 과장·추측 금지.\n\n"
            f"{listing}"
        )
        data = self._backend.complete_json(prompt, _SUMMARY_SCHEMA)
        by_index = {s["index"]: s["summary"] for s in data.get("summaries", [])}
        return [by_index.get(i) or extractive_summary(c) for i, c in enumerate(clusters)]


def make_summarizer(config: NewsBotConfig) -> LLMSummarizer | None:
    """LLM 요약기를 생성한다. 백엔드가 없으면 None."""
    backend = get_backend(config)
    return LLMSummarizer(backend) if backend else None


def summarize_clusters(clusters: list[Cluster], summarizer: LLMSummarizer | None) -> None:
    """긴급 클러스터에 요약을 채운다(요약기 없거나 실패 시 추출식)."""
    if summarizer is None:
        for cluster in clusters:
            cluster.summary = extractive_summary(cluster)
        return
    try:
        summaries = summarizer.summarize(clusters)
    except Exception as exc:  # pragma: no cover - 백엔드/네트워크 의존
        LOGGER.warning("LLM 요약 실패: %s — 추출식 폴백", exc)
        for cluster in clusters:
            cluster.summary = extractive_summary(cluster)
        return
    for cluster, summary in zip(clusters, summaries):
        cluster.summary = summary
