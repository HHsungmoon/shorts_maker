"""데모 리허설 도구 (update_plan M9, upgrade_plan §3).

리허설은 **같은 자리에서 여러 번** 돌려야 한다. 그러려면 매번 같은 출발점으로 되돌릴 수 있어야
하고, 무대에 서기 전에 "지금 준비가 됐는가" 를 한 번에 볼 수 있어야 한다. 셋을 만든다.

  `reset`  질문·클러스터·답변을 지우고 **영상 준비물은 남긴다**
  `seed`   질문을 좋아요와 함께 미리 넣는다
  `unseed` **시드한 것만** 골라 지운다 — 진짜 시청자 질문은 남는다
  `check`  데모 시나리오 8단계의 전제가 갖춰졌는지 점검한다

🔴 **pg_dump 를 쓰지 않는다.** 앱 이미지에 없기도 하지만, 그보다 **되돌리고 싶은 것이 DB 전체가
아니기 때문**이다. 전사·구간 분할·임베딩은 편당 수십 분이 드는 자산이라 리허설마다 날리면 안 되고,
질문·클러스터·클립만 지워야 한다. 표적 초기화가 덤프·복원보다 이 일에 맞는다.
"""

import json
import pathlib

import psycopg

# 리허설에서 지워지는 것. 🔴 순서가 있다 — 외래키를 거스르면 중간에 멈춘다.
# `sources`·`chunks`·`utterances`·`segments` 와 구간 임베딩은 **건드리지 않는다.**
RESET_NOTE = "질문·클러스터·답변·클립·이벤트를 지운다. 영상 준비물(청크·발화·구간·구간 임베딩)은 남는다"

# 🔴 시드가 넣은 행의 표식. `viewer_id` 에 붙여 두면 리허설 뒤에 **시드한 것만** 골라 지울 수 있다.
# 진짜 시청자 질문과 섞이면 수요 순위가 거짓이 되고, 그건 이 제품이 보여주려는 바로 그 숫자다.
SEED_PREFIX = "seed-"


def reset(conn: psycopg.Connection, source_id: int) -> dict[str, int]:
    """이 영상을 **질문이 들어오기 직전 상태**로 되돌린다.

    🔴 `stage_calls` 를 **run 보다 먼저** 지운다. `stage_calls.run_id` 가 `on delete set null`
    이라 run 을 먼저 지우면 답변에 쓴 호출 기록이 고아로 남고, 그 순간 그것들이 **회차 준비
    비용으로 둔갑한다**(`report.cost_split` 은 run_id 로 가른다). 리허설을 돌릴수록 준비비가
    불어나는 거짓 숫자가 만들어진다.
    """
    counted: dict[str, int] = {}

    def run(label: str, sql: str, params: tuple) -> None:
        counted[label] = conn.execute(sql, params).rowcount

    runs = "(select id from runs where source_id = %s)"

    # ① 답변에 쓴 호출 기록 — run 을 지우기 전에.
    run("stage_calls", f"delete from stage_calls where run_id in {runs}", (source_id,))
    # ② 이 영상에서 일어난 시청자 이벤트.
    run("viewer_events", "delete from viewer_events where source_id = %s", (source_id,))
    # ③ 질문·클러스터의 벡터. 구간(segment) 벡터는 남긴다 — 그게 비싼 자산이다.
    run(
        "embeddings",
        """delete from embeddings e where
           (e.kind = 'cluster' and e.ref_id in (select id from question_clusters where source_id = %s))
           or (e.kind = 'question' and e.ref_id in (select id from questions where source_id = %s))""",
        (source_id, source_id),
    )
    # ④ 질문과 묶음. 좋아요·클러스터는 캐스케이드로 따라간다.
    run("questions", "delete from questions where source_id = %s", (source_id,))
    run("question_clusters", "delete from question_clusters where source_id = %s", (source_id,))
    # ⑤ run — 클립·조각·후보·리뷰가 캐스케이드로 따라간다.
    run("runs", "delete from runs where source_id = %s", (source_id,))
    return counted


