# 콕알림 DB 스키마 정본 (Single Source of Truth)

> 상태: 정본 / 작성일: 2026-07-07
> 이 문서가 DB 스키마의 유일한 정본이다. [architecture.md](architecture.md) §5는 요약+링크만 유지한다.
> 근거 코드(M1 Step 1 재편 완료 기준): `server/kokalim/state.py`, `server/kokalim/core/context/situation.py`,
> `server/kokalim/core/context/investigate.py`, `server/kokalim/core/context/graph.py`,
> `server/kokalim/core/models.py`, `server/kokalim/config.py`

## 0. 설계 원칙

1. **DB는 채널 공용 코어다.** 앱인토스가 주 소비자이고, 카카오 봇·텔레그램(운영)·CLI가 같은 스키마를 공유한다. 채널 전용 스키마 분기는 만들지 않는다.
2. **판정은 글로벌 1회, 발송은 유저별** ([architecture.md](architecture.md) §2.4). `events`는 유저 무관 사건, `deliveries`는 유저×채널 발송 상태.
3. **운영 DB는 Supabase(관리형 Postgres)로 확정.** §4.2 Postgres DDL이 그대로 Supabase DDL이다. 개발/CLI 오프라인 테스트는 SQLite 유지. 운영 접근 규칙은 §4.3.
4. **하위호환: 필드 추가만 허용.** 컬럼 제거·의미 변경은 새 테이블/`/v2`로 ([architecture.md](architecture.md) §7). 모든 신규 컬럼은 NULL 허용 또는 DEFAULT 필수.
5. **단일유저 CLI 호환:** `user_id = 0`을 예약 유저로 시드하고 기존 CLI 경로는 이 유저로 동작한다 ([restructuring.md](restructuring.md) Step 2).

## 1. ERD (텍스트)

```
users 1 ──── n identities            (provider: toss|kakao, UNIQUE(provider, provider_user_id))
users 1 ──── n watchlist n ──── 1 companies
users 1 ──── n saved_news n ──── 1 events
users 1 ──── n briefings
users 1 ──── n deliveries n ──── 1 events
users 1 ──── n kakao_messages
companies 1 ─ n events               (events.ticker FK)
situations 1 ─ n situation_timeline  (글로벌 — user FK 없음, "판정은 글로벌 1회")
legacy_seen                          (독립 전환 테이블, TTL 24h 후 drop)
```

`situations`/`situation_timeline`은 의도적으로 유저와 무관하다. 연관 컨텍스트 판정은 이벤트 판정과 마찬가지로 글로벌 1회이며, 유저별 개인화가 필요해지는 시점에 별도 테이블로 확장한다(원칙 4).

## 2. 테이블 카탈로그

| 테이블 | 목적 | 주 쓰기 주체 | 주 읽기 주체 |
|---|---|---|---|
| `users` | 채널 무관 유저 정체성 | api(auth) | 전 채널 |
| `identities` | 채널 계정 바인딩 (토스 userKey / 카카오 id) | api(auth), api(kakao) | api |
| `companies` | 기업 마스터 (검색 대상) | seed 스크립트 | api(검색), workers |
| `watchlist` | 콕리스트 + 종 버튼 + 종류 필터 + 토스 동의 | api(웹뷰) | api, workers(fan-out) |
| `events` | 채널·유저 무관 뉴스/공시/주가 사건 | workers(poller) | api(상세·랜딩·저장함), workers |
| `deliveries` | 유저×채널 발송 큐 겸 로그 (재발송 방지) | workers | workers, 운영 모니터링 |
| `saved_news` | 저장함 | api(웹뷰) | api |
| `briefings` | 아침 브리핑 payload + 발송 기록 | workers(briefing_job) | api(브리핑 뷰) |
| `situations` | 감시 유닛별 현재 국면 (요약/스탠스) | workers(context) | workers, api(상세 확장 시) |
| `situation_timeline` | 유닛별 실질 변화 타임라인 | workers(context) | workers(LLM prior), api |
| `kakao_messages` | 카카오 봇 대화 로그 (CS·어뷰징·발화 분석) | api(kakao) | 운영, 분석 배치 |
| `legacy_seen` | seen-set 전환용 시한부 테이블 | 마이그레이션 1회 | workers(poller, 24h간) |

## 3. 테이블 상세

### 3.1 `users`

채널 무관 단일 유저. 개인정보 컬럼 없음(원칙: 식별자 최소주의).

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `id` | INTEGER | PK | `0` = 단일유저 CLI 예약 (시드 시 삽입) |
| `created_at` | TIMESTAMP | NOT NULL DEFAULT now | |
| `briefing_time` | TIME(SQLite: TEXT 'HH:MM') | NULL | 지정 시간 브리핑 수신 시각 — **KST로 해석**. NULL=미수신. (m002, [mvp-decisions.md](mvp-decisions.md)) |

읽기/쓰기: api(auth)가 생성. 모든 채널이 FK로 참조만.

### 3.2 `identities`

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `user_id` | INTEGER | FK→users, NOT NULL | |
| `provider` | TEXT | CHECK IN ('toss','kakao','telegram'), NOT NULL | telegram은 m002에서 추가 |
| `provider_user_id` | TEXT | NOT NULL | 토스 `userKey`(숫자지만 TEXT 저장 — 앱 단위 고유, [apps-in-toss.md](apps-in-toss.md) §4) / 카카오 봇 user id / 텔레그램 chat_id |
| `created_at` | TIMESTAMP | NOT NULL DEFAULT now | |

