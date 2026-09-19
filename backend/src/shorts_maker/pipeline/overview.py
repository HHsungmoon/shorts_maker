"""영상 개요 초안 — 구간 요약을 한두 문장으로 (프롬프트 3층).

`sources.context` 는 구간 분할·순위·자르기·후보 생성 **모든 호출에 붙는** 한두 문장이다. 사람이
직접 쓰면 되지만, 2시간짜리 설명회를 처음 받은 사람이 "이게 무엇에 대한 영상인가" 를 쓰려면
영상을 먼저 봐야 한다 — 그 답은 이미 `segments.description` 에 수십 줄로 쌓여 있다. LLM 1회로
줄여 **초안**을 준다.

## 🔴 구간 분할 **뒤**에 쓴다 (2026-09-19, 순환을 이쪽으로 끊었다)

개요는 구간 분할 프롬프트에도 들어간다(`segmentation.build_prompt` 의 `강연 개요:`). 그러니
분할 결과로 개요를 지으면 **그 분할은 개요 없이 된 것**이다. 순환이고, 끊는 자리는 둘이었다.

  ① 전사 직후 — 재료가 도입부 발화뿐이다. 2시간짜리의 앞 5분은 인사와 안내라 후반 주제를
     통째로 놓친다. 분할에는 반영되지만 그 개요가 틀리면 분할이 더 나빠진다
  ② **분할 직후** — 재료가 영상 전체다. 분할에는 못 쓴다

②를 골랐다. 개요가 실제로 값을 하는 곳은 순위·자르기·답하기이고 거기엔 저장 즉시 반영된다.
분할까지 고치고 싶으면 개요를 저장하고 **다시 나누면** 된다 — 전사는 그대로 재사용되므로
다시 나누기는 몇 분이고, 개요를 잘못 짚어 전사부터 다시 하는 것보다 싸다.

## 🔴 저장하지 않는다

초안을 돌려주고 멈춘다. 이 글은 이후 모든 호출에 붙어서 틀린 한 줄이 조용히 파이프라인 전체에
퍼진다 — 사람이 읽고 누르는 관문이 있어야 한다. 후보를 저장하고 멈추는 [답하기]와 같은 이유다
(`answers/answer.py`).

## 🔴 관리자 기준(2층)을 넣지 않는다

개요는 "이 영상이 무엇인가" 라는 **사실**이다. 취향이 섞이면 그 취향이 3층으로 위장해 이후 모든
호출에 따라붙고, 그때는 어느 층에서 왔는지 아무도 모른다. `tests/test_standards.py` 가 지킨다.
"""

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import gemini

# `sources.context` 의 상한과 같다(http/studio.py SourcePatchIn). 여기서 더 길게 만들면
# 초안이 화면에 들어가자마자 저장이 막힌다.
MAX_CHARS = 500

# 🔴 요약을 **고르게 솎는다.** 2시간짜리는 구간이 200개를 넘는데(21분에 44개였다), 앞에서부터
# 자르면 후반 주제를 통째로 잃는다 — 그러면 "무엇에 대한 영상인가" 가 틀린다. 개수를 줄이는
# 것은 비용보다 희석 때문이다: 요약 200줄을 주면 모델이 한두 문장에 담을 것을 고르지 못한다.
MAX_SEGMENTS = 80

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"context": {"type": "STRING"}},
    "required": ["context"],
}

PROMPT = """아래는 영상 하나를 주제 단위로 나눈 구간 요약이다. 이 영상이 무엇인지 한두 문장으로 적어라.

규칙:
- 요약에 적힌 것만 쓴다. 없는 내용을 추측해서 채우지 않는다.
- 무엇을 다루는 자리이고 누가 말하는지가 드러나게 쓴다.
- 사실만 쓴다. 좋다·유익하다 같은 평가는 쓰지 않는다.
- 200자 안으로 쓴다. 문장이 아니라 명사구로 끝나도 된다.

좋은 예) 쏘카 개발·프로덕트·데이터 직군 채용설명회. CTO 발표와 본부장 패널 토크.

제목: {title}

구간 요약:
{lines}"""


class OverviewError(RuntimeError):
    pass


