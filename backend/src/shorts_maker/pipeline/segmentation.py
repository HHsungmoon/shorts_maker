"""A4 — [3] 주제 단위 구간 분할 (문서 §4-1).

강연에는 카메라 컷이 없어서 샷 검출이 안 먹는다(§1-2 실측). 그래서 영화판의 `[2]+[3]`
자리에 **트랜스크립트 기반 주제 분할**이 들어간다 — 이 파이프라인에서 LLM 이 처음 등장하는 곳.

🔴 이 단계는 **기준 중립**이어야 한다(§3). "인상적인" "명장면 후보" 같은 말을 프롬프트에
넣는 순간 Segment 가 중립 자산이 아니게 되고, §6-2 의 "기준만 바꿔 다시 돌리기"가 깨진다.

🔴 모델은 **발화 인덱스만** 고른다. 초는 utterances 에서 되찾는다(§12).
"""

import json
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import gemini


class SegmentationError(RuntimeError):
    pass


# 응답 형식을 스키마로 강제한다 — 깨진 JSON 이라는 실패 경로를 없앤다.
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "segments": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "start_idx": {"type": "INTEGER"},
                    "end_idx": {"type": "INTEGER"},
                    "summary": {"type": "STRING"},
                },
                "required": ["start_idx", "end_idx", "summary"],
            },
        }
    },
    "required": ["segments"],
}

PROMPT_TEMPLATE = """다음은 한 강연의 일부를 음성 인식한 발화 목록이다.
각 줄은 `[번호] 발화 내용` 형식이다.

이것을 **주제 단위**로 나눠라.

규칙:
- 구간은 논지·화제가 바뀌는 지점에서 나눈다. 문장 단위가 아니다.
- 모든 발화가 정확히 하나의 구간에 속해야 한다. 빠지거나 겹치면 안 된다.
- 첫 구간은 {first_idx}번에서 시작하고, 마지막 구간은 {last_idx}번에서 끝나야 한다.
- summary 는 그 구간에서 **무엇을 말하는지**만 적는다. 한두 문장.
  좋다/인상적이다/중요하다 같은 평가나 인용 가치 판단은 쓰지 마라. 내용만 적는다.

{context_block}발화 목록:
{utterance_block}"""


@dataclass
class SegmentSpec:
    start_idx: int
    end_idx: int
    summary: str


def build_prompt(utterances: list[dict], context: str | None) -> str:
    if not utterances:
        raise SegmentationError("발화가 없다 — 먼저 `sm stt run` 을 돌린다")
    lines = "\n".join(f"[{u['idx']}] {u['text']}" for u in utterances)
    context_block = f"강연 개요: {context.strip()}\n\n" if context and context.strip() else ""
    return PROMPT_TEMPLATE.format(
        first_idx=utterances[0]["idx"],
        last_idx=utterances[-1]["idx"],
        context_block=context_block,
        utterance_block=lines,
    )


