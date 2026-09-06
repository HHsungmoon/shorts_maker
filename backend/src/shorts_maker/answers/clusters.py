"""질문 묶기와 클러스터 상태 기계 (tease §3-2 · §5-3, update_plan §2-3).

**역할 분담이 이 파일의 핵심이다.**

| 하는 일 | 누가 | 왜 |
|---|---|---|
| 그룹의 **대표 문장을 짓는다** | LLM 1회 | 여러 표현을 아우르는 한 문장은 사람 언어의 일이다 |
| 질문이 **어느 그룹인지 정한다** | 임베딩 코사인 | 🔴 LLM 에게 인덱스를 맡기면 틀린다 — 없는 번호를 대거나 빠뜨린다 |
| 그룹에 **몇 개인지 센다** | SQL | 🔴 LLM 은 셈을 못한다. 세는 건 DB 가 늘 정확하다 |

그리고 **크리에이터가 트리거한다**(update_plan D11). 질문이 들어올 때마다 부르지 않는다 —
공개 경로에서 요청마다 유료 API 를 부르면 그게 공격면이고, 제품 정의상 "취합"은 크리에이터의 행동이다.

재집계는 **증분**이다. `cluster_id` 가 이미 있는 질문은 건드리지 않는다 — 원문도, 소속도, 좋아요도
그대로다(불변식 I12). 그래서 몇 번을 눌러도 안전하다.
"""

import json

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import gemini
from . import embeddings

# 🔴 상태 전이는 **여기 한 곳**에서만 한다(D7). `update question_clusters set status = ...` 를 다른
# 파일에서 쓰면 이 표가 거짓말이 된다. 표 그대로가 tease §6-6 의 그림이다.
TRANSITIONS: dict[str, frozenset[str]] = {
    # 크리에이터가 [답하기] 를 누른다
    "OPEN": frozenset({"IN_PROGRESS"}),
    # 파이프라인이 끝났거나(REVIEW), 답할 구간이 없거나(UNANSWERABLE), 잡이 죽어 되돌린다(OPEN)
    "IN_PROGRESS": frozenset({"REVIEW", "UNANSWERABLE", "OPEN"}),
    # 사람이 보고 발행하거나 거절한다
    "REVIEW": frozenset({"PUBLISHED", "DECLINED"}),
    # 발행 취소
    "PUBLISHED": frozenset({"REVIEW"}),
    # 다시 열기
    "DECLINED": frozenset({"OPEN"}),
    "UNANSWERABLE": frozenset({"OPEN"}),
}
STATUSES = tuple(TRANSITIONS)

# LLM 이 새 대표 문장을 몇 개까지 낼 수 있나. 미분류 질문 수를 넘으면 그건 그룹이 아니라 헛소리다.
MAX_NEW_CANONICAL = 20
CANONICAL_MAX_LEN = 200


class ClusterError(RuntimeError):
    pass


# ---------------------------------------------------------------- 상태 기계

def transition(
    conn: psycopg.Connection,
    cluster_id: int,
    to: str,
    run_id: int | None = None,
    suggested_source_id: int | None = None,
) -> dict:
    """상태를 바꾼다. 허용되지 않은 전이면 `ClusterError` 로 거부하고 아무것도 바꾸지 않는다.

    같은 상태로의 전이도 거부한다 — 무해해 보이지만 대개 호출부의 논리 오류이고, 조용히 통과하면
    "왜 두 번 실행됐지"를 나중에 추적하게 된다.
    """
    if to not in TRANSITIONS:
        raise ClusterError(f"없는 상태다: {to}")
    row = conn.execute(
        "select id, status from question_clusters where id = %s", (cluster_id,)
    ).fetchone()
    if row is None:
        raise ClusterError(f"cluster {cluster_id} 없음")
    current = row["status"]
    if to not in TRANSITIONS[current]:
        allowed = ", ".join(sorted(TRANSITIONS[current])) or "없음"
        raise ClusterError(f"{current} → {to} 는 허용되지 않는다 (가능: {allowed})")
    updated = conn.execute(
        """update question_clusters
           set status = %s,
               run_id = coalesce(%s, run_id),
               suggested_source_id = coalesce(%s, suggested_source_id),
               updated_at = now()
           where id = %s returning *""",
        (to, run_id, suggested_source_id, cluster_id),
    ).fetchone()
    return dict(updated)


