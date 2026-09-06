"""라우팅 — 검색 경로 vs rank 경로 (tease §5-4).

두 종류의 요청이 들어온다.

  "연봉 얼마예요?"        → **쿼리가 있다.** 그 얘기를 하는 구간을 먼저 찾고(검색) 거기서 고른다
  "핵심만 30초로 잘라줘"  → **쿼리가 없다.** 찾을 대상이 없으니 전 구간을 놓고 고른다(기존 rank)

🔴 두 경로가 반드시 공존한다. 뒤쪽이 곧 기존 크리에이터 경로이고, 그걸 없애면 "기준을 바꿔
다시 뽑기" 가 사라진다.

규칙 기반(의문사·길이)으로도 대부분 갈리지만 "핵심만" 과 "핵심 논지가 뭐예요?" 를 못 가른다.
그래서 LLM 1회로 판정한다 — 수십 토큰이라 비용은 무시할 수준이다(tease §13 결정).
규칙의 판정도 함께 기록해서 나중에 "규칙만으로 충분했나" 를 데이터로 답할 수 있게 한다.
"""

import json
import re

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import gemini

RETRIEVAL = "retrieval"
RANK = "rank"

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "route": {"type": "STRING", "enum": [RETRIEVAL, RANK]},
        "reason": {"type": "STRING"},
    },
    "required": ["route", "reason"],
}

PROMPT = """아래 문장이 **무엇에 대한 요청**인지 판정하라.

- `retrieval`: 영상 안에서 **특정 내용을 찾아 달라**는 요청. 답이 영상의 어느 부분에 있는지가 정해져 있다.
  예) "연봉은 얼마인가요?", "면접 절차가 궁금해요", "재택근무 되나요?"
- `rank`: 찾을 대상이 없고 **전체에서 좋은 부분을 골라 달라**는 요청.
  예) "핵심만 30초로", "재밌는 부분", "가장 인상적인 장면"

reason 은 한 문장.

문장: {text}"""

# 규칙 기반 판정 — LLM 과 비교만 한다. 판단의 근거로 쓰지 않는다.
# 의문사나 물음표가 있으면 대개 찾아 달라는 요청이다.
_QUESTION_MARKS = re.compile(r"[?？]")
_INTERROGATIVES = ("어떻게", "무엇", "뭐", "왜", "언제", "어디", "누가", "얼마", "몇", "인가요", "나요", "까요")


def rule_of_thumb(text: str) -> str:
    if _QUESTION_MARKS.search(text) or any(word in text for word in _INTERROGATIVES):
        return RETRIEVAL
    return RANK


def parse(raw: str) -> tuple[str, str]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 파싱 실패: {exc}") from exc
    route = str(payload.get("route", "")).strip()
    if route not in (RETRIEVAL, RANK):
        raise ValueError(f"알 수 없는 경로다: {route!r}")
    return route, str(payload.get("reason", "")).strip()


def classify(
    conn: psycopg.Connection, cfg: config.Config, text: str, source_id: int | None = None
) -> str:
    """'retrieval' 또는 'rank'. 판정에 실패하면 **검색 경로로 떨어뜨린다.**

    실패 시 기본값이 검색인 이유: 시청자 질문이 이 경로의 대부분이고, 검색이 빗나가도 rank 가
    뒤에서 한 번 더 거른다. 반대로 rank 로 잘못 보내면 전 구간을 LLM 에 보내 비싸고 느리다.
    """
    raw, usage, latency_ms = gemini.generate_json(cfg, PROMPT.format(text=text), SCHEMA)
    try:
        route, reason = parse(raw)
    except ValueError as exc:
        route, reason = RETRIEVAL, f"판정 실패로 기본값 — {exc}"
    conn.execute(
        """insert into stage_calls (source_id, stage, model, input_tokens, output_tokens,
                                    thinking_tokens, total_tokens, cached_tokens, latency_ms, params)
           values (%s, 'classify', %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            source_id, cfg.gemini_model, usage["input_tokens"], usage["output_tokens"],
            usage["thinking_tokens"], usage["total_tokens"], usage["cached_tokens"], latency_ms,
            # 규칙 판정을 함께 남긴다 — 나중에 `select params->>'rule' = params->>'route'` 로 일치율이 나온다.
            Jsonb({"route": route, "rule": rule_of_thumb(text), "reason": reason}),
        ),
    )
    return route
