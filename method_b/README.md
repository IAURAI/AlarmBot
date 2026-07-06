# Method B: 개인 거래비중 x 관심 이벤트 패널 회귀

이 디렉터리는 종목-일 패널에서 `Att x RetailShare` 상호작용이 단기 CAR를 키우고 이후 반전을 심화하는지 검정하는 파이프라인입니다.

## 실행

오프라인 합성 데이터 검증:

```bash
.venv/bin/python -m method_b.run --offline-synthetic --out output/synth_demo
```

실데이터 실행:

```bash
export KRX_ID="..."
export KRX_PW="..."
.venv/bin/python -m method_b.run \
  --start 20160104 --end 20260630 \
  --sample-tickers 200 --seed 42 \
  --markets KOSPI KOSDAQ \
  --out output/
```

KRX 투자자별 거래대금, 시가총액, 지수, 상장 리스트는 KRX 로그인이 필요합니다. `KRX_ID`/`KRX_PW`가 없으면 CLI는 안내 메시지를 출력하고 exit code 2로 종료합니다. OHLCV는 네이버 소스를 사용하지만, 전체 파이프라인에는 KRX 로그인 데이터가 필요합니다.

## 산출물

- `panel.parquet`: 회귀용 종목-일 패널
- `results.csv`: 회귀 계수 long table
- `results.md`: 해석 요약과 계수표
- `fig_interaction_by_horizon.png`: B1 ALL 상호작용 계수와 95% CI
- `run_meta.json`: 실행 인자, 종목 수, fallback 여부

## 원시 데이터 스키마

| 테이블 | 주요 컬럼 |
|---|---|
| `tickers` | `ticker`, `market`, `name` |
| `ohlcv` | `ticker`, `market`, `date`, `open`, `high`, `low`, `close`, `volume` |
| `trading_buy/sell` | `ticker`, `market`, `date`, `inst`, `other_corp`, `retail`, `foreign`, `total` |
| `market_cap` | `ticker`, `market`, `date`, `mktcap`, `volume`, `value`, `shares`, `close` |
| `index_ohlcv` | `market`, `date`, `open`, `high`, `low`, `close`, `volume`, `value` |
| `market_trading_buy/sell` | `market`, `date`, `inst`, `other_corp`, `retail`, `foreign`, `total` |

## 패널 컬럼

| 컬럼 | 의미 |
|---|---|
| `ret` | 수정 종가 기준 일수익률 |
| `value_gross` | 전체 매수대금 + 전체 매도대금 |
| `retail_share_raw` | `(개인매수+개인매도)/value_gross` |
| `retail_share` | 직전 20영업일 평균 retail share, 1일 시프트 |
| `netbuy_mktcap` | 개인 순매수 / 시가총액 |
| `retail_share_mkt` | 시장 월간 retail share, 1개월 래그 |
| `AR` | t-120~t-21 rolling market model abnormal return |
| `car_1_1`, `car_1_5`, `car_3_20`, `car_21_60` | 전방 AR 합 |
| `netbuy_fwd_0_2` | t~t+2 개인 순매수/시총 합 |
| `log_mktcap`, `mom_20`, `vol_20` | 회귀 controls |
| `att_abnvol` | 거래대금이 베이스라인 중앙값의 4배 이상 |
| `att_extret`, `ext_up`, `ext_down` | 극단 수익률 이벤트와 방향 |
| `att_news` | 선택 뉴스 CSV 제공 시 뉴스 관심 이벤트 |

## 뉴스 CSV

`--news-csv PATH`는 `date,ticker,count` 컬럼을 가진 CSV를 받습니다. 네이버 뉴스 API는 날짜 필터가 제한적이라 v1에서는 연동하지 않고, 빅카인즈 등에서 기간 필터 후 내보낸 CSV만 어댑터로 처리합니다.

## 한계

- 실데이터 자체 검증은 KRX 로그인 환경변수와 네트워크 접근이 필요합니다.
- KRX/pykrx 반환 컬럼은 pykrx 1.2.8 docstring 기준으로 정규화하며, 기대 컬럼이 없으면 원본 컬럼 목록을 포함해 예외를 던집니다.
- 합성 데이터는 효과 복원 검증용이며 실제 시장 미시구조를 완전히 모사하지 않습니다.
- rolling market model은 종목별 루프를 사용하므로 전체 유니버스 실행은 캐시가 있어도 시간이 걸릴 수 있습니다.
