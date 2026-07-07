"""중복 기사 클러스터링: 같은 통신사 기사가 여러 매체에 복제되는 문제를 해결."""

from __future__ import annotations

import re

from kokalim.core.models import Article, Cluster

_PUNCT = re.compile(r"[^\w가-힣]+")
_WS = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """제목을 정규화한다(기호 제거, 공백 축약, 소문자화)."""
    t = _PUNCT.sub(" ", title)
    t = _WS.sub(" ", t).strip().lower()
    return t


def token_set(title: str) -> set[str]:
    """정규화 제목의 문자 바이그램 집합(한국어 근접 중복 탐지에 강건)."""
    condensed = normalize_title(title).replace(" ", "")
    return {condensed[i:i + 2] for i in range(len(condensed) - 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    """두 집합의 자카드 유사도."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def overlap(a: set[str], b: set[str]) -> float:
    """겹침 계수(교집합/작은 쪽 크기) — 길이 차에 강건해 근접 중복에 적합."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def cluster_articles(articles: list[Article], threshold: float) -> list[Cluster]:
    """제목 유사도로 기사를 그리디 클러스터링한다.

    새 기사는 각 클러스터의 '모든 멤버 시그니처 중 최대 유사도'와 비교한다.
    시그니처를 합집합으로 키우면 union이 커지면서 유사도가 희석돼 같은 사건이
    쪼개지므로, 멤버별 시그니처를 각각 보관해 비교한다.
    """
    clusters: list[Cluster] = []
    member_sigs: list[list[set[str]]] = []
    for article in articles:
        sig = token_set(article.title)
        best_idx, best_sim = -1, threshold
        for idx, sigs in enumerate(member_sigs):
            sim = max(overlap(sig, s) for s in sigs)
            if sim >= best_sim:
                best_idx, best_sim = idx, sim
        if best_idx >= 0:
            clusters[best_idx].articles.append(article)
            member_sigs[best_idx].append(sig)
        else:
            clusters.append(Cluster(articles=[article]))
            member_sigs.append([sig])
    return clusters
