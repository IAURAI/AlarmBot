"""News alert bot: crawl → dedup → urgency filter → summarize → notify."""

import os as _os
from pathlib import Path as _Path


def _load_dotenv() -> None:
    """프로젝트 루트 .env의 미설정 키만 환경변수로 채운다.

    Anthropic SDK(ANTHROPIC_API_KEY)와 텔레그램/네이버/카카오 토큰을
    코드 임포트 시점에 자동 로드한다. 기존 환경변수는 덮어쓰지 않는다.
    """
    path = _Path(".env")
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in _os.environ:
            _os.environ[key] = value


_load_dotenv()
