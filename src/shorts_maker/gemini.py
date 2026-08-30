"""Gemini 클라이언트. A0 에서는 연결 확인까지만 담당한다.

모든 호출은 llm_calls 에 기록해야 하지만(문서 §12) 그 테이블은 A2 에서 생긴다.
호출 지점을 여기 한 곳으로 모아 두어야 A2 에서 기록을 빠뜨리지 않는다.
"""

from google import genai

from . import config


class GeminiError(RuntimeError):
    pass


_clients: dict[str, genai.Client] = {}


def client(cfg: config.Config) -> genai.Client:
    """API 키당 Client 를 하나만 만들어 재사용한다.

    🔴 매번 새로 만들면 안 된다. `models.list()` 는 **지연 페이징**이라 이터레이션 도중에
    임시 Client 가 GC 되면 그 안의 HTTP 세션이 닫히고
    `Cannot send a request, as the client has been closed` 로 죽는다. 실제로 겪었다.
    """
    if not cfg.gemini_api_key:
        raise GeminiError("GEMINI_API_KEY 가 비어 있다 (.env 참고)")
    existing = _clients.get(cfg.gemini_api_key)
    if existing is None:
        existing = genai.Client(api_key=cfg.gemini_api_key)
        _clients[cfg.gemini_api_key] = existing
    return existing


def generative_model_ids(cfg: config.Config) -> list[str]:
    active = client(cfg)
    ids = []
    # 페이저를 먼저 리스트로 소진한다 — 위 주석의 이유로 이터레이션 중에 다른 일을 하지 않는다.
    for model in list(active.models.list()):
        actions = getattr(model, "supported_actions", None) or []
        if "generateContent" in actions:
            ids.append((model.name or "").removeprefix("models/"))
    return sorted(i for i in ids if i)


def ping(cfg: config.Config) -> dict:
    """가장 싼 실호출 1회. 과금 경로가 실제로 열려 있는지는 이걸로만 알 수 있다."""
    response = client(cfg).models.generate_content(model=cfg.gemini_model, contents="ping")
    usage = getattr(response, "usage_metadata", None)
    return {
        "text": (getattr(response, "text", "") or "").strip()[:40],
        "input_tokens": getattr(usage, "prompt_token_count", None),
        "output_tokens": getattr(usage, "candidates_token_count", None),
        # 문서 §7 이 경고한 항목 — 사고 토큰은 출력 단가로 과금되고 여기서 비용이 튄다.
        "thinking_tokens": getattr(usage, "thoughts_token_count", None),
    }


RESPONSE_SCHEMA_KEY = "response_schema"

# 재시도할 상태코드. 과부하·일시 장애만 다시 부른다.
# 🔴 4xx 는 재시도하지 않는다 — 잘못된 키나 잘못된 요청을 반복해봐야 같은 답이고 돈만 든다.
TRANSIENT_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4
BASE_BACKOFF_SEC = 2.0


def _status_of(exc: Exception) -> int | None:
    """예외에서 HTTP 상태코드를 뽑는다.

    google-genai 의 예외 계층에 기대지 않는다 — 버전마다 클래스가 바뀌어서, 못 알아보면
    재시도가 조용히 사라진다. code 속성을 먼저 보고 없으면 메시지에서 찾는다.
    """
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    import re

    found = re.search(r"\b(429|5\d\d)\b", str(exc))
    return int(found.group(1)) if found else None


def is_transient(exc: Exception) -> bool:
    return _status_of(exc) in TRANSIENT_STATUS


def generate_json(cfg: config.Config, prompt: str, response_schema: dict) -> tuple[str, dict, int]:
    """JSON 을 강제해서 한 번 호출한다. (본문, 사용량, 소요 ms) 를 돌려준다.

    response_schema 를 쓰는 이유는 편의가 아니라 **런타임 동작**이다 — 응답이 스키마로
    강제되면 "JSON 이 깨져서 재시도" 라는 실패 경로 자체가 사라진다. 우리가 검증해야 할 건
    형식이 아니라 내용(인덱스가 실제로 존재하는가)만 남는다(§12).
    """
    import logging
    import time

    from google.genai import types

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=response_schema,
    )
    active = client(cfg)
    started = time.monotonic()

    # 파이프라인은 LLM 을 연달아 3번 이상 부른다. 그중 하나가 과부하로 실패하면 잡 전체가
    # 죽는데, 그건 대개 몇 초 뒤면 되는 종류의 실패다.
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = active.models.generate_content(
                model=cfg.gemini_model, contents=prompt, config=config
            )
            break
        except Exception as exc:
            if attempt == MAX_ATTEMPTS or not is_transient(exc):
                raise
            wait = BASE_BACKOFF_SEC * (2 ** (attempt - 1))
            logging.warning(
                "Gemini call failed (%s), retrying in %.0fs [%d/%d]",
                _status_of(exc), wait, attempt, MAX_ATTEMPTS,
            )
            time.sleep(wait)

    latency_ms = int((time.monotonic() - started) * 1000)
    usage = getattr(response, "usage_metadata", None)
    return (
        getattr(response, "text", "") or "",
        {
            "input_tokens": getattr(usage, "prompt_token_count", None),
            "output_tokens": getattr(usage, "candidates_token_count", None),
            "thinking_tokens": getattr(usage, "thoughts_token_count", None),
            "attempts": attempt,
        },
        latency_ms,
    )


def model_catalog(cfg: config.Config) -> list[dict]:
    """generateContent 가 되는 모델을 한도와 함께 돌려준다.

    모델명은 자주 바뀐다(문서 §7 도 "가격·모델명은 자주 바뀐다"고 적어놨다).
    문서에 적힌 이름을 믿지 말고 여기서 확인한 걸 SHORTS_GEMINI_MODEL 에 넣는다.
    """
    active = client(cfg)
    rows = []
    for model in list(active.models.list()):
        actions = getattr(model, "supported_actions", None) or []
        if "generateContent" not in actions:
            continue
        rows.append(
            {
                "id": (model.name or "").removeprefix("models/"),
                "label": getattr(model, "display_name", "") or "",
                "input_limit": getattr(model, "input_token_limit", None),
                "output_limit": getattr(model, "output_token_limit", None),
            }
        )
    return sorted(rows, key=lambda r: r["id"])
