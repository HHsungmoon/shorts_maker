"""유튜브 Shorts 업로드 — 연결된 채널에 렌더한 클립을 올린다(tease §7-5, 2026-10-31).

파이프라인의 마지막 바깥 단계다. 우리 페이지 발행(`clips.published_at`)과는 **다른 행동**이라 묶지 않는다 —
/watch 에 내보내는 것과 채널에 올리는 것은 보는 사람도 되돌리는 방법도 다르다.

🔴 사람이 누를 때만 돈다. 개발자 정책 III.I.2 · III.E.3: 업로드는 행동마다 사용자의 명시적 동의가 필요하고,
공개 범위 선택지를 화면에 보여야 한다. 스튜디오의 [유튜브에 올리기] 한 번이 그 동의다 — 발행하면 알아서
올라가는 식의 자동화는 하지 않는다.

🔴 못 하는 것: Shorts 의 '관련 동영상'(원본으로 가는 링크)은 API 에 필드가 없다. Studio 에서 손으로 단다.
그래서 기록마다 Studio 편집 주소를 함께 싣는다. Shorts 설명란 링크는 클릭되지 않지만(2023-08~) 텍스트로는 남긴다.

어느 채널에 올리나(연결 · 토큰)는 `pipeline/youtube_channel.py` 가 맡는다.
"""

import time

import psycopg
from psycopg.types.json import Jsonb

from .. import config
from ..adapters import youtube
from . import youtube_channel

PRIVACY = ("private", "unlisted", "public")
# 🔴 기본값은 private 다. 원티드 영상으로 만든 숏폼을 개인 채널에 공개로 올리는 사고를 막는다 —
# 허락 범위를 넘을 수 있고 Content ID 경고가 채널에 쌓인다. 공개는 사람이 고른다.
DEFAULT_PRIVACY = "private"
# 유튜브 카테고리 27 = Education. 1단계가 강연(LECTURE)이라 고정한다. FILM 단계에서 다시 본다.
CATEGORY_EDUCATION = "27"
# 유튜브가 거는 한도. 제목은 글자 수, 설명은 **바이트** 수다(한글은 한 글자 3바이트라 1666자 남짓).
TITLE_MAX = 100
DESCRIPTION_MAX_BYTES = 5000
# 제목·설명에 들어가면 유튜브가 invalidTitle/invalidDescription 으로 거절하는 글자.
FORBIDDEN = ("<", ">")
# 화면이 받는 기록 한 줄. 잡 결과와 클립의 업로드 목록이 같은 모양이다(`_view`).
_UPLOAD_COLUMNS = "id, title, privacy, status, youtube_video_id, channel_id, error, created_at, finished_at"


class UploadError(RuntimeError):
    """사람이 고칠 수 있는 이유로 올릴 수 없다. 메시지를 그대로 화면에 보인다."""


class ClipNotFound(UploadError):
    pass


class NotReady(UploadError):
    """클립·채널 상태가 아직 아니다(렌더 전 · 파일 없음 · 채널 미연결). 요청은 옳고 지금 상태와 충돌한다."""


# ---------------------------------------------------------------- 메타데이터

def check_metadata(title: str, description: str, privacy: str) -> tuple[str, str, str]:
    """사람이 대화상자에서 고친 값을 검사한다. 고치지 않고 **거절한다.**

    🔴 개발자 정책 III.C.3: 사용자가 준 값을 동의 없이 자르거나 바꿔 보내지 않는다. 100자를 넘는 제목을
    조용히 잘라 올리면 채널에 사람이 쓴 적 없는 제목이 남는다. 앞뒤 공백만 걷는다.
    """
    title = title.strip()
    description = description.strip()
    if not title:
        raise UploadError("제목을 입력하세요")
    if len(title) > TITLE_MAX:
        raise UploadError(f"제목은 {TITLE_MAX}자까지다(지금 {len(title)}자)")
    size = len(description.encode("utf-8"))
    if size > DESCRIPTION_MAX_BYTES:
        raise UploadError(f"설명은 {DESCRIPTION_MAX_BYTES}바이트까지다(지금 {size}바이트, 한글은 한 글자 3바이트)")
    for text, name in ((title, "제목"), (description, "설명")):
        if any(mark in text for mark in FORBIDDEN):
            raise UploadError(f"{name}에 < > 를 쓸 수 없다 — 유튜브가 거절한다")
    if privacy not in PRIVACY:
        raise UploadError(f"공개 범위는 {'/'.join(PRIVACY)} 중 하나다: {privacy!r}")
    return title, description, privacy