- `UNIQUE(provider, provider_user_id)` — 로그인 upsert 키.
- `PRIMARY KEY(user_id, provider)` — 한 유저당 채널별 1계정.
- 토스 accessToken/refreshToken은 **저장하지 않는다**. 로그인 시 `login-me`로 userKey만 취득하면 이후 재조회가 불필요하다([apps-in-toss.md](apps-in-toss.md) §4). 토큰 보관이 필요해지면 별도 테이블 추가(원칙 4).

읽기/쓰기: api(auth)의 토스 로그인 교환, api(kakao)의 최초 발화 시 kakao identity 자동 생성.

### 3.3 `companies`

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `ticker` | TEXT | PK | 권장 포맷 `{시장}:{심볼}` 예: `KRX:005930`, `NASDAQ:TSLA` (아래 결정 필요 D1) |
| `name` | TEXT | NOT NULL | 표시명 ("삼성전자") |
| `market` | TEXT | NOT NULL | `KOSPI` / `KOSDAQ` / `NASDAQ` … |
| `aliases` | TEXT(JSON) / Postgres JSONB | NOT NULL DEFAULT '[]' | 검색 별칭 ["삼전", "Samsung Electronics"] |

읽기: api `GET /companies?q=` (name/aliases LIKE → 규모 커지면 FTS), workers(수집 키워드).
쓰기: `server/scripts/seed_companies.py` (결정 필요 D1·D2).

### 3.4 `watchlist`

콕리스트 + 알림 설정. fan-out의 좌변.

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `user_id` | INTEGER | FK→users | |
| `ticker` | TEXT | FK→companies | |
| `created_at` | TIMESTAMP | NOT NULL DEFAULT now | 콕 누른 시각 |
| `notify_enabled` | BOOLEAN(SQLite: INTEGER 0/1) | NOT NULL DEFAULT 0 | 종 버튼 |
| `notify_types` | TEXT(JSON) / JSONB | NOT NULL DEFAULT '["news","disclosure","price"]' | 종류 필터 |
| `toss_consent_at` | TIMESTAMP | NULL | **앱인토스 스마트 발송 동의 전용.** `requestNotificationAgreement` 성공 시각. NULL이면 토스 발송 금지 ([apps-in-toss.md](apps-in-toss.md) §5.1) |

- `PRIMARY KEY(user_id, ticker)`.
- 카카오 유저도 같은 행을 공유한다. 카카오는 능동 푸시가 없으므로 `toss_consent_at`은 토스 채널 발송 가드에만 쓰인다.

읽기: api `GET /watchlist`, workers fan-out. 쓰기: api `POST/DELETE/PATCH /watchlist/{ticker}` — **콕/설정 쓰기는 공용 웹뷰에서만** 발생하고, 카카오 봇은 조회만 한다([architecture.md](architecture.md) §7).

### 3.5 `events`

유저 무관 "사건". 판정·요약은 여기서 1회. 컬럼은 `core/models.py`의 `Article`/`Cluster` 필드에서 도출.

| 컬럼 | 타입 | 제약 | 설명 (근거 코드) |
|---|---|---|---|
| `id` | INTEGER / BIGSERIAL | PK | |
| `ticker` | TEXT | FK→companies, NOT NULL | 대상 종목 |
| `type` | TEXT | CHECK IN ('news','disclosure','price'), NOT NULL | |
| `cluster_key` | TEXT | NOT NULL | sha1(정규화 제목)[:16] — `state.py _key()`와 동일 알고리즘. 재수집 dedup 키 |
| `headline` | TEXT | NOT NULL | 대표 제목 (`Cluster.title`) |
| `summary_line` | TEXT | NOT NULL DEFAULT '' | LLM 한 줄 요약 → 푸시 `headline` 변수 |
| `url` | TEXT | NOT NULL | 대표 기사 원문 링크 (`Article.link`) |
| `source` | TEXT | NOT NULL DEFAULT '' | 대표 매체 (`Article.source`) |
| `source_count` | INTEGER | NOT NULL DEFAULT 1 | 보도 매체 수 (`Cluster.source_count`) |
| `keyword_score` | REAL | NOT NULL DEFAULT 0 | 키워드 게이트 점수 (`Cluster.keyword_score`) |
| `urgency_score` | REAL | NULL | LLM 최종 중요도 (keyword 모드면 NULL) |
| `reason` | TEXT | NOT NULL DEFAULT '' | 판정 사유 (`Cluster.reason`) |
| `published_at` | TIMESTAMP | NOT NULL | 발행 시각 (`Article.published_ts`) |
| `created_at` | TIMESTAMP | NOT NULL DEFAULT now | 수집 시각 |

- `UNIQUE(ticker, type, cluster_key)` — 같은 사건 재삽입 방지. seen-set의 역할을 이벤트 레벨에서 승계 (TTL은 UNIQUE 위반 시 무시-갱신으로 대체).
- 뉴스 전문은 저장하지 않는다(저작권 — 요약+헤드라인+링크만, [architecture.md](architecture.md) 기존 정책).

읽기: api `GET /companies/{ticker}/today`, `GET /events/{id}`(알림 랜딩), `GET /saved`, 카카오 봇 질의응답. 쓰기: workers(poller) upsert.

### 3.6 `deliveries`

