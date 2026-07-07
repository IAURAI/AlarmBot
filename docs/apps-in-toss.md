# 콕알림 — 앱인토스 미니앱 설계

> 상태: 설계 확정 대기 / 작성일: 2026-07-07
> 근거: 앱인토스 공식 문서 (developers-apps-in-toss.toss.im, 2026-07 기준)
> 상위 문서: [architecture.md](architecture.md)

## 0. 결론 요약

- 개발 방식: **WebView** (React + TypeScript + TDS, `@apps-in-toss/web-framework`)
- 인증: **토스 로그인** — 클라 `appLogin()` → 서버 토큰 교환 → `userKey` 확보
- 푸시: **스마트 발송 API** — 콕알림의 "알림 종"이 앱인토스 공식 문서의 알림 동의문 필요 사례("가격 변동 알림 신청")와 정확히 일치. `requestNotificationAgreement` → 동의 → 서버 발송
- 배포: `npm run build` → `.ait` 번들 → 콘솔 업로드 → 검수 → 출시 (비게임 출시 가이드 준수)
- 서버 간 API(토큰 교환·스마트 발송)는 전부 **mTLS 인증서** 필요

## 1. 사전 준비 (콘솔, 코드 밖 작업)

| 순서 | 작업 | 비고 |
|---|---|---|
| 1 | [서비스 오픈 정책](https://developers-apps-in-toss.toss.im/intro/guide.html) 확인 | 뉴스 서비스 → 정치 카테고리 주의사항(중립성·사실 전달) 해당 가능성 검토 |
| 2 | 콘솔 워크스페이스 + 미니앱 등록 | `appName`(예: `kok-alarm`) 확정 — 딥링크 `intoss://{appName}`에 쓰임, 이후 변경 곤란 |
| 3 | 사업자 등록/계약 | 사업자 없이도 출시 가능하나 수익화 시 필요 |
| 4 | 토스 로그인 설정 + 약관 등록 | scope 최소화: `user_key`만으로 충분 (개인정보 불필요) |
| 5 | **mTLS 인증서 발급** | [API 사용하기](https://developers-apps-in-toss.toss.im/development/integration-process.html) — 서버 배포 전 필수 |
| 6 | 알림 동의문 작성 → 기능성 캠페인 생성 → `templateSetCode` 발급 + **문구 검수** | 검수 승인 전엔 테스트 발송도 불가 → **가장 먼저 넣어야 하는 크리티컬 패스** |

## 2. 프로젝트 셋업

```bash
# apps/toss-webapp — 신규 생성이므로 create-ait-app 사용
npx create-ait-app kok-alarm
# 패키지 매니저: npm / 템플릿: react-ts / TDS: Y / AI skills: 사용 도구 선택
```

```ts
// apps/toss-webapp/granite.config.ts
import { defineConfig } from '@apps-in-toss/web-framework/config';

export default defineConfig({
  appName: 'kok-alarm',                  // 콘솔 등록값과 동일
  brand: {
    displayName: '콕알림',
    primaryColor: '#3182F6',             // 확정 시 교체
    icon: '<콘솔 업로드 이미지 URL>',
  },
  web: {
    host: 'localhost',
    port: 5173,
    commands: { dev: 'vite dev', build: 'vite build' },
  },
  permissions: [],                       // 카메라·위치 등 불필요 — 빈 배열 유지
  outdir: 'dist',
});
```

- TDS: `@toss/tds-mobile` + `@toss/tds-mobile-ait` 사용. 심사 시 디자인 가이드 준수 항목이므로 커스텀 컴포넌트 남발 금지.
- 개발 중 AI 도구 쓸 경우 `ax` MCP(`ax mcp start`) + Apps In Toss Skills(`docs-search`) 연결 권장.

## 3. 화면 설계 (라우팅)

| 경로 | 화면 | 주요 TDS 컴포넌트 |
|---|---|---|
| `/` | 내 콕리스트 (빈 상태면 검색 유도) | ListRow, Badge(신규 소식 수), BottomCTA |
| `/search` | 기업 검색 + 콕 버튼 | Top(검색바), ListRow + 우측 콕 Button |
| `/company/:ticker` | 오늘의 뉴스·공시·주가 한 화면 | Tab(뉴스/공시/주가), ListRow |
| `/company/:ticker/settings` | 알림 종류 필터 (주가만/공시만/뉴스만) | ListRow + Switch |
| `/news/:eventId` | 뉴스 상세 (알림 랜딩) | Text, 저장 Button, 원문 링크는 `openURL` |
| `/saved` | 저장함 | ListRow |
| `/briefing/:id` | 오늘의 브리핑 카드 스와이프 | 가로 스와이프 카드 |

**알림 랜딩:** 스마트 발송 메시지의 랜딩을 `intoss://kok-alarm?route=/news/{eventId}` 형태로 걸고, 앱 진입 시 `useParams`(쿼리 파라미터 SDK)로 라우팅한다. 브리핑 푸시는 `route=/briefing/{id}`. 콘솔의 "앱 내 기능" 등록으로 특정 화면 직행 경로도 함께 등록.

**뒤로가기/닫기:** 루트에서는 `closeView`로 미니앱 종료, 하위 화면은 back-event 제어 — 심사 체크 항목.

## 4. 인증 플로우 (토스 로그인)

```
[웹뷰]  appLogin() ──authorizationCode(10분, 일회성)──▶ [server/api]
[server] POST /user/oauth2/generate-token (mTLS)  → accessToken(1h) + refreshToken(14d)
[server] GET  /user/oauth2/login-me (Bearer)      → userKey (앱 단위 고유 식별자)
[server] users/identities upsert(provider='toss', provider_user_id=userKey)
         → 자체 세션 토큰(JWT) 발급 → 웹뷰는 이후 자체 API만 호출
```

구현 규칙:
- `authorizationCode`는 클라에서 저장하지 않고 즉시 서버로 전달. 토큰 교환·보관은 전부 서버.
- scope는 `user_key`만 요청 — 이름·전화번호 등 개인정보 scope를 요구하면 심사와 개인정보 처리 부담만 늘어난다. 콕알림은 식별자만 있으면 된다.
- `login-me` 응답의 `scope`에 정의되지 않은 값이 추가될 수 있으므로(공식 공지) 파싱은 관용적으로.
- 재방문 시 `appLogin()`은 로그인 창 없이 즉시 인가 코드를 반환하므로 앱 진입 시 자동 로그인 플로우로 사용.
- 토스 accessToken/refreshToken은 재조회 필요 시에만 서버 보관, 만료 시 refresh-token API로 갱신. 우리 세션은 자체 JWT로 관리(토스 토큰 수명에 UX를 묶지 않는다).

## 5. 푸시 설계 (스마트 발송)

### 5.1 동의 플로우 — "알림 종" 버튼

공식 문서 기준, 가격 변동·이벤트성 알림은 **알림 동의문 필수**. 콕알림의 뉴스/공시/주가 알림이 전부 여기에 해당한다.

```
[웹뷰] 종 버튼 탭
  → requestNotificationAgreement()   // 토스 네이티브 동의 UI
  → 동의 성공 시 PATCH /watchlist/{ticker} { notify_enabled: true }
  → 서버: watchlist.toss_consent_at 기록
```

- 동의문은 콘솔에서 먼저 작성해 기능성 캠페인에 연결해둬야 한다 (1장 6번).
- 동의 없이 `send-message`를 호출하면 발송 실패 — fan-out 쿼리에서 `toss_consent_at IS NOT NULL` 가드 필수.

### 5.2 발송 — 서버 → 앱인토스

- BaseURL `https://apps-in-toss-api.toss.im`, **mTLS 필수**
- 단건: `POST /api-partner/v1/apps-in-toss/messenger/send-message` + 헤더 `x-toss-user-key`
- 대량: `POST /api-partner/v1/apps-in-toss/messenger/send-bulk-message` (50건 이상, 최대 2,500건/회) — **브리핑과 인기 종목 이벤트는 반드시 bulk로 배칭**
- 제한: userKey당 분당 10회 → 같은 유저에게 이벤트가 몰릴 때 서버 측 per-user 스로틀 필요

### 5.3 템플릿 전략

문구는 자유 텍스트가 아니라 **사전 검수된 템플릿 + context 변수**다. 템플릿 3종으로 시작:

| templateSetCode | 용도 | context 변수 | 예시 렌더링 |
|---|---|---|---|
| `KOK_EVENT_ALERT` | 개별 소식 알림 | `companyName`, `headline`, `eventId` | "삼성전자, 오늘 실적 발표" |
| `KOK_PRICE_ALERT` | 주가 변동 | `companyName`, `changePct`, `eventId` | "카카오 주가 +7.2% 급등" |
| `KOK_DAILY_BRIEFING` | 아침 브리핑 | `count`, `topHeadline`, `briefingId` | "오늘의 콕 브리핑 — 관심 기업 소식 5건" |

- LLM이 만드는 `summary_line`(한 줄 요약)을 `headline` 변수로 주입 → 템플릿 검수 체계 안에서 자유로운 내용 전달.
- `channels/toss_push.py`는 응답의 `fail` 목록을 파싱해 `deliveries`에 실패 사유를 기록하고, 텔레그램 운영 채널로 집계 리포트.
- 실배포 전 `send-test-message`(`deploymentId` = 콘솔 업로드 번들)로 검증하는 스모크 스크립트를 `server/scripts/`에 둔다.

## 6. 서버 연동 규격

```
apps/toss-webapp ──HTTPS──▶ server/api (자체 도메인, CORS: 웹뷰 오리진 허용)
server/api·workers ──mTLS──▶ apps-in-toss-api.toss.im (토큰 교환, 스마트 발송)
```

- 웹뷰 번들은 토스 인프라에 올라가는 정적 자산이므로 **API 도메인은 절대 URL**로 환경변수 주입 (`VITE_API_BASE_URL`).
- 네트워크 유틸은 SDK의 network/HTTP 모듈 또는 fetch — 응답 스키마는 `packages/api-schema`에서 생성한 타입 사용.
- 에러 모니터링: Sentry (앱인토스 공식 가이드 있음).

## 7. 개발 → 테스트 → 출시 파이프라인

```
로컬 개발     vite dev + 샌드박스 앱 (intoss://kok-alarm)
             실기기: web.host를 LAN IP로, vite --host
디버깅       Android: chrome://inspect / iOS: Safari Web Inspector
번들         npm run build → .ait 생성
토스앱 테스트  콘솔 '앱 출시'에 .ait 업로드 → QR(intoss-private://kok-alarm) 테스트
             + send-test-message로 푸시 스모크
검수 요청     비게임 출시 가이드 체크리스트 통과 확인 후 콘솔에서 요청
출시/롤백     콘솔 버전 관리, 긴급 수정·점검 공지 기능 활용
```

**비게임 심사 대비 체크리스트(요지):**
- [ ] 내비게이션 바·뒤로가기·닫기 동작 규격 준수
- [ ] 토스 로그인 플로우 규격 준수 (약관 화면 포함)
- [ ] TDS/디자인 가이드, UX 라이팅(해요체) 준수
- [ ] 다크패턴 없음, 외부 링크 제한 정책 준수 (뉴스 원문은 `openURL` 사용 — 자사 앱 설치 유도 금지)
- [ ] 뉴스 저작권: 전문 재배포 금지 → 요약+헤드라인+원문 링크만 (기존 정책 유지)
- [ ] 알림 동의문·템플릿 문구 검수 승인 완료

**릴리스 운영 규칙:** 검수 리드타임이 있으므로 (1) 서버 API는 하위호환 유지, (2) 웹뷰 신기능은 feature flag로 잠근 채 심사 통과 후 서버에서 활성화, (3) 콘솔 롤백 절차를 런북에 문서화.

## 8. 수익화/성장 (출시 후, 참고만)

- 인앱 광고(배너/보상형), IAP 정기결제(프리미엄: 실시간 속보·연관 컨텍스트 리포트) — SDK 지원 확인됨. M2 범위 아님.
- 공유: `getTossShareLink`로 기업 카드 공유 → 유입. 세그먼트/스마트 발송 마케팅 기능은 리텐션 단계에서 검토.

## 9. 리스크

| 리스크 | 대응 |
|---|---|
| 템플릿·동의문 검수 리드타임 | 콘솔 작업을 개발과 병렬로 최우선 착수 (크리티컬 패스) |
| userKey 분당 10회 제한 | per-user 스로틀 + 이벤트 병합(같은 기업 연속 소식은 묶음 발송) |
| 뉴스 서비스 정책(정치·민감 콘텐츠) | 주의사항 문서 사전 검토, 중립성·사실 전달 원칙을 요약 프롬프트에 명시 |
| mTLS 인증서 관리 | 시크릿 스토어 보관, 만료 캘린더 등록 |
| 검수 반려 | 비게임 체크리스트를 PR 템플릿에 포함해 상시 준수 |
