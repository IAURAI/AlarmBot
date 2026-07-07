"""codex 호출량 요약 — `python -m kokalim.core.usage`.

codex_usage.jsonl(호출마다 1줄)을 읽어 호출 수·호출률·소요시간·토큰(있으면)을 집계한다.
구독제 레이트리밋 대비 지속 사용량을 가늠하는 용도.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from kokalim.config import NewsBotConfig


def summarize(path: str | Path) -> str:
    """사용량 로그를 사람이 읽는 요약 문자열로 만든다."""
    p = Path(path)
    if not p.exists():
        return f"아직 codex 호출 기록이 없습니다 ({path})."
    rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        return "기록이 비어 있습니다."

    times = sorted(datetime.fromisoformat(r["at"]) for r in rows)
    span_h = (times[-1] - times[0]).total_seconds() / 3600
    n = len(rows)
    ok = sum(1 for r in rows if r.get("ok"))
    total_s = sum(r.get("elapsed_s") or 0 for r in rows)
    tokens = sum(r.get("tokens") or 0 for r in rows)
    rate = n / span_h if span_h > 0 else float(n)

    lines = [
        "📊 codex 사용량",
        f"- 호출: {n}건 (성공 {ok}, 실패 {n - ok})",
        f"- 기간: {times[0]:%m-%d %H:%M} ~ {times[-1]:%H:%M} ({span_h:.2f}시간)",
        f"- 호출률: {rate:.1f}건/시간" + (f" → 5시간 추정 {rate * 5:.0f}건" if span_h > 0 else ""),
        f"- 소요시간: 총 {total_s:.0f}s (평균 {total_s / n:.1f}s/건)",
    ]
    if tokens:
        lines.append(f"- 토큰(추정 합): {tokens:,}")
    return "\n".join(lines)


def main() -> int:
    """CLI 엔트리포인트."""
    print(summarize(NewsBotConfig().codex_usage_log))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
