-- Supabase 스키마: alerts 테이블 + 인덱스 + RLS
-- 실행: Supabase 대시보드 → SQL Editor에 붙여넣고 Run.
-- 로컬 봇이 여기에 INSERT → Database Webhook이 Vercel /api/notify 호출 → 텔레그램 발송.

create table if not exists public.alerts (
  id           bigint generated always as identity primary key,
  created_at   timestamptz not null default now(),
  ticker       text,                              -- 관심종목명(없으면 시장 일반)
  headline     text not null,                     -- 알림 제목/헤드라인
  reason       text default '',                   -- 왜 중요한지 한 줄
  url          text,                              -- 원문 링크
  urgency      text not null default 'medium',    -- high | medium | low
  score        numeric not null default 0,        -- 점수(기본=키워드점수, 컨텍스트=중요도)
  source_count int  not null default 1,           -- 보도 매체 수
  kind         text not null default 'urgent',    -- urgent | context
  sent         boolean not null default false     -- Vercel이 텔레그램 발송 완료 표시(멱등)
);

create index if not exists alerts_created_at_idx on public.alerts (created_at desc);
create index if not exists alerts_ticker_idx     on public.alerts (ticker);
-- 미발송 행 스윕/복구용 부분 인덱스
create index if not exists alerts_unsent_idx     on public.alerts (created_at) where sent = false;

-- RLS 켜기: 정책을 만들지 않으므로 anon/authenticated 접근은 전면 차단된다.
-- 로컬 sink와 Vercel 함수는 service_role 키를 쓰며, service_role은 RLS를 우회한다.
alter table public.alerts enable row level security;


-- ── 개인별 관심종목 (Phase 1) ──────────────────────────────────────────────
-- 봇 사용자(구독자)
create table if not exists public.subscribers (
  chat_id    bigint primary key,             -- 텔레그램 chat id
  username   text,
  created_at timestamptz not null default now(),
  active     boolean not null default true   -- /stop 시 false (관심종목은 보존)
);
alter table public.subscribers enable row level security;

-- 사용자별 관심종목(다대다)
create table if not exists public.user_watchlist (
  chat_id    bigint not null references public.subscribers(chat_id) on delete cascade,
  ticker     text   not null,
  created_at timestamptz not null default now(),
  primary key (chat_id, ticker)
);
-- "이 종목 보는 사람" 역조회용(팬아웃 라우팅)
create index if not exists user_watchlist_ticker_idx on public.user_watchlist (ticker);
alter table public.user_watchlist enable row level security;

-- 지원 종목: 로컬 봇이 기동 시 config.watchlist를 upsert로 동기화(단일 소스).
-- /watch 검증과 /tickers 목록에 사용.
create table if not exists public.supported_tickers (
  ticker     text primary key,
  active     boolean not null default true,
  updated_at timestamptz not null default now()
);
alter table public.supported_tickers enable row level security;
-- 초기 시드(로컬이 이후 최신 유지 — 없어도 로컬 첫 기동 시 채워짐)
insert into public.supported_tickers (ticker) values
  ('삼성전자'),('삼성전기'),('SK하이닉스'),('LG에너지솔루션'),('삼성바이오로직스'),
  ('현대차'),('기아'),('네이버'),('카카오'),('포스코'),('셀트리온')
on conflict (ticker) do nothing;
