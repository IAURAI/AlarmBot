# 콕알림 아키텍처 설계

> 상태: 설계 확정 대기 / 작성일: 2026-07-07
> 관련 문서: [apps-in-toss.md](apps-in-toss.md) (앱인토스 미니앱 상세), [restructuring.md](restructuring.md) (구조 재편성 실행 계획), [mvp-serving-spec.md](mvp-serving-spec.md) (서빙 아키텍처·설계 백로그)

## 1. 제품 정의

**콕알림** — 관심 기업 소식 알림 서비스.

| 기능 | 설명 |
|---|---|
| 기업 검색 | 이름 입력 → 기업 리스트 (삼성전자, 카카오, 테슬라 등) |
| 콕 (관심 등록) | 검색 결과에서 콕 버튼 → "내 콕리스트"에 카드로 쌓임 |
| 기업 상세 | 카드 탭 → 오늘의 뉴스·공시·주가 변동 한 화면 |
| 알림 종 | 카드별 종 버튼 → 새 소식 발생 시 휴대폰 푸시 |
| 알림 랜딩 | 푸시 한 줄("삼성전자, 오늘 실적 발표") 탭 → 뉴스 전문 화면 |
| 저장함 | 뉴스 읽다가 저장 → "저장함" 화면에서 재열람 |
| 알림 종류 필터 | 카드별 설정 → 주가 변동만 / 공시만 / 뉴스만 선택 수신 |
| 오늘의 브리핑 | 매일 아침 전체 콕리스트 요약 푸시 → 카드 스와이프 뷰 |

## 2. 핵심 원칙

1. **제품은 하나, 채널은 여러 개.** 카카오톡 봇과 앱인토스는 프레젠테이션 레이어일 뿐이다. 비즈니스 로직(수집→중복제거→긴급도→요약→발송 판단)은 `server/kokalim/core`에만 존재한다.
2. **채널 디렉토리에는 비즈니스 로직 0줄.** 채널 코드는 API 호출 + 채널별 포맷팅만 한다.
3. **레포는 하나, 배포 단위는 독립.** 경로 기반 CI로 바뀐 디렉토리만 배포한다.
4. **판정은 글로벌 1회, 발송은 유저별.** 중복제거·LLM 긴급도 판정은 이벤트(사건) 단위로 1회 수행하고, 유저별 구독 설정으로 필터링해서 fan-out 한다. 유저 수에 LLM 비용이 비례하면 설계 실패다.

## 3. 채널 전략

| | 앱인토스 (메인) | 카카오 봇 (보조) | 텔레그램 (운영) |
|---|---|---|---|
| 역할 | 풀 UI + 실시간 푸시 + 브리핑 | 조회형 인터페이스, 유입 채널 | 개발자 모니터링 |
| 푸시 | **스마트 발송 API** (토스 앱푸시) | 불가 (챗봇은 응답만 가능) | 기존 봇 유지 |
| UI | 웹뷰 (React + TDS) | 챗봇 응답 + 웹뷰 링크 | — |
| 인증 | 토스 로그인 (`appLogin` → userKey) | 카카오 사용자 ID | — |

**카카오 제약 (설계 고정 사항):**
- 카카오 챗봇은 유저 발화에만 응답 가능 — 능동 푸시 불가.
- 알림톡은 사업자 + 템플릿 사전 심사 → 실시간 자유형 뉴스 알림 부적합.
- 친구톡은 채널 친구 한정 + 광고성 규제.
- → **알림(푸시)의 주력 채널은 앱인토스.** 카카오 봇은 "삼성전자 오늘 소식", "콕리스트" 같은 질의응답 + 상세보기 웹뷰 링크로 설계한다.

**웹뷰는 하나만 만든다.** 앱인토스용 웹앱(`apps/toss-webapp`)을 카카오 봇의 "자세히 보기" 링크에서도 그대로 연다. 플랫폼 차이(토스 로그인/브릿지 vs 일반 웹)는 프론트의 `PlatformBridge` 어댑터 한 곳에서 격리한다.

```ts
// apps/toss-webapp/src/platform/bridge.ts
interface PlatformBridge {
  login(): Promise<{ authorizationCode: string } | { kakaoToken: string }>;
  requestNotificationConsent(): Promise<boolean>; // 토스: requestNotificationAgreement / 웹: no-op
  share(url: string): void;
  close(): void;
}
// 구현체: TossBridge(@apps-in-toss/web-framework), WebBridge(일반 브라우저/카카오 인앱)
```

## 4. 레포 구조 (목표)