def defaults(conn: psycopg.Connection, clip_id: int) -> dict:
    """대화상자에 채워 둘 값. 사람이 고친 뒤 보내는 것이 실제 값이다."""
    clip = _clip(conn, clip_id)
    title = _clean(clip["title"] or clip["source_title"])[:TITLE_MAX].strip()
    return {
        "title": title,
        "description": describe(clip["source_title"], clip["youtube_id"], float(clip["start_sec"])),
        "privacy": DEFAULT_PRIVACY,
    }


def describe(source_title: str, youtube_id: str | None, start_sec: float) -> str:
    """설명란 기본값 — 원본 제목 · 원본의 그 초로 가는 링크 · #Shorts.

    시작 초는 클립의 `start_sec`(발화 타임스탬프에서 나온 값)이다 — LLM 이 말한 초를 쓰지 않는다(규약).
    조합 클립이면 봉투의 시작, 즉 첫 조각의 시작이다. 링크는 Shorts 에서 클릭되지 않지만 복사는 된다.
    파일로 올린 원본(youtube_id 없음)은 링크 줄을 뺀다 — 엉뚱한 주소를 짓느니 없는 편이 낫다.
    """
    lines = [_clean(source_title)]
    if youtube_id:
        second = max(0, int(start_sec))
        lines.append(f"원본 영상 {second // 60}:{second % 60:02d}부터: https://youtu.be/{youtube_id}?t={second}")
    lines += ["", "#Shorts"]
    return "\n".join(lines)


def _clean(text: str) -> str:
    # 기본값은 우리가 짓는 문장이라 바꿔도 된다(III.C.3 은 사용자가 준 값의 이야기다). 원본 제목에 < > 가
    # 있으면 기본값 그대로 보냈을 때 거절되므로 전각 문자로 바꿔 둔다.
    return text.replace("<", "〈").replace(">", "〉")


def metadata(title: str, description: str, privacy: str, language: str | None) -> dict:
    """videos.insert 본문. Shorts 전용 필드는 없다 — 세로·3분 이하면 유튜브가 Shorts 로 분류한다."""
    snippet: dict = {"title": title, "description": description, "categoryId": CATEGORY_EDUCATION}
    if language:
        # 원본의 STT 언어(ko 등). 비어 있으면(자동 감지) 넣지 않는다 — 틀린 언어를 적느니 비운다.
        snippet["defaultLanguage"] = language
        snippet["defaultAudioLanguage"] = language
    return {
        "snippet": snippet,
        "status": {
            "privacyStatus": privacy,
            # 강연 클립이다. 아동용이 아니고, 실제 영상의 컷이라 AI 로 만든 실사 콘텐츠(A/S)도 아니다.
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": False,
        },
    }


# ---------------------------------------------------------------- 업로드

def check_ready(conn: psycopg.Connection, cfg: config.Config, clip_id: int) -> dict:
    """잡을 띄우기 **전에** 막을 것을 막는다. 큐에 넣고 실패하면 사람은 한참 뒤에야 이유를 안다."""
    if not youtube_channel.configured(cfg):
        raise NotReady("SHORTS_YOUTUBE_CLIENT_ID · SHORTS_YOUTUBE_CLIENT_SECRET 이 .env 에 없다")
    clip = _clip(conn, clip_id)
    if not clip["rendered"] or not clip["path"]:
        raise NotReady("아직 렌더되지 않은 클립입니다")
    if not cfg.work_file(clip["path"]).is_file():
        raise NotReady("렌더 파일이 디스크에 없습니다. 다시 렌더하세요")
    if youtube_channel.channel(conn) is None:
        raise NotReady("유튜브 채널이 연결돼 있지 않습니다. 스튜디오 홈에서 먼저 연결하세요")
    return clip


