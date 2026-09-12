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
from ..adapters import gemini
from . import embeddings


@dataclass
class Candidates:
    """임베딩 top-k 결과. 이 영상의 구간만 담는다.

    다른 편 안내는 여기 없다 — `suggest_elsewhere` 가 **답할 수 없다고 판정된 그때만** 따로 구한다.
    답을 만들 수 있는 질문에서 형제 영상까지 임베딩할 이유가 없다.
    """

    segments: list[dict]
    best_score: float


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


def ensure_siblings_indexed(
    conn: psycopg.Connection, cfg: config.Config, source_id: int
) -> list[int]:
    """같은 채널의 **발행된 다른 영상**들의 구간을 검색 대상으로 만든다. 인덱싱한 영상 id 목록.

    🔴 **없으면 채널 교차 안내가 조용히 퇴화한다.** `_best_elsewhere` 는 이미 임베딩된 것만 보는데,
    자동 인덱싱은 지금까지 "답하는 그 영상" 에만 걸렸다. 그래서 다른 편을 손으로
    `sm answers index` 하지 않으면 "ep.N 에서 다룹니다" 대신 "답을 찾지 못했어요" 가 떴다.
    실패가 아니라 **조용한 퇴화**여서 화면만 보고는 알 수 없었다 — 데모에서 가장 똑똑해 보이는
    장면이 이것이다(tease §3-4).

    비용을 걱정하지 않는 이유: 대상은 **발행됐고 구간 분할까지 끝난** 영상뿐이다. 그 상태가 되려면
    편당 전사 수 분이 들기 때문에 뒤에 쌓여 있을 수가 없다. 게다가 임베딩은 캐시되므로 영상당 한 번이고,
    generate_content 와 달리 값이 거의 안 나간다.

    🔴 실패해도 답하기를 죽이지 않는다. 이건 **안내**를 위한 부수 작업이다 — 할당량이 떨어졌다고
    답을 만들 수 있는 질문에 답하지 못하게 되면 그게 더 나쁘다.
    """
    rows = conn.execute(
        """select distinct ch.source_id
           from segments sg
           join chunks ch on ch.id = sg.chunk_id
           join sources other on other.id = ch.source_id
           join sources mine on mine.id = %s
           where ch.source_id <> %s
             and other.published
             and mine.channel is not null and other.channel = mine.channel
             and sg.description is not null and length(trim(sg.description)) > 0
             -- 아직 이 모델·차원·용도로 임베딩되지 않은 구간이 하나라도 있는 영상만.
             and not exists (
               select 1 from embeddings e
               where e.kind = 'segment' and e.ref_id = sg.id
                 and e.model = %s and e.dim = %s and e.task_type = %s
             )""",
        (source_id, source_id, cfg.embed_model, cfg.embed_dim, embeddings.DOCUMENT),
    ).fetchall()
    done: list[int] = []
    for row in rows:
        other_id = row["source_id"]
        try:
            segments = load_segments(conn, other_id)
            ensure_indexed(conn, cfg, other_id, segments)
        except gemini.GeminiError:
            # 여기서 멈춘다. 할당량이 떨어졌으면 다음 영상도 마찬가지다.
            break
        done.append(other_id)
    return done


def candidates(
    conn: psycopg.Connection, cfg: config.Config, cluster: dict, top_k: int | None = None,
    run_id: int | None = None,
) -> Candidates:
    """클러스터 대표 문장으로 구간을 찾는다. **지금은 폴백 경로다.**

    🔴 평소에는 `routing.decide` 가 LLM 으로 구간을 고른다 — 이 임베딩 top-k 가 실측에서 안 듣는다는
    것이 드러났기 때문이다(update_plan M5 실측: 답이 담긴 구간이 44개 중 29위). 여기는 그 판정을
    못 읽었을 때만 온다. 약한 신호지만 아무것도 없이 포기하는 것보다는 낫다.


    🔴 대표 문장은 **검색어**로 다시 임베딩한다(`RETRIEVAL_QUERY`). 묶기에 쓴 벡터
    (`SEMANTIC_SIMILARITY`)와는 다른 벡터다 — 짧은 질문으로 긴 설명을 찾는 건 비대칭 검색이라
    task_type 이 다르고, 섞으면 순위가 미묘하게 어긋난다(마이그레이션 002 가 둘을 갈라 둔 이유).
    """
    top_k = top_k or cfg.retrieval_top_k
    source_id = cluster["source_id"]
    segments = load_segments(conn, source_id)
    if not segments:
        return Candidates([], 0.0)

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

    conn.execute(
        """insert into stage_calls (source_id, run_id, stage, params)
           values (%s, %s, 'retrieve', %s)""",
        (
            source_id, run_id,
            Jsonb({
                "cluster_id": cluster["id"], "purpose": "fallback",
                "segments": len(usable), "top_k": top_k,
                "best": round(best, 4), "min_sim": cfg.retrieval_min_sim,
            }),
        ),
    )
    return Candidates(ranked[:top_k], best)


