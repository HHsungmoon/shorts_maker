"""A5 — [5] rank (문서 §4-[5], §6).

프롬프트는 §6-1 대로 [고정] + [가변] 으로 조립한다.
  [고정] 자립성 관문 · 출력 형식   ← 여기서 튜닝된다. 그래서 실제로 보낸 전문을 runs.prompt 에 남긴다
  [가변] criteria_prompt          ← Run 마다 바뀌는 유일한 부분

🔴 자립성은 **감점이 아니라 후보 제외**다(§1 EchoCut 교훈 4). 감점으로 두면 모델이 실제로
안 뺀다. 제외된 구간은 segments.excluded_by='auto' 로 표시된다.
"""

import json
import sqlite3
from dataclasses import dataclass

from . import config, gemini


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


def load_segments(conn: sqlite3.Connection, source_id: int) -> list[dict]:
    rows = conn.execute(
        """select sg.* from segments sg
           join chunks c on c.id = sg.chunk_id
           where c.source_id = ? order by sg.idx""",
        (source_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def run_for_source(
    conn: sqlite3.Connection, cfg: config.Config, source_id: int, criteria: str | None, admin_id: int | None
) -> int:
    source = conn.execute("select * from sources where id = ?", (source_id,)).fetchone()
    if source is None:
        raise RankingError(f"source {source_id} 없음")

    segments = load_segments(conn, source_id)
    prompt = build_prompt(segments, source["context"], criteria)

    run = conn.execute(
        """insert into runs (source_id, criteria_prompt, prompt, requested_by_admin_id, status)
           values (?, ?, ?, ?, 'RUNNING')""",
        (source_id, (criteria or "").strip() or None, prompt, admin_id),
    )
    run_id = run.lastrowid
    conn.commit()

    try:
        raw, usage, latency_ms = gemini.generate_json(cfg, prompt, RESPONSE_SCHEMA)
    except Exception as exc:
        conn.execute("update runs set status = 'FAILED', error = ? where id = ?", (str(exc), run_id))
        conn.commit()
        raise

    conn.execute(
        """insert into stage_calls
           (source_id, run_id, stage, model, input_tokens, output_tokens, thinking_tokens, latency_ms)
           values (?, ?, 'rank', ?, ?, ?, ?, ?)""",
        (
            source_id, run_id, cfg.gemini_model,
            usage["input_tokens"], usage["output_tokens"], usage["thinking_tokens"], latency_ms,
        ),
    )
    conn.commit()

    try:
        ranked, excluded = parse_response(raw, {s["idx"] for s in segments})
    except RankingError as exc:
        conn.execute("update runs set status = 'FAILED', error = ? where id = ?", (str(exc), run_id))
        conn.commit()
        raise

    by_idx = {s["idx"]: s for s in segments}
    for item in excluded:
        conn.execute(
            "update segments set excluded_by = 'auto', excluded_reason = ? where id = ?",
            (item.reason, by_idx[item.idx]["id"]),
        )
    conn.execute(
        "update runs set status = 'DONE', ranked = ?, updated_at = datetime('now') where id = ?",
        (
            json.dumps(
                {
                    "raw": json.loads(raw),
                    "ranked": [vars(r) for r in ranked],
                    "excluded": [vars(e) for e in excluded],
                },
                ensure_ascii=False,
            ),
            run_id,
        ),
    )
    conn.commit()
    return run_id
