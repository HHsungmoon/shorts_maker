"""임베딩 저장과 코사인 검색 (tease §5-3).

벡터 DB 를 쓰지 않는다. 세그먼트 수백 개 · 질문 수백 개 규모에서 numpy 브루트포스가 1ms 밑이고,
"N<1000 이라 벡터 DB 를 안 썼다"는 판단이 도입보다 낫다. 수만 개가 되면 pgvector 를 켠다 —
DB 가 Postgres 라 확장 하나면 된다.

🔴 **저장 전에 정규화한다.** 코사인 유사도를 행렬곱 한 번으로 계산하려면 모든 벡터의 길이가 1
이어야 한다. gemini-embedding-001 은 3072 이 아닌 차원을 요청하면 정규화되지 않은 벡터를 주므로,
안 하면 유사도가 조용히 틀린다(순위는 비슷해 보여서 더 나쁘다 — θ 임계값이 의미를 잃는다).

🔴 **(model, dim, task_type) 이 다르면 섞지 않는다.** 같은 문장이라도 task_type 이 다르면 다른
벡터다. 읽을 때 조건으로 걸러서, 설정을 바꾸면 옛 벡터가 자동으로 무시되고 다시 계산된다.
"""

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import gemini

# task_type — 무엇에 쓸 벡터인가. 값은 Gemini API 의 것을 그대로 쓴다.
SIMILARITY = "SEMANTIC_SIMILARITY"  # 문장끼리 대칭 비교(질문 묶기)
DOCUMENT = "RETRIEVAL_DOCUMENT"  # 검색 대상(구간 설명)
QUERY = "RETRIEVAL_QUERY"  # 검색어(클러스터 대표 문장) — M5


class EmbeddingError(RuntimeError):
    pass


def to_blob(vector: list[float]) -> bytes:
    """float32 리틀엔디언 blob. 🔴 여기서 정규화한다 — 저장된 벡터는 항상 길이 1이다."""
    array = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(array))
    if norm == 0.0:
        raise EmbeddingError("길이가 0인 벡터는 저장하지 않는다 — 코사인이 정의되지 않는다")
    return (array / norm).astype("<f4").tobytes()


def from_blobs(blobs: list[bytes], dim: int) -> np.ndarray:
    """blob 목록 → (N, dim) 행렬. 전부 정규화된 상태라고 가정한다(to_blob 이 보장)."""
    if not blobs:
        return np.zeros((0, dim), dtype=np.float32)
    return np.vstack([np.frombuffer(b, dtype="<f4") for b in blobs])


def cosine(queries: np.ndarray, targets: np.ndarray) -> np.ndarray:
    """(Q, D) × (T, D) → (Q, T) 유사도. 정규화돼 있으므로 내적이 곧 코사인이다."""
    if queries.size == 0 or targets.size == 0:
        return np.zeros((queries.shape[0], targets.shape[0]), dtype=np.float32)
    return queries @ targets.T


# ---------------------------------------------------------------- 저장·조회

def store(
    conn: psycopg.Connection,
    cfg: config.Config,
    kind: str,
    ref_ids: list[int],
    vectors: list[list[float]],
    task_type: str,
) -> None:
    """벡터를 덮어쓴다. 같은 (kind, ref_id, model, task_type) 이면 갱신한다.

    커밋은 호출부가 한다 — 클러스터 배정과 같은 트랜잭션에 묶여야 한다.
    """
    if len(ref_ids) != len(vectors):
        raise EmbeddingError(f"개수가 안 맞는다: ref {len(ref_ids)}, vector {len(vectors)}")
    with conn.cursor() as cur:
        cur.executemany(
            """insert into embeddings (kind, ref_id, model, dim, vector, task_type)
               values (%(kind)s, %(ref_id)s, %(model)s, %(dim)s, %(vector)s, %(task_type)s)
               on conflict (kind, ref_id, model, task_type)
               do update set vector = excluded.vector, dim = excluded.dim, created_at = now()""",
            [
                {
                    "kind": kind, "ref_id": ref_id, "model": cfg.embed_model,
                    "dim": cfg.embed_dim, "vector": to_blob(vector), "task_type": task_type,
                }
                for ref_id, vector in zip(ref_ids, vectors)
            ],
        )


