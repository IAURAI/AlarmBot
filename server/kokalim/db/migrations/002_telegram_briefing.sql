-- m002: 텔레그램 우선 가동 (docs/mvp-decisions.md — 카톡 트랙 중단 중)
-- users.briefing_time : 지정 시간 브리핑 수신 시각. KST로 해석, NULL=미수신
-- identities.provider : 'telegram' 허용 (provider_user_id = 텔레그램 chat_id)
-- 알림톡용 users.phone / alimtalk_consent_at 은 카톡 트랙 재개 시 m003+ 에서 추가

ALTER TABLE users ADD COLUMN IF NOT EXISTS briefing_time TIME;

ALTER TABLE identities DROP CONSTRAINT identities_provider_check;
ALTER TABLE identities ADD CONSTRAINT identities_provider_check
  CHECK (provider IN ('toss', 'kakao', 'telegram'));
