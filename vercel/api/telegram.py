"""POST /api/telegram — 텔레그램 웹훅. 개인별 관심종목 명령 처리.

명령:
  /start            등록 + 도움말 + 내 관심종목
  /watch <종목>     관심종목 추가 (지원 종목만, 1인 최대 20)
  /unwatch <종목>   관심종목 제거
  /my               내 관심종목 목록
  /tickers          지원 종목 목록
  /latest           내 관심종목 최근 알림 5건
  /status           내 관심종목 오늘(KST) 알림 현황
  /stop             알림 중지(active=false)
  /<지원종목>       해당 종목 최근 알림(임시 조회)
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime  # noqa: E402
from http.server import BaseHTTPRequestHandler  # noqa: E402

from _common import (  # noqa: E402
    KST,
    esc,
    fmt_ts,
    quote,
    secure_eq,
    supabase_count,
    supabase_delete,
    supabase_get,
    supabase_patch,
    supabase_upsert,
    telegram_send,
)

WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
MAX_WATCH = 20
_SELECT = "select=ticker,headline,reason,url,urgency,created_at"

HELP = (
    "🤖 <b>주식 알림봇</b> — 개인별 관심종목\n"
    "• /watch 삼성전자 — 관심종목 추가\n"
    "• /unwatch 삼성전자 — 제거\n"
    "• /my — 내 관심종목\n"
    "• /tickers — 지원 종목\n"
    "• /latest — 내 최근 알림\n"
    "• /status — 오늘 현황\n"
    "• /stop — 알림 중지"
)


# ---- Supabase 조회/변경 헬퍼 ------------------------------------------------

def _supported() -> list:
    """지원 종목 목록."""
    rows = supabase_get("/rest/v1/supported_tickers?select=ticker&active=eq.true&order=ticker")
    return [r["ticker"] for r in rows if r.get("ticker")]


def _my_tickers(chat_id) -> list:
    """내 관심종목 목록."""
    rows = supabase_get(f"/rest/v1/user_watchlist?chat_id=eq.{quote(chat_id)}&select=ticker&order=ticker")
    return [r["ticker"] for r in rows if r.get("ticker")]


def _register(chat_id, username) -> None:
    """구독자 upsert(active=true). username은 있을 때만 저장(기존 값 NULL 덮어쓰기 방지)."""
    row = {"chat_id": chat_id, "active": True}
    if username:
        row["username"] = username
    supabase_upsert("/rest/v1/subscribers", [row])


def _in_filter(tickers) -> str:
    """PostgREST in.(...) 필터 값(각 종목 URL 인코딩, 구분자 콤마는 리터럴)."""
    return "in.(" + ",".join(quote(t) for t in tickers) + ")"


def _fmt_rows(rows, title, empty) -> str:
    """알림 행 목록을 메시지로."""
    if not rows:
        return empty
    lines = [title]
    for row in rows:
        tk = row.get("ticker") or "시장"
        lines.append(f"\n• ({fmt_ts(row.get('created_at'))}) <b>{esc(tk)}</b> {esc(row.get('headline'))}")
        if row.get("url"):
            lines.append(f"  {esc(row.get('url'))}")
    return "\n".join(lines)


# ---- 명령 핸들러 ------------------------------------------------------------

def _start(chat_id, username) -> str:
    _register(chat_id, username)
    mine = _my_tickers(chat_id)
    if mine:
        return HELP + "\n\n현재 관심종목: " + ", ".join(esc(t) for t in mine)
    return HELP + "\n\n아직 관심종목이 없어요. /tickers 로 종목을 보고 /watch 로 추가하세요."


def _watch(chat_id, username, arg) -> str:
    ticker = arg.strip()
    if not ticker:
        return "사용법: <code>/watch 삼성전자</code>"
    supported = _supported()
    if ticker not in supported:
        return f"'{esc(ticker)}'는 지원 종목이 아니에요.\n지원: " + ", ".join(esc(t) for t in supported)
    _register(chat_id, username)
    mine = _my_tickers(chat_id)
    if ticker in mine:
        return f"이미 관심종목이에요: {esc(ticker)}"
    if len(mine) >= MAX_WATCH:
        return f"관심종목은 최대 {MAX_WATCH}개예요. /unwatch 로 정리 후 추가하세요."
    supabase_upsert("/rest/v1/user_watchlist", [{"chat_id": chat_id, "ticker": ticker}])
    return f"✅ 추가: {esc(ticker)}\n관심종목: " + ", ".join(esc(t) for t in (mine + [ticker]))


def _unwatch(chat_id, arg) -> str:
    ticker = arg.strip()
    if not ticker:
        return "사용법: <code>/unwatch 삼성전자</code>"
    supabase_delete(f"/rest/v1/user_watchlist?chat_id=eq.{quote(chat_id)}&ticker=eq.{quote(ticker)}")
    mine = _my_tickers(chat_id)
    tail = ("관심종목: " + ", ".join(esc(t) for t in mine)) if mine else "관심종목이 비었어요."
    return f"🗑 제거: {esc(ticker)}\n{tail}"


def _my(chat_id) -> str:
    mine = _my_tickers(chat_id)
    if not mine:
        return "관심종목이 없어요. /tickers 참고 후 /watch 로 추가하세요."
    return "⭐ 내 관심종목\n" + ", ".join(esc(t) for t in mine)


def _tickers() -> str:
    supported = _supported()
    if not supported:
        return "지원 종목이 아직 준비 중이에요(로컬 봇 첫 동기화 대기)."
    return "📋 지원 종목\n" + ", ".join(esc(t) for t in supported) + "\n\n<code>/watch 종목명</code> 으로 추가하세요."


def _latest_mine(chat_id) -> str:
    mine = _my_tickers(chat_id)
    if not mine:
        return "관심종목이 없어요. /watch 로 추가하세요."
    rows = supabase_get(f"/rest/v1/alerts?{_SELECT}&ticker={_in_filter(mine)}&order=created_at.desc&limit=5")
    return _fmt_rows(rows, "🗞 <b>내 최근 알림</b>", "관심종목에 최근 알림이 없어요.")


def _latest_ticker(ticker) -> str:
    rows = supabase_get(f"/rest/v1/alerts?{_SELECT}&ticker=eq.{quote(ticker)}&order=created_at.desc&limit=5")
    return _fmt_rows(rows, f"🗞 <b>[{esc(ticker)}]</b> 최근 알림", f"[{esc(ticker)}] 최근 알림이 없어요.")


def _status(chat_id) -> str:
    mine = _my_tickers(chat_id)
    if not mine:
        return "관심종목이 없어요. /watch 로 추가하세요."
    now_kst = datetime.now(KST)
    midnight = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
    midnight_iso = midnight.strftime("%Y-%m-%dT%H:%M:%S%z")
    flt = _in_filter(mine)
    count = supabase_count(f"/rest/v1/alerts?select=id&ticker={flt}&created_at=gte.{quote(midnight_iso)}")
    last = supabase_get(f"/rest/v1/alerts?{_SELECT}&ticker={flt}&order=created_at.desc&limit=1")
    msg = f"📊 오늘(KST) 내 알림 <b>{count}</b>건"
    if last:
        row = last[0]
        msg += f"\n최근: ({fmt_ts(row.get('created_at'))}) [{esc(row.get('ticker') or '시장')}] {esc(row.get('headline'))}"
    return msg


def _stop(chat_id) -> str:
    supabase_patch(f"/rest/v1/subscribers?chat_id=eq.{quote(chat_id)}", {"active": False})
    return "🔕 알림을 중지했어요. 다시 받으려면 /start."


def _reply_for(text, chat_id, username) -> str:
    """명령 텍스트 → 응답."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return HELP                       # 명령(/)이 아니면 반응 안 함(그룹 오작동 방지)
    parts = text.split(maxsplit=1)
    cmd = parts[0][1:].split("@")[0]      # 앞의 / 와 @BotName 제거
    arg = parts[1].strip() if len(parts) > 1 else ""
    low = cmd.lower()
    if low in ("start", "help"):
        return _start(chat_id, username)
    if low == "watch":
        return _watch(chat_id, username, arg)
    if low == "unwatch":
        return _unwatch(chat_id, arg)
    if low in ("my", "list"):
        return _my(chat_id)
    if low == "tickers":
        return _tickers()
    if low == "latest":
        return _latest_mine(chat_id)
    if low == "status":
        return _status(chat_id)
    if low == "stop":
        return _stop(chat_id)
    if cmd in _supported():               # /<지원종목> 임시 조회
        return _latest_ticker(cmd)
    return HELP