def pick(descriptions: list[str], limit: int = MAX_SEGMENTS) -> list[str]:
    """요약이 너무 많으면 처음과 끝을 지키면서 고르게 솎는다."""
    usable = [d.strip() for d in descriptions if d and d.strip()]
    if len(usable) <= limit:
        return usable
    # 마지막 칸이 반드시 마지막 요약이 되도록 간격을 (n-1)/(limit-1) 로 잡는다.
    step = (len(usable) - 1) / (limit - 1)
    return [usable[round(i * step)] for i in range(limit)]


def build_prompt(title: str, descriptions: list[str]) -> str:
    lines = "\n".join(f"- {d}" for d in pick(descriptions))
    return PROMPT.format(title=title.strip() or "(제목 없음)", lines=lines)


def parse_response(raw: str) -> str:
    """초안 한 덩어리를 꺼내 다듬는다.

    🔴 길이를 **자를 때 문장 끝을 먼저 찾는다.** 프롬프트가 200자를 요구하므로 여기 걸리는 일은
    드물지만, 걸렸을 때 낱말 한가운데서 끊긴 초안은 사람이 고치기보다 지우고 다시 쓰게 만든다.
    """
    import json

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OverviewError("개요 응답을 읽지 못했다") from exc
    text = str(data.get("context") or "").strip()
    if not text:
        raise OverviewError("개요가 비어 있다")
    if len(text) <= MAX_CHARS:
        return text
    cut = text[:MAX_CHARS]
    end = max(cut.rfind("다."), cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return cut[: end + 1].strip() if end > MAX_CHARS // 2 else cut.strip()


def draft(conn: psycopg.Connection, cfg: config.Config, source_id: int) -> str:
    """구간 요약을 읽어 개요 초안을 만든다. **DB 에 쓰지 않는다.**"""
    source = conn.execute(
        "select id, title from sources where id = %s", (source_id,)
    ).fetchone()
    if source is None:
        raise OverviewError(f"source {source_id} 없음")

    descriptions = [
        row["description"]
        for row in conn.execute(
            """select sg.description from segments sg join chunks ch on ch.id = sg.chunk_id
               where ch.source_id = %s and sg.description is not null order by sg.idx""",
            (source_id,),
        )
    ]
    if not descriptions:
        # 🔴 요약이 없으면 부르지 않는다. 제목만으로 쓴 개요는 그럴듯하되 근거가 없고,
        # 그게 이후 모든 호출에 붙는다.
        raise OverviewError("구간 요약이 아직 없습니다 — 주제 분할을 먼저 끝내 주세요")

    prompt = build_prompt(source["title"], descriptions)
    try:
        raw, usage, latency_ms = gemini.generate_json(cfg, prompt, RESPONSE_SCHEMA)
    except Exception as exc:
        # 🔴 실패한 호출도 남긴다(규약: 모든 단계). 안 남기면 "왜 이 시각에 아무 일도 없었나" 를 못 푼다.
        conn.execute(
            "insert into stage_calls (source_id, stage, model, error) values (%s, 'overview', %s, %s)",
            (source_id, cfg.gemini_model, f"{type(exc).__name__}: {exc}"),
        )
        conn.commit()
        raise

    call_id = conn.execute(
        """insert into stage_calls
           (source_id, stage, model, input_tokens, output_tokens, thinking_tokens,
            total_tokens, cached_tokens, latency_ms, prompt, response, params)
           values (%s, 'overview', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
        (
            source_id,
            cfg.gemini_model,
            usage["input_tokens"],
            usage["output_tokens"],
            usage["thinking_tokens"],
            usage["total_tokens"],
            usage["cached_tokens"],
            latency_ms,
            prompt if cfg.store_prompts else None,
            raw if cfg.store_prompts else None,
            # 몇 줄을 주고 몇 줄이 있었는지. 솎은 뒤 품질이 달라지면 여기서 비교한다.
            Jsonb({"segments": len(descriptions), "sent": len(pick(descriptions))}),
        ),
    ).fetchone()["id"]
    conn.commit()

    try:
        return parse_response(raw)
    except OverviewError as exc:
        conn.execute("update stage_calls set error = %s where id = %s", (str(exc), call_id))
        conn.commit()
        raise