def load(
    conn: psycopg.Connection, cfg: config.Config, kind: str, ref_ids: list[int], task_type: str
) -> dict[int, bytes]:
    """지금 설정(model·dim·task_type)으로 만든 벡터만 돌려준다. 없는 것은 키가 없다."""
    if not ref_ids:
        return {}
    rows = conn.execute(
        """select ref_id, vector from embeddings
           where kind = %s and model = %s and dim = %s and task_type = %s and ref_id = any(%s)""",
        (kind, cfg.embed_model, cfg.embed_dim, task_type, ref_ids),
    ).fetchall()
    return {r["ref_id"]: r["vector"] for r in rows}


def embed_and_store(
    conn: psycopg.Connection,
    cfg: config.Config,
    kind: str,
    items: list[tuple[int, str]],
    task_type: str,
    source_id: int | None = None,
    reuse: bool = True,
) -> dict[int, bytes]:
    """필요한 것만 임베딩해서 저장하고, 요청한 전부의 벡터를 돌려준다.

    `reuse=True` 면 이미 있는 것은 다시 부르지 않는다 — 재집계가 증분으로 도는 근거다(I12).
    텍스트가 바뀐 대상(대표 문장 수정)은 호출부가 `reuse=False` 로 부르거나 미리 지운다.

    호출 1회는 `stage_calls(stage='embed')` 한 줄이다(규약: 모든 단계를 기록한다).
    """
    have = load(conn, cfg, kind, [ref for ref, _ in items], task_type) if reuse else {}
    todo = [(ref, text) for ref, text in items if ref not in have]
    if todo:
        vectors, latency_ms, calls = gemini.embed_texts(cfg, [text for _, text in todo], task_type)
        store(conn, cfg, kind, [ref for ref, _ in todo], vectors, task_type)
        conn.execute(
            """insert into stage_calls (source_id, stage, model, latency_ms, params)
               values (%s, 'embed', %s, %s, %s)""",
            (
                source_id, cfg.embed_model, latency_ms,
                Jsonb({"kind": kind, "task_type": task_type, "texts": len(todo),
                       "dim": cfg.embed_dim, "batches": calls}),
            ),
        )
        have = have | load(conn, cfg, kind, [ref for ref, _ in todo], task_type)
    return have


def index_segments(conn: psycopg.Connection, cfg: config.Config, source_id: int) -> int:
    """이 원본의 구간 설명을 검색 대상으로 임베딩한다. 이미 된 것은 건너뛴다.

    **자동 경로는 따로 있다** — `retrieval.ensure_indexed`(답하는 그 영상)와
    `retrieval.ensure_siblings_indexed`(같은 채널의 발행된 다른 영상)가 검색 직전에 만든다.
    segmentation 에 매달지 않은 이유는 그 함수들 주석에 있다(임베딩 실패가 구간 분할 실패가 된다).

    이 함수는 `sm answers index` 가 쓴다 — 데모 전에 미리 만들어 두거나, 자동 경로가 할당량으로
    건너뛴 것을 손으로 채울 때다.
    """
    rows = conn.execute(
        """select sg.id, sg.description from segments sg
           join chunks ch on ch.id = sg.chunk_id
           where ch.source_id = %s and sg.description is not null and length(trim(sg.description)) > 0
           order by sg.id""",
        (source_id,),
    ).fetchall()
    items = [(r["id"], r["description"]) for r in rows]
    before = len(load(conn, cfg, "segment", [i for i, _ in items], DOCUMENT))
    embed_and_store(conn, cfg, "segment", items, DOCUMENT, source_id=source_id)
    conn.commit()
    return len(items) - before
