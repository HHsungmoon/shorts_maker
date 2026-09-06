"""전체 실행 — [3] → [5] → [6] → [7] 을 잡 하나로 묶는다.

🔴 **중립 자산은 재사용한다.** 구간이 이미 있으면 다시 나누지 않는다 — 그게 §3 이
중립/주관을 나눈 이유고, "기준만 바꿔 rank 만 다시 돌리기"(§6-2)가 여기서 성립한다.
다시 나누려면 `resegment=True`(화면의 `주제 분할` 버튼)로 명시한다.
"""

import psycopg

from .. import config
from . import cutting, ranking, render, segmentation


class PipelineError(RuntimeError):
    pass


def run_all(
    conn: psycopg.Connection,
    cfg: config.Config,
    source_id: int,
    criteria: str | None,
    resegment: bool = False,
    burn_subtitles: bool = True,
) -> dict:
    chunks = conn.execute(
        "select id from chunks where source_id = %s order by idx", (source_id,)
    ).fetchall()
    if not chunks:
        raise PipelineError("청크가 없다 — 먼저 구간을 추출한다")

    segmented = 0
    for chunk in chunks:
        existing = conn.execute(
            "select count(*) as n from segments where chunk_id = %s", (chunk["id"],)
        ).fetchone()["n"]
        if existing and not resegment:
            continue
        segmented += len(segmentation.run_for_chunk(conn, cfg, chunk["id"], force=True))

    run_id = ranking.run_for_source(conn, cfg, source_id, criteria)
    # runs.ranked 는 jsonb — 이미 dict 로 온다.
    ranked = conn.execute("select ranked from runs where id = %s", (run_id,)).fetchone()["ranked"]["ranked"]
    if not ranked:
        raise PipelineError("rank 결과가 비었다")

    top = ranked[0]
    segment = conn.execute(
        """select sg.id from segments sg join chunks c on c.id = sg.chunk_id
           where c.source_id = %s and sg.idx = %s""",
        (source_id, top["idx"]),
    ).fetchone()
    if segment is None:
        raise PipelineError(f"1위 구간 [{top['idx']}] 을 찾을 수 없다")

    clip_id = cutting.run_for_segment(conn, cfg, run_id, segment["id"], top["score"])
    out = render.run_for_clip(conn, cfg, clip_id, force=True, burn_subtitles=burn_subtitles)
    return {
        "runId": run_id,
        "segmentsCreated": segmented,
        "topSegmentIdx": top["idx"],
        "score": top["score"],
        "clipId": clip_id,
        "path": str(out),
    }
