"""News bot CLI 엔트리포인트."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import time

from .config import NewsBotConfig
from .pipeline import run_cycle
from .pipeline_context import build_situation_report, run_context_cycle


def main(argv: list[str] | None = None) -> int:
    """CLI를 실행한다."""
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = _build_config(args)

    if args.expand_graph:
        return _expand_graph(config)
    if args.report:
        return _send_report(config, args.dry_run)

    cycle = run_context_cycle if args.context else run_cycle
    if args.loop:
        _run_loop(cycle, config, args)
        return 0
    result = cycle(config, offline=args.offline, dry_run=args.dry_run)
    print(_summary(result))
    return 0


def _summary(result) -> str:
    """결과 dataclass를 한 줄 요약으로."""
    if hasattr(result, "urgent"):
        return (f"완료: 수집={result.fetched} 신규={result.fresh} "
                f"클러스터={result.clusters} 긴급={result.urgent} 발송={result.sent}")
    return (f"완료(컨텍스트): 수집={result.fetched} 활성유닛={result.items_with_news} "
            f"알림={result.alerts} 발송={result.sent}")


def _run_loop(cycle, config: NewsBotConfig, args: argparse.Namespace) -> None:
    """interval 간격으로 사이클을 반복한다."""
    logging.info("루프 시작 — %d초 간격", config.interval_seconds)
    while True:
        try:
            result = cycle(config, offline=args.offline, dry_run=args.dry_run)
            logging.info("%s", _summary(result))
        except Exception:  # pragma: no cover - 운영 견고성
            logging.exception("사이클 실패 — 다음 주기에 재시도")
        time.sleep(config.interval_seconds)


def _expand_graph(config: NewsBotConfig) -> int:
    """LLM으로 컨텍스트 그래프를 확장해 캐시에 저장한다(하이브리드)."""
    from .context import CONTEXT_GRAPH, save_expansion
    from .investigate import discover_expansion
    from .llm import get_backend

    backend = get_backend(config)
    if backend is None:
        print("LLM 백엔드(codex 또는 ANTHROPIC_API_KEY)가 없어 확장할 수 없습니다.")
        return 2
    expansion = discover_expansion(backend, list(CONTEXT_GRAPH))
    save_expansion(config.expansion_path, expansion)
    print(f"그래프 확장 저장: {len(expansion)}개 종목 → {config.expansion_path}")
    return 0


def _send_report(config: NewsBotConfig, dry_run: bool) -> int:
    """추적 상황 주기 요약을 발송한다."""
    from .notify import get_notifier
    from .situation import load_situations

    text = build_situation_report(load_situations(config.situation_path))
    get_notifier(config, dry_run).send(text)
    return 0


def _build_config(args: argparse.Namespace) -> NewsBotConfig:
    """기본 설정에 CLI 오버라이드를 적용한다."""
    overrides: dict[str, object] = {}
    if args.platform:
        overrides["platform"] = args.platform
    if args.scope:
        overrides["scope"] = args.scope
    if args.mode:
        overrides["urgency_mode"] = args.mode
    if args.interval:
        overrides["interval_seconds"] = args.interval
    return dataclasses.replace(NewsBotConfig(), **overrides)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """명령행 인자를 파싱한다."""
    parser = argparse.ArgumentParser(description="주식 뉴스 긴급 알림봇")
    parser.add_argument("--context", action="store_true", help="연관 컨텍스트 추적 모드")
    parser.add_argument("--report", action="store_true", help="추적 상황 주기 요약 발송")
    parser.add_argument("--expand-graph", action="store_true", help="LLM으로 컨텍스트 그래프 확장")
    parser.add_argument("--loop", action="store_true", help="interval 간격으로 반복 실행")
    parser.add_argument("--offline", action="store_true", help="픽스처로 오프라인 실행(네트워크 0)")
    parser.add_argument("--dry-run", action="store_true", help="실제 발송 없이 콘솔 출력")
    parser.add_argument("--platform", choices=["console", "telegram", "kakao"])
    parser.add_argument("--scope", choices=["watchlist", "market", "all"])
    parser.add_argument("--mode", choices=["hybrid", "llm", "keyword"], help="긴급도 판정 방식")
    parser.add_argument("--interval", type=int, help="루프 간격(초)")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
