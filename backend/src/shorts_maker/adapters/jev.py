"""Jev 클라이언트 — TypeSafe AI 의 System One 모델 (2026-10-31 도입).

Jev 는 글을 쓰지 않는다. `state`(판단할 내용) 하나에 질문 여러 개를 던지면 질문마다 **숫자**를
돌려준다. 질문은 세 종류다.

  noul   — 명제가 참일 확률 0~1
  choice — 선택지마다 확률(합 1). 선택지는 최대 255개
  score  — 순서 있는 등급(1~10개)의 확률과 그 기대값

질문들은 같은 state 를 보고 **서로 독립적으로, 병렬로** 평가된다. 그래서 한 요청에 질문을 많이
넣어도 지연이 거의 늘지 않는다(docs.typesafe.ai/patterns/fan-out). Gemini 에 판정 여러 개를 한
JSON 으로 묶으면 앞에서 생성한 판정이 뒤 판정을 끌고 가는데, 여기에는 그 간섭이 없다.

🔴 **SDK(typesafe-sdk)를 쓰지 않는다.** 하는 일이 POST 한 번이고, SDK 가 더해 주는 런타임 동작은
재시도뿐이다 — 그건 아래에 직접 둔다(429·529 만, retry-after 를 따른다). 의존성을 늘릴 근거가 없다.

🔴 **한국어는 영어보다 정확도가 낮다**고 공식 문서가 직접 적는다(docs.typesafe.ai/models#language-support).
그래서 질문 문구(instructions)는 영어로 쓰고 판단할 대사(state)만 한국어로 둔다. 판정을 그대로 믿지
않고 합격선을 우리 데이터로 다시 잡는 이유도 이것이다(answers/proposals.py).
"""

import json
import logging
import time
import urllib.error
import urllib.request

from .. import config

ENDPOINT = "https://api.typesafe.ai/v1/systemone"


class JevError(RuntimeError):
    pass


# 🔴 재시도는 둘뿐이다. 429(분당 한도)와 529(TypeSafe 과부하). 문서가 "짧게 기다렸다 다시" 라고 하는
# 것이 이 둘이다(docs.typesafe.ai/api/errors). 4xx 는 다시 보내도 같은 답이다.
TRANSIENT_STATUS = {429, 500, 502, 503, 504, 529}
MAX_ATTEMPTS = 4
BASE_BACKOFF_SEC = 1.0
# 서버가 retry-after 를 아무리 길게 말해도 이보다는 안 기다린다 — 잡 워커가 하나라 큐 전체가 멈춘다.
MAX_SERVER_WAIT_SEC = 20.0


