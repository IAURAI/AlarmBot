# News Bot — 주식 뉴스 긴급 알림봇

언론의 개인투자자 파급력을 활용해, 뉴스/기사를 5분 간격으로 크롤링하고 **급한 것만** 추려
텔레그램(또는 카카오 '나에게 보내기')으로 알림을 보낸다.

## 파이프라인

```
수집(RSS + 네이버 API)  →  중복 제거(클러스터링)  →  긴급도 판정(키워드+LLM)
   →  요약(한 줄)  →  발송(텔레그램/카카오/콘솔)  →  상태 저장(재발송 방지)
```

봇 발송은 전체의 일부일 뿐이고, 핵심은 **중복 제거 + 긴급도 판정**이다. 같은 통신사 기사가
수십 매체에 복제되므로 제목 유사도로 하나의 사건으로 묶고, rolling seen-set으로 재발송을 막는다.

## 실행

```bash
# 오프라인(네트워크 0) — 픽스처로 파이프라인 확인
.venv/bin/python -m news_bot.run --offline --dry-run

# 실데이터 1회 (콘솔 출력)
.venv/bin/python -m news_bot.run --dry-run

# 5분 루프 + 텔레그램 발송
.venv/bin/python -m news_bot.run --loop --platform telegram
```

플래그: `--loop`(반복), `--offline`(픽스처), `--dry-run`(콘솔만), `--platform {console,telegram,kakao}`,
`--scope {watchlist,market,all}`, `--mode {hybrid,llm,keyword}`, `--interval <초>`.

## 자격증명 (.env)

프로젝트 루트 `.env`에 넣으면 `news_bot/__init__.py`가 자동 로드한다.

| 키 | 용도 |
|---|---|
| `NEWSBOT_TELEGRAM_BOT_TOKEN`, `NEWSBOT_TELEGRAM_CHAT_ID` | 텔레그램 발송(기존 봇과 분리). 없으면 `TELEGRAM_BOT_TOKEN`/`_CHAT_ID`로 폴백 |
| `KAKAO_ACCESS_TOKEN` | 카카오 '나에게 보내기'(memo API) — 본인 수신 전용, 6시간마다 갱신 필요 |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` | 네이버 뉴스 검색 API(관심종목 폴링) |
| `ANTHROPIC_API_KEY` | `llm_backend="anthropic"`일 때만 필요. 기본 codex 백엔드는 쓰지 않음 |

LLM은 기본으로 **codex 백엔드**(구독제)를 쓴다 — `codex` CLI가 설치·인증(ChatGPT 로그인)돼 있으면
토큰 비용 없이 동작한다. 자격증명이 없으면 해당 경로는 조용히 비활성화되고 콘솔/키워드/휴리스틱으로 폴백한다.

## 플랫폼 선택

- **텔레그램(권장)**: BotFather 토큰 → `sendMessage`. 무료, 채널 무제한 브로드캐스트.
- **카카오 '나에게 보내기'**: 본인 카톡에만. 타인 발송 불가(알림톡은 사업자+템플릿 심사라 자유 뉴스 부적합).

## 긴급도 판정 모드

- `hybrid`(기본): 키워드로 1차 판정 → 애매한 구간만 Claude가 판정. 비용/품질 균형.
- `llm`: 키워드로 잡음 제거 후 나머지를 전량 Claude 판정.
- `keyword`: 규칙만(무료, LLM 불필요).

LLM 백엔드 기본값은 **codex(구독제)** — `codex exec --output-schema`로 최종 응답을 JSON 스키마에
강제해 호출한다(ChatGPT 구독 인증, 토큰 비용 없음). `config.py`의 `llm_backend`를 `"anthropic"`으로
바꾸면 `ANTHROPIC_API_KEY`로 Claude(`llm_model`, 기본 opus-4-8)를 쓴다. codex 속도/비용은
`codex_effort`(기본 low)로 조절.

## 연관 컨텍스트 추적 (`--context`)

그 기업 뉴스뿐 아니라 **연관된 중요 소식**(공급망·고객·경쟁사·테마)까지 잡고, 시간에 따른
변화를 추적한다.

- **컨텍스트 그래프**(`context.py`): 관심종목마다 `self + related(엔티티) + theme` 감시 유닛을
  시드. `--expand-graph`를 돌리면 LLM이 찾은 연관 항목이 캐시에 병합됨(하이브리드).
- **병렬 조사**: 새 뉴스가 들어온 유닛만 골라 `ThreadPoolExecutor`로 동시 평가. 키가 없으면
  결정적 휴리스틱(매체 corroboration + 키워드), 있으면 유닛별 Claude 판정.
- **상황 상태**(`situations.json`): 유닛별 롤링 요약 + 스탠스 + 변화 타임라인을 영속화.
  각 평가는 *이전 상황*을 컨텍스트로 받아 델타(무엇이 바뀌었는지)를 판단.
- **두 트리거**: 실질적 변화(급변)는 즉시 알림, 완만한 변화는 `--report`로 주기 요약.

```bash
.venv/bin/python -m news_bot.run --context --offline --dry-run   # 오프라인 확인
.venv/bin/python -m news_bot.run --context --loop --platform telegram
.venv/bin/python -m news_bot.run --expand-graph                  # LLM 그래프 확장(1회)
.venv/bin/python -m news_bot.run --report --platform telegram    # 추적 상황 요약 발송
```

알림은 종목별로 묶여 `직접 소식 + 연관 동향(무엇이 바뀌었는지·중요도)`로 온다.

## method_b 연동

수집·중복제거 단계에서 종목별 일일 기사 수를 집계하면 그대로 `method_b`의
`--news-csv`(date,ticker,count) 입력이 된다. 알림봇이 그 파이프라인의 데이터 수집기를 겸한다.

## 한계 / 주의

- RSS URL은 예시값 — 매체별 경로가 바뀌므로 실서비스 전 확인 필요.
- 네이버 뉴스 API는 날짜 필터가 없는 검색형이라 실시간은 RSS가 주력.
- 뉴스 전문 재배포는 저작권 이슈 → 요약+헤드라인+링크로 발송.

## 테스트

```bash
.venv/bin/python -m pytest news_bot/tests/ -q   # 네트워크·API 키 불필요
```
