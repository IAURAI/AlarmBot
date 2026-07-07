# 구조 재편성 실행 계획 (M1)

> 상태: 실행 대기 — 이 문서만 보고 다른 세션에서 실행 가능하도록 작성됨
> 상위 문서: [architecture.md](architecture.md) (목표 구조·데이터 모델), [apps-in-toss.md](apps-in-toss.md)

## 목표

현재 단일 유저 CLI인 `news_bot`을 멀티유저 서비스 백엔드 `server/kokalim`으로 재편한다.
**동작 보존이 최우선** — 각 단계 후 기존 테스트가 전부 통과해야 하고, `--offline --dry-run` 스모크가 동일하게 동작해야 한다.

## 현재 상태 (2026-07-07)

```
kokalim/
├── news_bot/          # 실시간 뉴스 긴급 알림 봇 (단일 유저 CLI, 텔레그램/카카오 memo)
│   ├── models.py sources.py dedup.py urgency.py llm.py summarize.py
│   ├── pipeline.py pipeline_context.py context.py investigate.py situation.py
│   ├── notify.py state.py config.py run.py usage.py
│   ├── fixtures/ tests/(4개)
├── method_b/          # 패널 회귀 연구 파이프라인
├── tests/             # method_b 테스트 (4개)
├── requirements.txt .env.example
```

- 검증 명령(재편 전 기준선): `.venv/bin/python -m pytest news_bot/tests/ tests/ -q` (네트워크·키 불필요)
- 오프라인 스모크: `.venv/bin/python -m news_bot.run --offline --dry-run`

## 파일 매핑 (news_bot → server/kokalim)

| 현재 | 이동 후 | 변경 내용 |
|---|---|---|
| `news_bot/models.py` | `server/kokalim/core/models.py` | 이동 + `Event`, `User` 등 신규 모델 추가 |
| `news_bot/sources.py` | `server/kokalim/core/ingest/sources.py` | 이동만 |
| `news_bot/dedup.py` | `server/kokalim/core/dedup.py` | 이동만 |
| `news_bot/urgency.py` | `server/kokalim/core/triage/urgency.py` | 이동만 |
| `news_bot/llm.py` | `server/kokalim/core/triage/llm.py` | 이동만 |
| `news_bot/summarize.py` | `server/kokalim/core/summarize.py` | 이동만 |
| `news_bot/context.py` | `server/kokalim/core/context/graph.py` | 이동만 |
| `news_bot/investigate.py` | `server/kokalim/core/context/investigate.py` | 이동만 |
| `news_bot/situation.py` | `server/kokalim/core/context/situation.py` | 이동만 |
| `news_bot/pipeline.py` | `server/kokalim/core/pipeline.py` | 이동만 |
| `news_bot/pipeline_context.py` | `server/kokalim/core/pipeline_context.py` | 이동만 |
| `news_bot/usage.py` | `server/kokalim/core/usage.py` | 이동만 |
| `news_bot/notify.py` | `server/kokalim/channels/{telegram,kakao_memo,console}.py` | **분해**: 플랫폼 스위치 → 채널 어댑터 + 공통 `Notifier` 프로토콜 |
| `news_bot/state.py` | `server/kokalim/db/` | **대체**: seen-set 파일 → `deliveries` 테이블 (단일 유저 모드는 user_id=0으로 호환) |
| `news_bot/config.py` | `server/kokalim/config.py` | 이동 + 환경변수 로딩 유지 |
| `news_bot/run.py` | `server/kokalim/workers/poller.py` + `server/kokalim/cli.py` | CLI 플래그 호환 유지 (`--offline --dry-run --loop --context ...`) |
| `news_bot/fixtures/` | `server/kokalim/fixtures/` | 이동만 |
| `news_bot/tests/` | `server/tests/` | import 경로만 수정 |
| `method_b/` | `research/method_b/` | 이동만 (import 자립적) |
| `tests/` (method_b용) | `research/method_b/tests/` | 이동 + pytest 경로 수정 |
| `requirements.txt` | `server/pyproject.toml` | 의존성 이관 + fastapi, sqlalchemy(or sqlite3 직접), uvicorn 추가 |

## 실행 단계

각 단계는 독립 커밋. 단계 끝의 검증이 실패하면 다음 단계로 넘어가지 않는다.

### Step 1 — 디렉토리 이동 (동작 불변)

1. `server/kokalim/` 트리 생성, 위 매핑대로 파일 이동 (`git mv`).
2. `notify.py`·`state.py`·`run.py`는 이 단계에서 **내용 수정 없이** `server/kokalim/notify.py` 등으로 우선 이동 (분해는 Step 3·4).
3. import 경로 일괄 수정 (`news_bot.` → `kokalim.`), `server/pyproject.toml` 작성 (editable install).
4. `method_b` → `research/method_b`, 루트 `tests/` → `research/method_b/tests/`.
5. 루트 README의 실행 명령 갱신.