발송 **큐 겸 로그**. `state.py` seen-set(파일)의 멀티유저 대체물. status 라이프사이클로 스로틀·bulk 배칭·실패 기록까지 커버한다.

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `event_id` | INTEGER | FK→events | |
| `user_id` | INTEGER | FK→users | |
| `channel` | TEXT | CHECK IN ('toss','telegram','console'), NOT NULL | **'kakao' 없음** — 카카오는 능동 푸시 불가이므로 푸시 row가 생기지 않는다 |
| `status` | TEXT | CHECK IN ('pending','sent','failed','skipped'), NOT NULL DEFAULT 'pending' | |
| `fail_reason` | TEXT | NULL | 스마트 발송 응답 `fail[].reachedFailReason` ([apps-in-toss.md](apps-in-toss.md) §5.3) |
| `provider_content_id` | TEXT | NULL | 스마트 발송 응답 `contentId` |
| `attempt` | INTEGER | NOT NULL DEFAULT 0 | 재시도 횟수 |
| `created_at` | TIMESTAMP | NOT NULL DEFAULT now | fan-out 시각 |
| `sent_at` | TIMESTAMP | NULL | 발송 완료 시각 |

- `PRIMARY KEY(event_id, user_id, channel)` — **재발송 방지의 핵심**. fan-out이 INSERT OR IGNORE로 멱등.
- CLI 단일유저 모드: `user_id=0`, `channel='console'|'telegram'`.

읽기/쓰기: workers 전용. 운영 모니터링(텔레그램 리포트)이 실패 집계를 읽는다.

### 3.7 `saved_news`

| 컬럼 | 타입 | 제약 |
|---|---|---|
| `user_id` | INTEGER | FK→users |
| `event_id` | INTEGER | FK→events |
| `saved_at` | TIMESTAMP | NOT NULL DEFAULT now |

`PRIMARY KEY(user_id, event_id)`. 읽기/쓰기: api `POST /saved/{event_id}`, `GET /saved` (웹뷰 전용 화면 — 카카오 봇 읽기는 허용).

### 3.8 `briefings`

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `id` | INTEGER / BIGSERIAL | PK | 푸시 랜딩 `route=/briefing/{id}` |
| `user_id` | INTEGER | FK→users, NOT NULL | |
| `date` | TEXT(ISO date) / DATE | NOT NULL | |
| `payload` | TEXT(JSON) / JSONB | NOT NULL | 카드 배열: `[{ticker, headline, summary_line, event_ids[]}, …]` |
| `sent_at` | TIMESTAMP | NULL | 스마트 발송 완료 시각. NULL=생성만 됨 |
| `created_at` | TIMESTAMP | NOT NULL DEFAULT now | |

- `UNIQUE(user_id, date)` — 하루 1브리핑.
- 브리핑은 event 단위가 아니므로 `deliveries`를 거치지 않고 자체 `sent_at`으로 발송 기록. **카카오에는 브리핑 발송이 생기지 않는다**(푸시 불가) — 카카오 봇이 "오늘 브리핑" 질의에 이 테이블을 읽는 것은 허용.

### 3.9 `situations` + `situation_timeline` — 스펙 공백 A 확정

`core/context/situation.py`의 실제 payload에서 도출:

```python
# situation.py — 엔트리 구조 (코드 원문)
entry = {"summary": "", "stance": "neutral", "updated": None, "timeline": []}
entry["timeline"].append({"at": ..., "change": assessment.changed, "importance": assessment.importance})
entry["timeline"] = entry["timeline"][-20:]   # 최근 20건만 보관
# 키: WatchItem.key = f"{company}::{name}"   (graph.py)
# stance enum: positive|negative|neutral, importance: 0~10 정수  (investigate.py _ASSESS_SCHEMA)
```

**`situations`** (유닛별 현재 국면 — 글로벌, user FK 없음):

| 컬럼 | 타입 | 제약 | 근거 |
|---|---|---|---|
| `unit_key` | TEXT | PK | `"{company}::{name}"` (`WatchItem.key`) |
| `company` | TEXT | NOT NULL | 키 분해 — 종목별 조회용 |
| `entity_name` | TEXT | NOT NULL | 키 분해 (self면 company와 동일) |
| `kind` | TEXT | CHECK IN ('self','related','theme'), NULL | `WatchItem.kind`. 기존 JSON에 없어 마이그레이션 시 그래프 대조로 채우되 미상이면 NULL |
| `summary` | TEXT | NOT NULL DEFAULT '' | 롤링 요약 (`entry["summary"]`) |
| `stance` | TEXT | CHECK IN ('positive','negative','neutral'), NOT NULL DEFAULT 'neutral' | `_ASSESS_SCHEMA` enum |
| `updated_at` | TIMESTAMP | NULL | `entry["updated"]` (ISO) |

**`situation_timeline`** (실질 변화 이력):

| 컬럼 | 타입 | 제약 | 근거 |
|---|---|---|---|
| `id` | INTEGER / BIGSERIAL | PK | |
| `unit_key` | TEXT | FK→situations, NOT NULL | |
| `at` | TIMESTAMP | NOT NULL | `timeline[].at` |
| `change` | TEXT | NOT NULL | `timeline[].change` (한 줄) |
| `importance` | INTEGER | CHECK 0~10, NOT NULL | `timeline[].importance` |

- JSON 컬럼 대신 자식 테이블을 채택한 이유: (1) "최근 N건" 보존을 DELETE로 표현 가능, (2) `--report` 주기 요약이 기간 조회를 함, (3) 원칙 4(필드 추가만)와 잘 맞음. 애플리케이션은 기존 20건 캡 동작을 유닛별 DELETE로 유지한다(코드 `[-20:]`와 동등).
- LLM 확장 캐시(`graph_expansion.json`)와 codex usage 로그는 **유저 데이터가 아닌 파생 캐시/운영 텔레메트리**이므로 파일 유지, DB 범위 밖.

