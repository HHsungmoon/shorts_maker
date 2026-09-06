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
