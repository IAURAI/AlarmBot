"""Core data structures for the news bot pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Article:
    """수집된 개별 기사."""

    source: str
    title: str
    link: str
    summary: str = ""
    published: str | None = None
    published_ts: float | None = None
    matched_terms: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        """제목+요약 결합 텍스트(키워드 매칭용)."""
        return f"{self.title} {self.summary}".strip()


@dataclass
class Cluster:
    """중복 제거로 묶인 하나의 뉴스 사건."""

    articles: list[Article]
    keyword_score: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)
    urgent: bool = False
    reason: str = ""
    summary: str = ""

    @property
    def representative(self) -> Article:
        """대표 기사(가장 긴 요약을 가진 기사)."""
        return max(self.articles, key=lambda a: len(a.summary))

    @property
    def title(self) -> str:
        """대표 제목."""
        return self.representative.title

    @property
    def source_count(self) -> int:
        """이 사건을 보도한 매체 수."""
        return len({a.source for a in self.articles})

    @property
    def combined_text(self) -> str:
        """클러스터 전체 텍스트(제목+요약 합본)."""
        return " ".join(a.text for a in self.articles)
