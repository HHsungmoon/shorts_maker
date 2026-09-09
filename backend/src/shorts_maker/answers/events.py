"""시청자 이벤트 적재 (tease §9). append-only.

"숏폼이 원본 유입을 늘린다"는 이 표에 쌓인 행 없이는 주장할 수 없다. 그래서 지금부터
모은다 — 퍼널의 뒷부분(재생·완주)은 클립이 생기는 M4 부터 채워지지만, 앞부분(질문·좋아요·
CTA 클릭)은 지금 화면에서 이미 일어난다.

🔴 kind 검증은 **스키마 CHECK 가 한다**(001_baseline.sql). 여기서 목록을 다시 적으면 두 곳이
어긋난다. 대신 사용자가 보낼 수 있는 것만 KIND_FROM_CLIENT 로 좁힌다 — 서버가 직접 넣는
`question_post`·`like` 를 클라이언트가 위조해서 넣으면 퍼널 수치가 거짓이 된다.
"""

import psycopg
from psycopg.types.json import Jsonb

# 클라이언트(브라우저)가 `POST /api/watch/events` 로 보낼 수 있는 것. 나머지는 서버가 넣는다.
KIND_FROM_CLIENT = frozenset({"short_play", "short_complete", "cta_click", "origin_seek", "origin_play"})


def record(
    conn: psycopg.Connection,
    viewer_id: str,
    kind: str,
    source_id: int | None = None,
    clip_id: int | None = None,
    payload: dict | None = None,
) -> None:
    """이벤트 한 줄. 커밋은 호출부가 한다 — 질문 등록처럼 같은 트랜잭션에 묶여야 하는 곳이 있다."""
    conn.execute(
        "insert into viewer_events (viewer_id, source_id, clip_id, kind, payload)"
        " values (%s, %s, %s, %s, %s)",
        (viewer_id, source_id, clip_id, kind, Jsonb(payload) if payload else None),
    )


# 퍼널의 순서. 이 배열 순서가 화면의 열 순서이고, 뒤로 갈수록 수가 줄어야 정상이다
# (tease §9). 늘어나면 계측이 틀린 것이다 — 예를 들어 완주가 재생보다 많으면 `short_play`
# 를 못 받고 있다는 뜻이다.
FUNNEL_KINDS: tuple[str, ...] = (
    "short_play",
    "short_complete",
    "cta_click",
    "origin_seek",
    "origin_play",
)

# 🔴 세는 단위는 **사람(viewer_id) 수**이지 이벤트 수가 아니다. 같은 사람이 숏폼을 세 번
# 돌려 보면 재생 이벤트는 3줄이지만 "본 사람"은 1명이다. 퍼널은 단계마다 사람이 얼마나
# 남는지를 보는 도구라서 이벤트 수로 세면 앞 단계가 부풀어 유입률이 실제보다 낮아 보인다.
_PER_KIND = ", ".join(
    f"count(distinct e.viewer_id) filter (where e.kind = '{kind}') as {kind}"
    for kind in FUNNEL_KINDS
)


def funnel(conn: psycopg.Connection, source_id: int) -> dict:
    """이 영상의 숏폼별 퍼널 + 영상 전체 합계 (tease §9, update_plan M6b).

    **묶는 단위는 클러스터가 아니라 클립이다.** 계획 문서는 "클러스터별" 이라고 적었지만,
    클러스터가 없는 클립(크리에이터가 자기 기준으로 뽑은 것)도 같은 목록에 발행되고
    시청자에게는 구분 없이 보인다 — 클러스터로 묶으면 그것들이 표에서 사라진다.
    질문에서 나온 클립은 클러스터가 정확히 하나라(불변식 I1) 클립 단위가 클러스터 단위를
    포함한다.

    🔴 **비율을 계산하지 않는다.** 3명 중 1명을 33% 로 적으면 거짓말이 된다(update_plan M6b).
    비율이 필요해지는 건 분모가 수백이 된 다음이고, 그때 화면에서 판단한다.
    """
    clips = conn.execute(
        f"""select c.id as clip_id,
                   c.question_cluster_id as cluster_id,
                   c.published_at,
                   c.total_sec,
                   -- 제목 규칙은 시청자 화면과 같다(watch.get_source) — 크리에이터가 고친
                   -- 문장이 있으면 그것, 없으면 질문 대표 문장.
                   coalesce(c.title, qc.canonical_text) as label,
                   qc.canonical_text as question,
                   {_PER_KIND}
            from clips c
            join runs r on r.id = c.run_id
            left join question_clusters qc on qc.id = c.question_cluster_id
            -- 🔴 left join 이다. 이벤트가 없는 클립은 0 으로 보여야 한다 — 표에서 빠지면
            -- "아무도 안 봤다" 와 "발행된 적 없다" 가 구분되지 않는다.
            left join viewer_events e on e.clip_id = c.id
            where r.source_id = %s and c.published_at is not null
            group by c.id, qc.id
            order by c.published_at desc""",
        (source_id,),
    ).fetchall()

    # 영상 전체 합계. 클립에 붙지 않는 두 종류(질문 등록·좋아요)가 여기 들어간다 —
    # 퍼널의 입구이면서 클립보다 먼저 일어나는 일이라 클립별로는 셀 수 없다.
    totals = conn.execute(
        f"""select count(distinct e.viewer_id) as viewers,
                   count(distinct e.viewer_id) filter (where e.kind = 'question_post') as question_post,
                   count(distinct e.viewer_id) filter (where e.kind = 'like') as "like",
                   {_PER_KIND}
            from viewer_events e
            where e.source_id = %s""",
        (source_id,),
    ).fetchone()

    return {
        "kinds": list(FUNNEL_KINDS),
        "clips": [dict(row) for row in clips],
        "totals": dict(totals) if totals else {},
    }