def suggest_elsewhere(
    conn: psycopg.Connection, cfg: config.Config, cluster: dict, segments: list[dict],
    run_id: int | None = None,
) -> int | None:
    """"이 영상엔 없어요, 저 편에서 다룹니다" 의 **저 편**. 없으면 None.

    🔴 **답할 수 없다고 판정된 그때만 부른다.** 안내가 필요한 순간이 그때뿐이고, 답을 만들 수 있는
    질문에서는 형제 영상을 임베딩할 이유가 없다. 예전에는 검색 단계가 매번 계산했다.

    🔴 **절대 임계값을 쓰지 않는다.** 실측에서 한 영상 안의 유사도가 0.502~0.755 좁은 띠에 몰렸다
    (update_plan M5 실측). 그 폭에서는 어떤 절대값을 잡아도 늘 통과하거나 늘 막힌다. 그래서
    **이 영상의 최고점보다 더 가까운** 다른 편만 가리킨다 — 상대 비교가 유일하게 뜻이 있다.
    아무 영상이나 가리키는 것은 "못 찾았어요" 보다 나쁘다.
    """
    query_vectors = embeddings.embed_and_store(
        conn, cfg, "cluster", [(cluster["id"], cluster["canonical_text"])],
        embeddings.QUERY, source_id=cluster["source_id"],
    )
    query = embeddings.from_blobs([query_vectors[cluster["id"]]], cfg.embed_dim)

    stored = ensure_indexed(conn, cfg, cluster["source_id"], segments)
    usable = [s for s in segments if s["id"] in stored]
    mine = 0.0
    if usable:
        scores = embeddings.cosine(
            query, embeddings.from_blobs([stored[s["id"]] for s in usable], cfg.embed_dim)
        )[0]
        mine = float(np.max(scores))

    ensure_siblings_indexed(conn, cfg, cluster["source_id"])
    other_id, other_score = _best_elsewhere(conn, cfg, cluster, query)
    conn.execute(
        "insert into stage_calls (source_id, run_id, stage, params)"
        " values (%s, %s, 'retrieve', %s)",
        (
            cluster["source_id"], run_id,
            Jsonb({
                "cluster_id": cluster["id"], "purpose": "suggest",
                "mine_best": round(mine, 4), "elsewhere_source_id": other_id,
                "elsewhere": round(other_score, 4),
            }),
        ),
    )
    if other_id is not None and other_score > mine:
        return other_id
    return None


def _best_elsewhere(
    conn: psycopg.Connection, cfg: config.Config, cluster: dict, query: np.ndarray
) -> tuple[int | None, float]:
    """같은 채널의 **발행된 다른** 영상에서 가장 가까운 구간의 영상 id. 안내용이다.

    🔴 **발행된 것만 고른다.** 시청자 화면은 발행된 영상만 가리키므로(`http/watch.py` 의
    `and s.published`), 미발행 영상을 골라 저장하면 안내가 화면에서 조용히 사라진다 —
    "ep.N 에서 다룹니다" 대신 일반 문구가 뜨고, DB 에는 추천이 들어 있어서 왜 안 보이는지
    알 수 없다.

    이미 임베딩된 것만 본다. 인덱싱은 `ensure_siblings_indexed` 가 이 함수 직전에 한다.
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
             and other.published
             and mine.channel is not null and other.channel = mine.channel""",
        (cluster["source_id"], cfg.embed_model, cfg.embed_dim, embeddings.DOCUMENT, cluster["source_id"]),
    ).fetchall()
    if not rows:
        return None, 0.0
    matrix = embeddings.from_blobs([r["vector"] for r in rows], cfg.embed_dim)
    scores = embeddings.cosine(query, matrix)[0]
    best = int(np.argmax(scores))
    return rows[best]["source_id"], float(scores[best])
