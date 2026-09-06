"""A5 — [5] rank (문서 §4-[5], §6).

프롬프트는 §6-1 대로 [고정] + [가변] 으로 조립한다.
  [고정] 자립성 관문 · 출력 형식   ← 여기서 튜닝된다. 그래서 실제로 보낸 전문을 runs.prompt 에 남긴다
  [가변] criteria_prompt          ← Run 마다 바뀌는 유일한 부분

🔴 자립성은 **감점이 아니라 후보 제외**다(§1 EchoCut 교훈 4). 감점으로 두면 모델이 실제로
안 뺀다. 제외 목록은 `runs.ranked` JSON 안에만 남는다 — 예전엔 `segments.excluded_by='auto'`
에도 썼는데, 그건 **run 의 판정을 중립 자산에 영구 기록**하는 것이라 뺐다(2026-09-06, M0).
기준을 바꿔 다시 돌린 run 이 다른 판정을 내도 segments 에는 첫 판정이 그대로 남았다.
`excluded_by` 컬럼은 사람이 손으로 빼는 'human' 용도로 남겨둔다.
"""

import json
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import gemini


class RankingError(RuntimeError):
    pass


RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "excluded": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "idx": {"type": "INTEGER"},
                    "reason": {"type": "STRING"},
                },
                "required": ["idx", "reason"],
            },
        },
        "ranked": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "idx": {"type": "INTEGER"},
                    "score": {"type": "INTEGER"},
                    "reason": {"type": "STRING"},
                },
                "required": ["idx", "score", "reason"],
            },
        },
    },
    "required": ["excluded", "ranked"],
}

FIXED_PROMPT = """다음은 한 강연을 주제 단위로 나눈 구간 목록이다.
이 중에서 **숏폼 클립으로 잘라낼 구간**을 고른다.

1단계 — 자립성 관문 (반드시 먼저)
앞부분을 듣지 않은 사람이 이 구간만 보고도 이해할 수 있어야 한다.
앞의 논의를 전제해야만 말이 되는 구간은 **점수를 깎지 말고 excluded 에 넣어라.**
지시대명사가 앞을 가리키거나("그것은", "아까 말한"), 앞에서 세운 전제 없이는
결론만 남는 구간이 여기 해당한다.

2단계 — 남은 구간에 점수
100점 절대 기준으로 매긴다. 다른 구간과 비교한 상대 순위가 아니다.
reason 은 왜 그 점수인지 한두 문장.

규칙:
- 모든 구간이 excluded 나 ranked 중 정확히 한 곳에 들어가야 한다. 빠뜨리지 마라.
- idx 는 아래 목록에 있는 번호만 쓴다.
{criteria_block}
{context_block}구간 목록:
{segment_block}"""

CRITERIA_HEADER = "\n무엇을 좋게 볼 것인가:\n"


def build_prompt(segments: list[dict], context: str | None, criteria: str | None) -> str:
    if not segments:
        raise RankingError("구간이 없다 — 먼저 `sm segment run` 을 돌린다")
    lines = "\n".join(
        f"[{s['idx']}] ({s['end_sec'] - s['start_sec']:.0f}초) {s['description']}" for s in segments
    )
    # 🔴 §6-3: 기준이 비면 그 줄 자체를 뺀다. 빈 지시문을 남기면 모델이 그걸 해석한다.
    criteria_block = f"{CRITERIA_HEADER}{criteria.strip()}\n" if criteria and criteria.strip() else ""
    context_block = f"강연 개요: {context.strip()}\n\n" if context and context.strip() else ""
    return FIXED_PROMPT.format(
        criteria_block=criteria_block, context_block=context_block, segment_block=lines
    )


@dataclass
class Ranked:
    idx: int
    score: int
    reason: str


@dataclass
class Excluded:
    idx: int
    reason: str


