"""유튜브 댓글 → 질문 · 감상 (tease §3-5 · §3-6, 2026-11-01).

영상의 최상위 댓글을 받아 **두 갈래**로 나눈다.

- **질문** — 문장 끝이 물음표·의문형 어미인 것. `questions` 에 `origin = 'comment'` 로도 넣는다. 그 뒤는 우리 페이지
  질문과 똑같다 — [집계] 가 묶고, [답하기] 가 숏폼을 만든다. 숏폼이 생기면 그 댓글에 답글을 달 수 있다
  (`answers/replies.py`) — `external_id` 가 답글의 부모 댓글 id 다.
- **감상** — 나머지 전부. 리포트의 "시청자 반응"(`answers/reactions.py`)으로 간다.

받은 것은 갈래와 함께 전부 `source_comments` 에 한 줄씩 남는다(영상 주인이 쓴 것만 뺀다).

🔴 **채팅 규칙(`livechat.QUESTION`)을 그대로 쓰지 않는다.** 삼성(원본 4) 댓글 28개에 대자 12개가 질문으로 들어왔는데
거의 전부 감상이었다("…어떻게 답변을 준비해야 할지 조금은 알겠어요"). 채팅 규칙은 `어떻게`·`어떤` 이 **어디에** 있어도
질문으로 본다 — 짧은 채팅엔 맞고 긴 감상 댓글엔 틀린다. 그래서 **문장 끝**만 본다(`is_question`).
애매하면 감상 쪽이다 — 감상으로 잘못 가면 리포트에 한 줄이 늘 뿐이고, 질문으로 잘못 가면 [집계]·[답하기] 비용이 든다.
질문 갈래에는 채팅과 같은 나머지 거르기(`livechat.pick` — 동의·안내 계정·중복·너무 짧은 것)를 한 번 더 댄다.

다른 점 몇 가지:

- **공식 API(`commentThreads.list`)로 받는다.** 답글을 달려면 어차피 공식 API 토큰과 댓글 id 가 필요하고, yt-dlp 의
  정책 부채를 늘리지 않는다. 🔴 그래서 채널이 연결돼 있고 댓글 권한을 받았어야 한다(`youtube_channel.comment_access`).
- **최상위 댓글만.** 댓글에 달린 답글은 대화라 혼자서는 질문이 안 된다.
- 영상 주인 채널이 쓴 댓글은 운영 쪽 글로 뺀다(`staff`) — 질문도 감상도 아니다.

🔴 **작성자 이름을 남기지 않는다.** 이름은 어댑터에서 이미 버렸고(`youtube.Comment`), 채널 id 도 해시만
`viewer_id` 로 둔다 — 채팅과 같은 함수라 같은 사람이 채팅과 댓글에 다 물어도 한 사람으로 센다.
"""

import re
import time
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import youtube
from ..pipeline import youtube_channel
from . import livechat

# 쪽마다 100개라 1,000개에서 멈춘다(쿼터는 쪽당 1). 실측 원티드 영상 댓글은 편당 2~29개(2026-10-31).
MAX_PAGES = 10

# 이보다 길면 질문 하나가 아니라 글이다(사연 + 여러 질문) — 감상 갈래로 간다. 우리 페이지 질문 상한은 200자
# (http/watch.py) — 댓글은 앞에 사정을 붙이는 일이 많아 넉넉히 둔다. 공백을 접은 뒤 글자 수.
MAX_CHARS = 500

QUESTION = "question"
REVIEW = "review"

# 문장 끝의 의문형 어미. 🔴 끝에 붙은 것만 본다(`is_question`). `는지`·`은지` 는 넣지 않는다 — "…할지는 모르겠지만"
# 꼴이 감상에 흔하다. `하나` 도 뺀다 — "또 하나", "그중 하나" 로 끝나는 감상이 있다.
QUESTION_END = re.compile(
    r"(나요|까요|가요|던가요|ㄴ가요|는지요|은지요|습니까|합니까|됩니까|입니까|봅니까|갑니까|옵니까|줍니까|니까요|나여|까여|가여|뭐예요|뭔가요|뭐죠|건가요|인지요|"
    r"있을까|없을까|될까|할까|일까|좋을까|맞나|되나|있나|없나|인가)$"
)

# 문장 끝에 흔히 붙는 꼬리 — 웃음·이모지·물결·마침표. 걷어 낸 뒤 어미를 본다("…볼까요ㅎㅎ", "…되나요~~").
TAIL = re.compile(r"[\s.~!…ㅎㅋㅠㅜ^♡♥)\]\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]+$")

