# AlarmBot — 한국 주식 뉴스 긴급 알림 봇 (+ 언론 파급력 연구)

한국 주식시장을 위한 두 파이프라인이 한 저장소에 있습니다.

## 1. `news_bot` — 실시간 뉴스 긴급 알림 봇 (메인)

관심종목 뉴스를 5분마다 **수집 → 중복제거 → 키워드 하드게이트 → LLM 최종 중요도 판정 → 텔레그램 발송**.

- **소스**: 네이버 뉴스검색 API(종목 정조준, 분 단위 신선) + 경제 RSS(연합·매경·한경·조선비즈)
- **신선도 필터**(기본 12h)로 옛 기사 재발송 차단
- **필터**: 키워드로 하드 게이트 → 통과분만 LLM이 알림 여부 최종 결정(키워드 강해도 LLM 반려면 미발송)
- **LLM 백엔드**: codex(구독제, 기본, 토큰비용 0) 또는 Anthropic API — `config.py`에서 전환
- **연관 컨텍스트 추적**(`--context`): 공급망·경쟁사·테마까지 감시하고 상황 변화를 시간순으로 추적

→ 상세: [`news_bot/README.md`](news_bot/README.md)

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # 값 채우기 (아래 자격증명 참고)
.venv/bin/python -m news_bot.run --offline --dry-run          # 오프라인 확인(네트워크·키 불필요)
.venv/bin/python -m news_bot.run --loop --platform telegram   # 실시간 운영
```

## 2. `method_b` — "개인 거래비중 × 관심 이벤트" 패널 회귀 (연구)

언론/관심 이벤트의 주가 파급력이 개인 투자자 거래비중과 함께 커지는지(단기 증폭·중기 반전) 검정하는
종목-일 패널 회귀 파이프라인(KOSPI+KOSDAQ, pykrx).

→ 상세: [`method_b/README.md`](method_b/README.md)

## 자격증명 (`.env`)

`.env.example`을 `.env`로 복사해 채우세요. **`.env`는 절대 커밋 금지**(`.gitignore` 처리됨).

| 키 | 용도 | 발급 |
|---|---|---|
| `NEWSBOT_TELEGRAM_BOT_TOKEN` / `_CHAT_ID` | news_bot 텔레그램 발송 | BotFather |
| `NAVER_CLIENT_ID` / `_SECRET` | news_bot 종목 뉴스 검색 | developers.naver.com (앱에 **"검색"** API 추가 필수) |
| `KRX_ID` / `KRX_PW` | method_b 데이터 | data.krx.co.kr |

자격증명이 없으면 해당 경로는 조용히 비활성화되고 콘솔/휴리스틱으로 폴백합니다.

## 테스트

```bash
.venv/bin/python -m pytest news_bot/tests/ tests/ -q   # 네트워크·API 키 불필요
```

## 라이선스

개인 프로젝트. 뉴스 전문 재배포는 저작권 이슈가 있어 요약+헤드라인+링크로만 발송합니다.