읽기/쓰기: workers(context 파이프라인)가 평가 prior로 읽고 갱신. api는 기업 상세의 "연관 동향" 확장 시 읽기(현행 API 표면엔 미노출 — 추가 시 필드 추가로만).

### 3.10 `kakao_messages` — 스펙 공백 B 확정

**저장한다.** 판단 근거: (1) CS 분쟁 시 대화 재구성, (2) 어뷰징/스팸 발화 rate-limit 근거, (3) 인텐트 분류 개선용 학습 데이터. 단, 자유 발화에는 개인정보가 유입될 수 있으므로 보존정책이 스키마의 일부다(§8).

| 컬럼 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `id` | INTEGER / BIGSERIAL | PK | |
| `user_id` | INTEGER | FK→users, NOT NULL | 최초 발화 시 identities(provider='kakao') 자동 생성 후 연결 |
| `direction` | TEXT | CHECK IN ('in','out'), NOT NULL | in=유저 발화, out=봇 응답 |
| `text` | TEXT | NOT NULL | 발화/응답 본문 (보존기간 경과 시 파기 대상) |
| `intent` | TEXT | NULL | 분류된 인텐트 (`company_today`, `watchlist_show`, …) — 분류 실패 시 NULL |
| `payload` | TEXT(JSON) / JSONB | NULL | 스킬 요청/응답 부가 구조 (블록·버튼 등), out row 위주 |
| `created_at` | TIMESTAMP | NOT NULL DEFAULT now | |

읽기/쓰기: api(kakao) webhook이 in/out 각 1행 기록. 분석은 배치가 `intent`/집계만 사용.

### 3.11 `legacy_seen` — 전환 전용 (시한부)

`state.py` seen-set은 `sha1(정규화 제목)[:16] → ISO시각` 맵이고 event_id와 매핑이 불가능하다. 가짜 이벤트를 합성하는 대신, TTL이 24h(`config.seen_ttl_hours`)라는 점을 이용해 **컷오버 후 24시간만 참조하는 전환 테이블**로 처리한다.

| 컬럼 | 타입 | 제약 |
|---|---|---|
| `title_hash` | TEXT | PK — `state.py _key()`와 동일 sha1[:16] |
| `seen_at` | TIMESTAMP | NOT NULL |

poller는 컷오버 후 24h 동안 `events` UNIQUE 검사에 더해 `legacy_seen`을 조회하고, 이후 테이블을 DROP한다(§7).

## 4. DDL

### 4.1 SQLite (개발) — 실행 검증됨

```sql
-- kokalim sqlite ddl v1
PRAGMA foreign_keys = ON;

CREATE TABLE users (
  id            INTEGER PRIMARY KEY,                      -- PG: BIGINT GENERATED ALWAYS AS IDENTITY
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),  -- PG: timestamptz NOT NULL DEFAULT now()
  briefing_time TEXT                                      -- PG: TIME — 'HH:MM' KST, NULL=브리핑 미수신 (m002)
);

CREATE TABLE identities (
  user_id           INTEGER NOT NULL REFERENCES users(id),
  provider          TEXT NOT NULL CHECK (provider IN ('toss','kakao','telegram')),  -- telegram: m002
  provider_user_id  TEXT NOT NULL,
  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (user_id, provider),
  UNIQUE (provider, provider_user_id)
);

CREATE TABLE companies (
  ticker   TEXT PRIMARY KEY,                              -- 권장 '{시장}:{심볼}' — 결정 필요 D1
  name     TEXT NOT NULL,
  market   TEXT NOT NULL,
  aliases  TEXT NOT NULL DEFAULT '[]'                     -- PG: JSONB NOT NULL DEFAULT '[]'
);

CREATE TABLE watchlist (
  user_id         INTEGER NOT NULL REFERENCES users(id),
  ticker          TEXT NOT NULL REFERENCES companies(ticker),
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  notify_enabled  INTEGER NOT NULL DEFAULT 0,             -- PG: BOOLEAN NOT NULL DEFAULT false
  notify_types    TEXT NOT NULL DEFAULT '["news","disclosure","price"]',  -- PG: JSONB
  toss_consent_at TEXT,                                   -- PG: timestamptz — NULL이면 토스 발송 금지
  PRIMARY KEY (user_id, ticker)
);

CREATE TABLE events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,        -- PG: BIGINT GENERATED ALWAYS AS IDENTITY
  ticker        TEXT NOT NULL REFERENCES companies(ticker),
  type          TEXT NOT NULL CHECK (type IN ('news','disclosure','price')),
  cluster_key   TEXT NOT NULL,                            -- sha1(정규화 제목)[:16]
  headline      TEXT NOT NULL,
  summary_line  TEXT NOT NULL DEFAULT '',
  url           TEXT NOT NULL,
  source        TEXT NOT NULL DEFAULT '',
  source_count  INTEGER NOT NULL DEFAULT 1,
  keyword_score REAL NOT NULL DEFAULT 0,
  urgency_score REAL,
  reason        TEXT NOT NULL DEFAULT '',
  published_at  TEXT NOT NULL,                            -- PG: timestamptz
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (ticker, type, cluster_key)
);

CREATE TABLE deliveries (
  event_id            INTEGER NOT NULL REFERENCES events(id),
  user_id             INTEGER NOT NULL REFERENCES users(id),
  channel             TEXT NOT NULL CHECK (channel IN ('toss','telegram','console')),
  status              TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','sent','failed','skipped')),
  fail_reason         TEXT,
  provider_content_id TEXT,
  attempt             INTEGER NOT NULL DEFAULT 0,
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  sent_at             TEXT,
  PRIMARY KEY (event_id, user_id, channel)
);

CREATE TABLE saved_news (
  user_id   INTEGER NOT NULL REFERENCES users(id),
  event_id  INTEGER NOT NULL REFERENCES events(id),
  saved_at  TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (user_id, event_id)
);

CREATE TABLE briefings (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL REFERENCES users(id),
  date        TEXT NOT NULL,                              -- PG: DATE
  payload     TEXT NOT NULL,                              -- PG: JSONB
  sent_at     TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (user_id, date)
);

CREATE TABLE situations (
  unit_key     TEXT PRIMARY KEY,                          -- "{company}::{name}"
  company      TEXT NOT NULL,
  entity_name  TEXT NOT NULL,
  kind         TEXT CHECK (kind IN ('self','related','theme')),
  summary      TEXT NOT NULL DEFAULT '',
  stance       TEXT NOT NULL DEFAULT 'neutral'
               CHECK (stance IN ('positive','negative','neutral')),
  updated_at   TEXT
);

CREATE TABLE situation_timeline (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  unit_key    TEXT NOT NULL REFERENCES situations(unit_key),
  at          TEXT NOT NULL,
  change      TEXT NOT NULL,
  importance  INTEGER NOT NULL CHECK (importance BETWEEN 0 AND 10)
);

CREATE TABLE kakao_messages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL REFERENCES users(id),
  direction   TEXT NOT NULL CHECK (direction IN ('in','out')),
  text        TEXT NOT NULL,
  intent      TEXT,
  payload     TEXT,                                       -- PG: JSONB
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE legacy_seen (
  title_hash  TEXT PRIMARY KEY,
  seen_at     TEXT NOT NULL
);

-- 인덱스 (§5)
CREATE INDEX idx_watchlist_fanout   ON watchlist (ticker) WHERE notify_enabled = 1;  -- PG: WHERE notify_enabled
CREATE INDEX idx_identities_user    ON identities (user_id);
CREATE INDEX idx_events_ticker_pub  ON events (ticker, published_at DESC);
CREATE INDEX idx_deliveries_queue   ON deliveries (channel, created_at) WHERE status = 'pending';
CREATE INDEX idx_saved_user         ON saved_news (user_id, saved_at DESC);
CREATE INDEX idx_timeline_unit      ON situation_timeline (unit_key, at DESC);
CREATE INDEX idx_kakao_user_time    ON kakao_messages (user_id, created_at);
CREATE INDEX idx_kakao_created      ON kakao_messages (created_at);                   -- 보존정책 purge용
CREATE INDEX idx_companies_name     ON companies (name);

-- 예약 유저: 단일유저 CLI 호환 (restructuring Step 2)
INSERT INTO users (id, created_at) VALUES (0, datetime('now'));
```