# 문장 나누기. 물음표·마침표·느낌표·줄바꿈 뒤에서 끊는다.
SENTENCE = re.compile(r"(?<=[?？.!。\n])")


class CommentError(RuntimeError):
    """사람이 고칠 수 있는 이유로 가져올 수 없다. 메시지를 그대로 화면에 보인다."""


@dataclass(frozen=True)
class _Said:
    """`livechat.pick` 이 보는 모양(`livechat.Said`)으로 옮긴 댓글."""

    id: str
    text: str
    author_id: str
    staff: bool
    published_at: str | None = None


def is_question(text: str) -> bool:
    """문장 끝이 물음표나 의문형 어미인 문장이 하나라도 있나.

    🔴 **끝만 본다.** "어떻게 준비해야 할지 조금은 알겠어요" 는 `어떻게` 가 있어도 감상이다(머리 주석의 실측).
    `@` 로 시작하면 다른 댓글을 부르는 말이라 질문으로 치지 않는다.
    """
    stripped = text.strip()
    if not stripped or stripped.startswith("@"):
        return False
    for sentence in SENTENCE.split(stripped):
        sentence = sentence.strip()
        if not sentence:
            continue
        if sentence.rstrip().endswith(("?", "？")):
            return True
        if QUESTION_END.search(TAIL.sub("", sentence)):
            return True
    return False


def import_comments(conn: psycopg.Connection, cfg: config.Config, source_id: int) -> dict:
    """댓글을 받아 갈래를 매기고 질문은 `questions` 에도 넣는다. 몇 번 불러도 같다(댓글 id 로 중복을 막는다)."""
    source = conn.execute("select youtube_id from sources where id = %s", (source_id,)).fetchone()
    if source is None:
        raise CommentError(f"source {source_id} 없음")
    if not source["youtube_id"]:
        raise CommentError("유튜브 영상 id 가 없는 원본입니다. 영상 준비 탭에서 유튜브 주소를 먼저 넣어 주세요")
    try:
        connected = youtube_channel.comment_access(conn, cfg)
    except youtube_channel.NoCommentAccess as exc:
        raise CommentError(str(exc)) from exc
    refresh_token = youtube_channel.refresh_token(conn)
    # 🔴 네트워크 앞에서 트랜잭션을 끊는다(규약).
    conn.commit()

    started = time.monotonic()
    try:
        token = youtube.access_token(cfg, refresh_token)
        comments, owner, truncated = youtube.comment_threads(token, source["youtube_id"], MAX_PAGES)
    except youtube.YoutubeError as exc:
        _record(conn, source_id, started, {"youtube_id": source["youtube_id"]}, f"{type(exc).__name__}: {exc}")
        conn.commit()
        raise

    # 유튜브는 새것부터 준다. 오래된 것부터 거른다 — 같은 사람이 같은 질문을 또 썼으면 먼저 쓴 것이 남는다.
    said: list[_Said] = []
    reviews: list[_Said] = []
    too_long = staff = 0
    for comment in reversed(comments):
        is_staff = owner is not None and comment.author_channel_id == owner
        if is_staff:
            staff += 1
            continue
        raw = comment.text
        # 줄바꿈을 접는다. 질문 목록·반응 목록은 한 줄씩 보인다.
        text = " ".join(raw.split())
        if not text:
            continue
        one = _Said(
            id=comment.id,
            text=text,
            # 채널이 없는 작성자(드물다)는 댓글마다 다른 사람으로 센다 — 남과 한 사람으로 묶이지 않게.
            author_id=comment.author_channel_id or f"comment:{comment.id}",
            staff=False,
            published_at=comment.published_at,
        )
        if len(text) > MAX_CHARS:
            too_long += 1
            reviews.append(one)
        elif is_question(raw):
            said.append(one)
        else:
            reviews.append(one)
    kept, skipped = livechat.pick(said, shape=False)
    if staff:
        skipped["staff"] = staff
    if too_long:
        skipped["long"] = too_long
    # 질문 꼴인데 `pick` 이 뺀 것(동의 · 중복 · 너무 짧음)은 질문 갈래가 아니다 — 감상 목록에도 넣지 않는다.
    # "저도 궁금해요" 는 반응이 아니라 남의 질문에 대한 동의다.

    for one in kept:
        _keep(conn, source_id, one, QUESTION)
    for one in reviews:
        _keep(conn, source_id, one, REVIEW)

    inserted = 0
    for comment in kept:
        row = conn.execute(
            """insert into questions (source_id, text, viewer_id, origin, external_id)
               values (%s, %s, %s, 'comment', %s)
               on conflict (source_id, origin, external_id) where external_id is not null do nothing
               returning id""",
            (source_id, comment.text, livechat.viewer_id(comment.author_id), comment.id),
        ).fetchone()
        inserted += row is not None

    withdrawn = _withdraw(conn, source_id, {one.id for one in reviews})

    result = {
        "comments": len(comments),
        "questions": len(kept),
        "inserted": inserted,
        # 지난번에 이미 가져온 것. 다시 눌렀을 때 이 숫자가 늘어야 "중복이 막혔다" 가 보인다.
        "already": len(kept) - inserted,
        # 감상 갈래. 리포트의 "시청자 반응" 으로 간다.
        "reviews": len(reviews),
        # 예전 규칙으로 질문이 됐는데 이제 감상인 것 — 아직 안 묶였고 답글도 없어 질문에서 뺐다.
        "withdrawn": withdrawn,
        "skipped": skipped,
        # 쪽 상한(1,000개)에서 멈췄다 — 더 오래된 댓글은 안 봤다.
        "truncated": truncated,
        # 🔴 답글은 영상 주인 채널로만 단다(replies.py). 가져오면서 미리 알린다 — 질문을 다 묶고 숏폼까지 만든 뒤에야
        # "이 영상엔 답글을 못 단다" 를 알게 하지 않는다. 채널 id 를 아직 모르면 None(답글 직전에 다시 본다).
        "ownChannel": None if not owner or not connected["channel_id"] else owner == connected["channel_id"],
    }
    _record(conn, source_id, started, result, None)
    conn.commit()
    return result