**검증:** `pytest server/tests research/method_b/tests -q` 전체 통과 + `python -m kokalim.cli --offline --dry-run` 출력이 재편 전과 동일.

### Step 2 — DB 도입

1. [architecture.md §5](architecture.md) 스키마로 `server/kokalim/db/schema.sql` + 리포지토리 모듈 작성 (개발: SQLite).
2. `state.py` seen-set → `deliveries` 테이블로 대체. 단일 유저 CLI 모드는 `user_id=0` 고정으로 기존 동작 보존.
3. `situations.json` → `situations` 테이블 (JSON payload 컬럼으로 시작해도 됨).
4. config의 watchlist → `watchlist` 테이블 시드 스크립트 (`server/scripts/seed_watchlist.py`).

**검증:** 기존 테스트 통과 + dedup/재발송 방지 동작을 검증하는 DB 기반 테스트 신규 추가.

### Step 3 — notify 분해 → channels

1. `Notifier` 프로토콜 정의 (`send(user, payload) -> DeliveryResult`).
2. `channels/telegram.py`, `channels/kakao_memo.py`, `channels/console.py`로 분해.
3. `channels/toss_push.py` 스켈레톤 추가 — 스마트 발송 규격([apps-in-toss.md §5](apps-in-toss.md))대로 구현하되, mTLS 인증서·templateSetCode가 없으면 조용히 비활성 (기존 자격증명 폴백 관행 유지).
4. `core/notification/fanout.py` — watchlist·notify_types·toss_consent_at·deliveries 기반 수신자 산출.

**검증:** 채널별 단위 테스트(모킹) + fan-out 필터 테스트 (notify_types 필터, 동의 가드, 중복 발송 차단).

### Step 4 — API 추가

1. `server/kokalim/api/` FastAPI 앱 — [architecture.md §7](architecture.md)의 라우트.
2. `api/auth/toss.py` — authorizationCode → generate-token → login-me → identities upsert → 자체 JWT.
3. OpenAPI 스펙 export → `packages/api-schema/openapi.json`.
4. 카카오 스킬 webhook은 라우터 스켈레톤만 (`POST /kakao/skill`, M4에서 구현).

**검증:** httpx TestClient 기반 라우트 테스트 (auth는 토스 API 모킹).

### Step 5 — workers 정리

1. `workers/poller.py` — 5분 루프를 DB fan-out 경로로 전환.
2. `workers/briefing_job.py` — 유저별 브리핑 payload 생성 + bulk 발송 (M3 본구현 전 스켈레톤 가능).
3. CLI(`kokalim.cli`)는 기존 플래그 유지한 채 workers를 호출하는 얇은 래퍼로.

**검증:** `--offline --dry-run` 스모크 + poller 1사이클 통합 테스트 (픽스처 → events → fan-out → console 채널).

### Step 6 — 프론트 스캐폴드 + CI

1. `apps/toss-webapp` — `npx create-ait-app` 산출물 커밋, `granite.config.ts`는 [apps-in-toss.md §2](apps-in-toss.md) 값.
2. `.github/workflows/` 경로 기반 트리거: `server/**` → 테스트+배포, `apps/toss-webapp/**` → 빌드(.ait 아티팩트), `research/**` → 테스트만.
3. `.env.example`에 신규 키 추가: `TOSS_MTLS_CERT_PATH`, `TOSS_MTLS_KEY_PATH`, `KOK_TEMPLATE_EVENT`, `KOK_TEMPLATE_PRICE`, `KOK_TEMPLATE_BRIEFING`, `JWT_SECRET`, `DATABASE_URL`.

**검증:** CI 전 잡 그린 + 웹앱 `npm run build` 성공(.ait 생성).

## 하지 않는 것 (M1 범위 밖)

- 카카오 챗봇 스킬 구현 (M4)
- 브리핑 카드 UI·앱인토스 콘솔 검수 절차 (M2/M3)
- `research/method_b` 코드 수정 — 이동만 하고 내용은 건드리지 않는다
- 텔레그램 발송 제거 — 운영 모니터링 채널로 유지

## 완료 기준 (Definition of Done)

- [ ] 루트에 `news_bot/` 디렉토리가 없고 `server/`, `apps/`, `packages/`, `research/`, `docs/` 구조로 재편됨
- [ ] 이동 전 존재하던 테스트 8개 파일 전부 신규 경로에서 통과
- [ ] `python -m kokalim.cli --offline --dry-run` 이 재편 전과 동등한 출력
- [ ] `deliveries` 기반 재발송 방지 + fan-out 필터 테스트 통과
- [ ] FastAPI 앱 기동 + OpenAPI export 동작
- [ ] `apps/toss-webapp` 빌드로 `.ait` 생성 확인
- [ ] 루트/서브 README가 새 구조·명령 기준으로 갱신됨