### 4.2 PostgreSQL (운영, Supabase 호환)

```sql
-- kokalim postgres ddl v1
CREATE TABLE users (
  id            BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,  -- BY DEFAULT: id=0 예약행 삽입 허용
  created_at    timestamptz NOT NULL DEFAULT now(),
  briefing_time TIME                                                  -- 'HH:MM' KST 해석, NULL=브리핑 미수신 (m002)
);

CREATE TABLE identities (
  user_id           BIGINT NOT NULL REFERENCES users(id),
  provider          TEXT NOT NULL CHECK (provider IN ('toss','kakao','telegram')),  -- telegram: m002
  provider_user_id  TEXT NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, provider),
  UNIQUE (provider, provider_user_id)
);

CREATE TABLE companies (
  ticker   TEXT PRIMARY KEY,
  name     TEXT NOT NULL,
  market   TEXT NOT NULL,
  aliases  JSONB NOT NULL DEFAULT '[]'
);

CREATE TABLE watchlist (
  user_id         BIGINT NOT NULL REFERENCES users(id),
  ticker          TEXT NOT NULL REFERENCES companies(ticker),
  created_at      timestamptz NOT NULL DEFAULT now(),
  notify_enabled  BOOLEAN NOT NULL DEFAULT false,
  notify_types    JSONB NOT NULL DEFAULT '["news","disclosure","price"]',
  toss_consent_at timestamptz,
  PRIMARY KEY (user_id, ticker)
);

CREATE TABLE events (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker        TEXT NOT NULL REFERENCES companies(ticker),
  type          TEXT NOT NULL CHECK (type IN ('news','disclosure','price')),
  cluster_key   TEXT NOT NULL,
  headline      TEXT NOT NULL,
  summary_line  TEXT NOT NULL DEFAULT '',
  url           TEXT NOT NULL,
  source        TEXT NOT NULL DEFAULT '',
  source_count  INTEGER NOT NULL DEFAULT 1,
  keyword_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  urgency_score DOUBLE PRECISION,
  reason        TEXT NOT NULL DEFAULT '',
  published_at  timestamptz NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (ticker, type, cluster_key)
);

CREATE TABLE deliveries (
  event_id            BIGINT NOT NULL REFERENCES events(id),
  user_id             BIGINT NOT NULL REFERENCES users(id),
  channel             TEXT NOT NULL CHECK (channel IN ('toss','telegram','console')),
  status              TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','sent','failed','skipped')),
  fail_reason         TEXT,
  provider_content_id TEXT,
  attempt             INTEGER NOT NULL DEFAULT 0,
  created_at          timestamptz NOT NULL DEFAULT now(),
  sent_at             timestamptz,
  PRIMARY KEY (event_id, user_id, channel)
);

CREATE TABLE saved_news (
  user_id   BIGINT NOT NULL REFERENCES users(id),
  event_id  BIGINT NOT NULL REFERENCES events(id),
  saved_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, event_id)
);

CREATE TABLE briefings (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id     BIGINT NOT NULL REFERENCES users(id),
  date        DATE NOT NULL,
  payload     JSONB NOT NULL,
  sent_at     timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, date)
);

CREATE TABLE situations (
  unit_key     TEXT PRIMARY KEY,
  company      TEXT NOT NULL,
  entity_name  TEXT NOT NULL,
  kind         TEXT CHECK (kind IN ('self','related','theme')),
  summary      TEXT NOT NULL DEFAULT '',
  stance       TEXT NOT NULL DEFAULT 'neutral'
               CHECK (stance IN ('positive','negative','neutral')),
  updated_at   timestamptz
);

CREATE TABLE situation_timeline (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  unit_key    TEXT NOT NULL REFERENCES situations(unit_key),
  at          timestamptz NOT NULL,
  change      TEXT NOT NULL,
  importance  INTEGER NOT NULL CHECK (importance BETWEEN 0 AND 10)
);

CREATE TABLE kakao_messages (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id     BIGINT NOT NULL REFERENCES users(id),
  direction   TEXT NOT NULL CHECK (direction IN ('in','out')),
  text        TEXT NOT NULL,
  intent      TEXT,
  payload     JSONB,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE legacy_seen (
  title_hash  TEXT PRIMARY KEY,
  seen_at     timestamptz NOT NULL
);

CREATE INDEX idx_watchlist_fanout   ON watchlist (ticker) WHERE notify_enabled;
CREATE INDEX idx_identities_user    ON identities (user_id);
CREATE INDEX idx_events_ticker_pub  ON events (ticker, published_at DESC);
CREATE INDEX idx_deliveries_queue   ON deliveries (channel, created_at) WHERE status = 'pending';
CREATE INDEX idx_saved_user         ON saved_news (user_id, saved_at DESC);
CREATE INDEX idx_timeline_unit      ON situation_timeline (unit_key, at DESC);
CREATE INDEX idx_kakao_user_time    ON kakao_messages (user_id, created_at);
CREATE INDEX idx_kakao_created      ON kakao_messages (created_at);
CREATE INDEX idx_companies_name     ON companies (name);

INSERT INTO users (id) VALUES (0);  -- 단일유저 CLI 예약
```