def parse_response(raw: str, valid_idx: set[int]) -> tuple[list[Ranked], list[Excluded]]:
    """§12 검증: 존재하는 구간만 · 중복 없이 · 전부 한 번씩 · 점수는 0~100."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RankingError(f"JSON 파싱 실패: {exc}") from exc
    if not isinstance(payload, dict):
        raise RankingError("응답이 객체가 아니다")

    excluded: list[Excluded] = []
    for item in payload.get("excluded") or []:
        try:
            excluded.append(Excluded(int(item["idx"]), str(item["reason"]).strip()))
        except (KeyError, TypeError, ValueError) as exc:
            raise RankingError(f"excluded 항목 형식이 잘못됐다: {item!r}") from exc

    ranked: list[Ranked] = []
    for item in payload.get("ranked") or []:
        try:
            entry = Ranked(int(item["idx"]), int(item["score"]), str(item["reason"]).strip())
        except (KeyError, TypeError, ValueError) as exc:
            raise RankingError(f"ranked 항목 형식이 잘못됐다: {item!r}") from exc
        if not 0 <= entry.score <= 100:
            raise RankingError(f"[{entry.idx}] 점수가 범위를 벗어났다: {entry.score}")
        ranked.append(entry)

    if not ranked:
        raise RankingError("ranked 가 비었다 — 모든 구간이 제외되면 뽑을 게 없다")

    seen = [e.idx for e in excluded] + [r.idx for r in ranked]
    unknown = sorted(set(seen) - valid_idx)
    if unknown:
        raise RankingError(f"존재하지 않는 구간을 지목했다: {unknown}")
    duplicated = sorted({i for i in seen if seen.count(i) > 1})
    if duplicated:
        raise RankingError(f"같은 구간이 두 번 나온다: {duplicated}")
    missing = sorted(valid_idx - set(seen))
    if missing:
        # 빠뜨린 구간은 조용히 후보에서 사라진다 — segmentation 의 빈틈과 같은 실패다.
        raise RankingError(f"어느 목록에도 없는 구간이 있다: {missing}")

    ranked.sort(key=lambda r: -r.score)
    return ranked, excluded


def load_segments(conn: psycopg.Connection, source_id: int) -> list[dict]:
    rows = conn.execute(
        """select sg.* from segments sg
           join chunks c on c.id = sg.chunk_id
           where c.source_id = %s order by sg.idx""",
        (source_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def run_for_source(
    conn: psycopg.Connection, cfg: config.Config, source_id: int, criteria: str | None
) -> int:
    source = conn.execute("select * from sources where id = %s", (source_id,)).fetchone()
    if source is None:
        raise RankingError(f"source {source_id} 없음")

    segments = load_segments(conn, source_id)
    prompt = build_prompt(segments, source["context"], criteria)

    run = conn.execute(
        """insert into runs (source_id, criteria_prompt, prompt, status)
           values (%s, %s, %s, 'RUNNING') returning id""",
        (source_id, (criteria or "").strip() or None, prompt),
    )
    run_id = run.fetchone()["id"]
    conn.commit()

    try:
        raw, usage, latency_ms = gemini.generate_json(cfg, prompt, RESPONSE_SCHEMA)
    except Exception as exc:
        conn.execute(
            "insert into stage_calls (source_id, run_id, stage, model, error) values (%s, %s, 'rank', %s, %s)",
            (source_id, run_id, cfg.gemini_model, f"{type(exc).__name__}: {exc}"),
        )
        conn.execute("update runs set status = 'FAILED', error = %s where id = %s", (str(exc), run_id))
        conn.commit()
        raise

    conn.execute(
        """insert into stage_calls
           (source_id, run_id, stage, model, input_tokens, output_tokens, thinking_tokens,
            total_tokens, cached_tokens, latency_ms)
           values (%s, %s, 'rank', %s, %s, %s, %s, %s, %s, %s)""",
        (
            source_id, run_id, cfg.gemini_model,
            usage["input_tokens"], usage["output_tokens"], usage["thinking_tokens"],
            usage["total_tokens"], usage["cached_tokens"], latency_ms,
        ),
    )
    conn.commit()

    try:
        ranked, excluded = parse_response(raw, {s["idx"] for s in segments})
    except RankingError as exc:
        conn.execute("update runs set status = 'FAILED', error = %s where id = %s", (str(exc), run_id))
        conn.commit()
        raise

    conn.execute(
        "update runs set status = 'DONE', ranked = %s, updated_at = now() where id = %s",
        (
            # ranked 는 jsonb. raw 는 모델이 준 **문자열**이라 여기서 한 번 파싱해 넣는다 — 원문 구조를 그대로 보존한다.
            Jsonb(
                {
                    "raw": json.loads(raw),
                    "ranked": [vars(r) for r in ranked],
                    "excluded": [vars(e) for e in excluded],
                }
            ),
            run_id,
        ),
    )
    conn.commit()
    return run_id


# ==================================================================== 답하기 경로
#
# 기존 rank(위)는 "이 영상에서 좋은 구간을 골라" 다. 아래는 "이 **질문**에 답하는 클립을 어떻게
# 자를지 제안해" 다. 둘은 프롬프트도 출력도 달라서 함수를 나눴다 — 하나로 합치면 분기가 프롬프트
# 안까지 들어가고, 그러면 기존 경로가 조용히 망가진다(tease §5-4: 두 경로는 반드시 공존한다).
#
# 🔴 후보를 **한 번에 3개** 받는다. 질문 하나에 답하는 방식이 여럿이기 때문이다 —
# 직접 답하는 한 덩어리 / 흩어진 답을 이어붙인 조합 / 핵심만 짧게. 어느 게 나은지는 잘라놓은
# **대사를 읽어봐야** 알고, 그건 judge 가 한다(answers/judge.py). 셋을 미리 받아두면 판정을
# 병렬로 돌려 체감 시간이 한 번과 같고, 고를 것이 실제로 여러 개다.

ANSWER_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "answerable": {"type": "BOOLEAN"},
        "reason": {"type": "STRING"},
        "candidates": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "label": {"type": "STRING", "enum": ["single", "combo", "tight"]},
                    "reason": {"type": "STRING"},
                    "parts": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "start_line": {"type": "INTEGER"},
                                "end_line": {"type": "INTEGER"},
                            },
                            "required": ["start_line", "end_line"],
                        },
                    },
                },
                "required": ["label", "reason", "parts"],
            },
        },
    },
    "required": ["answerable", "reason", "candidates"],
}

ANSWER_PROMPT = """아래는 한 영상에서 **이 질문과 관련 있을 만한 부분**의 대사다.
각 줄은 `[번호] (길이초) 발화` 형식이고, 번호는 전체에서 유일하다.

