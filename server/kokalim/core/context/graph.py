"""컨텍스트 그래프: 관심종목 → 자기 자신 + 연관 엔티티 + 테마.

각 항목은 자기 키워드를 가진 '감시 유닛'이고, 뉴스 수집·평가가 유닛별로 병렬로 돈다.
기본은 수기 시드(결정적). config.context_expand로 --expand-graph를 돌리면 LLM이 찾은
연관 항목이 캐시에 저장되고, 여기에 병합되어 하이브리드로 동작한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from kokalim.core.models import Article


@dataclass(frozen=True)
class WatchItem:
    """하나의 감시 유닛(대상 종목의 self/related/theme 항목)."""

    company: str          # 대상 종목
    kind: str             # "self" | "related" | "theme"
    name: str             # 엔티티명 또는 테마명
    relation: str         # 관계 설명(중요도 판단 컨텍스트)
    keywords: tuple[str, ...]

    @property
    def key(self) -> str:
        """상황 상태 저장 키."""
        return f"{self.company}::{self.name}"


# 수기 시드 그래프 — related: {엔티티: 관계}, themes: {테마명: [키워드…]}
CONTEXT_GRAPH: dict[str, dict[str, object]] = {
    "삼성전자": {
        "related": {
            "SK하이닉스": "경쟁(메모리)", "TSMC": "경쟁(파운드리)",
            "엔비디아": "고객(HBM)", "ASML": "장비 공급", "마이크론": "경쟁(메모리)",
            "삼성전기": "계열(부품)",
        },
        "themes": {
            "HBM": ["HBM", "고대역폭"],
            "D램 가격": ["D램", "디램", "DRAM", "메모리 가격"],
            "감산": ["감산"],
            "미중 반도체 규제": ["수출규제", "수출 통제", "상무부", "반도체 규제", "대중 제재"],
            "AI 반도체 수요": ["AI 반도체", "AI 칩", "데이터센터"],
        },
    },
    "삼성전기": {
        "related": {"삼성전자": "고객(계열)", "LG이노텍": "경쟁", "애플": "고객(MLCC·카메라)", "무라타": "경쟁(MLCC)"},
        "themes": {
            "MLCC": ["MLCC", "적층세라믹콘덴서", "적층세라믹"],
            "카메라모듈": ["카메라모듈", "카메라 모듈"],
            "전장": ["전장", "전장용"],
        },
    },
    "SK하이닉스": {
        "related": {"삼성전자": "경쟁(메모리)", "엔비디아": "고객(HBM)", "마이크론": "경쟁(메모리)"},
        "themes": {"HBM": ["HBM", "고대역폭"], "D램 가격": ["D램", "메모리 가격"],
                   "미중 반도체 규제": ["수출규제", "상무부", "반도체 규제"]},
    },
    "현대차": {
        "related": {"기아": "계열", "테슬라": "경쟁(EV)", "LG에너지솔루션": "배터리 공급", "도요타": "경쟁"},
        "themes": {"전기차 수요": ["전기차", "EV 수요", "전동화"],
                   "미국 IRA": ["IRA", "인플레이션 감축법", "보조금"], "관세": ["관세"]},
    },
    "네이버": {
        "related": {"카카오": "경쟁", "쿠팡": "경쟁(커머스)", "구글": "경쟁"},
        "themes": {"AI 검색": ["AI 검색", "생성형 AI"], "커머스": ["커머스", "이커머스"],
                   "플랫폼 규제": ["플랫폼 규제", "공정위"]},
    },
    "카카오": {
        "related": {"네이버": "경쟁", "토스": "경쟁(핀테크)"},
        "themes": {"AI": ["AI"], "플랫폼 규제": ["플랫폼 규제", "공정위"], "카카오뱅크": ["카카오뱅크"]},
    },
}


def _merge(graph: dict[str, dict[str, object]], expansion: dict | None) -> dict[str, dict[str, dict]]:
    """시드 그래프에 LLM 확장 항목을 병합한다."""
    merged: dict[str, dict[str, dict]] = {
        c: {"related": dict(s.get("related", {})), "themes": dict(s.get("themes", {}))}  # type: ignore[arg-type]
        for c, s in graph.items()
    }
    for company, spec in (expansion or {}).items():
        slot = merged.setdefault(company, {"related": {}, "themes": {}})
        slot["related"].update(spec.get("related", {}))
        slot["themes"].update(spec.get("themes", {}))
    return merged


def build_watch_items(
    graph: dict[str, dict[str, object]] | None = None,
    expansion: dict | None = None,
) -> list[WatchItem]:
    """그래프(+확장)를 평탄한 감시 유닛 리스트로 전개한다."""
    merged = _merge(graph or CONTEXT_GRAPH, expansion)
    items: list[WatchItem] = []
    for company, spec in merged.items():
        items.append(WatchItem(company, "self", company, "대상 종목", (company,)))
        for entity, relation in spec["related"].items():
            items.append(WatchItem(company, "related", entity, str(relation), (entity,)))
        for theme, kws in spec["themes"].items():
            items.append(WatchItem(company, "theme", theme, "테마", tuple(kws)))
    return items


def item_matches(item: WatchItem, article: Article) -> bool:
    """기사가 이 감시 유닛의 키워드에 해당하는지(관련성 하드 필터)."""
    text = article.text
    return any(kw in text for kw in item.keywords)


def load_expansion(path: str | Path) -> dict | None:
    """LLM 확장 캐시를 읽는다. 없으면 None."""
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save_expansion(path: str | Path, expansion: dict) -> None:
    """LLM 확장 캐시를 저장한다."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(expansion, ensure_ascii=False, indent=2), encoding="utf-8")
