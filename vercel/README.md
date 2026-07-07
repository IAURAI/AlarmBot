# Vercel + Supabase — 다중 사용자 텔레그램 봇 (개인별 관심종목)

로컬 봇이 **판정**하고, Supabase가 **저장**하고, Vercel이 **개인별 텔레그램 봇**으로 동작합니다.

```
로컬(--sink supabase, Codex 무료 유지)
   └ 크롤 → LLM 판정 → alerts(ticker=삼성전자) INSERT        ← 판정은 종목당 1회
        └ Supabase Database Webhook (INSERT)
             └ POST → Vercel /api/notify
                  └ "삼성전자 관심 등록한 활성 구독자" 조회 → 각자에게 팬아웃 발송
사용자 ── 텔레그램 명령 ──▶ Vercel /api/telegram → Supabase 조회/수정 → 응답
```

**핵심**: 판정(크롤+LLM)은 **종목당 1번**, 배달만 사용자별. 그래서 **구독자가 늘어도 LLM/Codex
비용은 그대로**입니다(늘어나는 건 텔레그램 전송뿐). Vercel 함수는 표준 라이브러리만 써 무의존성.

---

## 봇 명령 (누구나 사용)

| 명령 | 동작 |
|---|---|
| `/start` | 등록 + 도움말 + 내 관심종목 |
| `/watch 삼성전자` | 관심종목 추가(지원 종목만, 1인 최대 20) |
| `/unwatch 삼성전자` | 관심종목 제거 |
| `/my` | 내 관심종목 |
| `/tickers` | 지원 종목 목록 |
| `/latest` | 내 관심종목 최근 알림 5건 |
| `/status` | 내 관심종목 오늘(KST) 현황 |
| `/stop` | 알림 중지(다시 받으려면 `/start`) |

> **오너(나)도 알림을 받으려면 봇에게 `/start` 후 `/watch <종목>` 하세요.** 이제 단일 chat_id로
> 쏘지 않고, **관심종목을 등록한 구독자에게만** 발송합니다. 남들은 봇 링크 `t.me/<봇username>`만
> 공유하면 각자 `/watch`로 자기 관심종목을 담아 알림을 받습니다.

---

## 0. 시크릿 준비

| 이름 | 어디에 | 값 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Vercel | BotFather 토큰 |
| `SUPABASE_URL` | Vercel + 로컬 `.env` | `https://xxxx.supabase.co` |
| `SUPABASE_SERVICE_KEY` | Vercel + 로컬 `.env` | Supabase → Settings → API → **service_role** |
| `SUPABASE_WEBHOOK_SECRET` | Vercel + Supabase 웹훅 헤더 | 난수. `openssl rand -hex 16` |
| `TELEGRAM_WEBHOOK_SECRET` | Vercel + Telegram setWebhook | 난수. `openssl rand -hex 16` |

> `service_role` 키는 RLS를 우회하는 쓰기 키 — 로컬 `.env`·Vercel 환경변수에만, **커밋 금지**.
> (이전의 `TELEGRAM_CHAT_ID`는 팬아웃으로 바뀌며 더 이상 필요 없습니다.)

## 1. Supabase 스키마

대시보드 → **SQL Editor** → [`../supabase/schema.sql`](../supabase/schema.sql) 붙여넣고 **Run**.
`alerts` + `subscribers` + `user_watchlist` + `supported_tickers`(11종목 시드) + 인덱스 + RLS 생성.

## 2. Vercel 배포

1. [vercel.com](https://vercel.com) → **Add New → Project** → 저장소(IAURAI/AlarmBot) import
2. **Root Directory = `vercel`** 지정 → Framework **Other** → Deploy
3. **Settings → Environment Variables** 에 위 표의 Vercel 대상 4개
   (`TELEGRAM_BOT_TOKEN`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_WEBHOOK_SECRET`,
   `TELEGRAM_WEBHOOK_SECRET`) 등록 → **Redeploy**

헬스체크: `curl https://<APP>.vercel.app/api/notify` → `{"ok": true, "service": "notify"}`

## 3. Supabase Database Webhook

대시보드 → **Database → Webhooks → Create a new hook**
- Table `public.alerts`, Events **Insert**, Type **HTTP Request**, Method **POST**
- URL `https://<APP>.vercel.app/api/notify`
- **HTTP Headers**: `x-webhook-secret` = `<SUPABASE_WEBHOOK_SECRET>`

## 4. Telegram 웹훅 등록

```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -d "url=https://<APP>.vercel.app/api/telegram" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

## 5. 로컬 봇을 Supabase 모드로

`.env` 에 두 줄 추가 후, 루프를 `--sink supabase` 로 재시작:

```bash
.venv/bin/python -m news_bot.run --loop --sink supabase
```

기동 시 로컬이 `config.watchlist`(지원 11종목)를 `supported_tickers` 테이블에 **자동 동기화**합니다
(단일 소스). 종목을 늘리려면 `config.py`의 watchlist에 추가 후 재시작하면 봇의 `/tickers`에 반영됩니다.

## 6. 동작 점검

1. 봇에게 `/start` → `/watch 삼성전자` → `/my` 로 등록 확인
2. SQL Editor에서 알림 한 줄 삽입 → **삼성전자를 watch한 사용자**에게 텔레그램 도착 확인
   ```sql
   insert into public.alerts (ticker, headline, reason, urgency)
   values ('삼성전자', '테스트 알림', '웹훅 점검', 'high');
   ```
3. `/latest`·`/status` 로 조회 확인

---

## 보안 체크리스트

- [ ] `.env`·service_role 키 커밋 안 됨(`.gitignore`)
- [ ] `/api/notify` 는 `x-webhook-secret` 불일치/미설정 시 401 (fail-closed)
- [ ] `/api/telegram` 은 `X-Telegram-Bot-Api-Secret-Token` 불일치/미설정 시 401
- [ ] RLS on + 정책 없음 → anon 키로 접근 불가

## 신뢰성 · 확장 한계

- 발송은 **멱등**: `/api/notify` 가 `sent=false→true` 를 원자적으로 클레임해 중복 웹훅 전달에도
  이중발송하지 않음. 수신자 조회는 클레임 전이라, 조회 실패 시 미발송으로 남아 복구 가능.
- 팬아웃은 **best-effort**: 개별 수신 실패는 건너뛰고, 차단/탈퇴(403)한 구독자는 자동 비활성화.
- **확장 한계**: 한 알림을 한 함수 실행(`maxDuration 30s`) 안에서 순차 발송하므로, 종목당 구독자가
  수십 명을 넘어가면 큐잉이 필요합니다. 그 규모가 되면 Supabase에 발송 큐 + Vercel Cron/워커로
  분산하는 업그레이드를 붙이면 됩니다(요청 주세요).

## 테스트

```bash
.venv/bin/python -m pytest news_bot/tests/ vercel/tests/ -q   # 네트워크·키 불필요
```
