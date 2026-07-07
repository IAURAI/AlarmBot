"""Vercel 함수(notify/telegram) 로직 테스트 — Supabase/텔레그램은 모듈 레벨에서 목킹."""

from __future__ import annotations

import notify
import telegram


def _router(supported, mine, alerts=None):
    """path로 라우팅하는 가짜 supabase_get."""
    def _get(path):
        if "supported_tickers" in path:
            return [{"ticker": t} for t in supported]
        if "user_watchlist" in path:
            return [{"ticker": t} for t in mine]
        if "/alerts" in path:
            return list(alerts or [])
        return []
    return _get


# ---- telegram 명령 ----------------------------------------------------------

def test_bare_text_returns_help(monkeypatch):
    monkeypatch.setattr(telegram, "supabase_get", _router(["삼성전자"], []))
    assert "주식 알림봇" in telegram._reply_for("삼성전자", 1, "u")   # 슬래시 없음 → HELP


def test_watch_rejects_unsupported(monkeypatch):
    monkeypatch.setattr(telegram, "supabase_get", _router(["삼성전자", "네이버"], []))
    ups = []
    monkeypatch.setattr(telegram, "supabase_upsert", lambda p, b: ups.append((p, b)))
    out = telegram._reply_for("/watch 한미반도체", 1, "u")
    assert "지원 종목이 아니" in out
    assert ups == []                       # 등록/추가 안 함


def test_watch_adds_supported(monkeypatch):
    monkeypatch.setattr(telegram, "supabase_get", _router(["삼성전자", "네이버"], []))
    ups = []
    monkeypatch.setattr(telegram, "supabase_upsert", lambda p, b: ups.append((p, b)))
    out = telegram._reply_for("/watch 삼성전자", 1, "u")
    assert "추가" in out and "삼성전자" in out
    paths = [p for p, _ in ups]
    assert any("subscribers" in p for p in paths)       # 등록
    assert any("user_watchlist" in p for p in paths)    # 관심종목 추가


def test_watch_enforces_cap(monkeypatch):
    mine = [f"종목{i}" for i in range(telegram.MAX_WATCH)]
    monkeypatch.setattr(telegram, "supabase_get", _router(["네이버", *mine], mine))
    ups = []
    monkeypatch.setattr(telegram, "supabase_upsert", lambda p, b: ups.append((p, b)))
    out = telegram._reply_for("/watch 네이버", 1, "u")
    assert f"최대 {telegram.MAX_WATCH}" in out
    assert not any("user_watchlist" in p for p, _ in ups)   # 관심종목 추가는 안 함


def test_unwatch_deletes(monkeypatch):
    monkeypatch.setattr(telegram, "supabase_get", _router([], ["삼성전자"]))
    dels = []
    monkeypatch.setattr(telegram, "supabase_delete", lambda p: dels.append(p))
    out = telegram._reply_for("/unwatch 삼성전자", 1, "u")
    assert "제거" in out
    assert dels and "user_watchlist" in dels[0] and "ticker=eq." in dels[0]


def test_my_empty_and_list(monkeypatch):
    monkeypatch.setattr(telegram, "supabase_get", _router([], []))
    assert "없어요" in telegram._reply_for("/my", 1, "u")
    monkeypatch.setattr(telegram, "supabase_get", _router([], ["삼성전자", "네이버"]))
    out = telegram._reply_for("/my", 1, "u")
    assert "삼성전자" in out and "네이버" in out


def test_tickers_lists_supported(monkeypatch):
    monkeypatch.setattr(telegram, "supabase_get", _router(["삼성전자", "SK하이닉스"], []))
    out = telegram._reply_for("/tickers", 1, "u")
    assert "삼성전자" in out and "SK하이닉스" in out


def test_latest_scoped_to_watchlist(monkeypatch):
    alert = {"ticker": "삼성전자", "headline": "MLCC 투자", "url": "http://x",
             "created_at": "2026-07-07T03:00:00+00:00"}
    captured = {}

    def _get(path):
        if "user_watchlist" in path:
            return [{"ticker": "삼성전자"}]
        if "/alerts" in path:
            captured["path"] = path
            return [alert]
        return []

    monkeypatch.setattr(telegram, "supabase_get", _get)
    out = telegram._reply_for("/latest", 1, "u")
    assert "MLCC 투자" in out
    assert "ticker=in.(" in captured["path"]       # 관심종목으로 한정


