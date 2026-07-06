"""알림 발송: 콘솔(dry-run) / 텔레그램 / 카카오 '나에게 보내기'."""

from __future__ import annotations

import html
import logging
import os

import requests

from .config import NewsBotConfig
from .models import Cluster

LOGGER = logging.getLogger(__name__)
_TELEGRAM_LIMIT = 4096
_KAKAO_MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"


def format_digest(clusters: list[Cluster], config: NewsBotConfig, use_html: bool) -> str:
    """긴급 클러스터 목록을 다이제스트 텍스트로 만든다."""
    items = clusters[: config.max_items_per_digest]
    lines = [f"🚨 긴급 뉴스 {len(items)}건"]
    for cluster in items:
        title = cluster.summary or cluster.title
        link = cluster.representative.link
        tag = f" ({cluster.source_count}개 매체)" if cluster.source_count > 1 else ""
        if use_html:
            safe = html.escape(title)
            head = f'• <a href="{html.escape(link)}">{safe}</a>{tag}' if link else f"• {safe}{tag}"
        else:
            head = f"• {title}{tag}" + (f"\n  {link}" if link else "")
        lines.append(head)
        if cluster.reason:
            lines.append(f"  ↳ {html.escape(cluster.reason) if use_html else cluster.reason}")
    dropped = len(clusters) - len(items)
    if dropped > 0:
        lines.append(f"…외 {dropped}건 생략")
    return "\n".join(lines)


class ConsoleNotifier:
    """표준 출력으로 발송(안전한 dry-run 기본값)."""

    uses_html = False

    def send(self, text: str) -> None:
        """다이제스트를 콘솔에 출력한다."""
        print("\n----- NEWS DIGEST (dry-run) -----")
        print(text)
        print("---------------------------------\n")


class TelegramNotifier:
    """텔레그램 봇 sendMessage로 발송."""

    uses_html = True

    def __init__(self, token: str, chat_id: str, parse_mode: str) -> None:
        """봇 토큰과 대상 chat_id를 설정한다."""
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._parse_mode = parse_mode

    def send(self, text: str) -> None:
        """다이제스트를 텔레그램으로 전송한다(길면 분할)."""
        for chunk in _split(text, _TELEGRAM_LIMIT):
            resp = requests.post(
                self._url,
                json={
                    "chat_id": self._chat_id,
                    "text": chunk,
                    "parse_mode": self._parse_mode,
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            resp.raise_for_status()


class KakaoSelfNotifier:
    """카카오톡 '나에게 보내기'(memo API)로 발송 — 본인 수신 전용."""

    uses_html = False

    def __init__(self, access_token: str) -> None:
        """카카오 access token을 설정한다."""
        self._headers = {"Authorization": f"Bearer {access_token}"}

    def send(self, text: str) -> None:
        """텍스트 템플릿으로 나에게 보내기 전송."""
        import json as _json

        template = {
            "object_type": "text",
            "text": text[:_TELEGRAM_LIMIT],
            "link": {"web_url": "https://finance.naver.com"},
        }
        resp = requests.post(
            _KAKAO_MEMO_URL,
            headers=self._headers,
            data={"template_object": _json.dumps(template, ensure_ascii=False)},
            timeout=10,
        )
        resp.raise_for_status()


def _split(text: str, limit: int) -> list[str]:
    """텍스트를 limit 이하 청크로 줄 경계에서 나눈다."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit and current:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


def get_notifier(config: NewsBotConfig, dry_run: bool):
    """설정/플래그에 맞는 발송기를 만든다. 자격증명 없으면 콘솔로 폴백."""
    if dry_run or config.platform == "console":
        return ConsoleNotifier()
    if config.platform == "telegram":
        # NEWSBOT_ 네임스페이스를 우선 사용 → 기존 텔레그램 봇과 토큰 혼용 방지.
        token = os.getenv("NEWSBOT_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
        chat = os.getenv("NEWSBOT_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat:
            LOGGER.warning("텔레그램 자격증명 없음 — 콘솔로 폴백")
            return ConsoleNotifier()
        return TelegramNotifier(token, chat, config.telegram_parse_mode)
    if config.platform == "kakao":
        token = os.getenv("KAKAO_ACCESS_TOKEN")
        if not token:
            LOGGER.warning("KAKAO_ACCESS_TOKEN 없음 — 콘솔로 폴백")
            return ConsoleNotifier()
        return KakaoSelfNotifier(token)
    LOGGER.warning("알 수 없는 platform=%s — 콘솔로 폴백", config.platform)
    return ConsoleNotifier()
