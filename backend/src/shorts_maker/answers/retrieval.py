"""구간 검색 — 질문에 답이 있을 만한 구간을 먼저 좁힌다 (tease §5-3 b, §5-4).

전 구간을 rank 에 보내는 대신 임베딩으로 top-k 를 고른다. 이득 둘:
  ① 95분짜리 영상의 구간 수십~수백 개를 매번 프롬프트에 넣지 않는다(비용·지연)
  ② "이 영상엔 그 얘기가 없다" 를 **rank 전에** 알 수 있다 — 최대 유사도가 바닥이면 그 신호다

🔴 **같은 채널의 다른 영상도 본다.** 다만 그건 "저 영상에 있어요" 라고 **안내**하는 데만 쓴다 —
다른 영상에서 클립을 만들지는 않는다. 클러스터는 이 영상에 달려 있고, 다른 영상에서 자른
클립을 이 영상의 run 에 매다는 순간 "이 클립은 어느 영상 것인가" 가 어디서도 답이 안 된다.
채널 횡단 클립은 그 자체로 하나의 기능이고 지금 할 일이 아니다.
"""

from dataclasses import dataclass

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from .. import config
from . import embeddings


@dataclass
class Candidates:
    """검색 결과. `segments` 는 이 영상 것만, `elsewhere` 는 같은 채널의 다른 영상 최고 후보."""

    segments: list[dict]
    best_score: float
    elsewhere_source_id: int | None
    elsewhere_score: float


def load_segments(conn: psycopg.Connection, source_id: int) -> list[dict]:
    rows = conn.execute(
        """select sg.id, sg.idx, sg.description, sg.start_sec, sg.end_sec, sg.chunk_id,
                  sg.start_utterance_idx, sg.end_utterance_idx
           from segments sg join chunks ch on ch.id = sg.chunk_id
           where ch.source_id = %s and sg.excluded_by is null
           order by sg.idx""",
        (source_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def ensure_indexed(
    conn: psycopg.Connection, cfg: config.Config, source_id: int, segments: list[dict]
) -> dict[int, bytes]:
    """검색 직전에 없으면 만든다.

    segmentation 경로에 임베딩을 매달지 않은 이유: 거기서 외부 호출이 실패하면 구간 분할이
    통째로 실패한다. 검색은 어차피 벡터가 필요한 자리라 여기서 만드는 게 실패의 책임 소재가 맞다.
    """
    items = [(s["id"], s["description"]) for s in segments if (s["description"] or "").strip()]
    return embeddings.embed_and_store(
        conn, cfg, "segment", items, embeddings.DOCUMENT, source_id=source_id
    )


def candidates(
    conn: psycopg.Connection, cfg: config.Config, cluster: dict, top_k: int | None = None
) -> Candidates:
    """클러스터 대표 문장으로 구간을 찾는다.

    🔴 대표 문장은 **검색어**로 다시 임베딩한다(`RETRIEVAL_QUERY`). 묶기에 쓴 벡터
    (`SEMANTIC_SIMILARITY`)와는 다른 벡터다 — 짧은 질문으로 긴 설명을 찾는 건 비대칭 검색이라
    task_type 이 다르고, 섞으면 순위가 미묘하게 어긋난다(마이그레이션 002 가 둘을 갈라 둔 이유).
    """
    top_k = top_k or cfg.retrieval_top_k
    source_id = cluster["source_id"]
    segments = load_segments(conn, source_id)
    if not segments:
        return Candidates([], 0.0, None, 0.0)

    query_vectors = embeddings.embed_and_store(
        conn, cfg, "cluster", [(cluster["id"], cluster["canonical_text"])],
        embeddings.QUERY, source_id=source_id,
    )
    query = embeddings.from_blobs([query_vectors[cluster["id"]]], cfg.embed_dim)

    stored = ensure_indexed(conn, cfg, source_id, segments)
    usable = [s for s in segments if s["id"] in stored]
    scores = (
        embeddings.cosine(query, embeddings.from_blobs([stored[s["id"]] for s in usable], cfg.embed_dim))[0]
        if usable
        else np.zeros(0, dtype=np.float32)
    )
    ranked = sorted(
        ({**s, "score": float(score)} for s, score in zip(usable, scores)),
        key=lambda s: -s["score"],
    )
    best = ranked[0]["score"] if ranked else 0.0

    other_id, other_score = _best_elsewhere(conn, cfg, cluster, query)
    conn.execute(
        """insert into stage_calls (source_id, stage, params) values (%s, 'retrieve', %s)""",
        (
            source_id,
            Jsonb({
                "cluster_id": cluster["id"], "segments": len(usable), "top_k": top_k,
                "best": round(best, 4), "elsewhere_source_id": other_id,
                "elsewhere": round(other_score, 4), "min_sim": cfg.retrieval_min_sim,
            }),
        ),
    )
    return Candidates(ranked[:top_k], best, other_id, other_score)


def _best_elsewhere(
    conn: psycopg.Connection, cfg: config.Config, cluster: dict, query: np.ndarray
) -> tuple[int | None, float]:
    """같은 채널의 **다른** 영상에서 가장 가까운 구간의 영상 id. 안내용이다.

    이미 임베딩된 것만 본다 — 안내 하나 때문에 다른 영상 전부를 임베딩하지 않는다.
    """
    rows = conn.execute(
        """select e.ref_id, e.vector, ch.source_id
           from embeddings e
           join segments sg on sg.id = e.ref_id
           join chunks ch on ch.id = sg.chunk_id
           join sources other on other.id = ch.source_id
           join sources mine on mine.id = %s
           where e.kind = 'segment' and e.model = %s and e.dim = %s and e.task_type = %s
             and ch.source_id <> %s
             and mine.channel is not null and other.channel = mine.channel""",
        (cluster["source_id"], cfg.embed_model, cfg.embed_dim, embeddings.DOCUMENT, cluster["source_id"]),
    ).fetchall()
    if not rows:
        return None, 0.0
    matrix = embeddings.from_blobs([r["vector"] for r in rows], cfg.embed_dim)
    scores = embeddings.cosine(query, matrix)[0]
    best = int(np.argmax(scores))
    return rows[best]["source_id"], float(scores[best])