질문: {question}

이 질문에 답하는 **{budget:.0f}초 이내의 숏폼 클립**을 어떻게 자를지 제안하라.

먼저 판정한다:
- 이 대사 안에 질문의 답이 **실제로 있는가**. 주제만 스치고 답이 없으면 `answerable: false` 로 하고
  reason 에 왜 없는지 적는다. 없는 답을 억지로 만들지 마라.

답이 있으면 **서로 다른 3가지 방식**으로 후보를 낸다:
- `single`: 답을 가장 직접적으로 말하는 **연속된 한 덩어리**
- `combo`: 답이 여러 곳에 흩어져 있으면 **2~3개 조각을 시간 순서대로** 이어붙인 것.
  조각 사이에는 화면에 "몇 분에서 이어집니다" 안내가 뜨므로 점프 자체는 괜찮다.
  흩어져 있지 않으면 single 과 같은 범위를 다시 내도 된다.
- `tight`: 군더더기를 걷어낸 **가장 짧은** 컷. 핵심 문장만.

규칙:
- 각 조각은 `start_line`~`end_line` 이고, **같은 구간 안**이어야 한다(구간 머리글로 나뉘어 있다).
- 앞을 듣지 않은 사람이 이해할 수 있는 지점에서 시작한다. 지시대명사로 시작하지 않는다.
- 말이 도중에 끊기지 않게 문장이 완결되는 지점에서 끝낸다.
- 조각의 길이 합이 {budget:.0f}초를 넘지 않게 한다.
- reason 은 왜 이렇게 잘랐는지 한 문장.

