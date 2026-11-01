"""유튜브 채널 연결 — 어느 채널에 올리나(tease §7-5, 2026-10-31).

연결은 스튜디오 전체에 하나다(`youtube_channel` 한 행). 업로드(`pipeline/upload.py`) · 댓글 가져오기
(`answers/comments.py`) · 답글(`answers/replies.py`) · 성과(`pipeline/youtube_stats.py`)가 이 연결의 토큰을 쓴다 — 그래서 업로드 모듈에서 떼어 뒀다.
Google 과 말하는 일은 `adapters/youtube.py` 가 하고, 여기는 **무엇을 저장하고 언제 거절하나**를 정한다.

OAuth 웹 흐름: `start`(동의 화면 주소) → Google 동의 화면 → `finish`(code 를 토큰으로 바꿔 저장) → 홈.

🔴 refresh token 은 **비밀이다.** 이 값만 있으면 그 채널에 영상을 올릴 수 있다. 밖으로 나가는 `channel()`
에는 싣지 않고, 토큰이 필요한 쪽은 `refresh_token()` 으로 따로 읽는다.
"""

import logging
import secrets
import threading
import time
import urllib.parse

import psycopg

from .. import config
from ..adapters import youtube

# 동의 화면에 머무를 수 있는 시간. 넘으면 다시 누른다.
STATE_TTL_SEC = 600

# 🔴 state 는 **프로세스 메모리**에 둔다. 운영자 1명 · 프로세스 1개라 충분하고, 재기동하면 사라지는데
# 그건 "동의 화면에 있던 사람이 다시 누른다" 일 뿐이다. 워커를 여럿 띄우면 여기를 DB 로 옮긴다.
_states: dict[str, float] = {}
_lock = threading.Lock()


class ChannelError(RuntimeError):
    """사람이 고칠 수 있는 이유로 연결할 수 없다(설정 없음 · 호스트 다름 · Google 이 거절). 메시지를 그대로 보인다."""


class NoCommentAccess(ChannelError):
    """댓글 기능을 쓸 수 없다 — 연결이 없거나, 연결할 때 댓글 권한을 받지 못했다. 사람이 할 일을 말한다."""


class NoAnalyticsAccess(ChannelError):
    """성과를 읽을 수 없다 — 연결이 없거나, 연결할 때 분석 권한을 받지 못했다(tease §7-6). 사람이 할 일을 말한다."""


def configured(cfg: config.Config) -> bool:
    """앱 자격 증명(.env 의 OAuth 클라이언트 ID·시크릿)이 있나. 채널이 연결됐는지와는 다른 질문이다."""
    return youtube.configured(cfg)


# ---------------------------------------------------------------- 저장

def channel(conn: psycopg.Connection) -> dict | None:
    """연결된 채널. 🔴 refresh token 은 싣지 않는다 — 이 값은 API 응답으로 나간다.

    `can_comment` — 댓글 권한을 받았나. 댓글 기능 전에 연결했거나 동의 화면에서 그 권한을 풀었으면 거짓이고,
    화면은 "다시 연결" 을 안내한다. `can_analytics` — 성과(분석) 권한 둘을 다 받았나. 같은 판단이다.
    """
    row = conn.execute(
        "select channel_id, channel_title, scopes, connected_at from youtube_channel where id = 1"
    ).fetchone()
    if row is None:
        return None
    found = dict(row)
    granted = found["scopes"].split()
    found["can_comment"] = youtube.COMMENT_SCOPE in granted
    found["can_analytics"] = all(scope in granted for scope in youtube.ANALYTICS_SCOPES)
    return found


def comment_access(conn: psycopg.Connection, cfg: config.Config) -> dict:
    """댓글 기능을 띄우기 **전에** 막을 것을 막는다(네트워크 없음). 쓸 수 있으면 연결된 채널을 돌려준다."""
    if not configured(cfg):
        raise NoCommentAccess("SHORTS_YOUTUBE_CLIENT_ID · SHORTS_YOUTUBE_CLIENT_SECRET 이 .env 에 없습니다")
    found = channel(conn)
    if found is None:
        raise NoCommentAccess("유튜브 채널이 연결돼 있지 않습니다. 스튜디오 홈에서 먼저 연결하세요")
    if not found["can_comment"]:
        raise NoCommentAccess(
            "연결할 때 댓글 권한을 받지 못했습니다. 스튜디오 홈에서 유튜브를 끊고 다시 연결한 뒤, 동의 화면의 권한을 모두 체크하세요"
        )
    return found


