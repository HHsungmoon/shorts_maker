"""라우팅과 구간 선택 — 한 번의 LLM 호출로 둘 (tease §5-4).

두 종류의 요청이 들어온다.

  "연봉 얼마예요?"        → **쿼리가 있다.** 그 얘기를 하는 구간을 먼저 찾고 거기서 고른다
  "핵심만 30초로 잘라줘"  → **쿼리가 없다.** 찾을 대상이 없으니 전 구간을 놓고 고른다(기존 rank)

🔴 두 경로가 반드시 공존한다. 뒤쪽이 곧 기존 크리에이터 경로이고, 그걸 없애면 "기준을 바꿔
다시 뽑기" 가 사라진다.

규칙 기반(의문사·길이)으로도 대부분 갈리지만 "핵심만" 과 "핵심 논지가 뭐예요?" 를 못 가른다.
그래서 LLM 로 판정한다. 규칙의 판정도 함께 기록해서 나중에 "규칙만으로 충분했나" 를 데이터로
답할 수 있게 한다.

## 🔴 왜 여기서 구간까지 고르는가 (2026-09-09)

원래 구간 좁히기는 **임베딩 유사도 top-k** 였다. 실측에서 그게 안 듣는다는 것이 드러났다
(update_plan M5 실측): "개발 직군의 주요 기술 스택은?" 질문에서 답이 담긴 구간
(마이크로서비스 아키텍처, MLOps)이 44개 중 **29·30위**였다. 1위는 질문의 "기술" 과 글자가 겹치는
"기술 문화" 였다. 짧은 한국어 요약을 임베딩하면 구체적 기술 내용과 일반적 조직 얘기가 갈리지 않는다.
top_k 를 늘려서 될 일이 아니다 — 29위를 넣으려면 44개 중 30개를 보내야 하고 그건 좁히기를 안 하는 것이다.

그래서 고르는 주체를 임베딩에서 **LLM** 으로 바꿨다. 근거 셋:
  ① 기준(criteria) rank 경로가 이미 그렇게 한다 — 구간 요약 전부를 놓고 LLM 이 고른다. 되는 방식이다
  ② 요약 44줄은 약 2천 토큰이라 싸다. 비싼 건 요약이 아니라 **대사**다(구간당 약 870 토큰)
  ③ **호출 수가 늘지 않는다.** 라우팅이 이미 LLM 을 한 번 쓰는데 질문 문장만 보내고 있었다 —
     같은 호출에 구간 목록을 얹으면 경로 판정과 구간 선택이 한 번에 끝난다(답변당 5회 유지)

부수 효과가 하나 더 있다. "이 영상엔 그 얘기가 없다" 를 이제 **여기서** 안다 — 모델이 빈 배열을
주면 그 뜻이다. 옛 검출기(`SHORTS_RETRIEVAL_MIN_SIM`)는 실측에서 유사도가 0.50~0.755 좁은 띠에
몰려 **한 번도 아무것도 거르지 않았다.**

⚠️ 한 호출에 두 일을 시키면 둘 다 조금 나빠질 수 있다. 붙여 보고 재야 안다 —
`stage_calls.params` 에 route·rule·선택 결과를 다 남긴다.
"""

import json
import re
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import gemini

RETRIEVAL = "retrieval"
RANK = "rank"

# 🔴 다음 단계(`ranking.plan_answer`)에 대사를 넘길 구간 수의 상한.
#
# 실측 기준: 구간 5개의 답변 프롬프트가 입력 4384 토큰이었다(구간당 약 870). 8개면 약 7천 토큰으로
# 여전히 싸다. 더 늘리지 않는 이유는 비용이 아니라 **정밀도**다 — 다음 단계는 번호가 붙은 대사 줄에서
# 시작·끝점을 정확히 골라야 하고, 8개면 약 300줄이다. 44개 전부(1689줄)를 넘기면 검색 실패가
# 정밀도 실패로 바뀐다.
MAX_SEGMENTS = 8

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "route": {"type": "STRING", "enum": [RETRIEVAL, RANK]},
        "segments": {"type": "ARRAY", "items": {"type": "INTEGER"}},
        "reason": {"type": "STRING"},
    },
    "required": ["route", "segments", "reason"],
}

PROMPT = """아래는 한 영상의 **구간 목록**과 시청자가 남긴 **요청**이다. 두 가지를 판정하라.

1. `route` — 이 요청이 무엇인가.
   - `retrieval`: 영상 안에서 **특정 내용을 찾아 달라**는 요청. 답이 영상의 어느 부분에 있는지가 정해져 있다.
     예) "연봉은 얼마인가요?", "면접 절차가 궁금해요", "재택근무 되나요?"
   - `rank`: 찾을 대상이 없고 **전체에서 좋은 부분을 골라 달라**는 요청.
     예) "핵심만 30초로", "재밌는 부분", "가장 인상적인 장면"

2. `segments` — `retrieval` 이면 **이 요청의 답이 담겨 있을 구간 번호**를 관련 높은 순으로 최대 {limit}개.
   - 지금 보는 것은 **요약**이다. 대사를 읽어 실제로 자르는 건 다음 단계다. 그래서 **확실하지 않으면
     넣는 편이 낫다** — 빠뜨리면 그 답은 영원히 못 만들고, 넣어서 아니면 다음 단계가 걸러낸다.
   - 요청의 낱말이 요약에 그대로 없어도 된다. **내용상** 답이 될 만한 구간을 고른다.
     예를 들어 "어떤 기술을 쓰나" 라는 요청에는 특정 아키텍처나 프로젝트 사례를 말하는 구간이 답이다.
   - 이 영상에 그 내용이 **정말 없으면 빈 배열**로 둔다. 억지로 채우지 마라.
   - `rank` 면 빈 배열로 둔다(전 구간을 쓴다).

reason 은 한 문장. 왜 그 경로이고 왜 그 구간인지.

요청: {text}

구간 목록:
{segments}"""

