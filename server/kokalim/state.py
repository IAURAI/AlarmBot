"""State persistence: 이미 발송한 기사 seen-set과 마지막 실행 시각."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kokalim.core.dedup import normalize_title


def _key(title: str) -> str:
    """정규화 제목의 짧은 해시."""
    norm = normalize_title(title)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


class SeenStore:
    """정규화 제목 기준으로 재발송을 막는 rolling seen-set."""

    def __init__(self, entries: dict[str, str] | None = None, ttl_hours: int = 24) -> None:
        """기존 엔트리(해시→ISO시각)와 TTL로 초기화한다."""
        self._entries: dict[str, str] = dict(entries or {})
        self._ttl = timedelta(hours=ttl_hours)

    def contains(self, title: str) -> bool:
        """해당 제목을 최근에 이미 봤는지."""
        return _key(title) in self._entries

    def add(self, title: str, now: datetime) -> None:
        """제목을 seen-set에 기록한다."""
        self._entries[_key(title)] = now.isoformat()

    def prune(self, now: datetime) -> None:
        """TTL이 지난 엔트리를 제거한다."""
        cutoff = now - self._ttl
        self._entries = {
            k: ts for k, ts in self._entries.items()
            if _safe_parse(ts) >= cutoff
        }

    def as_dict(self) -> dict[str, str]:
        """직렬화용 dict."""
        return dict(self._entries)


def _safe_parse(ts: str) -> datetime:
    """ISO 문자열을 파싱하되 실패 시 aware epoch을 반환한다."""
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_state(path: str | Path) -> dict:
    """상태 JSON을 읽는다. 없으면 빈 상태."""
    p = Path(path)
    if not p.exists():
        return {"seen": {}, "last_run": None}
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(path: str | Path, seen: SeenStore, last_run: datetime) -> None:
    """seen-set과 마지막 실행 시각을 저장한다."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"seen": seen.as_dict(), "last_run": last_run.isoformat()}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
