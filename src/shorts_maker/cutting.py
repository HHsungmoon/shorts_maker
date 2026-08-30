"""A6 — [6] 구간 잘라내기 (문서 §4-[6]).

구간이 통째로 쓰기엔 길어서 안에서 30~60초를 고른다.

🔴 모델은 **발화 인덱스만** 고른다. 초는 utterances 에서 되찾는다(§12). 그래서 말 중간에서
잘리는 일이 구조적으로 없다 — 발화 경계가 곧 컷 지점이다.
"""

import json
import sqlite3
from dataclasses import dataclass

from . import config, gemini

TARGET_MIN_SEC = 30.0
TARGET_MAX_SEC = 60.0


class CuttingError(RuntimeError):
    pass


RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "start_idx": {"type": "INTEGER"},
        "end_idx": {"type": "INTEGER"},
        "reason": {"type": "STRING"},
    },
    "required": ["start_idx", "end_idx", "reason"],
}

PROMPT_TEMPLATE = """다음은 한 강연 구간의 발화 목록이다. 각 줄은 `[번호] (길이초) 발화` 형식이다.

이 안에서 **{min_sec:.0f}~{max_sec:.0f}초 분량의 연속 구간**을 하나 골라라.
숏폼 클립으로 그대로 쓸 부분이다.

규칙:
- start_idx 와 end_idx 는 아래 목록에 있는 번호여야 하고, start_idx <= end_idx 다.
- 고른 구간의 길이 합이 {min_sec:.0f}~{max_sec:.0f}초에 들어가게 맞춰라.
- 말이 도중에 시작하거나 끝나지 않게, 문장이 완결되는 지점을 고른다.
- 앞을 듣지 않아도 이해되는 지점에서 시작한다.
- reason 은 왜 이 구간인지 한두 문장.

{context_block}구간 요약: {description}

발화 목록:
{utterance_block}"""


@dataclass
class Cut:
    start_idx: int
    end_idx: int
    reason: str


def build_prompt(utterances: list[dict], description: str, context: str | None) -> str:
    if not utterances:
        raise CuttingError("발화가 없다")
    lines = "\n".join(
        f"[{u['idx']}] ({u['end_sec'] - u['start_sec']:.0f}초) {u['text']}" for u in utterances
    )
    context_block = f"강연 개요: {context.strip()}\n\n" if context and context.strip() else ""
    return PROMPT_TEMPLATE.format(
        min_sec=TARGET_MIN_SEC,
        max_sec=TARGET_MAX_SEC,
        context_block=context_block,
        description=description,
        utterance_block=lines,
    )


def parse_response(raw: str, valid_idx: set[int]) -> Cut:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CuttingError(f"JSON 파싱 실패: {exc}") from exc
    try:
        cut = Cut(int(payload["start_idx"]), int(payload["end_idx"]), str(payload["reason"]).strip())
    except (KeyError, TypeError, ValueError) as exc:
        raise CuttingError(f"응답 형식이 잘못됐다: {payload!r}") from exc
    if cut.start_idx not in valid_idx or cut.end_idx not in valid_idx:
        raise CuttingError(
            f"구간 밖의 발화를 지목했다: {cut.start_idx}~{cut.end_idx} "
            f"(허용 {min(valid_idx)}~{max(valid_idx)})"
        )
    if cut.start_idx > cut.end_idx:
        raise CuttingError(f"start_idx 가 end_idx 보다 크다: {cut.start_idx} > {cut.end_idx}")
    return cut


def load_segment_utterances(conn: sqlite3.Connection, segment: sqlite3.Row) -> list[dict]:
    rows = conn.execute(
        """select idx, start_sec, end_sec, text from utterances
           where chunk_id = ? and idx between ? and ? order by idx""",
        (segment["chunk_id"], segment["start_utterance_idx"], segment["end_utterance_idx"]),
    ).fetchall()
    return [dict(r) for r in rows]


def existing_clip(conn: sqlite3.Connection, run_id: int, segment_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "select * from clips where run_id = ? and segment_id = ?", (run_id, segment_id)
    ).fetchone()


