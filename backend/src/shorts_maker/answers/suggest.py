"""질문을 남기는 순간, 이미 발행된 숏폼 중 비슷한 궁금증에 답한 것을 추천한다 (update_plan D13).

이미 답이 나가 있는데 시청자가 그걸 모르고 같은 걸 또 묻는 게 가장 아까운 순간이다. 그 순간에
"그 답, 여기 있어요" 를 보여준다.

## 🔴 공개 경로에서 외부 API 를 부르는 유일한 자리다

원래 질문 등록은 insert 만이었다(D11). 여기서 그 규칙을 **이 한 곳에서만** 푼다. 위험은 둘이었고
둘 다 구조로 막는다.

  1. **질문 등록이 멈춘다.** `gemini.embed_texts` 는 분당 한도에 걸리면 서버가 말한 만큼(최대 65초)
     기다리며 6번까지 다시 부른다. 크리에이터 작업엔 맞지만 시청자 요청이 1분 멈추면 안 된다
     → 재시도 없는 `gemini.embed_once` + 제한 시간(`SHORTS_SUGGEST_TIMEOUT_SEC`)
  2. **도배가 크리에이터의 분당 임베딩 한도(100)를 먹는다.** 먹히면 [집계]와 답하기가 멈춘다
     → 공개 경로 전용 분당 상한(`SHORTS_SUGGEST_PER_MIN`) + **비교할 발행 숏폼이 없으면 호출 0회**

그리고 **질문은 호출보다 먼저 커밋된다**(http/watch.py). 여기서 무엇이 실패하든 질문은 이미 남았고,
실패하면 추천만 조용히 빠진다. LLM(generate_content)은 여전히 0회다.

## 무엇과 무엇을 비교하나

질문(`RETRIEVAL_QUERY`) ↔ 클립의 **실제 대사**(`RETRIEVAL_DOCUMENT`). 실측(2026-09-15, 질문 8 · 숏폼 2):

| 비교 대상 | 맞는 연결 최저 | 붙으면 안 되는 것 최고 | 틈 |
|---|---|---|---|
| 클립 제목 | 0.666 | 0.745 | -0.079 |
| 구간 설명 | 0.707 | 0.744 | -0.038 |
| 실제 대사 | 0.735 | 0.720 | +0.015 |

🔴 묶기용 벡터(`SEMANTIC_SIMILARITY`, θ 0.85)를 쓰지 않는다. 그걸로 제목과 비교하면 "쏘카 복지는
어떤가요?" 가 기술문화 숏폼에 0.870 으로 붙었다. 묶기는 "무엇을 만들까" 를 정하는 일이고 이건
"이미 나간 답을 찾는" 일이라 도구가 다르다.

🔴 **틈이 0.015 다.** 그래서 화면은 "답입니다" 가 아니라 "비슷한 궁금증에 답한 숏폼이 있어요" 로
말하고, 질문을 묶음에 자동으로 붙이지 않는다. 기준선은 평가 세트로 다시 정한다(upgrade_plan §2-9).
"""

import logging
import threading
import time

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import gemini
from . import embeddings

KIND = "clip"

# 벡터로 만들 대사의 최대 글자 수. 30초 숏폼의 대사는 수백 자라 대개 다 들어가고, 옛 기준 경로의
# 60초 클립도 이 안이다. 임베딩 입력 한도보다 한참 아래라 잘리는 쪽이 모델이 아니라 우리다.
DOC_CHARS = 1500

# 공개 경로 전용 분당 상한. 🔴 프로세스 메모리에 있다 — 시청자 레이트리밋(viewers.py)과 같은 절충이고,
# 워커가 여럿이 되면 워커마다 따로 센다. FastAPI 는 동기 핸들러를 스레드 풀에서 돌리므로 잠금이 필요하다.
BUDGET_WINDOW_SEC = 60
_budget: list[float] = []
_budget_lock = threading.Lock()


def reset_budget() -> None:
    """테스트용. 상한 창을 비운다."""
    with _budget_lock:
        _budget.clear()