def analytics_access(conn: psycopg.Connection, cfg: config.Config) -> dict:
    """성과 가져오기를 띄우기 **전에** 막을 것을 막는다(네트워크 없음). 쓸 수 있으면 연결된 채널을 돌려준다.

    🔴 여기서 못 보는 것: Cloud 콘솔에서 YouTube Analytics API 를 켰는지. 그건 첫 호출의 403 이 알려 주고,
    어댑터가 그 할 일을 말한다(`youtube.SERVICE_DISABLED_HINT`).
    """
    if not configured(cfg):
        raise NoAnalyticsAccess("SHORTS_YOUTUBE_CLIENT_ID · SHORTS_YOUTUBE_CLIENT_SECRET 이 .env 에 없습니다")
    found = channel(conn)
    if found is None:
        raise NoAnalyticsAccess("유튜브 채널이 연결돼 있지 않습니다. 스튜디오 홈에서 먼저 연결하세요")
    if not found["can_analytics"]:
        raise NoAnalyticsAccess(
            "연결할 때 성과(분석) 권한을 받지 못했습니다. 스튜디오 홈에서 유튜브를 끊고 다시 연결한 뒤, 동의 화면의 권한을 모두 체크하세요"
        )
    return found


def refresh_token(conn: psycopg.Connection) -> str | None:
    """토큰이 필요한 쪽(업로드 · 앞으로의 댓글 답글)만 부른다. 연결이 없으면 None."""
    row = conn.execute("select refresh_token from youtube_channel where id = 1").fetchone()
    return row["refresh_token"] if row else None


def save_channel(conn: psycopg.Connection, grant: youtube.Grant) -> None:
    """연결을 저장한다. 이미 있으면 덮어쓴다 — 다른 채널을 연결하는 것이 곧 바꾸기다.

    채널 id·이름은 비운다. 새 토큰이 같은 채널이라는 보장이 없고(동의 화면에서 채널을 고른다),
    틀린 이름을 보여 주는 것보다 "첫 업로드 뒤 표시" 가 정직하다.
    """
    conn.execute(
        """insert into youtube_channel (id, refresh_token, scopes) values (1, %s, %s)
           on conflict (id) do update
              set refresh_token = excluded.refresh_token, scopes = excluded.scopes,
                  channel_id = null, channel_title = null, connected_at = now()""",
        (grant.refresh_token, grant.scopes),
    )


def note_channel(conn: psycopg.Connection, channel_id: str, channel_title: str | None) -> None:
    """유튜브 응답에서 알게 된 채널 id·이름을 적는다.

    `youtube.upload` 범위로는 `channels.list` 를 부를 수 없어서, 업로드 응답(snippet)이 처음 알려 준다.
    그걸 위해 범위를 늘리지 않는다(개발자 정책 III.D.2).
    """
    conn.execute(
        "update youtube_channel set channel_id = %s, channel_title = %s where id = 1",
        (channel_id, channel_title),
    )


def forget_channel(conn: psycopg.Connection) -> str | None:
    """연결을 지운다. 거두기(revoke)에 쓰라고 지운 토큰을 돌려준다. 연결이 없었으면 None."""
    row = conn.execute("delete from youtube_channel where id = 1 returning refresh_token").fetchone()
    return row["refresh_token"] if row else None


# ---------------------------------------------------------------- 연결 흐름

def start(cfg: config.Config, hostname: str | None) -> str:
    """동의 화면 주소. `hostname` 은 지금 스튜디오를 연 주소의 호스트다."""
    if not configured(cfg):
        raise ChannelError(
            "SHORTS_YOUTUBE_CLIENT_ID · SHORTS_YOUTUBE_CLIENT_SECRET 이 .env 에 없습니다(backend/.env.example 참고)"
        )
    check_host(cfg, hostname)
    return youtube.authorize_url(cfg, issue_state())