def _keep(conn: psycopg.Connection, source_id: int, one: _Said, kind: str) -> None:
    """받은 댓글 한 줄. 🔴 다시 가져오면 본문·갈래·받은 때를 갱신한다 — 개발자 정책 III.E.4 의 "갱신" 이 이것이다."""
    conn.execute(
        """insert into source_comments (source_id, comment_id, kind, text, viewer_id, published_at)
           values (%s, %s, %s, %s, %s, %s)
           on conflict (source_id, comment_id) do update
              set kind = excluded.kind, text = excluded.text, viewer_id = excluded.viewer_id,
                  published_at = excluded.published_at, fetched_at = now()""",
        (source_id, one.id, kind, one.text, livechat.viewer_id(one.author_id), one.published_at),
    )


def _withdraw(conn: psycopg.Connection, source_id: int, review_ids: set[str]) -> int:
    """예전 규칙으로 질문이 된 댓글 중 이제 감상인 것을 질문에서 뺀다.

    🔴 **안 묶였고(`cluster_id` 없음) 답글도 없는 것만.** 묶였으면 이미 [집계] 결과의 일부이고, 답글이 달렸으면 그 기록이
    질문을 가리킨다 — 둘 다 사람이 본 것이라 조용히 지우지 않는다.
    """
    if not review_ids:
        return 0
    rows = conn.execute(
        """delete from questions q
            where q.source_id = %s and q.origin = 'comment' and q.external_id = any(%s)
              and q.cluster_id is null
              and not exists (select 1 from question_replies r where r.question_id = q.id)
           returning q.id""",
        (source_id, list(review_ids)),
    ).fetchall()
    return len(rows)


def reviews_of(conn: psycopg.Connection, source_id: int) -> list[dict]:
    """감상 댓글, 오래된 것부터. 리포트 목록과 요약 입력이 같은 순서를 쓴다."""
    return [
        dict(r)
        for r in conn.execute(
            """select id, text, published_at from source_comments
                where source_id = %s and kind = 'review'
                order by published_at nulls last, id""",
            (source_id,),
        )
    ]


def counts(conn: psycopg.Connection, source_id: int) -> dict:
    """받은 댓글 · 질문 · 감상 수. 한 번도 안 가져왔으면 전부 0."""
    row = conn.execute(
        """select count(*) as total,
                  count(*) filter (where kind = 'question') as questions,
                  count(*) filter (where kind = 'review') as reviews,
                  max(fetched_at) as fetched_at
             from source_comments where source_id = %s""",
        (source_id,),
    ).fetchone()
    return dict(row)


def _record(conn: psycopg.Connection, source_id: int, started: float, params: dict, error: str | None) -> None:
    # 🔴 가져오기도 stage_calls 에 남긴다(규약: 모든 단계). LLM 이 아니라 model·토큰은 null 이다.
    conn.execute(
        "insert into stage_calls (source_id, stage, latency_ms, params, error) values (%s, 'comment', %s, %s, %s)",
        (source_id, int((time.monotonic() - started) * 1000), Jsonb(params), error),
    )
