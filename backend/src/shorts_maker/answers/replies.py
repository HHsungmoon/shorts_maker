"""유튜브 댓글 질문에 답글 (tease §7-5, 2026-11-01).

댓글에서 가져온 질문(`answers/comments.py`)에 숏폼이 생기면 **그 댓글에 답글**을 단다. 루프가 유튜브 안에서
닫힌다: 댓글 질문 → 숏폼 → 그 댓글에 "여기서 답했다".

흐름: `draft`(입력란에 채울 초안) → 사람이 고친다 → `post`(답글 하나). 잡이 아니다 — 요청 두세 번(몇 초)이라
바로 결과를 돌려주고 워커를 잡지 않는다.

🔴 울타리 넷.
① **사람이 누를 때만.** 자동 답글은 두지 않는다 — 행동마다 동의(개발자 정책 III.I.2), 그리고 같은 꼴의 답글을
   기계가 줄줄이 달면 스팸으로 읽힌다.
② **영상 주인 채널로만.** 달기 직전에 그 댓글 스레드를 다시 읽어 영상의 채널이 연결된 채널인지 본다. 개발 중
   운영자 개인 채널로 원티드 영상에 공개 답글을 남기는 사고를 막는다. 다시 읽기는 지워진 댓글도 먼저 알려 준다.
③ **다시 보내지 않는다.** 응답만 잃었으면 같은 답글이 둘 달린다. 실패는 FAILED 로 남기고 사람이 확인한 뒤 다시 누른다.
④ **같은 댓글에 두 번 달지 않는다.** 부모 댓글 id 의 유일 인덱스(실패한 것 제외)가 마지막 울타리다.
"""

import time

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import youtube
from ..pipeline import upload, youtube_channel

# 우리 상한. 유튜브 댓글 상한(1만 자)보다 훨씬 짧다 — 답글은 시각 한 줄과 주소 한 줄이면 된다.
TEXT_MAX = 2000

# 답글을 달 만한 클러스터 상태 — 만든 숏폼이 있다(검토 대기 · 발행됨). 화면의 [답글] 도 이때만 보인다.
ANSWERED = ("REVIEW", "PUBLISHED")

_REPLY_COLUMNS = "id, question_id, clip_id, video_id, text, status, youtube_comment_id, channel_id, error, created_at, finished_at"


class ReplyError(RuntimeError):
    """사람이 고칠 수 있는 이유로 달 수 없다. 메시지를 그대로 화면에 보인다."""


class QuestionNotFound(ReplyError):
    pass


class InvalidText(ReplyError):
    pass


class NotReady(ReplyError):
    """요청은 옳고 지금 상태와 충돌한다(댓글 질문 아님 · 숏폼 없음 · 권한 없음 · 이미 달았음 · 남의 영상)."""


# ---------------------------------------------------------------- 초안

def draft(conn: psycopg.Connection, cfg: config.Config, question_id: int) -> dict:
    """입력란에 채울 값과 지금 상태. 네트워크를 쓰지 않는다 — 영상 주인 확인은 `post` 가 한다."""
    question = _question(conn, question_id)
    clip = _answer_clip(conn, question_id)
    latest = latest_reply(conn, question_id)
    blocked = None
    if question["origin"] != "comment":
        blocked = "유튜브 댓글에서 가져온 질문에만 답글을 달 수 있습니다"
    elif clip is None:
        blocked = "아직 이 질문에 답한 숏폼이 없습니다. [답하기] 로 숏폼을 만든 뒤 답글을 달 수 있습니다"
    elif latest is not None and latest["status"] != "FAILED":
        blocked = "이 댓글에는 이미 답글을 달았습니다"
    else:
        try:
            youtube_channel.comment_access(conn, cfg)
        except youtube_channel.NoCommentAccess as exc:
            blocked = str(exc)
    return {
        "question": {"id": question["id"], "text": question["text"]},
        "text": compose(clip) if clip else "",
        "clip": clip,
        # 누구 이름으로 달리나. 입력란이 그대로 말한다.
        "channel": youtube_channel.channel(conn),
        "reply": latest,
        "blocked": blocked,
    }