def check_host(cfg: config.Config, hostname: str | None) -> None:
    """🔴 **호스트를 맞춘다.** 세션 쿠키는 호스트별이라, 스튜디오를 127.0.0.1 로 열어 두고 localhost 로
    돌아오면 콜백에 쿠키가 실리지 않아 401 이 된다 — 동의까지 마친 사람이 이유 없는 실패를 본다.
    그래서 보내기 전에 막고 어느 주소로 열라고 말한다. 포트는 보지 않는다(쿠키는 포트를 가르지 않는다 —
    vite 개발 서버 5173 에서 열어도 8100 콜백에 쿠키가 실린다).
    """
    expected = urllib.parse.urlsplit(cfg.youtube_redirect_uri)
    if expected.hostname and hostname and expected.hostname != hostname:
        raise ChannelError(
            f"스튜디오를 {expected.scheme}://{expected.netloc} 로 열고 다시 눌러 주세요. 연결이 끝나면 그 주소로 "
            f"돌아오는데, 지금 주소({hostname})의 로그인은 그쪽으로 따라가지 않습니다."
        )


def finish(conn: psycopg.Connection, cfg: config.Config, code: str) -> None:
    """Google 이 준 code 를 토큰으로 바꿔 저장한다. state 검사는 부르는 쪽이 **먼저** 끝냈어야 한다."""
    try:
        grant = youtube.exchange_code(cfg, code)
    except youtube.YoutubeError as exc:
        # 업로드 범위를 체크하지 않았거나 code 가 만료됐다. 저장하지 않는다.
        raise ChannelError(str(exc)) from exc
    save_channel(conn, grant)
    learn_channel(conn, cfg, grant)


def learn_channel(conn: psycopg.Connection, cfg: config.Config, grant: youtube.Grant) -> None:
    """연결 직후 채널 id·이름을 채운다 — 댓글이나 읽기 권한이 있을 때만(`channels.list` 는 그 범위로 부른다).

    🔴 답글 입력란이 "누구 이름으로 달리나" 를 말해야 하는데, 다시 연결하면 `save_channel` 이 이름을 비운다.
    실패해도 연결은 그대로 둔다 — 이름은 첫 업로드·답글 응답에서도 채워진다.
    """
    granted = grant.scopes.split()
    if youtube.COMMENT_SCOPE not in granted and youtube.READONLY_SCOPE not in granted:
        return
    try:
        mine = youtube.my_channel(youtube.access_token(cfg, grant.refresh_token))
    except youtube.YoutubeError as exc:
        logging.warning("YouTube channel lookup after connect failed: %s", exc)
        return
    if mine is not None:
        note_channel(conn, *mine)


def disconnect(conn: psycopg.Connection) -> dict:
    """연결을 끊는다. 토큰을 지우고 Google 쪽 권한도 거둔다(실패해도 지우기는 한다).

    개발자 정책 III.D.2: 앱 안에서 연결을 끊으면 그 데이터를 지운다. 🔴 그래서 유튜브에서 받아 온 성과
    (`short_stats` · `short_stats_daily`, tease §7-6)도 지운다 — 그 채널이 우리에게 준 데이터다. 올린 기록(clip_uploads)은
    남긴다 — 영상은 채널에 그대로 있고, 어디에 무엇을 올렸는지는 우리가 알아야 한다.
    """
    token = forget_channel(conn)
    conn.execute("delete from short_stats_daily")
    conn.execute("delete from short_stats")
    # 🔴 거두기(네트워크) 앞에서 커밋한다. Google 이 늦거나 실패해도 우리 쪽 삭제는 이미 끝나 있어야 한다.
    conn.commit()
    revoked = youtube.revoke(token) if token else False
    return {"disconnected": token is not None, "revoked": revoked}


def issue_state() -> str:
    """CSRF 막이 — 이 서버가 방금 시작한 연결인지 콜백에서 확인한다. 남이 만든 동의 링크로 남의 채널이
    우리 스튜디오에 연결되면 영상이 그쪽으로 올라간다."""
    state = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _lock:
        # 버려진 것(동의 화면을 닫고 떠난 것)을 여기서 치운다. 운영자 1명이라 많아야 몇 개다.
        for key in [k for k, expires in _states.items() if expires < now]:
            del _states[key]
        _states[state] = now + STATE_TTL_SEC
    return state


def take_state(state: str | None) -> bool:
    """🔴 한 번 쓰면 사라진다. 같은 콜백 주소를 다시 열어도(뒤로 가기·새로고침) 두 번 저장되지 않는다."""
    if not state:
        return False
    with _lock:
        expires = _states.pop(state, None)
    return expires is not None and expires >= time.monotonic()
