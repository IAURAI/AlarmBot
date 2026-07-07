"""News bot configuration — 모든 파라미터를 한 곳에 모은다."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NewsBotConfig:
    """뉴스 알림봇 설정값."""

    # --- 뉴스 범위 ---
    # "watchlist": 관심종목 키워드가 포함된 기사만 / "market": 전체 / "all": 필터 없음
    scope: str = "watchlist"
    watchlist: tuple[str, ...] = (
        "삼성전자", "삼성전기", "SK하이닉스", "LG에너지솔루션", "삼성바이오로직스",
        "현대차", "기아", "네이버", "카카오", "포스코", "셀트리온",
    )

    # RSS 피드 (실서비스 전 URL 유효성 확인 권장 — 매체마다 경로가 바뀜)
    rss_feeds: tuple[str, ...] = (
        "https://www.yna.co.kr/rss/economy.xml",       # 연합뉴스 경제
        "https://www.mk.co.kr/rss/30100041/",           # 매일경제
        "https://www.hankyung.com/feed/economy",        # 한국경제(브라우저 UA 필요)
        "https://biz.chosun.com/arc/outboundfeeds/rss/category/stock/?outputType=xml",  # 조선비즈 증시
    )
    # 네이버 뉴스 검색 API 사용 여부(관심종목 키워드로 폴링). 자격증명은 .env에서 로드.
    use_naver: bool = True
    naver_display: int = 30

    # --- 긴급도 판정 ---
    # "hybrid": 키워드 1차 + 애매한 것만 LLM / "llm": 전량 LLM / "keyword": 규칙만
    urgency_mode: str = "hybrid"
    # 고가중치 키워드(하나만 나와도 긴급 후보). weight = high_weight
    high_keywords: tuple[str, ...] = (
        "속보", "단독", "상한가", "하한가", "급락", "급등", "유상증자", "무상증자",
        "감자", "상장폐지", "거래정지", "횡령", "배임", "분식", "디폴트", "채무불이행",
        "리콜", "화재", "폭발", "어닝쇼크", "실적쇼크", "감사의견", "관리종목",
    )
    # 중가중치 키워드(2개 이상 또는 LLM 판정 대상). weight = medium_weight
    medium_keywords: tuple[str, ...] = (
        "실적", "공시", "인수", "합병", "M&A", "지분", "계약", "수주", "특허",
        "제재", "조사", "소송", "임상", "승인", "목표주가", "신고가", "신저가",
    )
    high_weight: float = 3.0
    medium_weight: float = 1.0
    # 키워드 하드 필터: noise_low 미만이면 후보에서 제외(알림 대상 아님).
    # noise_low 이상인 클러스터만 LLM 중요도 판정 대상 → LLM이 알림 여부 최종 결정.
    noise_low: float = 1.0
    # urgent_high는 LLM이 없는 keyword 모드에서만 폴백 임계값으로 쓴다.
    urgent_high: float = 3.0
    # 아래 키워드가 있으면 LLM 판정 없이 즉시 알림(명백한 초긴급용). 기본은 비활성 —
    # 사용자 요구대로 기본 동작은 'LLM이 최종 판단'. 필요하면 여기에 키워드를 채운다.
    always_alert_keywords: tuple[str, ...] = ()

    # --- LLM (Anthropic) ---
    # 기본값은 지침에 따라 Opus. 5분마다 도는 고빈도 작업이라 비용이 부담되면
    # "claude-haiku-4-5"($1/$5 per MTok)로 낮추는 것을 권장 — 분류/요약엔 충분함.
    llm_model: str = "claude-opus-4-8"
    llm_max_tokens: int = 1024
    # LLM 백엔드: "codex"(구독제, 토큰 비용 없음) 또는 "anthropic"(API 키)
    llm_backend: str = "codex"
    codex_model: str = ""        # 비우면 codex 기본 모델(gpt-5.5)
    codex_effort: str = "low"    # codex reasoning effort (low/medium/high)
    codex_timeout: int = 180     # codex 호출 타임아웃(초)
    codex_usage_log: str = "news_bot/state/codex_usage.jsonl"  # 호출량 측정 로그

    # --- 중복 제거 ---
    # 문자 바이그램 겹침 계수 임계값(0~1). 높을수록 엄격.
    dedup_threshold: float = 0.5
    seen_ttl_hours: int = 24
    # 발행 시각이 이보다 오래된 기사는 알림 후보에서 제외(속보만; 옛 기사 재발송 방지).
    max_article_age_hours: int = 12

    # --- 발송 ---
    # "console"(dry-run 안전) / "telegram" / "kakao"
    platform: str = "console"
    max_items_per_digest: int = 12
    telegram_parse_mode: str = "HTML"
    # 발송 경로: "notifier"(콘솔/텔레그램/카카오 직접 발송) 또는
    # "supabase"(구조화 행을 Supabase에 적재 → Database Webhook이 Vercel /api/notify 호출 → 텔레그램).
    # supabase면 로컬은 직접 발송하지 않는다(중복 방지). 자격증명 없으면 자동으로 notifier 폴백.
    sink: str = "notifier"
    supabase_table: str = "alerts"

    # --- 스케줄 ---
    interval_seconds: int = 300

    # --- 상태 파일 ---
    state_path: str = "news_bot/state/state.json"
    fixture_path: str = "news_bot/fixtures/sample_articles.json"

    # --- 연관 컨텍스트 추적(--context 모드) ---
    situation_path: str = "news_bot/state/situations.json"
    context_fixture_path: str = "news_bot/fixtures/context_sample.json"
    context_alert_min: int = 3       # 연관 소식 알림 최소 중요도(0~10)
    context_expand: bool = False     # True면 LLM이 컨텍스트 그래프를 확장(하이브리드)
    naver_max_queries: int = 40      # 컨텍스트 모드 네이버 질의 상한
    context_max_workers: int = 8     # 항목별 병렬 조사 동시성
    expansion_path: str = "news_bot/state/graph_expansion.json"  # LLM 확장 캐시

    @property
    def keyword_weights(self) -> dict[str, float]:
        """키워드 → 가중치 매핑."""
        weights: dict[str, float] = {k: self.high_weight for k in self.high_keywords}
        for k in self.medium_keywords:
            weights.setdefault(k, self.medium_weight)
        return weights