def _take_budget(per_min: int) -> bool:
    """이번 호출을 상한 안에 넣을 수 있으면 자리를 차지하고 True."""
    if per_min <= 0:
        return False
    now = time.monotonic()
    with _budget_lock:
        _budget[:] = [t for t in _budget if now - t < BUDGET_WINDOW_SEC]
        if len(_budget) >= per_min:
            return False
        _budget.append(now)
        return True


# ---------------------------------------------------------------- 색인 (크리에이터 경로)

def clip_text(conn: psycopg.Connection, clip_id: int) -> str | None:
    """클립이 실제로 말하는 대사. 없으면 None.

    조각이 있으면(답하기 경로) 조각마다 발화 번호 범위로 모은다 — 조합 클립이면 떨어진 두 곳의
    대사가 이어진다. 조각이 없는 옛 기준 경로 클립은 **시간 범위**로 모은다(발화 번호를 저장하지
    않았다). 경계 발화가 반올림으로 빠지지 않게 0.5초 여유를 준다.
    """
    parts = conn.execute(
        """select sg.chunk_id, p.start_utterance_idx, p.end_utterance_idx
           from clip_parts p join segments sg on sg.id = p.segment_id
           where p.clip_id = %s order by p.ordinal""",
        (clip_id,),
    ).fetchall()
    texts: list[str] = []
    if parts:
        for part in parts:
            texts += [
                r["text"] for r in conn.execute(
                    """select text from utterances where chunk_id = %s and idx between %s and %s
                       order by idx""",
                    (part["chunk_id"], part["start_utterance_idx"], part["end_utterance_idx"]),
                )
            ]
    else:
        clip = conn.execute(
            """select c.start_sec, c.end_sec, sg.chunk_id from clips c
               join segments sg on sg.id = c.segment_id where c.id = %s""",
            (clip_id,),
        ).fetchone()
        if clip is None:
            return None
        texts = [
            r["text"] for r in conn.execute(
                """select text from utterances where chunk_id = %s
                   and start_sec >= %s - 0.5 and end_sec <= %s + 0.5 order by idx""",
                (clip["chunk_id"], clip["start_sec"], clip["end_sec"]),
            )
        ]
    joined = " ".join(t.strip() for t in texts if t and t.strip())
    return joined[:DOC_CHARS] or None


def index_clip(conn: psycopg.Connection, cfg: config.Config, clip_id: int) -> str:
    """이 클립의 대사를 추천용으로 임베딩한다. 이미 있으면 건너뛴다.

    결과: `indexed` · `empty`(대사가 없다) · `failed`(임베딩 실패) · `missing`(클립이 없다).

    🔴 **실패를 올리지 않는다.** 발행 직후에 부르는데, 임베딩 할당량이 떨어졌다고 크리에이터의 발행이
    실패하면 안 된다. 실패는 `stage_calls` 에 남기고, 빠진 것은 `sm answers index-clips` 로 채운다.
    호출부는 **커밋된 상태에서** 부른다 — 실패하면 여기서 롤백한다.
    """
    row = conn.execute(
        "select r.source_id from clips c join runs r on r.id = c.run_id where c.id = %s", (clip_id,)
    ).fetchone()
    if row is None:
        return "missing"
    text = clip_text(conn, clip_id)
    if not text:
        return "empty"
    try:
        embeddings.embed_and_store(
            conn, cfg, KIND, [(clip_id, text)], embeddings.DOCUMENT, source_id=row["source_id"]
        )
    except gemini.GeminiError as exc:
        conn.rollback()
        conn.execute(
            """insert into stage_calls (source_id, stage, model, error, params)
               values (%s, 'embed', %s, %s, %s)""",
            (row["source_id"], cfg.embed_model, str(exc)[:1000],
             Jsonb({"kind": KIND, "purpose": "index", "clip_id": clip_id})),
        )
        return "failed"
    return "indexed"


def index_published(conn: psycopg.Connection, cfg: config.Config, source_id: int | None = None) -> dict:
    """발행됐는데 아직 벡터가 없는 클립을 전부 채운다. 클립마다 커밋한다."""
    rows = conn.execute(
        """select c.id from clips c join runs r on r.id = c.run_id
           where c.published_at is not null and (%s::int is null or r.source_id = %s)
             and not exists (
               select 1 from embeddings e where e.kind = %s and e.ref_id = c.id
                 and e.model = %s and e.dim = %s and e.task_type = %s)
           order by c.id""",
        (source_id, source_id, KIND, cfg.embed_model, cfg.embed_dim, embeddings.DOCUMENT),
    ).fetchall()
    conn.commit()
    counted = {"indexed": 0, "empty": 0, "failed": 0, "missing": 0}
    for row in rows:
        counted[index_clip(conn, cfg, row["id"])] += 1
        conn.commit()
    return counted