def reopen_stuck(conn: psycopg.Connection) -> int:
    """재기동 시 매달린 클러스터를 OPEN 으로 되돌린다.

    잡 큐는 메모리에만 있어서(jobs.py) IN_PROGRESS 는 이미 죽은 것이다. `sources`·`runs` 의
    RUNNING 정리와 같은 자리에서 부른다(http/server.serve).
    """
    done = conn.execute(
        "update question_clusters set status = 'OPEN', updated_at = now() where status = 'IN_PROGRESS'"
    )
    return done.rowcount


# ---------------------------------------------------------------- 수요 집계

def demand(conn: psycopg.Connection, source_id: int) -> list[dict]:
    """클러스터별 질문 수와 좋아요 합. 🔴 **세는 건 SQL 이다** — LLM 에게 시키지 않는다.

    좋아요는 클러스터가 아니라 질문에 붙어 있어서(재집계에도 보존된다) 여기서 합산한다.
    """
    rows = conn.execute(
        """select c.*,
                  count(distinct q.id) as question_count,
                  count(l.id) as like_count
           from question_clusters c
           left join questions q on q.cluster_id = c.id
           left join question_likes l on l.question_id = q.id
           where c.source_id = %s
           group by c.id
           order by question_count desc, like_count desc, c.id""",
        (source_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def questions_of(conn: psycopg.Connection, cluster_id: int) -> list[dict]:
    rows = conn.execute(
        """select q.id, q.text, q.created_at, count(l.id) as likes
           from questions q left join question_likes l on l.question_id = q.id
           where q.cluster_id = %s group by q.id order by likes desc, q.id""",
        (cluster_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def clip_of(conn: psycopg.Connection, cluster_id: int) -> dict | None:
    """이 묶음에 답한 클립 하나 + 최신 judge 소견.

    묶음당 클립은 하나다(다시 답하면 새 run 이 생기고 최신 것을 본다). judge 판정을 함께 주는
    이유는 크리에이터가 **발행 전에** 그걸 보고 판단해야 하기 때문이다 — 자립하지 않는다고
    판정된 클립이 조용히 발행되면 시청자가 먼저 발견한다.
    """
    clip = conn.execute(
        """select c.id, c.rendered, c.published_at, c.total_sec, c.reason, c.score, c.run_id
           from clips c where c.question_cluster_id = %s order by c.id desc limit 1""",
        (cluster_id,),
    ).fetchone()
    if clip is None:
        return None
    clip = dict(clip)
    review = conn.execute(
        """select verdict, note from clip_reviews
           where clip_id = %s and reviewer = 'llm' order by id desc limit 1""",
        (clip["id"],),
    ).fetchone()
    clip["llmVerdict"] = review["verdict"] if review else None
    clip["llmNote"] = review["note"] if review else None
    # 조각이 몇 개인지 — 조합 클립이면 화면에서 그렇게 알려준다.
    clip["parts"] = [
        dict(r) for r in conn.execute(
            "select ordinal, start_sec, end_sec from clip_parts where clip_id = %s order by ordinal",
            (clip["id"],),
        )
    ]
    return clip


def unclustered(conn: psycopg.Connection, source_id: int) -> list[dict]:
    rows = conn.execute(
        """select q.id, q.text, q.created_at, count(l.id) as likes
           from questions q left join question_likes l on l.question_id = q.id
           where q.source_id = %s and q.cluster_id is null
           group by q.id order by q.id""",
        (source_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- [집계]

NAMING_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "canonical": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["canonical"],
}

NAMING_PROMPT = """아래는 한 영상에 시청자들이 남긴 질문 원문이다.
이 질문들이 결국 무엇을 알고 싶어하는지 보고, **수요 하나당 한 문장의 대표 질문**을 써라.

규칙:
- 대표 문장은 물음표로 끝나는 자연스러운 한 문장이다.
- 이미 있는 대표 문장으로 충분히 덮이는 질문은 **새로 만들지 마라.**
- 질문 원문을 그대로 베끼지 말고, 여러 표현을 아우르는 문장으로 써라.
- 🔴 어떤 질문이 어디에 속하는지, 각 그룹에 몇 개가 있는지는 **판단하지 마라.** 그건 다른 단계가 한다.
- 새로 필요한 대표 문장이 없으면 빈 배열을 준다.
- 최대 {max_new}개.
{existing_block}
질문 원문:
{question_block}"""


def build_naming_prompt(questions: list[str], existing: list[str], max_new: int) -> str:
    if not questions:
        raise ClusterError("묶을 질문이 없다")
    existing_block = (
        "\n이미 있는 대표 문장:\n" + "\n".join(f"- {t}" for t in existing) + "\n" if existing else ""
    )
    return NAMING_PROMPT.format(
        max_new=max_new,
        existing_block=existing_block,
        question_block="\n".join(f"- {t}" for t in questions),
    )


def parse_naming(raw: str, existing: list[str], limit: int) -> list[str]:
    """§12 검증: 비어 있지 않고 · 너무 길지 않고 · 기존과 중복이 아니고 · 개수 상한 안."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClusterError(f"JSON 파싱 실패: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("canonical"), list):
        raise ClusterError("canonical 배열이 없다")

    known = {t.strip().casefold() for t in existing}
    out: list[str] = []
    for item in payload["canonical"]:
        text = str(item).strip()
        if not text:
            continue
        if len(text) > CANONICAL_MAX_LEN:
            text = text[:CANONICAL_MAX_LEN].rstrip()
        key = text.casefold()
        if key in known:
            continue  # 이미 있는 것을 다시 제안했다 — 조용히 버린다
        known.add(key)
        out.append(text)
    return out[:limit]


def aggregate(conn: psycopg.Connection, cfg: config.Config, source_id: int) -> dict:
    """[집계] — 미분류 질문을 묶는다. 크리에이터가 누를 때만 돈다.

    ① LLM 1회로 **새 대표 문장만** 받는다 (소속·개수는 안 시킨다)
    ② 대표 문장 + 미분류 질문을 배치 임베딩한다
    ③ 질문마다 가장 가까운 대표를 찾아 코사인 ≥ θ 면 붙이고, 아니면 그 질문 자체가 새 클러스터가 된다

    ③ 의 단독 클러스터는 만들어지는 즉시 후보 목록에 들어간다 — 뒤따르는 비슷한 질문이 거기
    붙는다. 안 그러면 LLM 이 놓친 표현들이 전부 1개짜리 클러스터로 흩어진다.
    """
    pending = unclustered(conn, source_id)
    if not pending:
        return {"newClusters": 0, "assigned": 0, "pending": 0, "theta": cfg.cluster_theta}

    existing = conn.execute(
        "select id, canonical_text from question_clusters where source_id = %s order by id",
        (source_id,),
    ).fetchall()
    existing_ids = [r["id"] for r in existing]
    existing_texts = [r["canonical_text"] for r in existing]

    # ① 이름 짓기 — LLM 1회
    prompt = build_naming_prompt(
        [q["text"] for q in pending], existing_texts, min(MAX_NEW_CANONICAL, len(pending))
    )
    raw, usage, latency_ms = gemini.generate_json(cfg, prompt, NAMING_SCHEMA)
    conn.execute(
        """insert into stage_calls (source_id, stage, model, input_tokens, output_tokens,
                                    thinking_tokens, total_tokens, cached_tokens, latency_ms, params)
           values (%s, 'cluster', %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            source_id, cfg.gemini_model, usage["input_tokens"], usage["output_tokens"],
            usage["thinking_tokens"], usage["total_tokens"], usage["cached_tokens"], latency_ms,
            Jsonb({"pending": len(pending), "existing": len(existing_texts)}),
        ),
    )
    proposed = parse_naming(raw, existing_texts, min(MAX_NEW_CANONICAL, len(pending)))

    created_ids: list[int] = []
    for text in proposed:
        new_id = conn.execute(
            "insert into question_clusters (source_id, canonical_text) values (%s, %s) returning id",
            (source_id, text),
        ).fetchone()["id"]
        created_ids.append(new_id)

    # ② 임베딩 — 대표 문장(새것만 새로 계산)과 미분류 질문
    candidate_ids = existing_ids + created_ids
    candidate_texts = existing_texts + proposed
    cluster_vectors = embeddings.embed_and_store(
        conn, cfg, "cluster", list(zip(candidate_ids, candidate_texts)),
        embeddings.SIMILARITY, source_id=source_id,
    )
    question_vectors = embeddings.embed_and_store(
        conn, cfg, "question", [(q["id"], q["text"]) for q in pending],
        embeddings.SIMILARITY, source_id=source_id,
    )

    # ③ 배정 — 코사인 ≥ θ
    pool_ids = [cid for cid in candidate_ids if cid in cluster_vectors]
    pool = embeddings.from_blobs([cluster_vectors[cid] for cid in pool_ids], cfg.embed_dim)
    assigned = 0
    for question in pending:
        vector = question_vectors.get(question["id"])
        if vector is None:
            continue
        row = embeddings.from_blobs([vector], cfg.embed_dim)
        target: int | None = None
        if len(pool_ids):
            scores = embeddings.cosine(row, pool)[0]
            best = int(np.argmax(scores))
            if float(scores[best]) >= cfg.cluster_theta:
                target = pool_ids[best]
        if target is None:
            # 어디에도 안 붙는다 — 이 질문 자체가 새 수요다. 대표 문장은 원문 그대로 쓰고,
            # 벡터도 이미 있으니 그대로 재사용한다(임베딩 호출을 아낀다).
            target = conn.execute(
                "insert into question_clusters (source_id, canonical_text) values (%s, %s) returning id",
                (source_id, question["text"][:CANONICAL_MAX_LEN]),
            ).fetchone()["id"]
            created_ids.append(target)
            embeddings.store(conn, cfg, "cluster", [target], [list(row[0])], embeddings.SIMILARITY)
            pool_ids.append(target)
            pool = np.vstack([pool, row]) if len(pool) else row
        conn.execute("update questions set cluster_id = %s where id = %s", (target, question["id"]))
        assigned += 1

    # LLM 이 제안했지만 아무 질문도 붙지 않은 대표 문장은 그냥 소음이다. 이번에 만든 것 중
    # 비어 있는 것만 지운다 — 크리에이터가 이미 손댄 클러스터는 건드리지 않는다.
    empty = conn.execute(
        """delete from question_clusters c
           where c.id = any(%s) and c.status = 'OPEN'
             and not exists (select 1 from questions q where q.cluster_id = c.id)
           returning c.id""",
        (created_ids,),
    ).fetchall()
    conn.commit()
    return {
        "newClusters": len(created_ids) - len(empty),
        "assigned": assigned,
        "pending": len(pending),
        "theta": cfg.cluster_theta,
    }


def rename(conn: psycopg.Connection, cfg: config.Config, cluster_id: int, text: str) -> dict:
    """대표 문장을 크리에이터가 고친다. 🔴 자동 재작성은 하지 않는다 — 사람이 고친 문장을
    다음 집계가 덮으면 고친 의미가 없다.

    문장이 바뀌면 벡터도 다시 계산해야 한다. 안 하면 다음 집계에서 옛 문장 기준으로 붙는다.
    """
    clean = text.strip()
    if not clean:
        raise ClusterError("대표 문장은 비울 수 없다")
    clean = clean[:CANONICAL_MAX_LEN]
    row = conn.execute(
        """update question_clusters set canonical_text = %s, updated_at = now()
           where id = %s returning *""",
        (clean, cluster_id),
    ).fetchone()
    if row is None:
        raise ClusterError(f"cluster {cluster_id} 없음")
    conn.execute(
        "delete from embeddings where kind = 'cluster' and ref_id = %s", (cluster_id,)
    )
    embeddings.embed_and_store(
        conn, cfg, "cluster", [(cluster_id, clean)], embeddings.SIMILARITY,
        source_id=row["source_id"],
    )
    conn.commit()
    return dict(row)
