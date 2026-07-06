"""긴급도 판정: 키워드 하드 게이트 + (선택) LLM 최종 판정 하이브리드."""

from __future__ import annotations

import logging

from .config import NewsBotConfig
from .llm import get_backend
from .models import Cluster

LOGGER = logging.getLogger(__name__)

_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "urgent": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "urgent", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


def keyword_score(text: str, weights: dict[str, float]) -> tuple[float, list[str]]:
    """텍스트에 등장한 키워드의 가중치 합과 매칭 목록을 반환한다."""
    matched = [kw for kw in weights if kw in text]
    score = float(sum(weights[kw] for kw in matched))
    return score, matched


def score_clusters(clusters: list[Cluster], config: NewsBotConfig) -> None:
    """각 클러스터에 키워드 점수와 매칭 키워드를 채운다."""
    weights = config.keyword_weights
    for cluster in clusters:
        score, matched = keyword_score(cluster.combined_text, weights)
        cluster.keyword_score = score
        cluster.matched_keywords = matched


class LLMJudge:
    """애매한 클러스터의 긴급 여부를 LLM 백엔드로 배치 판정한다."""

    def __init__(self, backend) -> None:
        """LLM 백엔드(codex/anthropic)를 받는다."""
        self._backend = backend

    def judge(self, clusters: list[Cluster]) -> list[tuple[bool, str]]:
        """클러스터 목록의 (긴급여부, 사유)를 순서대로 반환한다."""
        if not clusters:
            return []
        listing = "\n".join(
            f"[{i}] {c.title} — {c.representative.summary[:160]}"
            for i, c in enumerate(clusters)
        )
        prompt = (
            "다음은 한국 증시 관련 뉴스 헤드라인이다. 각 항목이 개인 투자자에게 "
            "'즉시 알림을 보낼 만큼 긴급하고 주가에 실질적 영향을 줄 뉴스'인지 판정하라. "
            "단순 시황·전망·홍보성 기사는 urgent=false. "
            "각 index에 대해 urgent(boolean)와 한 줄 reason을 JSON으로 답하라.\n\n"
            f"{listing}"
        )
        data = self._backend.complete_json(prompt, _VERDICT_SCHEMA)
        verdicts = {v["index"]: v for v in data.get("verdicts", [])}
        out: list[tuple[bool, str]] = []
        for i in range(len(clusters)):
            v = verdicts.get(i, {})
            out.append((bool(v.get("urgent", False)), str(v.get("reason", ""))))
        return out


def make_judge(config: NewsBotConfig) -> LLMJudge | None:
    """LLM 판정기를 생성한다. 백엔드가 없으면(keyword 모드/자격없음) None."""
    backend = get_backend(config)
    return LLMJudge(backend) if backend else None


def classify(clusters: list[Cluster], config: NewsBotConfig, judge: LLMJudge | None) -> None:
    """키워드 하드 게이트로 후보를 거른 뒤 LLM이 알림 여부를 최종 결정한다."""
    score_clusters(clusters, config)
    always = set(config.always_alert_keywords)

    # 1) 하드 필터: 키워드 점수가 게이트 미만이면 후보에서 제외.
    gated = [c for c in clusters if c.keyword_score >= config.noise_low]

    # 2) 초긴급 키워드는 LLM 없이 즉시 알림(기본 비활성).
    for cluster in gated:
        hits = [k for k in always if k in cluster.combined_text]
        if hits:
            cluster.urgent = True
            cluster.reason = "즉시알림: " + ", ".join(hits)
    pending = [c for c in gated if not c.urgent]

    # 3) LLM이 없으면(keyword 모드) 강한 키워드 신호만 알림.
    if judge is None:
        _keyword_fallback(pending, config, "키워드")
        return

    # 4) 게이트 통과분을 LLM이 판정 — 키워드가 강해도 LLM 반려면 알림 안 함.
    try:
        verdicts = judge.judge(pending)
    except Exception as exc:  # pragma: no cover - 백엔드/네트워크 의존
        LOGGER.warning("LLM 판정 실패: %s — 키워드 폴백", exc)
        _keyword_fallback(pending, config, "키워드(LLM실패)")
        return
    for cluster, (is_urgent, reason) in zip(pending, verdicts):
        cluster.urgent = bool(is_urgent)
        cluster.reason = ("LLM: " + reason) if is_urgent else reason


def _keyword_fallback(clusters: list[Cluster], config: NewsBotConfig, label: str) -> None:
    """LLM 없이 강한 키워드 신호(urgent_high 이상)만 긴급 처리."""
    for cluster in clusters:
        if cluster.keyword_score >= config.urgent_high:
            cluster.urgent = True
            cluster.reason = f"{label}: " + ", ".join(cluster.matched_keywords)