def load_questions(path: pathlib.Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload["questions"] if isinstance(payload, dict) else payload
    return [q for q in items if (q.get("text") or "").strip()]


def seed(
    conn: psycopg.Connection, source_id: int, questions: list[dict], likes_per_group: int = 3
) -> dict[str, int]:
    """질문을 **묶이지 않은 상태로** 넣는다. 좋아요도 함께.

    🔴 [집계]는 여기서 돌리지 않는다. 데모 2단계가 그 버튼을 누르는 장면이라 미리 눌러 두면
    보여줄 것이 사라진다.

    좋아요를 넣는 이유: 수요 순 정렬이 화면에서 **보여야** 한다. 전부 0이면 목록이 그냥 입력
    순서로 보이고, "몇 명이 같은 것을 물었나" 라는 이 제품의 요지가 화면에 안 나타난다.
    같은 group 의 질문일수록 좋아요를 더 준다 — 묶였을 때 수요가 커 보이게.
    """
    inserted = 0
    liked = 0
    seen_groups: dict[str, int] = {}
    for n, item in enumerate(questions):
        text = item["text"].strip()
        group = item.get("group") or "etc"
        # 🔴 표식을 남긴다. 이게 없으면 리허설 뒤에 **시드한 것만 골라 지울 수가 없고**,
        # 진짜 시청자 질문까지 함께 날리게 된다(`unseed`).
        viewer = f"{SEED_PREFIX}{source_id}-{n:03d}"
        question_id = conn.execute(
            "insert into questions (source_id, text, viewer_id) values (%s, %s, %s) returning id",
            (source_id, text, viewer),
        ).fetchone()["id"]
        inserted += 1
        # 그룹 안에서 앞쪽 질문일수록 좋아요가 많다. 수요 순 정렬이 눈에 보이게.
        rank = seen_groups.get(group, 0)
        seen_groups[group] = rank + 1
        for k in range(max(0, likes_per_group - rank)):
            conn.execute(
                "insert into question_likes (question_id, viewer_id) values (%s, %s)",
                (question_id, f"{SEED_PREFIX}like-{n:03d}-{k}"),
            )
            liked += 1
    return {"questions": inserted, "likes": liked}


def unseed(conn: psycopg.Connection, source_id: int) -> dict[str, int]:
    """**시드한 것만** 지운다. 진짜 시청자 질문은 남는다.

    🔴 `reset` 과 다른 일이다. reset 은 회차를 통째로 되돌리고, 이건 리허설 흔적만 걷어낸다 —
    실제 시청자가 남긴 질문이 이미 있는 영상에서 리허설을 돌린 뒤에 쓴다.

    좋아요는 `question_likes` 의 캐스케이드로 따라간다. 클러스터에 이미 붙은 시드 질문이 있으면
    그 묶음의 수요가 줄어드는데, 그게 맞다 — 시드는 처음부터 없던 것으로 쳐야 한다.
    """
    counted: dict[str, int] = {}
    counted["question_likes"] = conn.execute(
        "delete from question_likes where viewer_id like %s", (SEED_PREFIX + "%",)
    ).rowcount
    counted["questions"] = conn.execute(
        "delete from questions where source_id = %s and viewer_id like %s",
        (source_id, SEED_PREFIX + "%"),
    ).rowcount
    # 시드 질문만 있던 묶음은 빈 껍데기가 된다. 답을 만든 적 없는 것만 치운다 —
    # run 이 붙은 묶음은 지우면 그 비용 기록까지 사라진다.
    counted["question_clusters"] = conn.execute(
        """delete from question_clusters c where c.source_id = %s and c.run_id is null
           and not exists (select 1 from questions q where q.cluster_id = c.id)""",
        (source_id,),
    ).rowcount
    return counted


# 데모 시나리오(tease §10-2) 8단계의 전제. 🔴 화면에서 보여줄 순서 그대로 둔다.
def check(conn: psycopg.Connection, source_id: int) -> list[dict]:
    """각 단계가 지금 돌아갈 수 있는지. `(단계, 통과, 설명)` 목록."""
    source = conn.execute(
        "select id, title, channel, published, youtube_id from sources where id = %s", (source_id,)
    ).fetchone()
    if source is None:
        return [{"step": 0, "name": "영상", "ok": False, "note": f"source {source_id} 없음"}]

    one = lambda sql, params=(): conn.execute(sql, params).fetchone()["n"]  # noqa: E731
    segments = one(
        "select count(*) as n from segments sg join chunks ch on ch.id = sg.chunk_id"
        " where ch.source_id = %s", (source_id,)
    )
    unclustered = one(
        "select count(*) as n from questions where source_id = %s and cluster_id is null", (source_id,)
    )
    open_clusters = one(
        "select count(*) as n from question_clusters where source_id = %s and status = 'OPEN'",
        (source_id,),
    )
    published_clips = one(
        """select count(*) as n from clips c join runs r on r.id = c.run_id
           where r.source_id = %s and c.published_at is not null""", (source_id,)
    )
    siblings = one(
        """select count(*) as n from sources other join sources mine on mine.id = %s
           where other.id <> %s and other.published
             and mine.channel is not null and other.channel = mine.channel""",
        (source_id, source_id),
    )
    sibling_ready = one(
        """select count(*) as n from segments sg
           join chunks ch on ch.id = sg.chunk_id
           join sources other on other.id = ch.source_id
           join sources mine on mine.id = %s
           where other.id <> %s and other.published
             and mine.channel is not null and other.channel = mine.channel""",
        (source_id, source_id),
    )
    events = one("select count(*) as n from viewer_events where source_id = %s", (source_id,))
    costed = one(
        "select count(*) as n from stage_calls where source_id = %s and input_tokens > 0", (source_id,)
    )
    seekable = one(
        """select count(*) as n from clips c join runs r on r.id = c.run_id
           where r.source_id = %s and c.published_at is not null and c.start_sec > 0""",
        (source_id,),
    )

    return [
        {"step": 1, "name": "시청자 화면에 영상이 보인다",
         "ok": bool(source["published"]) and segments > 0,
         "note": f"발행={'예' if source['published'] else '아니오'} · 구간 {segments}개"},
        {"step": 2, "name": "질문을 남기고 [집계]로 묶는다", "ok": unclustered > 0,
         "note": f"묶이지 않은 질문 {unclustered}개 — 0이면 누를 것이 없다 (`sm tease seed`)"},
        {"step": 3, "name": "숏폼이 보인다", "ok": published_clips > 0,
         "note": f"발행된 클립 {published_clips}개"},
        {"step": 4, "name": "[답하기] 라이브", "ok": open_clusters > 0,
         "note": f"답을 기다리는 묶음 {open_clusters}개 — 🔴 라이브라 시간이 걸린다(실측 최대 428초)"},
        {"step": 5, "name": "CTA 로 원본의 그 초로 이동", "ok": bool(source["youtube_id"]) and seekable > 0,
         "note": f"youtube_id={'있음' if source['youtube_id'] else '없음'} · 이동 지점이 있는 클립 {seekable}개"},
        {"step": 6, "name": "이 영상엔 없고 저 편에 있다", "ok": siblings > 0 and sibling_ready > 0,
         "note": (f"같은 채널 발행 영상 {siblings}편 · 구간까지 준비된 것 {sibling_ready}건"
                  if siblings else "🔴 같은 채널 영상이 한 편도 없다 — 이 단계는 못 보여준다")},
        {"step": 7, "name": "퍼널 리포트", "ok": True,
         "note": f"이벤트 {events}건 — 0이어도 표는 그려진다"},
        {"step": 8, "name": "비용", "ok": costed > 0,
         "note": f"토큰이 기록된 호출 {costed}건"},
    ]