```
kokalim/
├── docs/                          # 설계 문서 (이 디렉토리)
├── server/                        # Python 백엔드 — 배포 단위: api / workers
│   ├── kokalim/
│   │   ├── core/                  # 도메인 로직 (채널 무관, I/O 최소)
│   │   │   ├── models.py          #   Article, Event, Company, ...
│   │   │   ├── ingest/            #   수집: 네이버 API, RSS  (← news_bot/sources.py)
│   │   │   ├── dedup.py           #   클러스터링             (← news_bot/dedup.py)
│   │   │   ├── triage/            #   긴급도: 키워드+LLM     (← urgency.py, llm.py)
│   │   │   ├── summarize.py       #   한 줄 요약             (← news_bot/summarize.py)
│   │   │   ├── context/           #   연관 컨텍스트 추적     (← context.py, investigate.py, situation.py)
│   │   │   ├── briefing.py        #   아침 브리핑 생성 (신규)
│   │   │   └── notification/      #   이벤트→구독필터→발송큐 fan-out (신규)
│   │   ├── db/                    # 스키마·리포지토리 (신규, ← state.py 대체)
│   │   ├── channels/              # 발송 어댑터 (← notify.py 분해)
│   │   │   ├── toss_push.py       #   스마트 발송 API (mTLS)
│   │   │   ├── telegram.py        #   운영 모니터링
│   │   │   └── kakao_memo.py      #   (레거시) 나에게 보내기
│   │   ├── api/                   # FastAPI — 웹앱용 REST + 카카오 스킬 webhook
│   │   │   ├── routes/            #   companies, watchlist, feed, saved, settings, briefing
│   │   │   ├── auth/              #   토스 로그인 토큰 교환, 세션
│   │   │   └── kakao/             #   카카오 챗봇 스킬 핸들러 (얇은 라우터)
│   │   └── workers/               # 폴러·스케줄러 (← news_bot/run.py)
│   │       ├── poller.py          #   5분 수집-판정-발송 루프
│   │       └── briefing_job.py    #   매일 아침 브리핑 발송
│   ├── tests/
│   └── pyproject.toml
├── apps/
│   └── toss-webapp/               # 앱인토스 미니앱 — 배포 단위: .ait 번들 (콘솔 업로드)
│       ├── granite.config.ts
│       └── src/
├── packages/
│   └── api-schema/                # OpenAPI 스펙 (server가 생성) → TS 타입 생성
├── research/
│   └── method_b/                  # 패널 회귀 연구 (변경 없음, 위치만 이동)
└── .github/workflows/             # 경로 기반 CI
```

**배포 단위 4개:** `server-api`, `server-workers`, `toss-webapp`(.ait 번들 + 콘솔 검수), `kakao-bot`(초기엔 server-api에 포함, 트래픽 분리 필요 시 별도 서비스로 분리 — 라우터가 이미 분리돼 있으므로 이동만 하면 됨).

## 5. 데이터 모델

> **정본: [db-schema.md](db-schema.md)** — 전체 DDL(SQLite 개발 / Postgres 운영), 테이블별 컬럼·제약·소비자 매핑, 인덱스, ERD, fan-out 재검증, 마이그레이션 계획, PII/보존정책은 해당 문서를 따른다. 이 절은 요약이다.

핵심 테이블 (12개):

| 그룹 | 테이블 | 요지 |
|---|---|---|
| 유저 | `users`, `identities` | 채널 무관 정체성 + 토스 userKey/카카오 id 바인딩. `id=0`은 CLI 예약 |
| 구독 | `companies`, `watchlist` | 콕리스트 + 종 버튼(`notify_enabled`) + 종류 필터(`notify_types`) + 토스 동의(`toss_consent_at`) |
| 사건 | `events` | 유저 무관 사건, 판정·요약 1회. `UNIQUE(ticker, type, cluster_key)`로 수집 dedup |
| 발송 | `deliveries` | 유저×채널 발송 큐 겸 로그. PK `(event_id, user_id, channel)`이 재발송 방지 |
| 화면 | `saved_news`, `briefings` | 저장함, 아침 브리핑 payload |
| 컨텍스트 | `situations`, `situation_timeline` | 감시 유닛별 국면(글로벌, user 무관) — `situations.json` 승계 |
| 카카오 | `kakao_messages` | 봇 대화 로그 (90일 보존, PII 정책은 정본 §8) |
| 전환 | `legacy_seen` | seen-set 마이그레이션용 시한부(24h) 테이블 |

