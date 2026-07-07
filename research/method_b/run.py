"""CLI entry point for Method B."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .attention import load_news_counts
from .config import PipelineConfig
from .ingest import check_krx_login, fetch_raw_data
from .panel import build_panel
from .regress import run_all_regressions
from .report import write_report
from .synth import generate


LOGIN_MESSAGE = "KRX 데이터는 로그인이 필요합니다. https://data.krx.co.kr 무료 가입 후 KRX_ID/KRX_PW 환경변수를 설정하세요."


def main(argv: list[str] | None = None) -> int:
    """CLI를 실행한다."""
    args = _parse_args(argv)
    out_dir = Path(args.out) if args.out else Path("output/synth_demo" if args.offline_synthetic else "output")
    config = PipelineConfig(
        start=args.start,
        end=args.end,
        markets=tuple(args.markets),
        sample_tickers=args.sample_tickers,
        seed=args.seed,
        out_dir=out_dir,
    )
    _setup_logging()
    if args.offline_synthetic:
        logging.info("Running offline synthetic mode")
        raw = generate(config.synth_n_stocks, config.synth_n_days, config.seed)
    else:
        if not check_krx_login(config):
            print(LOGIN_MESSAGE)
            return 2
        raw = fetch_raw_data(config)
    news_counts = load_news_counts(args.news_csv)
    panel, meta = build_panel(raw, config, out_dir=out_dir, news_counts=news_counts)
    results = run_all_regressions(panel, config)
    report_path = write_report(results, config, out_dir)
    _write_run_meta(out_dir, args, meta, raw, results)
    print(f"완료: panel_obs={len(panel)}, regress_rows={len(results)}, report={report_path}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """명령행 인자를 파싱한다."""
    parser = argparse.ArgumentParser(description="Method B retail-share x attention panel regression")
    parser.add_argument("--start", default="20160104")
    parser.add_argument("--end", default="20260630")
    parser.add_argument("--sample-tickers", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--offline-synthetic", action="store_true")
    parser.add_argument("--markets", nargs="+", default=["KOSPI", "KOSDAQ"])
    parser.add_argument("--news-csv")
    parser.add_argument("--out")
    return parser.parse_args(argv)


def _setup_logging() -> None:
    """CLI 진행 로그 포맷을 설정한다."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _write_run_meta(
    out_dir: Path,
    args: argparse.Namespace,
    panel_meta: dict[str, object],
    raw: object,
    results: object,
) -> None:
    """실행 인자와 주요 산출 메타데이터를 JSON으로 저장한다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "args": vars(args),
        "panel_meta": panel_meta,
        "raw_tickers": int(len(raw.tickers)),
        "result_rows": int(len(results)),
    }
    (out_dir / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
