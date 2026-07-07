"""POST /api/notify — Supabase INSERT 웹훅을 받아 구독자에게 팬아웃 발송.

흐름: 로컬 봇이 alerts에 INSERT → Supabase Database Webhook이 이 함수를 호출 →
그 종목(ticker)을 관심종목에 담은 **활성 구독자**를 조회 → 멱등 클레임(sent=false→true)
→ 각 구독자에게 텔레그램 발송. 봇을 차단/탈퇴한 사용자(403)는 자동 비활성화한다.

브로드캐스트라 발송은 best-effort다: 일부 수신 실패가 있어도 클레임을 되돌리지 않는다
(멱등 클레임은 중복 웹훅 전달에 대한 이중발송만 막는다). 수신자 조회는 클레임 전에 하여,
조회가 실패하면(500) 아직 미발송(sent=false)으로 남아 복구 가능하게 한다.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from http.server import BaseHTTPRequestHandler  # noqa: E402

from _common import (  # noqa: E402
    esc,
    quote,
    secure_eq,
    supabase_get,
    supabase_patch,
    telegram_send_safe,
)

WEBHOOK_SECRET = os.environ.get("SUPABASE_WEBHOOK_SECRET", "")
_ICON = {"high": "🚨", "medium": "🔔", "low": "•"}


def _format(record: dict) -> str:
    """alerts 행을 텔레그램 메시지로."""
    ticker = record.get("ticker") or "시장"
    icon = _ICON.get(record.get("urgency"), "🔔")
    lines = [f"{icon} <b>[{esc(ticker)}]</b> {esc(record.get('headline'))}"]
    if record.get("reason"):
        lines.append(f"↳ {esc(record.get('reason'))}")
    if record.get("url"):
        lines.append(f"🔗 {esc(record.get('url'))}")
    return "\n".join(lines)


def _ids(rows) -> list:
    """chat_id 추출 + 중복 제거(순서 유지)."""
    seen, out = set(), []
    for row in rows:
        cid = row.get("chat_id")
        if cid is not None and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def _all_active() -> list:
    """활성 구독자 전원 chat_id(종목이 없는 시장 일반 알림용)."""
    return _ids(supabase_get("/rest/v1/subscribers?select=chat_id&active=eq.true"))


def _recipients_for(ticker) -> list:
    """해당 종목을 관심종목에 담은 활성 구독자. 종목이 없으면(시장 일반) 활성 전원."""
    if not ticker:
        return _all_active()
    rows = supabase_get(
        f"/rest/v1/user_watchlist?ticker=eq.{quote(ticker)}"
        "&select=chat_id,subscribers!inner(active)&subscribers.active=eq.true"
    )
    return _ids(rows)


def _deactivate(chat_id) -> None:
    """봇 차단/탈퇴한 구독자를 비활성화한다."""
    try:
        supabase_patch(f"/rest/v1/subscribers?chat_id=eq.{quote(chat_id)}", {"active": False})
    except Exception:
        pass


def _dispatch(record: dict) -> str:
    """수신자 조회 → 멱등 클레임 → 팬아웃. 결과 상태 문자열."""
    row_id = record.get("id")
    if row_id is None:
        return "no-id"
    recipients = _recipients_for(record.get("ticker"))  # 클레임 전(실패 시 미발송 유지)
    claimed = supabase_patch(
        f"/rest/v1/alerts?id=eq.{quote(row_id)}&sent=eq.false",
        {"sent": True},
    )
    if not claimed:
        return "already-sent"        # 중복 웹훅 전달 → 재발송 안 함
    if not recipients:
        return "no-subscribers"
    message = _format(record)
    ok = 0
    for chat_id in recipients:
        result = telegram_send_safe(chat_id, message)
        if result == "ok":
            ok += 1
        elif result == "blocked":
            _deactivate(chat_id)
    return f"sent {ok}/{len(recipients)}"


class handler(BaseHTTPRequestHandler):
    """Supabase 웹훅 수신 엔드포인트."""

    def do_GET(self):
        """헬스체크."""
        self._respond(200, {"ok": True, "service": "notify"})

    def do_POST(self):
        """INSERT 웹훅 처리."""
        if not secure_eq(self.headers.get("x-webhook-secret"), WEBHOOK_SECRET):
            self._respond(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("content-length") or 0)
        except (TypeError, ValueError):
            length = 0
        length = max(0, min(length, 1_000_000))   # 본문 상한 1MB
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._respond(400, {"error": "bad json"})
            return
        record = payload.get("record")
        if not isinstance(record, dict):
            self._respond(200, {"ok": True, "skipped": "no record"})
            return
        try:
            status = _dispatch(record)
        except Exception as exc:
            self._respond(500, {"error": str(exc)})
            return
        self._respond(200, {"ok": True, "status": status})

    def _respond(self, status: int, obj: dict) -> None:
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        """접근 로그 억제."""