- DB: 개발 SQLite → **운영 Supabase(관리형 Postgres) 확정**. 접근은 서버 단일 클라이언트 + 전 테이블 RLS 가드 — 규칙은 [db-schema.md](db-schema.md) §4.3.
- 기존 `state.py`(seen-set 파일)는 `deliveries`+`legacy_seen`으로, `situations.json`은 `situations`+`situation_timeline`으로 흡수한다.

## 6. 알림 파이프라인 (fan-out)

```
[workers/poller — 5분 주기]
  수집(ingest) → 클러스터링(dedup) → 이벤트 upsert(events)
    → 긴급도 판정(triage: 키워드 하드게이트 → LLM)     ← 이벤트당 1회
    → 요약 한 줄(summarize)
    → fan-out: 알림 켠 유저 × 종류 필터 × 토스 동의 가드 → deliveries 'pending' 적재
      (확정 SQL은 db-schema.md §6 — INSERT OR IGNORE 멱등, 발송은 2단계 워커로 분리)
[workers/sender — 발송 워커]
  deliveries 'pending' 배치 조회 → 템플릿별 bulk 발송(스마트 발송)
    → 응답 반영: status/sent_at/fail_reason/provider_content_id
```

> 개념도이며, 확정 쿼리·스로틀/bulk 배칭 규칙은 [db-schema.md](db-schema.md) §6이 정본이다.

- 토스 스마트 발송은 **userKey당 분당 10회 제한**, 대량 발송은 `send-bulk-message`(최대 2,500건/회). 같은 이벤트를 다수 유저에게 보낼 땐 bulk API로 배칭한다.
- 브리핑은 `workers/briefing_job`이 매일 아침 유저별 payload를 만들어 `briefings`에 저장 후 bulk 발송. 푸시 탭 → 웹뷰 `/briefing/:id` 랜딩.
- 텔레그램은 운영자 모니터링 채널로 유지 (파이프라인 오류·발송 통계).

## 7. API 표면 (요약)

```
POST /auth/toss/login          # authorizationCode → 토큰 교환 → 세션 발급
GET  /companies?q=삼성          # 기업 검색
GET  /watchlist                # 내 콕리스트
POST /watchlist/{ticker}       # 콕
DELETE /watchlist/{ticker}
PATCH /watchlist/{ticker}      # notify_enabled, notify_types
GET  /companies/{ticker}/today # 상세: 오늘의 뉴스·공시·주가
GET  /events/{id}              # 알림 랜딩 (뉴스 전문 화면 데이터)
POST /saved/{event_id}         # 저장
GET  /saved
GET  /briefings/{id}           # 브리핑 카드 뷰
POST /kakao/skill              # 카카오 챗봇 webhook (내부에서 위 로직 재사용)
```

OpenAPI 스펙을 `packages/api-schema`로 내보내고 프론트 TS 타입을 생성한다. **하위호환 원칙: 필드 추가만 허용, 제거·의미 변경은 `/v2`.** (앱인토스 심사 중인 구버전 웹뷰가 항상 최신 API와 공존해야 하므로.)

## 8. CI / 배포

```yaml
# 경로 기반 트리거 (개념)
server-api:    server/**            → 컨테이너 배포 (즉시)
server-workers: server/**           → 컨테이너 배포 (즉시)
toss-webapp:   apps/toss-webapp/**, packages/** → .ait 빌드 → 콘솔 업로드 → 검수 (리드타임 있음)
```

- 앱인토스는 검수가 있으므로 웹뷰 신기능은 **feature flag**(서버 플래그)로 감춘 채 심사 통과시키고 서버에서 켠다.
- 시크릿: `.env`(로컬) / 배포 환경변수. mTLS 인증서(스마트 발송·토스 로그인 서버 API용)는 배포 시크릿 스토어에만 둔다. `.env` 커밋 금지 유지.

## 9. 마일스톤

1. **M1 — 서버 코어 승격**: `news_bot` → `server/kokalim` 재편 + DB 도입 (멀티유저화). [restructuring.md](restructuring.md) 참조.
2. **M2 — 앱인토스 단독 출시**: 토스 로그인 + 콕리스트 + 상세 + 스마트 발송 알림 + 저장함. [apps-in-toss.md](apps-in-toss.md) 참조.
3. **M3 — 브리핑**: 아침 브리핑 job + 카드 스와이프 뷰.
4. **M4 — 카카오 봇**: 스킬 webhook + 응답 포맷터 (API 재사용, 전체 공수의 10~15% 수준).

카카오 봇을 먼저 만들지 않는다 — 푸시 불가 제약에 코어가 왜곡되기 때문. 푸시가 되는 앱인토스가 제품의 존재 이유이므로 M2가 1차 출시다.
