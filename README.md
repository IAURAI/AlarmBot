# 콕알림 (kokalim) — 관심 기업 소식 알림 서비스 + 언론 파급력 연구

한국 주식시장을 위한 코드가 한 저장소에 있습니다. 현재 M1(서버 코어 승격) 재편 구조입니다.

> **설계 문서**: 앱인토스 미니앱 + 카카오 봇 멀티채널 서비스 설계가 [`docs/`](docs/)에 있습니다 —
> [architecture.md](docs/architecture.md)(전체 아키텍처) · [apps-in-toss.md](docs/apps-in-toss.md)(앱인토스 상세) · [restructuring.md](docs/restructuring.md)(구조 재편성 실행 계획, M1).

## 레포 구조

```
kokalim/
├── docs/                     # 설계 문서
├── server/                   # 백엔드 (배포 단위: api / workers) — 편집 설치되는 kokalim 패키지
│   ├── kokalim/
│   │   ├── core/             # 도메인 로직 (채널 무관): models, dedup, summarize, pipeline*
│   │   │   ├── ingest/       #   수집: 네이버 API + RSS + 픽스처
│   │   │   ├── triage/       #   긴급도: 키워드 하드게이트 + LLM
│   │   │   └── context/      #   연관 컨텍스트 추적 (graph/investigate/situation)
│   │   ├── channels 전 단계  # notify.py (Step 3에서 channels/로 분해 예정)
│   │   ├── config.py cli.py run.py state.py
│   │   └── fixtures/         # 오프라인 픽스처
│   ├── tests/                # 서버 코어 테스트
│   └── pyproject.toml        # kokalim 패키지 (pip install -e server)
├── research/
│   └── method_b/             # 패널 회귀 연구 (자립적, 위치만 이동)
│       └── tests/
└── pyproject.toml            # 리포 전체 pytest 설정 (pythonpath)
```

> `apps/toss-webapp`(미니앱)·`packages/api-schema`(OpenAPI)는 이후 마일스톤(M2~)에서 추가됩니다. [restructuring.md](docs/restructuring.md) 참조.

## 셋업

```bash
python -m venv .venv
.venv/bin/pip install -e server          # kokalim 패키지 편집 설치
.venv/bin/pip install -r requirements.txt # research/method_b용 과학 스택
cp .env.example .env                       # 값 채우기 (아래 자격증명 참고)
```

## 1. `server/kokalim` — 실시간 뉴스 긴급 알림 (메인)

관심종목 뉴스를 5분마다 **수집 → 중복제거 → 키워드 하드게이트 → LLM 최종 중요도 판정 → 발송**.

- **소스**: 네이버 뉴스검색 API(종목 정조준) + 경제 RSS(연합·매경·한경·조선비즈)
- **신선도 필터**(기본 12h)로 옛 기사 재발송 차단
- **필터**: 키워드로 하드 게이트 → 통과분만 LLM이 알림 여부 최종 결정
- **LLM 백엔드**: codex(구독제, 기본, 토큰비용 0) 또는 Anthropic API — `config.py`에서 전환
- **연관 컨텍스트 추적**(`--context`): 공급망·경쟁사·테마까지 감시하고 상황 변화를 시간순 추적

→ 상세: [`server/kokalim/README.md`](server/kokalim/README.md)

```bash
.venv/bin/python -m kokalim.cli --offline --dry-run          # 오프라인 확인(네트워크·키 불필요)
.venv/bin/python -m kokalim.cli --loop --platform telegram   # 실시간 운영
```

## 2. `research/method_b` — "개인 거래비중 × 관심 이벤트" 패널 회귀 (연구)

언론/관심 이벤트의 주가 파급력이 개인 투자자 거래비중과 함께 커지는지(단기 증폭·중기 반전) 검정하는
종목-일 패널 회귀 파이프라인(KOSPI+KOSDAQ, pykrx).

→ 상세: [`research/method_b/README.md`](research/method_b/README.md)

## 자격증명 (`.env`)

`.env.example`을 `.env`로 복사해 채우세요. **`.env`는 절대 커밋 금지**(`.gitignore` 처리됨).

| 키 | 용도 | 발급 |
|---|---|---|
| `NEWSBOT_TELEGRAM_BOT_TOKEN` / `_CHAT_ID` | 텔레그램 발송 | BotFather |
| `NAVER_CLIENT_ID` / `_SECRET` | 종목 뉴스 검색 | developers.naver.com (앱에 **"검색"** API 추가 필수) |
| `KRX_ID` / `KRX_PW` | method_b 데이터 | data.krx.co.kr |

자격증명이 없으면 해당 경로는 조용히 비활성화되고 콘솔/휴리스틱으로 폴백합니다.

## 테스트

```bash
.venv/bin/python -m pytest -q   # 리포 루트에서 server/tests + research/method_b/tests 전부 (네트워크·키 불필요)
```

## 라이선스

개인 프로젝트. 뉴스 전문 재배포는 저작권 이슈가 있어 요약+헤드라인+링크로만 발송합니다.