# ---------------------------------------------------------------- 추천 (공개 경로)

def _record(conn: psycopg.Connection, cfg: config.Config, source_id: int, called: bool,
            latency_ms: int | None, error: str | None, params: dict) -> None:
    """🔴 모든 단계는 stage_calls 에 남긴다. 기준선을 다시 정할 때 이 줄들이 실사용 데이터다."""
    conn.execute(
        """insert into stage_calls (source_id, stage, model, latency_ms, error, params)
           values (%s, 'embed', %s, %s, %s, %s)""",
        (source_id, cfg.embed_model if called else None, latency_ms, error, Jsonb(params)),
    )
    conn.commit()


def match(
    conn: psycopg.Connection, cfg: config.Config, source_id: int, text: str,
    question_id: int | None = None,
) -> list[tuple[int, float]]:
    """이 영상의 발행 숏폼 중 질문과 가까운 것. `(클립 id, 유사도)` 를 높은 순으로, 최대 `suggest_max` 개.

    🔴 **어떤 경우에도 올리지 않는다.** 실패하면 빈 목록이다 — 질문은 이미 커밋됐다.
    """
    if cfg.suggest_per_min <= 0 or cfg.suggest_max <= 0:
        return []
    rows = conn.execute(
        """select c.id, e.vector from clips c
           join runs r on r.id = c.run_id
           join embeddings e on e.kind = %s and e.ref_id = c.id
                and e.model = %s and e.dim = %s and e.task_type = %s
           where r.source_id = %s and c.published_at is not null and c.rendered
           order by c.id""",
        (KIND, cfg.embed_model, cfg.embed_dim, embeddings.DOCUMENT, source_id),
    ).fetchall()
    # 네트워크 호출 앞에서 읽기 트랜잭션을 끊는다 — 제한 시간 동안 연결을 붙잡고 있지 않게.
    conn.commit()
    if not rows:
        # 🔴 비교할 것이 없으면 **부르지 않는다.** 발행 숏폼이 없는 영상에서는 외부 호출이 0회다.
        return []

    params: dict = {"purpose": "suggest", "question_id": question_id, "compared": len(rows)}
    if not _take_budget(cfg.suggest_per_min):
        _record(conn, cfg, source_id, False, None, None, {**params, "skipped": "budget"})
        return []
    try:
        vector, latency_ms = gemini.embed_once(cfg, text, embeddings.QUERY, cfg.suggest_timeout_sec)
    except gemini.GeminiError as exc:
        _record(conn, cfg, source_id, True, None, str(exc)[:1000], params)
        return []

    query = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(query))
    if query.shape != (cfg.embed_dim,) or norm == 0.0:
        _record(conn, cfg, source_id, True, latency_ms, f"벡터 모양이 이상하다: {query.shape}", params)
        return []
    scores = embeddings.cosine(
        (query / norm).reshape(1, -1),
        embeddings.from_blobs([r["vector"] for r in rows], cfg.embed_dim),
    )[0]
    ranked = sorted(((float(s), r["id"]) for s, r in zip(scores, rows)), reverse=True)
    picked = [(clip_id, score) for score, clip_id in ranked if score >= cfg.suggest_min_sim]
    picked = picked[: cfg.suggest_max]
    _record(conn, cfg, source_id, True, latency_ms, None, {
        **params,
        # 🔴 고르지 않았을 때도 최고점을 남긴다. "왜 추천이 안 떴나" 와 기준선 재조정의 근거다.
        "top": round(ranked[0][0], 4),
        "top_clip_id": ranked[0][1],
        "picked": [clip_id for clip_id, _ in picked],
        "min_sim": cfg.suggest_min_sim,
    })
    logging.info("suggest: question %s → %s (top %.3f)", question_id, picked, ranked[0][0])
    return picked