주요 방언 차이 요약: `TEXT(JSON)`↔`JSONB`, `TEXT(ISO)`↔`timestamptz/DATE`, `INTEGER 0/1`↔`BOOLEAN`, `AUTOINCREMENT`↔`IDENTITY`, `datetime('now')`↔`now()`. partial index는 양쪽 모두 지원.

### 4.3 Supabase 운영 노트 (운영 DB 확정)

Supabase는 관리형 Postgres이므로 §4.2 DDL을 수정 없이 적용한다. 단, Supabase 고유 동작 때문에 아래 규칙이 스키마의 일부다.

**접근 모델 — 서버 단일 클라이언트 원칙:**

```
apps/toss-webapp ──▶ server/api ──▶ Supabase Postgres   (웹뷰는 DB에 직접 접근하지 않는다)
```

- DB 클라이언트는 `server/`(api·workers)뿐이다. 접속은 **service role 키 또는 직접 DB 접속 문자열**로 하며, 이 값은 서버 시크릿 스토어에만 둔다. `anon` 키를 포함해 어떤 Supabase 키도 웹뷰 번들(`VITE_*`)에 넣지 않는다.
- Supabase Auth는 사용하지 않는다 — 인증은 토스 로그인 → 자체 JWT([apps-in-toss.md](apps-in-toss.md) §4)이고, Supabase Auth는 토스를 provider로 지원하지 않는다. `users.id`는 우리 스키마의 BIGINT 그대로 간다(`auth.users` UUID와 무관).

**RLS 가드 — Supabase 최대 함정:**

Supabase는 `public` 스키마의 모든 테이블을 PostgREST 자동 REST API로 노출하며, **RLS가 꺼져 있으면 anon 키만으로 전 테이블이 읽힌다.** 우리 접근 경로(service role)는 RLS를 우회하므로, 전 테이블에 정책 없이 RLS만 켜서 외부 경로를 전부 차단한다:

```sql
-- kokalim supabase rls guard v1 — §4.2 DDL 직후 실행 (정책은 만들지 않는다 = 전부 거부)
ALTER TABLE users              ENABLE ROW LEVEL SECURITY;
ALTER TABLE identities         ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies          ENABLE ROW LEVEL SECURITY;
ALTER TABLE watchlist          ENABLE ROW LEVEL SECURITY;
ALTER TABLE events             ENABLE ROW LEVEL SECURITY;
ALTER TABLE deliveries         ENABLE ROW LEVEL SECURITY;
ALTER TABLE saved_news         ENABLE ROW LEVEL SECURITY;
ALTER TABLE briefings          ENABLE ROW LEVEL SECURITY;
ALTER TABLE situations         ENABLE ROW LEVEL SECURITY;
ALTER TABLE situation_timeline ENABLE ROW LEVEL SECURITY;
ALTER TABLE kakao_messages     ENABLE ROW LEVEL SECURITY;
ALTER TABLE legacy_seen        ENABLE ROW LEVEL SECURITY;
```

- 향후 웹뷰가 supabase-js로 직접 읽는 구조로 바꾸려면 테이블별 RLS 정책 설계가 선행돼야 한다(결정 필요 D6). 그 전까지 정책 0개가 정답이다.

**커넥션:**

