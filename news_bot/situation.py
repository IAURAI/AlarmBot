"""상황 상태(situation state): 감시 유닛별로 시간에 따라 갱신되는 국면과 타임라인."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def load_situations(path: str | Path) -> dict:
    """상황 상태 JSON을 읽는다. 없으면 빈 dict."""
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_situations(path: str | Path, situations: dict) -> None:
    """상황 상태를 저장한다."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(situations, ensure_ascii=False, indent=2), encoding="utf-8")


def update_situation(situations: dict, key: str, assessment, now: datetime) -> None:
    """평가 결과로 해당 유닛의 상황을 갱신한다(실질 변화면 타임라인 추가)."""
    entry = situations.setdefault(
        key, {"summary": "", "stance": "neutral", "updated": None, "timeline": []}
    )
    if assessment.summary:
        entry["summary"] = assessment.summary
    entry["stance"] = assessment.stance
    entry["updated"] = now.isoformat()
    if assessment.material:
        entry["timeline"].append(
            {"at": now.isoformat(), "change": assessment.changed, "importance": assessment.importance}
        )
        entry["timeline"] = entry["timeline"][-20:]  # 최근 20건만 보관
