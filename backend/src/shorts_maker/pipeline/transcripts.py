"""대사 두 층 — 받은 그대로(`transcripts`) · 지금 쓰는 것(`utterances`). tease §5-1 · §6-2, 2026-11-01.

```
transcripts (원본별 · 출처별 · 원본 절대 초)  ──use()──▶  utterances (청크별 · 지금 쓰는 대사)
  youtube  [유튜브 자막] 이 통째로 갈아 끼운다                 뒤 단계(분할·컷·자막·판정)가 읽는 것
  whisper  [음성 인식] 이 덧붙인다. 지우지 않는다
```

🔴 **`utterances` 를 채우는 길은 `use()` 하나다.** 대사를 갈아 끼우면 같은 발화 번호가 다른 문장이 되는데,
`segments`·`clip_parts` 는 발화를 **번호**로 가리키고 컷·추천·답하기가 그 번호로 대사를 읽는다. `use()` 는
갈아 끼운 뒤 그 번호를 **시각**으로 다시 맞춘다(`remap`). 다른 곳에서 utterances 를 지우고 채우면 이 일이
빠진다.
"""

import psycopg
from psycopg.types.json import Jsonb

from ..db import store

ORIGINS = ("youtube", "whisper")

# 화면·로그에 쓰는 이름.
LABEL = {"youtube": "유튜브 자막", "whisper": "음성 인식"}


class TranscriptError(RuntimeError):
    """사람이 고칠 수 있는 이유로 대사를 바꿀 수 없다. 메시지를 그대로 화면에 보인다."""


# ---------------------------------------------------------------- 보관

def store_rows(conn: psycopg.Connection, source_id: int, origin: str, rows: list[dict], replace: bool) -> None:
    """받은 대사를 보관한다. 행은 `start_sec · end_sec · text · words(· avg_logprob · no_speech_prob)`.

    자막은 `replace=True`(받을 때마다 통째로 — 1초라 아깝지 않다). 🔴 음성 인식은 `replace=False` 로만 부른다 —
    한 번 들은 오디오는 다시 듣지 않으므로 지울 일이 없다(stt.py).
    """
    if origin not in ORIGINS:
        raise TranscriptError(f"모르는 출처: {origin}")
    if replace:
        conn.execute("delete from transcripts where source_id = %s and origin = %s", (source_id, origin))
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """insert into transcripts (source_id, origin, start_sec, end_sec, text, words, avg_logprob, no_speech_prob)
               values (%(source_id)s, %(origin)s, %(start_sec)s, %(end_sec)s, %(text)s, %(words)s,
                       %(avg_logprob)s, %(no_speech_prob)s)""",
            [
                {
                    "source_id": source_id,
                    "origin": origin,
                    "start_sec": row["start_sec"],
                    "end_sec": row["end_sec"],
                    "text": row["text"],
                    "words": Jsonb(row["words"]) if row.get("words") else None,
                    "avg_logprob": row.get("avg_logprob"),
                    "no_speech_prob": row.get("no_speech_prob"),
                }
                for row in rows
            ],
        )


def rows_of(conn: psycopg.Connection, source_id: int, origin: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """select start_sec, end_sec, text, words, avg_logprob, no_speech_prob from transcripts
           where source_id = %s and origin = %s order by start_sec, id""",
        (source_id, origin),
    )]


def counts(conn: psycopg.Connection, source_id: int) -> dict[str, int]:
    found = {r["origin"]: r["n"] for r in conn.execute(
        "select origin, count(*) as n from transcripts where source_id = %s group by origin", (source_id,)
    )}
    return {origin: found.get(origin, 0) for origin in ORIGINS}


# ---------------------------------------------------------------- 쓰는 대사

def assign_to_chunks(utterances: list[dict], chunks: list[dict]) -> tuple[dict[int, list[dict]], int]:
    """발화를 **시작 초가 든** 청크에 나눠 담는다. 청크마다 idx 를 0 부터 다시 매긴다.

    보관한 대사는 영상 전체일 수 있지만(자막) 분석 범위는 사람이 고른 청크들이다 — 범위 밖 발화는 버리고
    개수만 센다.
    """
    by_chunk: dict[int, list[dict]] = {chunk["id"]: [] for chunk in chunks}
    outside = 0
    for utterance in utterances:
        home = next(
            (c for c in chunks if c["start_sec"] <= utterance["start_sec"] < c["end_sec"]), None
        )
        if home is None:
            outside += 1
            continue
        rows = by_chunk[home["id"]]
        rows.append({**utterance, "idx": len(rows)})
    return by_chunk, outside


def use(conn: psycopg.Connection, source_id: int, origin: str) -> dict:
    """`origin` 의 대사를 지금 쓰는 대사로 옮겨 담고 발화 번호를 다시 맞춘다. 커밋은 부른 쪽이 한다.

    🔴 부르는 쪽이 `store.source_lock` 을 쥐고 있어야 한다 — 도는 중인 전사·재분할과 겹치면 청크가 사라진다.
    """
    if origin not in ORIGINS:
        raise TranscriptError(f"모르는 출처: {origin}")
    chunks = [dict(c) for c in conn.execute(
        "select id, start_sec, end_sec from chunks where source_id = %s order by idx", (source_id,)
    )]
    by_chunk, outside = assign_to_chunks(rows_of(conn, source_id, origin), chunks)
    conn.execute(
        "delete from utterances where chunk_id in (select id from chunks where source_id = %s)", (source_id,)
    )
    rows = [{**row, "chunk_id": chunk_id} for chunk_id, chunk_rows in by_chunk.items() for row in chunk_rows]
    if rows:
        with conn.cursor() as cur:
            cur.executemany(
                """insert into utterances (chunk_id, idx, start_sec, end_sec, text, words, avg_logprob, no_speech_prob)
                   values (%(chunk_id)s, %(idx)s, %(start_sec)s, %(end_sec)s, %(text)s, %(words)s,
                           %(avg_logprob)s, %(no_speech_prob)s)""",
                [{**row, "words": Jsonb(row["words"]) if row.get("words") else None} for row in rows],
            )
    conn.execute("update sources set caption_source = %s where id = %s", (origin, source_id))
    remapped = remap(conn, source_id)
    return {"origin": origin, "utterances": len(rows), "outside": outside, **remapped}