def system_one(
    cfg: config.Config, state, questions: dict, timeout: float | None = None,
) -> tuple[dict, dict, int, str]:
    """한 번 부른다. (answers, usage, 소요 ms, 원본 응답 본문) 을 돌려준다.

    `answers` 는 검증을 마친 것이다 — 물은 질문이 전부 있고, 종류가 맞고, 숫자가 범위 안이다.
    🔴 규약: 외부 모델의 출력은 항상 검증한다. 형식은 스키마가 보장하지만 "물은 것에 다 답했나" 는
    우리가 본다. 빠진 판정을 0 으로 채우면 "아니다" 와 "답을 못 받았다" 가 구분되지 않는다.
    """
    if not cfg.jev_api_key:
        raise JevError("JEV_API_KEY 가 비어 있다 (backend/.env 참고)")
    if not questions:
        raise JevError("질문이 없다")
    body = json.dumps(
        {"model": cfg.jev_model, "state": state, "questions": questions}, ensure_ascii=False
    ).encode("utf-8")
    started = time.monotonic()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = urllib.request.Request(
            ENDPOINT, data=body, method="POST",
            headers={
                "Authorization": f"Bearer {cfg.jev_api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or cfg.jev_timeout_sec) as response:
                raw = response.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            if attempt == MAX_ATTEMPTS or exc.code not in TRANSIENT_STATUS:
                raise JevError(f"Jev {exc.code}: {detail}") from exc
            wait = _retry_after(exc) or BASE_BACKOFF_SEC * (2 ** (attempt - 1))
            logging.warning("Jev %s, retrying in %.1fs [%d/%d]", exc.code, wait, attempt, MAX_ATTEMPTS)
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as exc:
            # 연결 실패·제한 시간 초과. 한 번 더 해 볼 가치는 있지만 오래 붙들지는 않는다.
            if attempt == MAX_ATTEMPTS:
                raise JevError(f"Jev 에 닿지 못했다: {exc}") from exc
            time.sleep(BASE_BACKOFF_SEC * attempt)
    latency_ms = int((time.monotonic() - started) * 1000)

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise JevError(f"Jev 응답이 JSON 이 아니다: {raw[:200]!r}") from exc
    answers = validate(questions, payload.get("answers"))
    usage = payload.get("usage") or {}
    return (
        answers,
        {
            "input_tokens": usage.get("input_tokens"),
            # 출력은 확률 분포라 토큰이 있지만 **과금되지 않는다**(docs.typesafe.ai/models).
            "output_tokens": usage.get("output_tokens"),
            # stage_calls 의 Gemini 칸과 맞춘다. Jev 에는 사고 과정이 없다 — 0 이 아니라 해당 없음이다.
            "thinking_tokens": None,
            "total_tokens": None,
            "cached_tokens": None,
            "attempts": attempt,
            # 별칭으로 보냈어도 실제로 답한 버전. 합격선은 버전마다 다시 잡는다.
            "model": payload.get("model") or cfg.jev_model,
        },
        latency_ms,
        raw,
    )


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    value = exc.headers.get("retry-after") if exc.headers else None
    try:
        return min(float(value), MAX_SERVER_WAIT_SEC) if value else None
    except ValueError:
        return None


def validate(questions: dict, answers) -> dict:
    """물은 질문마다 맞는 종류의 답이 범위 안의 숫자로 왔는지 본다. 하나라도 틀리면 통째로 버린다."""
    if not isinstance(answers, dict):
        raise JevError(f"answers 가 객체가 아니다: {answers!r}")
    checked: dict = {}
    for name, question in questions.items():
        answer = answers.get(name)
        kind = question.get("type")
        if not isinstance(answer, dict) or answer.get("type") != kind:
            raise JevError(f"질문 {name!r} 의 답이 없거나 종류가 다르다: {answer!r}")
        if kind == "noul":
            checked[name] = {"noul": _unit(name, answer.get("noul"))}
        elif kind == "score":
            top = len(question.get("criteria") or []) - 1
            score = answer.get("score")
            if not isinstance(score, (int, float)) or not 0 <= score <= top:
                raise JevError(f"질문 {name!r} 의 점수가 0~{top} 밖이다: {score!r}")
            checked[name] = {
                "score": float(score),
                "levels": top + 1,
                "confidence": _unit(name, answer.get("confidence")),
            }
        elif kind == "choice":
            allowed = set(question.get("criteria") or {})
            probabilities = answer.get("probabilities") or {}
            # 🔴 선택지 밖의 키는 버린다(화이트리스트). 확률은 남은 것만으로 쓴다.
            kept = {key: _unit(name, value) for key, value in probabilities.items() if key in allowed}
            if not kept:
                raise JevError(f"질문 {name!r} 에 선택지 확률이 하나도 없다: {answer!r}")
            checked[name] = {
                "choice": answer.get("choice") if answer.get("choice") in allowed else max(kept, key=kept.get),
                "confidence": _unit(name, answer.get("confidence")),
                "probabilities": kept,
            }
        else:
            raise JevError(f"모르는 질문 종류다: {kind!r}")
    return checked


def _unit(name: str, value) -> float:
    if not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        raise JevError(f"질문 {name!r} 의 확률이 0~1 밖이다: {value!r}")
    return float(value)


def score_to_100(score: float, levels: int) -> int:
    """Score 의 기대값(0~levels-1)을 화면의 0~100 눈금으로. 옛 Gemini 판정과 같은 칸에 담기 위해서다."""
    if levels <= 1:
        return 0
    return round(100 * score / (levels - 1))