def test_status_scoped_count(monkeypatch):
    monkeypatch.setattr(telegram, "supabase_get", _router([], ["삼성전자"]))
    monkeypatch.setattr(telegram, "supabase_count", lambda p: 4)
    assert "4" in telegram._reply_for("/status", 1, "u")


def test_stop_deactivates(monkeypatch):
    patches = []
    monkeypatch.setattr(telegram, "supabase_patch", lambda p, b: patches.append((p, b)) or [])
    out = telegram._reply_for("/stop", 1, "u")
    assert "중지" in out
    assert patches and "subscribers" in patches[0][0] and patches[0][1] == {"active": False}


def test_adhoc_ticker_query(monkeypatch):
    alert = {"ticker": "네이버", "headline": "신고가", "created_at": "2026-07-07T03:00:00+00:00"}

    def _get(path):
        if "supported_tickers" in path:
            return [{"ticker": "네이버"}]
        if "/alerts" in path:
            return [alert]
        return []

    monkeypatch.setattr(telegram, "supabase_get", _get)
    out = telegram._reply_for("/네이버", 1, "u")
    assert "네이버" in out and "신고가" in out


def test_command_mention_stripped(monkeypatch):
    monkeypatch.setattr(telegram, "supabase_get", _router([], []))
    assert "없어요" in telegram._reply_for("/my@StockBot", 1, "u")   # /my@Bot == /my


# ---- notify 팬아웃 ----------------------------------------------------------

def test_fanout_sends_to_all_recipients(monkeypatch):
    monkeypatch.setattr(notify, "supabase_get", lambda p: [{"chat_id": 11}, {"chat_id": 22}])
    monkeypatch.setattr(notify, "supabase_patch", lambda p, b: [{"id": 1}])   # claimed
    sent = []
    monkeypatch.setattr(notify, "telegram_send_safe", lambda c, t: sent.append(c) or "ok")
    status = notify._dispatch({"id": 1, "ticker": "삼성전자", "headline": "h", "urgency": "high"})
    assert sent == [11, 22]
    assert status == "sent 2/2"


def test_fanout_idempotent_when_already_claimed(monkeypatch):
    monkeypatch.setattr(notify, "supabase_get", lambda p: [{"chat_id": 11}])
    monkeypatch.setattr(notify, "supabase_patch", lambda p, b: [])            # 클레임 실패
    sent = []
    monkeypatch.setattr(notify, "telegram_send_safe", lambda c, t: sent.append(c) or "ok")
    status = notify._dispatch({"id": 1, "ticker": "삼성전자", "headline": "h"})
    assert status == "already-sent"
    assert sent == []                        # 재발송 안 함


def test_fanout_deactivates_blocked(monkeypatch):
    monkeypatch.setattr(notify, "supabase_get", lambda p: [{"chat_id": 11}, {"chat_id": 22}])
    monkeypatch.setattr(notify, "supabase_patch", lambda p, b: [{"id": 1}])
    monkeypatch.setattr(notify, "telegram_send_safe", lambda c, t: "blocked" if c == 11 else "ok")
    deactivated = []
    monkeypatch.setattr(notify, "_deactivate", lambda c: deactivated.append(c))
    status = notify._dispatch({"id": 1, "ticker": "삼성전자", "headline": "h"})
    assert deactivated == [11]
    assert status == "sent 1/2"


def test_fanout_no_subscribers(monkeypatch):
    monkeypatch.setattr(notify, "supabase_get", lambda p: [])
    monkeypatch.setattr(notify, "supabase_patch", lambda p, b: [{"id": 1}])
    sent = []
    monkeypatch.setattr(notify, "telegram_send_safe", lambda c, t: sent.append(c) or "ok")
    status = notify._dispatch({"id": 1, "ticker": "한미반도체", "headline": "h"})
    assert status == "no-subscribers"
    assert sent == []


def test_fanout_null_ticker_broadcasts_all_active(monkeypatch):
    """종목 없는 시장 일반 알림 → 활성 구독자 전원."""
    def _get(path):
        return [{"chat_id": 1}, {"chat_id": 2}] if "subscribers" in path else []

    monkeypatch.setattr(notify, "supabase_get", _get)
    monkeypatch.setattr(notify, "supabase_patch", lambda p, b: [{"id": 9}])
    sent = []
    monkeypatch.setattr(notify, "telegram_send_safe", lambda c, t: sent.append(c) or "ok")
    status = notify._dispatch({"id": 9, "ticker": None, "headline": "코스피 급락"})
    assert sent == [1, 2]
    assert status == "sent 2/2"