- api·workers는 **Supavisor transaction 풀러(포트 6543)** 경유로 접속 — 커넥션 폭주 방지. `DATABASE_URL`=풀러 주소.
- 마이그레이션·DDL은 **직접 접속(포트 5432)** 사용 — transaction 풀러에서는 prepared statement/DDL 제약이 있다. `DIRECT_DATABASE_URL`을 별도 환경변수로 둔다.

**마이그레이션 운영:**

- §9의 순번제 마이그레이션(`server/kokalim/db/migrations/NNN_*.sql`)을 정본으로 유지하고, 적용은 서버 스크립트(`init_db.py`)가 `DIRECT_DATABASE_URL`로 실행한다. Supabase CLI(`supabase/migrations`)로 이관할지는 D7.
- 로컬 개발: CLI/pytest는 SQLite(§4.1), 통합 검증이 필요하면 `supabase start`(Docker 로컬 스택)로 §4.2+RLS를 그대로 실행.

**부가 기능 스탠스:** Realtime·Edge Functions·Storage는 v1 범위 밖. 필요해지면 이 문서 개정이 선행돼야 한다.


## 5. 인덱스 전략

| 인덱스 | 지원 쿼리 |
|---|---|
| `idx_watchlist_fanout (ticker) WHERE notify_enabled` | fan-out 좌변 — 이벤트 티커로 알림 켠 유저만 스캔 |
| `deliveries` PK `(event_id, user_id, channel)` | 멱등 INSERT + 재발송 방지 EXISTS |
| `idx_deliveries_queue (channel, created_at) WHERE status='pending'` | 발송 워커의 큐 폴링·bulk 배칭 |
| `idx_events_ticker_pub (ticker, published_at DESC)` | 기업 상세 "오늘" 조회, 카카오 봇 질의 |
| `events` UNIQUE `(ticker, type, cluster_key)` | 수집 dedup (seen-set 승계) |
| `idx_saved_user (user_id, saved_at DESC)` | 저장함 목록 |
| `idx_timeline_unit (unit_key, at DESC)` | 상황 prior 로드 + 20건 캡 트리밍 |
| `idx_kakao_created (created_at)` | 보존정책 purge 배치 |
| `idx_companies_name (name)` | 기업 검색 (aliases는 LIKE 스캔 — 규모 시 FTS/pg_trgm, 결정 필요 D3) |

## 6. Fan-out 재검증 (architecture §6 → 확정 스키마)

**1단계 — fan-out (poller, 이벤트 upsert 직후, 멱등):**

```sql
-- kokalim fanout query v1  (:event_id, :ticker, :type 바인딩)
INSERT OR IGNORE INTO deliveries (event_id, user_id, channel)   -- PG: ON CONFLICT DO NOTHING
SELECT :event_id, w.user_id, 'toss'
FROM watchlist w
WHERE w.ticker = :ticker
  AND w.notify_enabled = 1                                      -- PG: w.notify_enabled
  AND EXISTS (SELECT 1 FROM json_each(w.notify_types) jt        -- PG: w.notify_types ? :type
              WHERE jt.value = :type)
  AND w.toss_consent_at IS NOT NULL;                            -- 앱인토스 동의 가드 (필수)
```

architecture §6 초안과의 차이: (1) `NOT EXISTS(deliveries…)` 서브쿼리 → PK 충돌 무시(INSERT OR IGNORE)로 대체 — 동일 의미에 더 저렴하고 경합에 안전. (2) 발송을 즉시 하지 않고 `pending` 행만 적재 — 스로틀/배칭을 2단계로 분리.

**2단계 — 발송 워커 (스로틀·bulk 반영):**

```sql
SELECT d.event_id, d.user_id, i.provider_user_id AS toss_user_key,
       e.summary_line, e.headline, e.type, e.ticker
FROM deliveries d
JOIN identities i ON i.user_id = d.user_id AND i.provider = 'toss'
JOIN events e     ON e.id = d.event_id
WHERE d.channel = 'toss' AND d.status = 'pending'
ORDER BY d.created_at
LIMIT 2500;   -- 스마트 발송 bulk 1회 상한
```

- **bulk 배칭**: 결과를 템플릿 코드(type→`KOK_EVENT_ALERT`/`KOK_PRICE_ALERT`)별로 그룹핑해 `send-bulk-message`(최대 2,500건/회) 호출, 응답 `detail`/`fail`을 행별 `status/sent_at/fail_reason/provider_content_id`로 반영.
- **userKey 분당 10회 스로틀**: 같은 배치 내 동일 `user_id` 행이 10건을 넘으면 초과분을 다음 사이클로 이월(`created_at` 유지로 순서 보존). 애플리케이션 레벨 카운터로 처리하며 스키마 변경 불요 — `attempt`/`status='pending'`이 이월을 자연 표현.
- `identities`에 toss 바인딩이 없는 유저(카카오 전용 유저)는 JOIN에서 자연 탈락 → `status='skipped'`로 마킹해 큐 잔류 방지.

## 7. 마이그레이션 계획 (`server/scripts/`)

컷오버 순서대로. 전부 멱등하게 작성한다.

