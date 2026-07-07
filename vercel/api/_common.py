"""Vercel 서버리스 함수 공용 유틸 — 표준 라이브러리만 사용(무의존성).

두 함수(api/notify.py, api/telegram.py)가 공유한다. 밑줄(_) 시작 + handler 미정의라
Vercel은 이 파일을 라우트로 만들지 않고 지원 모듈로만 번들한다.

환경변수(모두 Vercel 대시보드에서 설정, 커밋 금지):
  TELEGRAM_BOT_TOKEN     텔레그램 봇 토큰
  TELEGRAM_CHAT_ID       푸시 대상 chat_id (notify)
  SUPABASE_URL           https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY   service_role 키(RLS 우회, 서버 전용)
  SUPABASE_WEBHOOK_SECRET  Supabase 웹훅이 보내는 x-webhook-secret 검증값 (notify)
  TELEGRAM_WEBHOOK_SECRET  Telegram setWebhook secret_token 검증값 (telegram)
"""

import hmac
import html
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

KST = timezone(timedelta(hours=9))


def secure_eq(got, expected) -> bool:
    """상수시간 비교. expected가 비어 있으면 항상 False(fail-closed)."""
    expected = str(expected or "")
    if not expected:
        return False
    # 바이트로 비교 — 헤더에 비ASCII가 와도 TypeError 없이 안전.
    return hmac.compare_digest(str(got or "").encode("utf-8", "ignore"), expected.encode("utf-8", "ignore"))


def esc(value) -> str:
    """텔레그램 HTML parse_mode용 이스케이프."""
    return html.escape(str(value or ""))


def fmt_ts(iso: str) -> str:
    """Supabase의 UTC ISO 타임스탬프를 KST 'MM/DD HH:MM'으로."""
    try:
        dt = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
        return dt.astimezone(KST).strftime("%m/%d %H:%M")
    except Exception:
        return (iso or "")[:16].replace("T", " ")


def _request(url: str, method: str = "GET", headers=None, body=None, timeout: int = 6):
    """JSON 요청을 보내고 (status, text)를 반환한다. 비2xx는 HTTPError를 올린다."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8")


def telegram_send(chat_id, text: str, parse_mode: str = "HTML", attempts: int = 2):
    """텔레그램 sendMessage(4096자 컷). 일시적 실패는 짧게 재시도(429/5xx/네트워크 블립 흡수)."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    body = {
        "chat_id": chat_id,
        "text": text[:4096],
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    last_exc = None
    for i in range(max(1, attempts)):
        try:
            return _request(url, method="POST", headers={"Content-Type": "application/json"}, body=body, timeout=4)
        except Exception as exc:
            last_exc = exc
            if i + 1 < attempts:
                time.sleep(0.4)
    raise last_exc


def telegram_send_safe(chat_id, text: str) -> str:
    """단건 발송. 'ok' | 'blocked'(403 차단/탈퇴) | 'error'. 429/5xx는 1회 재시도(Retry-After 존중).

    팬아웃용 — 브로드캐스트 중 429 스로틀은 흔하므로 한 번은 물러섰다 재시도한다.
    """
    for attempt in range(2):
        try:
            telegram_send(chat_id, text, attempts=1)
            return "ok"
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                return "blocked"
            transient = exc.code == 429 or 500 <= exc.code < 600
            if transient and attempt == 0:
                try:
                    delay = min(3.0, float(exc.headers.get("Retry-After") or 1.0))
                except (TypeError, ValueError):
                    delay = 1.0
                time.sleep(delay)
                continue
            return "error"
        except Exception:
            if attempt == 0:
                time.sleep(0.5)
                continue
            return "error"
    return "error"


def _sb_headers(extra=None):
    """Supabase PostgREST 공통 헤더(service_role)."""
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def supabase_get(path: str):
    """PostgREST GET. path 예: '/rest/v1/alerts?order=created_at.desc&limit=5'."""
    _, raw = _request(SUPABASE_URL + path, headers=_sb_headers())
    return json.loads(raw) if raw else []


def supabase_count(path: str) -> int:
    """count=exact로 총 개수만 반환(Content-Range 헤더 파싱). path에 select/필터 포함."""
    req = urllib.request.Request(
        SUPABASE_URL + path,
        method="GET",
        headers=_sb_headers({"Prefer": "count=exact", "Range": "0-0"}),
    )
    with urllib.request.urlopen(req, timeout=6) as resp:
        content_range = resp.headers.get("Content-Range", "")  # 예: "0-0/42"
    total = content_range.split("/")[-1]
    return int(total) if total.isdigit() else 0


def supabase_patch(path: str, body: dict):
    """PostgREST PATCH. 반영된 행 목록을 반환(return=representation)."""
    _, raw = _request(
        SUPABASE_URL + path,
        method="PATCH",
        headers=_sb_headers({"Prefer": "return=representation"}),
        body=body,
    )
    return json.loads(raw) if raw else []


def supabase_upsert(path: str, body) -> None:
    """PostgREST POST + merge-duplicates(PK 충돌 시 갱신). 반환 없음."""
    _request(
        SUPABASE_URL + path,
        method="POST",
        headers=_sb_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
        body=body,
    )


def supabase_delete(path: str) -> None:
    """PostgREST DELETE(필터는 path에 포함)."""
    _request(SUPABASE_URL + path, method="DELETE", headers=_sb_headers())


def quote(value: str) -> str:
    """쿼리 값 URL 인코딩(PostgREST 필터 값 안전화)."""
    return urllib.parse.quote(str(value), safe="")