def compose(clip: dict) -> str:
    """초안 — 숏폼 제목 · 원본 시각 · Shorts 주소(있을 때만).

    🔴 같은 영상의 댓글에 쓴 `12:34` 는 유튜브가 그 초로 가는 링크로 바꾼다. 주소가 없어도 원본의 답으로 데려간다.
    시각은 클립 조각의 발화 타임스탬프에서 나온 값이다(LLM 이 말한 초를 쓰지 않는다 — 규약).
    """
    stamps = " · ".join(stamp(part["start_sec"]) for part in clip["parts"])
    lines = []
    if clip["title"]:
        lines.append(clip["title"])
    lines.append(f"영상 {stamps}부터 답이 나와요." if len(clip["parts"]) == 1 else f"영상 {stamps} 에 답이 나와요.")
    if clip["shorts_url"]:
        lines.append(f"숏폼으로 모아 봤어요: {clip['shorts_url']}")
    return "\n".join(lines)


def stamp(sec: float) -> str:
    """유튜브가 링크로 바꾸는 시각 꼴. 한 시간이 넘으면 h:mm:ss(쏘카 LIVE 는 96분이다)."""
    whole = max(0, int(sec))
    hours, rest = divmod(whole, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


# ---------------------------------------------------------------- 답글

def check_text(text: str) -> str:
    """본문을 검사한다. 고치지 않고 **거절한다**(III.C.3 — 사람이 쓴 적 없는 글을 올리지 않는다). 앞뒤 공백만 걷는다."""
    text = text.strip()
    if not text:
        raise InvalidText("답글을 입력하세요")
    if len(text) > TEXT_MAX:
        raise InvalidText(f"답글은 {TEXT_MAX}자까지입니다(지금 {len(text)}자)")
    return text


def post(conn: psycopg.Connection, cfg: config.Config, question_id: int, text: str) -> dict:
    """답글 하나를 단다. 성공·실패 모두 `question_replies`(보낸 것) 와 `stage_calls` 에 남긴다."""
    text = check_text(text)
    question = _question(conn, question_id)
    if question["origin"] != "comment":
        raise NotReady("유튜브 댓글에서 가져온 질문에만 답글을 달 수 있습니다")
    clip = _answer_clip(conn, question_id)
    if clip is None:
        raise NotReady("아직 이 질문에 답한 숏폼이 없습니다")
    try:
        connected = youtube_channel.comment_access(conn, cfg)
    except youtube_channel.NoCommentAccess as exc:
        raise NotReady(str(exc)) from exc
    parent_id = question["external_id"]
    if _replied(conn, parent_id):
        raise NotReady("이 댓글에는 이미 답글을 달았습니다")
    refresh_token = youtube_channel.refresh_token(conn)
    # 🔴 네트워크 앞에서 트랜잭션을 끊는다(규약).
    conn.commit()

    started = time.monotonic()
    params = {"question_id": question_id, "clip_id": clip["id"], "parent_comment_id": parent_id, "chars": len(text)}

    # 1) 확인 — 아무것도 만들지 않는다. 거절이어도 기록만 남기고 답글 행은 만들지 않는다.
    try:
        token = youtube.access_token(cfg, refresh_token)
        thread = youtube.comment_thread(token, parent_id)
        if thread is None:
            raise NotReady("그 댓글이 유튜브에 없습니다. 지워졌을 수 있습니다")
        own = connected["channel_id"]
        if own is None:
            # 연결 직후 이름을 못 채웠다(learn_channel 실패). 지금 알아본다.
            mine = youtube.my_channel(token)
            if mine is None:
                raise NotReady("연결된 계정에 유튜브 채널이 없습니다")
            youtube_channel.note_channel(conn, *mine)
            conn.commit()
            own = mine[0]
        if thread.channel_id != own:
            raise NotReady(
                "이 영상은 연결된 채널의 영상이 아닙니다. 답글은 영상 주인 채널로만 답니다 — 다른 채널 이름으로"
                " 남의 영상에 링크 답글을 남기지 않습니다"
            )
    except (ReplyError, youtube.YoutubeError) as exc:
        _record(conn, question["source_id"], started, params, f"{type(exc).__name__}: {exc}")
        conn.commit()
        raise

    # 2) 보낸다. POSTING 행이 먼저 커밋돼야 같은 댓글로 오는 두 번째 요청이 유일 인덱스에 막힌다.
    try:
        reply_id = conn.execute(
            """insert into question_replies (question_id, clip_id, parent_comment_id, video_id, text)
               values (%s, %s, %s, %s, %s) returning id""",
            (question_id, clip["id"], parent_id, thread.video_id or question["youtube_id"], text),
        ).fetchone()["id"]
        conn.commit()
    except psycopg.errors.UniqueViolation as exc:
        conn.rollback()
        raise NotReady("이 댓글에는 이미 답글을 달았습니다(방금 다른 요청이 달았습니다)") from exc

    try:
        comment = youtube.reply(token, parent_id, text)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        conn.execute(
            "update question_replies set status = 'FAILED', error = %s, finished_at = now() where id = %s",
            (error, reply_id),
        )
        _record(conn, question["source_id"], started, {**params, "reply_id": reply_id}, error)
        conn.commit()
        raise

    snippet = comment.get("snippet") or {}
    channel_id = (snippet.get("authorChannelId") or {}).get("value") or own
    conn.execute(
        """update question_replies set status = 'DONE', youtube_comment_id = %s, channel_id = %s, finished_at = now()
           where id = %s""",
        (str(comment["id"]), channel_id, reply_id),
    )
    # 링크가 든 답글을 유튜브가 붙잡았는지(heldForReview 등). 있으면 남긴다 — 첫 답글로 확인할 것(tease §7-5).
    moderation = snippet.get("moderationStatus")
    _record(
        conn, question["source_id"], started,
        {**params, "reply_id": reply_id, "comment_id": str(comment["id"]), "moderation": moderation}, None,
    )
    conn.commit()
    return latest_reply(conn, question_id)


def latest_reply(conn: psycopg.Connection, question_id: int) -> dict | None:
    """이 질문에 단 가장 최근 답글(실패 포함) + 그 답글로 가는 주소."""
    row = conn.execute(
        f"select {_REPLY_COLUMNS} from question_replies where question_id = %s order by id desc limit 1",
        (question_id,),
    ).fetchone()
    return view(dict(row)) if row else None


def view(row: dict) -> dict:
    """답글 기록 한 줄. 🔴 주소는 어댑터(`youtube.comment_url`)에서만 짓는다."""
    comment_id = row.get("youtube_comment_id")
    row["url"] = youtube.comment_url(row["video_id"], comment_id) if comment_id else None
    return row


# ---------------------------------------------------------------- 읽기

def _question(conn: psycopg.Connection, question_id: int) -> dict:
    row = conn.execute(
        """select q.id, q.text, q.origin, q.external_id, q.source_id, s.youtube_id
           from questions q join sources s on s.id = q.source_id where q.id = %s""",
        (question_id,),
    ).fetchone()
    if row is None:
        raise QuestionNotFound("question not found")
    return dict(row)


def _answer_clip(conn: psycopg.Connection, question_id: int) -> dict | None:
    """이 질문의 클러스터가 만든 숏폼(최신 하나) — 조각 시각과, 비공개가 아닌 Shorts 업로드가 있으면 그 주소."""
    clip = conn.execute(
        """select c.id, c.title, c.start_sec
           from questions q
           join question_clusters qc on qc.id = q.cluster_id
           join clips c on c.question_cluster_id = qc.id
           where q.id = %s and qc.status = any(%s)
           order by c.id desc limit 1""",
        (question_id, list(ANSWERED)),
    ).fetchone()
    if clip is None:
        return None
    clip = dict(clip)
    parts = [
        dict(r) for r in conn.execute(
            "select start_sec, end_sec from clip_parts where clip_id = %s order by ordinal", (clip["id"],)
        )
    ]
    # 단일 컷도 조각 하나가 있다(baseline). 그래도 없으면 클립의 시작으로.
    clip["parts"] = parts or [{"start_sec": clip["start_sec"], "end_sec": clip["start_sec"]}]
    # 🔴 비공개 업로드는 남이 못 연다 — 주소를 실으면 깨진 링크가 된다. 일부 공개(unlisted)는 주소로 열린다.
    shared = next(
        (u for u in upload.history(conn, clip["id"]) if u["status"] == "DONE" and u["privacy"] != "private"), None
    )
    clip["shorts_url"] = shared["shorts_url"] if shared else None
    del clip["start_sec"]
    return clip


def _replied(conn: psycopg.Connection, parent_id: str) -> bool:
    return conn.execute(
        "select 1 from question_replies where parent_comment_id = %s and status <> 'FAILED'", (parent_id,)
    ).fetchone() is not None


def _record(conn: psycopg.Connection, source_id: int, started: float, params: dict, error: str | None) -> None:
    # 🔴 답글도 stage_calls 에 남긴다(규약: 모든 단계). 거절(남의 영상 · 지워진 댓글)도 남긴다.
    conn.execute(
        "insert into stage_calls (source_id, stage, latency_ms, params, error) values (%s, 'reply', %s, %s, %s)",
        (source_id, int((time.monotonic() - started) * 1000), Jsonb(params), error),
    )