| # | 스크립트 | 입력 → 출력 | 핵심 규칙 |
|---|---|---|---|
| 1 | `init_db.py` | §4 DDL 실행 (+ `users.id=0` 시드, Postgres면 §4.3 RLS 가드까지) | SQLite: `server/kokalim/state/kokalim.db` / Supabase: `DIRECT_DATABASE_URL`로 실행 |
| 2 | `seed_companies.py` | 기업 마스터 적재 | 결정 필요 D1·D2 확정 후 구현. `config.watchlist` 11개 기업명의 티커 수기 매핑을 최소 시드로 포함 |
| 3 | `seed_watchlist.py` | `config.watchlist` → `watchlist(user_id=0, notify_enabled=1)` | CLI 동작 보존. `notify_types` 기본값, `toss_consent_at=NULL` |
| 4 | `migrate_seen.py` | `state/state.json`의 `seen{hash:ts}` → `legacy_seen` | TTL(24h) 경과 엔트리는 스킵(`state.py prune`과 동일 기준). **가짜 events 합성 금지** — hash는 event로 역산 불가. poller는 컷오버 후 24h 동안 `legacy_seen`을 추가 조회, 이후 `DROP TABLE legacy_seen` |
| 5 | `migrate_situations.py` | `state/situations.json` → `situations` + `situation_timeline` | `unit_key`를 `::`로 분해해 `company`/`entity_name` 채움. `kind`는 `build_watch_items()` 결과와 대조해 채우고 미상이면 NULL. `timeline[]`을 자식 행으로 전개(원본이 이미 20건 캡) |

롤백: 컷오버 24h 내에는 `state.json`/`situations.json` 원본을 삭제하지 않으므로 파일 경로로 즉시 복귀 가능. 24h 후 파일은 `*.migrated` 리네임 보관.

## 8. PII / 보안 / 보존정책

- **식별자 최소주의**: 토스 로그인 scope는 `user_key`만 ([apps-in-toss.md](apps-in-toss.md) §4). 이름·전화번호·CI 등 개인정보 컬럼은 스키마 전체에 존재하지 않는다. 추가하려면 별도 테이블 + 암호화 + 문서 개정이 선행돼야 한다.
- **토스 토큰 비저장**: accessToken/refreshToken은 교환 직후 폐기(§3.2). 세션은 자체 JWT(무상태)로, 세션 테이블 없음.
- **`kakao_messages` 보존정책**: `text`·`payload`는 자유 발화라 PII 유입 가능. 기본 **90일 경과 시 행 삭제**(purge 배치, `idx_kakao_created` 사용), 분석은 `intent` 단위 익명 집계만 장기 보관. 90일이라는 기간 자체는 결정 필요 D4.
- **시크릿**: mTLS 인증서·`templateSetCode`·JWT 시크릿·Supabase service role 키·`DATABASE_URL`(풀러)·`DIRECT_DATABASE_URL`(직접 접속)은 `.env`/배포 시크릿 스토어. `.env` 커밋 금지 유지. **Supabase 키는 어떤 것도 웹뷰 번들에 넣지 않는다**(§4.3). DB에는 어떤 시크릿도 저장하지 않는다.
- **저작권**: 뉴스 전문 비저장 — `events`는 헤드라인+요약+링크만.

## 9. 하위호환 원칙 (스키마 변경 규칙)

1. 컬럼 **추가만** 허용 — NULL 허용 또는 DEFAULT 필수.
2. 컬럼 제거·타입/의미 변경 금지 — 필요하면 새 컬럼 추가 후 이중 기록 → 소비자 이전 → 구 컬럼은 미사용으로 남기고 문서에 deprecated 표기.
3. CHECK enum 확장(값 추가)은 허용, 값 제거는 금지.
4. API `/v2` 승격 사유가 되는 변경(응답 의미 변화)은 이 문서와 [architecture.md](architecture.md) §7을 함께 개정.
5. 마이그레이션 파일은 순번제(`server/kokalim/db/migrations/NNN_*.sql`)로 추가만 한다.

적용된 마이그레이션:

| # | 파일 | 내용 |
|---|---|---|
| 001 | `001_init.sql` | §4.2 전체 DDL + §4.3 RLS 가드 + `users.id=0` 시드 (2026-07-07 Supabase 적용 완료) |
| 002 | `002_telegram_briefing.sql` | `users.briefing_time` 추가, `identities.provider`에 `'telegram'` 허용 — 텔레그램 우선 가동([mvp-decisions.md](mvp-decisions.md)). 알림톡용 `users.phone`/`alimtalk_consent_at`은 카톡 트랙 재개 시 m003+로 추가 |

## 10. 결정 필요 (코드·문서로 확정 불가)

| # | 결정 | 배경 | 임시 기본값 |
|---|---|---|---|
| D1 | `ticker` 네임스페이스 포맷 | 제품 스펙에 테슬라(해외 종목) 포함 — `KRX:005930`/`NASDAQ:TSLA` 식 프리픽스 채택 여부와 v1 해외 종목 지원 범위 | 프리픽스 포맷 채택 가정, v1 국내 한정 |
| D2 | 기업 마스터 데이터 소스 | KRX 상장목록(research/method_b가 pykrx 보유) vs 수기 시드 vs 외부 API | `config.watchlist` 11개 수기 시드로 시작 |
| D3 | 기업 검색 방식 | `name`/`aliases` LIKE로 시작 → 규모 시 SQLite FTS5 / PG pg_trgm | LIKE |
| D4 | `kakao_messages` 보존기간 | 법무/개인정보 검토 필요 | 90일 |
| D5 | `situations`의 API 노출 | 기업 상세에 "연관 동향" 노출 여부 (현행 API 표면 밖) | 미노출, workers 전용 |
| D6 | 웹뷰의 Supabase 직접 접근 | supabase-js + RLS 정책 설계로 api 우회 가능하나 인증 통합(자체 JWT↔RLS) 복잡도 큼 | 금지 — 서버 단일 클라이언트(§4.3), 전 테이블 RLS 정책 0개 |
| D7 | 마이그레이션 도구 | 자체 순번제 스크립트 vs Supabase CLI(`supabase/migrations`) | 자체 순번제 유지, 적용은 `DIRECT_DATABASE_URL` |
