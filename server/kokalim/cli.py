"""콕알림 CLI 엔트리포인트 — `python -m kokalim.cli`.

기존 `run.py`의 로직을 그대로 위임하는 얇은 래퍼. CLI 플래그
(`--offline --dry-run --loop --context --platform ...`)는 `run.main`이 처리한다.
"""

from __future__ import annotations

from kokalim.run import main

if __name__ == "__main__":
    raise SystemExit(main())
