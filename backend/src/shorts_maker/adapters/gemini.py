"""Gemini 클라이언트. A0 에서는 연결 확인까지만 담당한다.

모든 호출은 llm_calls 에 기록해야 하지만(문서 §12) 그 테이블은 A2 에서 생긴다.
호출 지점을 여기 한 곳으로 모아 두어야 A2 에서 기록을 빠뜨리지 않는다.
"""

from google import genai

from .. import config


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


# 🔴 429 라고 다 같은 429 가 아니다. 분당 한도(잠깐 기다리면 풀림)와 **일일 한도**(내일까지 안 풀림)가
# 같은 코드로 온다. 일일 한도에 백오프 재시도를 걸면 14초를 버리고 요청 3번을 더 쓰고도 똑같이 실패한다.
# 구글은 quotaId 에 `PerDay` 를 넣어 구분해 준다(2026-09-06에 겪었다: 무료 등급 하루 20회).
_DAILY_QUOTA_MARKS = ("PerDay", "per day", "free_tier_requests")


def is_daily_quota(exc: Exception) -> bool:
    if _status_of(exc) != 429:
        return False
    text = str(exc)
    return any(mark in text for mark in _DAILY_QUOTA_MARKS)


def is_transient(exc: Exception) -> bool:
    if is_daily_quota(exc):
        return False
    return _status_of(exc) in TRANSIENT_STATUS


DAILY_QUOTA_HINT = (
    "Gemini 일일 할당량을 다 썼다. 재시도해도 오늘은 풀리지 않는다.\n"
    "  · 다른 모델로 바꾼다 — 모델마다 할당량이 따로다 (SHORTS_GEMINI_MODEL, `sm models` 로 목록)\n"
    "  · 결제를 붙여 유료 등급으로 올린다 (무료 등급은 모델당 하루 수십 회다)\n"
    "  · 태평양시 자정에 초기화될 때까지 기다린다"
)


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
            if is_daily_quota(exc):
                raise GeminiError(f"{DAILY_QUOTA_HINT}\n\n원문: {exc}") from exc
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
            # 🔴 총합을 함께 저장한다. 구글은 "output (including thinking tokens)" 으로 과금하는데,
            # candidates 에 thinking 이 이미 포함되는지가 SDK/모델마다 분명하지 않다.
            # total - prompt 로 계산하면 어느 쪽이든 정확하다.
            "total_tokens": getattr(usage, "total_token_count", None),
            # 캐시된 입력은 기본가의 10% 로 과금된다. 지금은 캐싱을 안 쓰지만 값은 남겨둔다.
            "cached_tokens": getattr(usage, "cached_content_token_count", None),
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


# 한 번에 보낼 텍스트 수. gemini-embedding-001 은 요청당 배치 상한이 있고, 넘으면 400 이다.
# 한도가 모델마다 다르므로 넉넉히 낮게 잡았다 — 질문 수백 개면 몇 번 나눠 보내도 1초 안쪽이다.
EMBED_BATCH = 100


def embed_texts(
    cfg: config.Config, texts: list[str], task_type: str
) -> tuple[list[list[float]], int, int]:
    """텍스트들을 임베딩한다. (벡터들, 총 소요 ms, 배치 호출 수).

    🔴 `task_type` 이 다르면 **다른 벡터**가 나온다. 같은 문장이라도 'SEMANTIC_SIMILARITY' 로
    뽑은 것과 'RETRIEVAL_DOCUMENT' 로 뽑은 것을 섞어 비교하면 안 된다 — 그래서 저장할 때
    task_type 을 키에 넣는다(answers/embeddings.py).

    🔴 정규화는 **여기서 하지 않는다.** 저장 직전에 한 번만 한다(embeddings.to_blob) — 두 곳에서
    하면 어디서 이미 했는지 헷갈린다. gemini-embedding-001 은 3072 이 아닌 차원을 요청하면
    정규화되지 않은 벡터를 주므로, 코사인을 내적으로 계산하려면 반드시 한 번은 해야 한다.
    """
    import time

    from google.genai import types

    if not texts:
        return [], 0, 0
    active = client(cfg)
    settings = types.EmbedContentConfig(
        task_type=task_type, output_dimensionality=cfg.embed_dim
    )
    vectors: list[list[float]] = []
    started = time.monotonic()
    calls = 0
    for offset in range(0, len(texts), EMBED_BATCH):
        batch = texts[offset : offset + EMBED_BATCH]
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = active.models.embed_content(
                    model=cfg.embed_model, contents=batch, config=settings
                )
                break
            except Exception as exc:
                if is_daily_quota(exc):
                    raise GeminiError(f"{DAILY_QUOTA_HINT}\n\n원문: {exc}") from exc
                if attempt == MAX_ATTEMPTS or not is_transient(exc):
                    raise
                import logging
                import time as _time

                wait = BASE_BACKOFF_SEC * (2 ** (attempt - 1))
                logging.warning("embed failed (%s), retrying in %.0fs", _status_of(exc), wait)
                _time.sleep(wait)
        calls += 1
        got = list(getattr(response, "embeddings", None) or [])
        if len(got) != len(batch):
            raise GeminiError(f"임베딩 개수가 안 맞는다: 보낸 {len(batch)}, 받은 {len(got)}")
        for item in got:
            values = getattr(item, "values", None)
            if not values:
                raise GeminiError("임베딩 응답에 값이 없다")
            vectors.append(list(values))
    return vectors, int((time.monotonic() - started) * 1000), calls
