"""시청자 API — `/api/watch/**`. **인증 없음. 인터넷에 열려 있다.**

스튜디오 라우터(`studio.py`)와 파일을 나눈 이유는 인증 때문이다. 거기는 라우터 레벨로
`require_auth` 가 걸려 있고 여기는 안 걸린다. 한 라우터에서 "인증됐으면 전부, 아니면
발행분만" 을 분기하면 그 분기 한 줄이 유일한 울타리가 된다(tease §7-3).

여기 라우트를 추가할 때 지켜야 하는 것 셋:

1. 🔴 **읽기는 발행된 것만.** 영상은 `sources.published`, 클립은 `clips.published_at is not null`.
   미발행은 403 이 아니라 **404** 다 — 존재 자체를 알리지 않는다.
2. 🔴 **외부 API 를 부르지 않는다.** 질문 등록은 insert 만이고 임베딩도 LLM 도 없다
   (update_plan D11). 공개 경로에서 요청마다 유료 API 를 부르면 그게 공격면이다.
   묶기는 크리에이터가 스튜디오에서 [집계]를 눌러야 돈다.
3. 🔴 **쓰기는 레이트리밋.** `viewers.check_rate`.

`tests/http/test_watch.py` 가 셋 다 지킨다.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..answers import events, viewers
from . import deps
from .deps import connect, rows

# 라우터 레벨 의존성은 **쿠키 발급**뿐이다 — 목록만 봐도 시청자 id 가 생긴다.
# 인증 의존성은 없다. 그게 이 파일의 존재 이유다.
router = APIRouter(prefix="/api/watch", dependencies=[Depends(viewers.viewer_id)])

# 한 화면에 한 번에 내려보내는 질문 수. 넘으면 잘라서 보낸다 — 지금은 페이지네이션을 넣지
# 않는다(영상 하나에 질문 수백 개가 쌓이면 그때 클러스터가 목록의 단위가 된다, M3).
QUESTION_LIMIT = 200


class QuestionIn(BaseModel):
    # 🔴 200자 상한(tease §7-4). 비속어 필터는 넣지 않는다 — 크리에이터가 거절하면 된다.
    text: str = Field(min_length=1, max_length=200)


class EventIn(BaseModel):
    kind: str
    sourceId: int | None = None
    clipId: int | None = None
    payload: dict | None = None


def _published_source(conn, source_id: int) -> dict:
    found = conn.execute(
        "select id, title, duration_sec, youtube_id, channel, origin, context"
        " from sources where id = %s and published",
        (source_id,),
    ).fetchone()
    if found is None:
        # 🔴 미발행도 404. 403 이면 "그 id 는 있다"를 알려주는 셈이다.
        raise HTTPException(404, "영상을 찾을 수 없습니다")
    return dict(found)


@router.get("/sources")
def list_sources() -> list[dict]:
    """발행된 영상 목록. 질문이 많은 순 — 시청자에게는 "말이 오가는 영상"이 먼저다."""
    with connect() as conn:
        return rows(
            conn,
            """select s.id, s.title, s.duration_sec, s.youtube_id, s.channel,
                      count(distinct q.id) as question_count,
                      count(distinct c.id) filter (where c.published_at is not null) as published_clip_count
               from sources s
               left join questions q on q.source_id = s.id
               left join runs r on r.source_id = s.id
               left join clips c on c.run_id = r.id
               where s.published
               group by s.id
               order by question_count desc, s.id desc""",
        )


@router.get("/sources/{source_id}")
def get_source(source_id: int, viewer: str = Depends(viewers.viewer_id)) -> dict:
    """영상 + 질문 목록(좋아요 순) + 발행된 클립.

    `likedByMe` 를 서버가 계산해 주는 이유: 쿠키가 httpOnly 라 브라우저가 자기 id 를 못 읽는다.
    """
    with connect() as conn:
        source = _published_source(conn, source_id)
        questions = rows(
            conn,
            """select q.id, q.text, q.cluster_id, q.created_at,
                      count(l.id) as likes,
                      bool_or(l.viewer_id = %s) as liked_by_me
               from questions q
               left join question_likes l on l.question_id = q.id
               where q.source_id = %s
               group by q.id
               order by likes desc, q.id desc
               limit %s""",
            (viewer, source_id, QUESTION_LIMIT),
        )
        for question in questions:
            # bool_or 은 좋아요가 하나도 없으면 null 을 준다.
            question["liked_by_me"] = bool(question["liked_by_me"])
        # 🔴 클립에 **그 질문**을 붙여 준다. 시청자에게 클립만 보여주면 "이게 왜 여기 있지" 가 된다 —
        # 이 제품의 요지는 "당신이 물어본 것에 대한 답" 이고, 그 연결이 화면에 보여야 한다.
        clips = rows(
            conn,
            """select c.id, c.total_sec, c.published_at, c.question_cluster_id,
                      -- 🔴 제목은 컬럼이다. 질문에서 나온 클립이든 크리에이터가 직접 뽑은 것이든
                      -- 같은 목록에 올라가고, 크리에이터가 고친 문장이 있으면 그게 우선이다.
                      coalesce(c.title, qc.canonical_text) as title,
                      qc.canonical_text as question,
                      (select count(*) from questions q where q.cluster_id = qc.id) as asked_by
               from clips c
               join runs r on r.id = c.run_id
               left join question_clusters qc on qc.id = c.question_cluster_id
               where r.source_id = %s and c.published_at is not null
               order by c.published_at desc""",
            (source_id,),
        )
        # 답할 구간이 없다고 판정된 질문. 다른 편을 가리킬 수 있으면 그 영상도 함께(발행된 것만).
        unanswerable = rows(
            conn,
            """select qc.id, qc.canonical_text as question,
                      s.id as suggested_source_id, s.title as suggested_title
               from question_clusters qc
               left join sources s on s.id = qc.suggested_source_id and s.published
               where qc.source_id = %s and qc.status = 'UNANSWERABLE'
               order by qc.id desc""",
            (source_id,),
        )
        return {
            "source": source, "questions": questions, "clips": clips,
            "unanswerable": unanswerable,
        }


@router.post("/sources/{source_id}/questions")
def add_question(
    source_id: int, body: QuestionIn, request: Request, viewer: str = Depends(viewers.viewer_id)
) -> dict:
    """질문 등록. 🔴 **insert 만 한다** — 임베딩도 LLM 도 부르지 않는다(update_plan D11).

    묶기는 크리에이터가 스튜디오에서 [집계]를 누를 때 일어난다. 그래서 `cluster_id` 는
    null 로 시작하고, 이 경로에는 외부 호출이 0회다.
    """
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "질문을 입력하세요")
    viewers.check_rate(request, viewer, "question")
    with connect() as conn:
        _published_source(conn, source_id)
        question_id = conn.execute(
            "insert into questions (source_id, text, viewer_id) values (%s, %s, %s) returning id",
            (source_id, text, viewer),
        ).fetchone()["id"]
        events.record(conn, viewer, "question_post", source_id=source_id)
        conn.commit()
    return {"questionId": question_id}


@router.post("/questions/{question_id}/like")
def toggle_like(
    question_id: int, request: Request, viewer: str = Depends(viewers.viewer_id)
) -> dict:
    """좋아요 토글. 한 번 더 누르면 취소된다.

    `unique(question_id, viewer_id)` 가 중복을 막지만, 토글이라 여기서는 지우고 넣는다 —
    제약은 동시에 두 요청이 들어왔을 때의 마지막 방어선이다.
    """
    viewers.check_rate(request, viewer, "like")
    with connect() as conn:
        # 발행된 영상의 질문만. 미발행 영상의 질문에 좋아요를 눌러 존재를 확인할 수 없게 한다.
        found = conn.execute(
            """select q.id from questions q join sources s on s.id = q.source_id
               where q.id = %s and s.published""",
            (question_id,),
        ).fetchone()
        if found is None:
            raise HTTPException(404, "질문을 찾을 수 없습니다")
        removed = conn.execute(
            "delete from question_likes where question_id = %s and viewer_id = %s returning id",
            (question_id, viewer),
        ).fetchone()
        liked = removed is None
        if liked:
            conn.execute(
                "insert into question_likes (question_id, viewer_id) values (%s, %s)",
                (question_id, viewer),
            )
            events.record(conn, viewer, "like", source_id=None)
        likes = conn.execute(
            "select count(*) as n from question_likes where question_id = %s", (question_id,)
        ).fetchone()["n"]
        conn.commit()
    return {"liked": liked, "likes": likes}


@router.post("/events")
def add_event(body: EventIn, request: Request, viewer: str = Depends(viewers.viewer_id)) -> dict:
    """브라우저가 보내는 퍼널 이벤트(재생·완주·CTA·원본 이동).

    🔴 `question_post`·`like` 는 서버가 직접 넣는다 — 클라이언트가 위조해 넣으면 퍼널 수치가
    거짓이 된다. 그래서 여기서 받는 종류를 좁힌다(`events.KIND_FROM_CLIENT`).
    """
    if body.kind not in events.KIND_FROM_CLIENT:
        raise HTTPException(422, f"보낼 수 없는 이벤트입니다: {body.kind}")
    # 재생 이벤트는 초 단위로 여러 번 올 수 있어 좋아요와 같은 한도를 쓴다.
    viewers.check_rate(request, viewer, "like")
    with connect() as conn:
        events.record(conn, viewer, body.kind, body.sourceId, body.clipId, body.payload)
        conn.commit()
    return {"ok": True}


@router.get("/clips/{clip_id}/file")
def get_clip_file(clip_id: int) -> Any:
    """🔴 **발행된 클립만.** 스튜디오의 `/api/clips/{id}/file` 은 인증 뒤에서 미발행도 준다
    (프리뷰용) — 그래서 경로를 나눴다(tease §7-3). 미발행은 404 다.
    """
    with connect() as conn:
        clip = conn.execute(
            "select path, rendered from clips where id = %s and published_at is not null",
            (clip_id,),
        ).fetchone()
    if clip is None or not clip["rendered"] or not clip["path"]:
        raise HTTPException(404, "클립을 찾을 수 없습니다")
    path = deps.cfg.work_file(clip["path"])
    if not path.is_file():
        raise HTTPException(404, "클립을 찾을 수 없습니다")
    return FileResponse(path, media_type="video/mp4", filename=path.name)