{context_block}대사:
{body}"""


@dataclass
class Part:
    start_line: int
    end_line: int


@dataclass
class Candidate:
    label: str
    reason: str
    parts: list[Part]


@dataclass
class AnswerPlan:
    answerable: bool
    reason: str
    candidates: list[Candidate]


def build_answer_prompt(
    question: str, lines: list[dict], budget_sec: float, context: str | None
) -> str:
    """`lines` 는 `number_lines()` 가 만든 것 — 구간 머리글과 연속 번호가 붙어 있다."""
    if not lines:
        raise RankingError("후보 대사가 없다 — 먼저 전사와 구간 분할을 돌린다")
    body: list[str] = []
    current_segment = None
    for line in lines:
        if line["segment_id"] != current_segment:
            current_segment = line["segment_id"]
            body.append(f"\n[구간 {line['segment_idx']}] {line['description'] or ''}".rstrip())
        body.append(f"[{line['line']}] ({line['end_sec'] - line['start_sec']:.0f}초) {line['text']}")
    context_block = f"영상 개요: {context.strip()}\n\n" if context and context.strip() else ""
    return ANSWER_PROMPT.format(
        question=question, budget=budget_sec, context_block=context_block, body="\n".join(body)
    )


def number_lines(conn, segments: list[dict]) -> list[dict]:
    """후보 구간들의 발화를 **연속 번호**로 늘어놓는다.

    🔴 `utterances.idx` 는 청크 안에서 0부터라 청크가 여럿이면 같은 번호가 둘이 된다. 프롬프트에
    그대로 쓰면 모델이 지목한 번호가 어느 것인지 알 수 없다. 그래서 프롬프트 전용 번호를 새로 매기고
    코드가 매핑을 들고 있는다 — 모델은 번호만 고르면 되고, 초는 우리가 되찾는다(§12).
    """
    lines: list[dict] = []
    for segment in segments:
        rows = conn.execute(
            """select idx, start_sec, end_sec, text from utterances
               where chunk_id = %s and idx between %s and %s order by idx""",
            (segment["chunk_id"], segment["start_utterance_idx"], segment["end_utterance_idx"]),
        ).fetchall()
        for row in rows:
            lines.append({
                "line": len(lines),
                "segment_id": segment["id"],
                "segment_idx": segment["idx"],
                "description": segment.get("description"),
                "chunk_id": segment["chunk_id"],
                "utterance_idx": row["idx"],
                "start_sec": float(row["start_sec"]),
                "end_sec": float(row["end_sec"]),
                "text": row["text"],
            })
    return lines


def parse_answer(raw: str, lines: list[dict]) -> AnswerPlan:
    """§12 검증: 존재하는 번호만 · 같은 구간 안 · 순서대로 · 겹치지 않게.

    검증이 프롬프트가 아니라 **코드**에 있는 이유는 규약이다 — 모델에게 "하지 마라" 고 지시하는
    대신 틀린 답을 받지 않는다.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RankingError(f"JSON 파싱 실패: {exc}") from exc
    if not isinstance(payload, dict):
        raise RankingError("응답이 객체가 아니다")

    reason = str(payload.get("reason", "")).strip()
    if not payload.get("answerable"):
        return AnswerPlan(False, reason, [])

    by_line = {line["line"]: line for line in lines}
    candidates: list[Candidate] = []
    for item in payload.get("candidates") or []:
        try:
            label = str(item["label"]).strip()
            raw_parts = item["parts"]
        except (KeyError, TypeError) as exc:
            raise RankingError(f"후보 형식이 잘못됐다: {item!r}") from exc
        if not isinstance(raw_parts, list) or not 1 <= len(raw_parts) <= 3:
            raise RankingError(f"조각은 1~3개여야 한다: {item!r}")

        parts: list[Part] = []
        for entry in raw_parts:
            try:
                part = Part(int(entry["start_line"]), int(entry["end_line"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise RankingError(f"조각 형식이 잘못됐다: {entry!r}") from exc
            if part.start_line not in by_line or part.end_line not in by_line:
                raise RankingError(
                    f"없는 번호를 지목했다: {part.start_line}~{part.end_line}"
                    f" (허용 0~{len(lines) - 1})"
                )
            if part.start_line > part.end_line:
                raise RankingError(f"시작이 끝보다 크다: {part.start_line} > {part.end_line}")
            # 🔴 한 조각은 시간상 연속이어야 한다. 구간을 넘나들면 조각 **안에서** 점프가 생기고,
            # 그 점프는 브릿지 카드 없이 붙어 시청자에게는 그냥 말이 튀는 것으로 보인다.
            if by_line[part.start_line]["segment_id"] != by_line[part.end_line]["segment_id"]:
                raise RankingError(f"한 조각이 여러 구간에 걸쳐 있다: {part.start_line}~{part.end_line}")
            parts.append(part)

        ordered = sorted(parts, key=lambda p: p.start_line)
        for before, after in zip(ordered, ordered[1:]):
            if after.start_line <= before.end_line:
                raise RankingError(f"조각이 겹친다: {before.end_line} 과 {after.start_line}")
        candidates.append(Candidate(label, str(item.get("reason", "")).strip(), ordered))

    if not candidates:
        raise RankingError("answerable 인데 후보가 없다")
    return AnswerPlan(True, reason, candidates)


def plan_answer(
    conn, cfg: config.Config, question: str, segments: list[dict], context: str | None,
    source_id: int | None = None, run_id: int | None = None,
) -> tuple[AnswerPlan, list[dict]]:
    """질문에 답하는 클립 후보 3개를 받는다. (계획, 번호 매긴 대사)."""
    lines = number_lines(conn, segments)
    prompt = build_answer_prompt(question, lines, cfg.teaser_max_sec, context)
    raw, usage, latency_ms = gemini.generate_json(cfg, prompt, ANSWER_SCHEMA)
    conn.execute(
        """insert into stage_calls
           (source_id, run_id, stage, model, input_tokens, output_tokens, thinking_tokens,
            total_tokens, cached_tokens, latency_ms)
           values (%s, %s, 'rank', %s, %s, %s, %s, %s, %s, %s)""",
        (
            source_id, run_id, cfg.gemini_model, usage["input_tokens"], usage["output_tokens"],
            usage["thinking_tokens"], usage["total_tokens"], usage["cached_tokens"], latency_ms,
        ),
    )
    return parse_answer(raw, lines), lines
