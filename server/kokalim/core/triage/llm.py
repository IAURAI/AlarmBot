"""LLM 백엔드 추상화 — Codex headless(구독제) 또는 Anthropic API.

두 백엔드 모두 `complete_json(prompt, schema) -> dict` 하나로 통일한다.
- CodexBackend: `codex exec --output-schema`로 최종 응답 형태를 스키마에 강제하고
  `--output-last-message`로 최종 메시지만 파일로 받아 파싱(로그 파싱 불필요). 토큰 비용 없음.
- AnthropicBackend: Messages API 구조화 출력.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from kokalim.config import NewsBotConfig

LOGGER = logging.getLogger(__name__)


class CodexBackend:
    """codex CLI를 headless로 호출해 스키마 강제 JSON을 받는다(ChatGPT 구독 인증)."""

    def __init__(self, model: str | None, effort: str, timeout: int, usage_log: str | None = None) -> None:
        """모델(비우면 codex 기본), reasoning effort, 타임아웃(초), 사용량 로그 경로."""
        self._model = model
        self._effort = effort
        self._timeout = timeout
        self._usage_log = usage_log

    def complete_json(self, prompt: str, schema: dict, max_tokens: int = 1024) -> dict:
        """프롬프트+스키마로 codex를 실행하고 JSON 객체를 반환한다(호출량 로깅)."""
        workdir = tempfile.mkdtemp(prefix="newsbot_codex_")
        started = time.monotonic()
        ok, tokens = False, None
        try:
            schema_path = os.path.join(workdir, "schema.json")
            out_path = os.path.join(workdir, "last.txt")
            with open(schema_path, "w", encoding="utf-8") as fh:
                json.dump(schema, fh, ensure_ascii=False)
            cmd = [
                "codex", "exec", "--skip-git-repo-check", "--ephemeral",
                "-s", "read-only", "-c", f"model_reasoning_effort={self._effort}",
                "--output-schema", schema_path, "-o", out_path,
            ]
            if self._model:
                cmd += ["-m", self._model]
            cmd.append(prompt)
            proc = subprocess.run(
                cmd, cwd=workdir, capture_output=True, text=True,
                timeout=self._timeout, check=True,
            )
            ok, tokens = True, _parse_tokens(proc.stdout)
            with open(out_path, encoding="utf-8") as fh:
                text = fh.read().strip()
            return _parse_json(text)
        finally:
            self._log_usage(ok, time.monotonic() - started, tokens)
            shutil.rmtree(workdir, ignore_errors=True)

    def _log_usage(self, ok: bool, elapsed: float, tokens: int | None) -> None:
        """codex 호출 1건을 JSONL로 기록한다(사용량 측정용)."""
        if not self._usage_log:
            return
        try:
            path = Path(self._usage_log)
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "at": datetime.now(timezone.utc).isoformat(),
                "ok": ok,
                "elapsed_s": round(elapsed, 1),
                "tokens": tokens,
            }
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except Exception:  # pragma: no cover - 로깅 실패는 무시
            pass


class AnthropicBackend:
    """Anthropic Messages API 구조화 출력 백엔드."""

    def __init__(self, model: str) -> None:
        """Anthropic 클라이언트를 준비한다."""
        import anthropic

        self._client = anthropic.Anthropic()
        self._model = model

    def complete_json(self, prompt: str, schema: dict, max_tokens: int = 1024) -> dict:
        """구조화 출력으로 스키마에 맞는 JSON을 받는다."""
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "{}")
        return json.loads(text)


def get_backend(config: NewsBotConfig):
    """설정에 맞는 LLM 백엔드를 만든다. keyword 모드거나 자격 없으면 None."""
    if config.urgency_mode == "keyword":
        return None
    if config.llm_backend == "codex":
        if shutil.which("codex"):
            return CodexBackend(config.codex_model or None, config.codex_effort, config.codex_timeout, config.codex_usage_log)
        LOGGER.warning("codex CLI 없음 — LLM 비활성(규칙/휴리스틱만)")
        return None
    if config.llm_backend == "anthropic":
        if os.getenv("ANTHROPIC_API_KEY"):
            return AnthropicBackend(config.llm_model)
        LOGGER.info("ANTHROPIC_API_KEY 없음 — LLM 비활성")
        return None
    LOGGER.warning("알 수 없는 llm_backend=%s — LLM 비활성", config.llm_backend)
    return None


def _parse_tokens(stdout: str) -> int | None:
    """codex 출력에서 토큰 사용량을 best-effort로 파싱한다(없으면 None)."""
    if not stdout:
        return None
    match = re.search(r"tokens used[:\s]*([\d,]+)", stdout, re.IGNORECASE)
    if match:
        try:
            return int(match.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def _parse_json(text: str) -> dict:
    """스키마 강제 응답을 파싱하되, 잡음이 섞이면 마지막 JSON 객체를 추출한다."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        objects = _json_objects(text)
        if objects:
            return objects[-1]
        raise


def _json_objects(text: str) -> list[dict]:
    """문자열에서 균형 잡힌 최상위 {...} 객체들을 스캔해 파싱한다."""
    objects: list[dict] = []
    depth = 0
    start: int | None = None
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    objects.append(json.loads(text[start:i + 1]))
                except json.JSONDecodeError:
                    pass
                start = None
    return objects