def parse_response(raw: str, max_idx: int) -> list[SegmentSpec]:
    """모델 응답을 검증한다. 형식이 아니라 **내용**을 본다(§12).

    통과 조건: 존재하는 인덱스만 쓸 것 · 0 에서 시작해 max_idx 에서 끝날 것 ·
    빈틈도 겹침도 없을 것. 하나라도 어기면 세그먼트를 만들지 않는다 — 조용히 발화를
    잃는 것보다 실패하는 편이 낫다.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SegmentationError(f"JSON 파싱 실패: {exc}") from exc

    items = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise SegmentationError("segments 배열이 비었다")

    specs: list[SegmentSpec] = []
    for i, item in enumerate(items):
        try:
            start, end = int(item["start_idx"]), int(item["end_idx"])
            summary = str(item["summary"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise SegmentationError(f"{i}번째 구간의 형식이 잘못됐다: {item!r}") from exc
        if not summary:
            raise SegmentationError(f"{i}번째 구간의 summary 가 비었다")
        if not (0 <= start <= end <= max_idx):
            raise SegmentationError(
                f"{i}번째 구간의 인덱스가 범위를 벗어났다: {start}~{end} (허용 0~{max_idx})"
            )
        specs.append(SegmentSpec(start, end, summary))

    specs.sort(key=lambda s: s.start_idx)
    if specs[0].start_idx != 0:
        raise SegmentationError(f"0번 발화가 어느 구간에도 없다 (첫 구간이 {specs[0].start_idx} 에서 시작)")
    if specs[-1].end_idx != max_idx:
        raise SegmentationError(f"마지막 발화 {max_idx} 가 어느 구간에도 없다")
    for previous, current in zip(specs, specs[1:]):
        if current.start_idx != previous.end_idx + 1:
            raise SegmentationError(
                f"구간이 이어지지 않는다: {previous.start_idx}~{previous.end_idx} 다음이 "
                f"{current.start_idx} 에서 시작 (기대: {previous.end_idx + 1})"
            )
    return specs


def load_utterances(conn: psycopg.Connection, chunk_id: int) -> list[dict]:
    rows = conn.execute(
        "select idx, start_sec, end_sec, text from utterances where chunk_id = %s order by idx",
        (chunk_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def run_for_chunk(
    conn: psycopg.Connection, cfg: config.Config, chunk_id: int, force: bool
) -> list[SegmentSpec]:
    chunk = conn.execute(
        "select c.*, s.id as source_id, s.context from chunks c join sources s on s.id = c.source_id"
        " where c.id = %s",
        (chunk_id,),
    ).fetchone()
    if chunk is None:
        raise SegmentationError(f"chunk {chunk_id} 없음")

    existing = conn.execute(
        "select count(*) as n from segments where chunk_id = %s", (chunk_id,)
    ).fetchone()["n"]
    if existing and not force:
        raise SegmentationError(f"chunk {chunk_id} 에 이미 구간 {existing}개가 있다 — 다시 하려면 --force")

    utterances = load_utterances(conn, chunk_id)
    prompt = build_prompt(utterances, chunk["context"])
    try:
        raw, usage, latency_ms = gemini.generate_json(cfg, prompt, RESPONSE_SCHEMA)
    except Exception as exc:
        # 🔴 실패한 호출도 남긴다(§12). 안 남기면 "왜 이 시각에 아무 일도 없었나"를 못 푼다.
        conn.execute(
            "insert into stage_calls (source_id, stage, model, error) values (%s, 'segment', %s, %s)",
            (chunk["source_id"], cfg.gemini_model, f"{type(exc).__name__}: {exc}"),
        )
        conn.commit()
        raise

    call = conn.execute(
        """insert into stage_calls
           (source_id, stage, model, input_tokens, output_tokens, thinking_tokens,
            total_tokens, cached_tokens, latency_ms, params)
           values (%s, 'segment', %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
        (
            chunk["source_id"],
            cfg.gemini_model,
            usage["input_tokens"],
            usage["output_tokens"],
            usage["thinking_tokens"],
            usage["total_tokens"],
            usage["cached_tokens"],
            latency_ms,
            Jsonb({"utterances": len(utterances), "attempts": usage.get("attempts")}),
        ),
    )
    call_id = call.fetchone()["id"]
    conn.commit()

    # 호출은 성공했지만 내용이 검증에 걸릴 수 있다. 그 사유를 같은 행에 남겨야
    # 나중에 "왜 이 호출만 세그먼트가 없지"를 추적할 수 있다.
    try:
        specs = parse_response(raw, max_idx=utterances[-1]["idx"])
    except SegmentationError as exc:
        conn.execute("update stage_calls set error = %s where id = %s", (str(exc), call_id))
        conn.commit()
        raise
    by_idx = {u["idx"]: u for u in utterances}

    conn.execute("delete from segments where chunk_id = %s", (chunk_id,))
    for position, spec in enumerate(specs):
        conn.execute(
            """insert into segments
               (chunk_id, idx, start_sec, end_sec, description, describe_model,
                start_utterance_idx, end_utterance_idx)
               values (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                chunk_id,
                position,
                by_idx[spec.start_idx]["start_sec"],
                by_idx[spec.end_idx]["end_sec"],
                spec.summary,
                cfg.gemini_model,
                spec.start_idx,
                spec.end_idx,
            ),
        )
    conn.commit()
    return specs