class handler(BaseHTTPRequestHandler):
    """텔레그램 웹훅 수신 엔드포인트."""

    def do_GET(self):
        """헬스체크."""
        self._respond(200, {"ok": True, "service": "telegram"})

    def do_POST(self):
        """텔레그램 업데이트 처리."""
        if not secure_eq(self.headers.get("X-Telegram-Bot-Api-Secret-Token"), WEBHOOK_SECRET):
            self._respond(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("content-length") or 0)
        except (TypeError, ValueError):
            length = 0
        length = max(0, min(length, 1_000_000))   # 본문 상한 1MB
        raw = self.rfile.read(length) if length else b""
        try:
            update = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._respond(200, {"ok": True})
            return
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = message.get("text")
        sender = message.get("from") or {}
        username = sender.get("username") or sender.get("first_name")
        if chat_id is None or not text:
            self._respond(200, {"ok": True, "skipped": True})
            return
        try:
            telegram_send(chat_id, _reply_for(text, chat_id, username))
        except Exception:
            try:
                telegram_send(chat_id, "⚠️ 처리 중 오류가 났어요. 잠시 후 다시 시도해 주세요.")
            except Exception:
                pass
        self._respond(200, {"ok": True})

    def _respond(self, status: int, obj: dict) -> None:
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        """접근 로그 억제."""