def run(
    conn: psycopg.Connection, cfg: config.Config, clip_id: int, title: str, description: str, privacy: str,
) -> dict:
    """한 번 올린다. 성공·실패 모두 `clip_uploads` 와 `stage_calls` 에 남긴다."""
    title, description, privacy = check_metadata(title, description, privacy)
    clip = check_ready(conn, cfg, clip_id)
    refresh_token = youtube_channel.refresh_token(conn)
    path = cfg.work_file(clip["path"])
    upload_id = conn.execute(
        "insert into clip_uploads (clip_id, title, privacy) values (%s, %s, %s) returning id",
        (clip_id, title, privacy),
    ).fetchone()["id"]
    # 🔴 네트워크 앞에서 트랜잭션을 끊는다(규약). UPLOADING 행이 먼저 보여야 화면이 "올리는 중" 을 그린다.
    conn.commit()

    started = time.monotonic()
    params = {"clip_id": clip_id, "upload_id": upload_id, "privacy": privacy, "bytes": path.stat().st_size}
    try:
        token = youtube.access_token(cfg, refresh_token)
        video = youtube.upload(token, path, metadata(title, description, privacy, clip["language"]))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        conn.execute(
            "update clip_uploads set status = 'FAILED', error = %s, finished_at = now() where id = %s",
            (error, upload_id),
        )
        _record(conn, clip, int((time.monotonic() - started) * 1000), params, error)
        conn.commit()
        raise
    latency_ms = int((time.monotonic() - started) * 1000)

    video_id = str(video["id"])
    snippet = video.get("snippet") or {}
    channel_id = snippet.get("channelId")
    conn.execute(
        """update clip_uploads set status = 'DONE', youtube_video_id = %s, channel_id = %s, finished_at = now()
           where id = %s""",
        (video_id, channel_id, upload_id),
    )
    if channel_id:
        youtube_channel.note_channel(conn, channel_id, snippet.get("channelTitle"))
    _record(conn, clip, latency_ms, {**params, "video_id": video_id}, None)
    conn.commit()
    row = conn.execute(f"select {_UPLOAD_COLUMNS} from clip_uploads where id = %s", (upload_id,)).fetchone()
    return _view(row)


def history(conn: psycopg.Connection, clip_id: int) -> list[dict]:
    """이 클립을 올린 기록, 최신 먼저."""
    return [
        _view(row)
        for row in conn.execute(
            f"select {_UPLOAD_COLUMNS} from clip_uploads where clip_id = %s order by id desc", (clip_id,)
        ).fetchall()
    ]


def _view(row: dict) -> dict:
    """기록 한 행 + 그 영상으로 가는 두 주소.

    🔴 주소는 **여기서만** 짓는다. 화면이 따로 지으면 둘 중 하나만 바뀌는 날이 온다.
    `studio_url` 은 '관련 동영상'을 원본으로 지정하는 자리다 — API 로는 못 단다(모듈 머리 주석).
    """
    view = dict(row)
    video_id = view["youtube_video_id"]
    view["shorts_url"] = f"https://youtube.com/shorts/{video_id}" if video_id else None
    view["studio_url"] = f"https://studio.youtube.com/video/{video_id}/edit" if video_id else None
    return view


def _clip(conn: psycopg.Connection, clip_id: int) -> dict:
    clip = conn.execute(
        """select cl.id, cl.run_id, cl.title, cl.start_sec, cl.path, cl.rendered,
                  r.source_id, s.title as source_title, s.youtube_id, s.language
           from clips cl
           join runs r on r.id = cl.run_id
           join sources s on s.id = r.source_id
           where cl.id = %s""",
        (clip_id,),
    ).fetchone()
    if clip is None:
        raise ClipNotFound("clip not found")
    return dict(clip)


def _record(conn: psycopg.Connection, clip: dict, latency_ms: int, params: dict, error: str | None) -> None:
    # 🔴 업로드도 stage_calls 에 남긴다(규약: 모든 단계). LLM 이 아니라 model·토큰은 null 이다.
    conn.execute(
        "insert into stage_calls (source_id, run_id, stage, latency_ms, params, error)"
        " values (%s, %s, 'upload', %s, %s, %s)",
        (clip["source_id"], clip["run_id"], latency_ms, Jsonb(params), error),
    )