def switch(conn: psycopg.Connection, source_id: int, origin: str) -> dict:
    """화면의 [이 대사로 쓰기]. 다시 받거나 다시 듣지 않는다 — 보관한 것을 옮겨 담을 뿐이다."""
    if origin not in ORIGINS:
        raise TranscriptError(f"모르는 출처: {origin}")
    if not conn.execute(
        "select 1 from transcripts where source_id = %s and origin = %s limit 1", (source_id, origin)
    ).fetchone():
        raise TranscriptError(f"{LABEL[origin]} 대사가 없습니다 — 먼저 가져와야 합니다")
    with store.source_lock(conn, source_id, "대사 작업"):
        result = use(conn, source_id, origin)
        conn.commit()
    return result


# ---------------------------------------------------------------- 발화 번호 다시 맞추기

def span_of(utterances: list[dict], start: float, end: float) -> tuple[int, int] | None:
    """시각 범위 [start, end) 를 발화 번호 범위로. 발화는 한 청크의 것, idx 순서.

    🔴 **가운데가 범위 안에 든** 발화를 고른다. 시작 초로 고르면 자막 문장이 0.2초 일찍 시작했을 때 앞 구간으로
    넘어가고, 겹침으로 고르면 경계 문장 하나가 두 구간에 다 들어간다. 가운데가 든 것이 없으면(짧은 구간이
    긴 발화 한복판에 있을 때) 가장 많이 겹치는 발화 하나, 그것도 없으면 가장 가까운 발화 하나.
    발화가 하나도 없으면 None — 맞출 수 없다.
    """
    if not utterances:
        return None
    inside = [u for u in utterances if start <= (u["start_sec"] + u["end_sec"]) / 2 < end]
    if inside:
        return inside[0]["idx"], inside[-1]["idx"]

    def overlap(u: dict) -> float:
        return min(end, u["end_sec"]) - max(start, u["start_sec"])

    best = max(utterances, key=overlap)
    if overlap(best) <= 0:
        best = min(utterances, key=lambda u: min(abs(u["start_sec"] - end), abs(u["end_sec"] - start)))
    return best["idx"], best["idx"]


def remap(conn: psycopg.Connection, source_id: int) -> dict[str, int]:
    """구간·클립 조각의 발화 번호를 시각으로 다시 맞춘다. 바뀐 행 수를 돌려준다.

    시각(`start_sec`·`end_sec`)이 진실이다 — 구간을 나눌 때 발화에서 되찾은 초이고 렌더도 그 초로 잘랐다.
    번호는 그 시각을 지금 쓰는 대사에서 읽기 위한 색인일 뿐이다. 구간 설명(LLM 요약)은 그대로 둔다 — 같은
    말을 다르게 받아 적은 것이라 내용은 같다.
    """
    by_chunk: dict[int, list[dict]] = {}
    for row in conn.execute(
        """select u.chunk_id, u.idx, u.start_sec, u.end_sec from utterances u
           join chunks c on c.id = u.chunk_id where c.source_id = %s order by u.chunk_id, u.idx""",
        (source_id,),
    ):
        by_chunk.setdefault(row["chunk_id"], []).append(dict(row))

    changed = {"segments": 0, "clip_parts": 0}
    targets = (
        ("segments", """select sg.id, sg.chunk_id, sg.start_sec, sg.end_sec, sg.start_utterance_idx, sg.end_utterance_idx
                        from segments sg join chunks c on c.id = sg.chunk_id where c.source_id = %s"""),
        # 🔴 조각의 청크는 **조각이 가리키는 구간의 청크**다 — 대사를 읽는 쪽(suggest·answer)이 그렇게 찾는다.
        # 구간이 지워져 segment_id 가 null 인 조각은 그 길로 읽히지 않으므로 건드리지 않는다.
        ("clip_parts", """select p.id, sg.chunk_id, p.start_sec, p.end_sec, p.start_utterance_idx, p.end_utterance_idx
                          from clip_parts p join segments sg on sg.id = p.segment_id
                          join chunks c on c.id = sg.chunk_id where c.source_id = %s"""),
    )
    for table, query in targets:
        for row in conn.execute(query, (source_id,)).fetchall():
            found = span_of(by_chunk.get(row["chunk_id"], []), row["start_sec"], row["end_sec"])
            if found is None or found == (row["start_utterance_idx"], row["end_utterance_idx"]):
                continue
            # 테이블 이름은 위 상수에서만 온다(사용자 입력 아님).
            conn.execute(
                f"update {table} set start_utterance_idx = %s, end_utterance_idx = %s where id = %s",
                (found[0], found[1], row["id"]),
            )
            changed[table] += 1
    return changed