def create_clip(
    conn: sqlite3.Connection, run_id: int, segment: sqlite3.Row, utterances: list[dict], cut: Cut,
    score: float | None, replace: bool = False,
) -> int:
    by_idx = {u["idx"]: u for u in utterances}
    start_sec = by_idx[cut.start_idx]["start_sec"]
    end_sec = by_idx[cut.end_idx]["end_sec"]

    previous = existing_clip(conn, run_id, segment["id"])
    if previous is not None:
        if not replace:
            # 🔴 조용히 하나 더 만들면 어느 게 최신인지 알 수 없게 된다. DB 유니크 인덱스가
            # 막고 있지만, 여기서 먼저 사람이 읽을 수 있는 이유로 돌려준다.
            raise CuttingError(
                f"이 구간으로 만든 클립 {previous['id']} 이 이미 있다 — 다시 만들려면 replace 를 준다"
            )
        # 파일은 남겨둔다. 렌더 결과를 지우는 건 렌더 단계의 일이고, 여기서 지우면
        # 실패 시 아무것도 남지 않는다.
        conn.execute("delete from clips where id = ?", (previous["id"],))

    cursor = conn.execute(
        """insert into clips (run_id, segment_id, start_sec, end_sec, score, reason)
           values (?, ?, ?, ?, ?, ?)""",
        (run_id, segment["id"], start_sec, end_sec, score, cut.reason),
    )
    conn.commit()
    return cursor.lastrowid


def run_for_segment(
    conn: sqlite3.Connection, cfg: config.Config, run_id: int, segment_id: int,
    score: float | None, manual: tuple[int, int] | None = None, replace: bool = False,
) -> int:
    segment = conn.execute("select * from segments where id = ?", (segment_id,)).fetchone()
    if segment is None:
        raise CuttingError(f"segment {segment_id} 없음")
    utterances = load_segment_utterances(conn, segment)
    valid = {u["idx"] for u in utterances}

    if manual is not None:
        # LLM 없이 범위를 직접 지정하는 경로. A7 렌더를 키 없이 검증할 때 쓴다.
        # 🔴 편의 경로지 rank 결과가 아니다 — reason 에 그렇게 남긴다.
        start, end = manual
        if start not in valid or end not in valid or start > end:
            raise CuttingError(f"수동 범위가 구간 밖이다: {start}~{end} (허용 {min(valid)}~{max(valid)})")
        cut = Cut(start, end, f"수동 지정 ({start}~{end})")
        return create_clip(conn, run_id, segment, utterances, cut, score, replace)

    if not replace and existing_clip(conn, run_id, segment_id) is not None:
        # 🔴 LLM 을 부르기 **전에** 막는다. 부르고 나서 거절하면 그 호출은 그냥 낭비다.
        raise CuttingError(f"이 구간으로 만든 클립이 이미 있다 — 다시 만들려면 replace 를 준다")

    source_id = conn.execute(
        "select source_id from chunks where id = ?", (segment["chunk_id"],)
    ).fetchone()["source_id"]
    context = conn.execute("select context from sources where id = ?", (source_id,)).fetchone()["context"]

    prompt = build_prompt(utterances, segment["description"] or "", context)
    raw, usage, latency_ms = gemini.generate_json(cfg, prompt, RESPONSE_SCHEMA)
    call = conn.execute(
        """insert into stage_calls
           (source_id, run_id, segment_id, stage, model, input_tokens, output_tokens,
            thinking_tokens, latency_ms)
           values (?, ?, ?, 'cut', ?, ?, ?, ?, ?)""",
        (
            source_id, run_id, segment_id, cfg.gemini_model,
            usage["input_tokens"], usage["output_tokens"], usage["thinking_tokens"], latency_ms,
        ),
    )
    conn.commit()

    try:
        cut = parse_response(raw, valid)
    except CuttingError as exc:
        conn.execute("update stage_calls set error = ? where id = ?", (str(exc), call.lastrowid))
        conn.commit()
        raise
    return create_clip(conn, run_id, segment, utterances, cut, score, replace)