# 규칙 기반 판정 — LLM 과 비교만 한다. 판단의 근거로 쓰지 않는다.
# 의문사나 물음표가 있으면 대개 찾아 달라는 요청이다.
_QUESTION_MARKS = re.compile(r"[?？]")
_INTERROGATIVES = ("어떻게", "무엇", "뭐", "왜", "언제", "어디", "누가", "얼마", "몇", "인가요", "나요", "까요")


def rule_of_thumb(text: str) -> str:
    if _QUESTION_MARKS.search(text) or any(word in text for word in _INTERROGATIVES):
        return RETRIEVAL
    return RANK


@dataclass
class Decision:
    """경로 + 고른 구간 번호.

    🔴 `selected` 의 `None` 과 `[]` 는 **뜻이 다르다.**
      - `None`: 판정을 못 읽었다 → 호출부가 임베딩 top-k 폴백으로 간다
      - `[]`  : 모델이 "이 영상엔 그 내용이 없다" 고 답했다 → 답변 불가로 간다
    둘을 합치면 파싱 실패가 "영상에 없음" 으로 조용히 바뀐다 — 시청자에게 잘못된 안내가 간다.
    """

    route: str
    selected: list[int] | None
    reason: str


def build_prompt(text: str, segments: list[dict]) -> str:
    if not segments:
        raise ValueError("구간이 없다 — 먼저 구간 분할을 돌린다")
    listing = "\n".join(
        f"[{s['idx']}] {(s['description'] or '').strip()}" for s in segments
    )
    return PROMPT.format(text=text, limit=MAX_SEGMENTS, segments=listing)


def parse(raw: str, allowed: set[int]) -> Decision:
    """🔴 구간 번호는 **화이트리스트**로 검증한다(규약: LLM 출력은 항상 검증한다).

    목록에 없는 번호는 버린다. 다만 **하나도 안 남으면 파싱 실패로 올린다** — 그대로 빈 배열로
    두면 "영상에 없음" 과 구분되지 않아, 모델이 엉뚱한 번호를 댄 것이 잘못된 안내로 바뀐다.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 파싱 실패: {exc}") from exc
    route = str(payload.get("route", "")).strip()
    if route not in (RETRIEVAL, RANK):
        raise ValueError(f"알 수 없는 경로다: {route!r}")
    reason = str(payload.get("reason", "")).strip()

    given = payload.get("segments") or []
    if not isinstance(given, list):
        raise ValueError(f"segments 가 배열이 아니다: {given!r}")
    kept: list[int] = []
    for value in given:
        try:
            idx = int(value)
        except (TypeError, ValueError):
            continue
        if idx in allowed and idx not in kept:
            kept.append(idx)
    if given and not kept:
        raise ValueError(f"가리킨 구간 번호가 목록에 하나도 없다: {given!r}")
    # 관련 높은 순으로 왔다고 보고 앞에서 자른다. 시간 순 정렬은 호출부가 한다 —
    # 여기서 정렬하면 "관련 높은 순" 정보가 사라져 무엇을 자를지 알 수 없다.
    return Decision(route, kept[:MAX_SEGMENTS], reason)


def decide(
    conn: psycopg.Connection,
    cfg: config.Config,
    text: str,
    segments: list[dict],
    source_id: int | None = None,
) -> Decision:
    """경로를 정하고, 검색 경로면 답이 있을 구간까지 고른다. **LLM 1회.**

    판정에 실패하면 **검색 경로로 떨어뜨리고 `selected=None`** 을 준다.

    실패 시 기본값이 검색인 이유: 시청자 질문이 이 경로의 대부분이고, 검색이 빗나가도 다음 단계가
    한 번 더 거른다. 반대로 rank 로 잘못 보내면 전 구간을 LLM 에 보내 비싸고 느리다.
    """
    allowed = {s["idx"] for s in segments}
    prompt = build_prompt(text, segments)
    raw, usage, latency_ms = gemini.generate_json(cfg, prompt, SCHEMA)
    try:
        decision = parse(raw, allowed)
    except ValueError as exc:
        decision = Decision(RETRIEVAL, None, f"판정 실패로 기본값 — {exc}")
    conn.execute(
        """insert into stage_calls (source_id, stage, model, input_tokens, output_tokens,
                                    thinking_tokens, total_tokens, cached_tokens, latency_ms,
                                    prompt, response, params)
           values (%s, 'classify', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            source_id, cfg.gemini_model, usage["input_tokens"], usage["output_tokens"],
            usage["thinking_tokens"], usage["total_tokens"], usage["cached_tokens"], latency_ms,
            # 🔴 모델이 무엇을 보고 무엇을 답했는지. 이게 없으면 판정이 이상할 때 같은 호출을
            # 다시 하는 것 말고 확인할 방법이 없다(마이그레이션 005).
            prompt if cfg.store_prompts else None,
            raw if cfg.store_prompts else None,
            # 규칙 판정을 함께 남긴다 — 나중에 `select params->>'rule' = params->>'route'` 로 일치율이 나온다.
            # `selected` 도 남긴다: 한 호출에 두 일을 시킨 것이 판정을 망치는지 보려면 이 값이 필요하다.
            Jsonb({
                "route": decision.route, "rule": rule_of_thumb(text), "reason": decision.reason,
                "selected": decision.selected, "offered": len(segments),
            }),
        ),
    )
    return decision
