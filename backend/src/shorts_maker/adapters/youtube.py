"""유튜브 — OAuth 토큰 · 영상 업로드 · 댓글(tease §7-5, 2026-10-31 · 댓글 2026-11-01).

하는 일: 동의 화면 주소 만들기 · code 를 토큰으로 바꾸기 · refresh token 으로 access token 받기 ·
영상 올리기(resumable 업로드 두 단계: 세션 열기 → 바이트 PUT) · 댓글 읽기(`commentThreads.list`) ·
답글 달기(`comments.insert`) · 연결된 채널 알아보기(`channels.list?mine=true`).

🔴 **SDK(google-api-python-client)를 쓰지 않는다.** 위 넷이 POST/PUT 몇 번이고, SDK 를 써도 런타임
동작은 같다 — 규약상 "코드가 줄어든다" 는 채택 근거가 아니다. jev.py 처럼 stdlib urllib 로 직접 부른다.

🔴 범위는 `youtube.upload` · `youtube.force-ssl`(댓글, 2026-11-01) 둘이다. 개발자 정책 III.D.2 — 지금 쓰는
범위만 요청한다. 댓글 쓰기(`comments.insert`)는 force-ssl 하나만 받는다 — 더 좁은 범위가 없다. 업로드 권한은
필수, 댓글 권한은 선택이다: 동의 화면에서 댓글만 풀면 연결은 되고 댓글 기능이 "다시 연결" 을 안내한다.

근거가 된 공식 문서(2026-10-15 갱신본): videos.insert 는 2026-06 부터 전용 버킷 하루 100건·건당 1,
미검증 프로젝트 업로드는 "비공개로 제한되지 않는다". Shorts 전용 필드는 없다 — 세로·3분 이하면
유튜브가 Shorts 로 분류한다.
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .. import config

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
API_URL = "https://www.googleapis.com/youtube/v3"

UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
# 댓글 읽기·쓰기 · 채널 알아보기. 넓은 범위다(영상·댓글·자막 관리) — 쓰는 곳은 그 셋뿐이다(tease §7-5).
COMMENT_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
SCOPES = (UPLOAD_SCOPE, COMMENT_SCOPE)

# 토큰 요청은 몇백 ms 다. 업로드 세션 열기도 메타데이터 POST 하나라 짧다.
TOKEN_TIMEOUT_SEC = 15.0
INIT_TIMEOUT_SEC = 30.0
# Data API 의 GET·댓글 POST. 응답이 작다.
API_TIMEOUT_SEC = 30.0
# 🔴 urllib 의 timeout 은 **전체가 아니라 소켓 동작 하나**의 대기 상한이다. 60초 클립은 수십 MB 라
# 전체 전송은 이보다 길 수 있지만, 한 번의 쓰기·읽기가 2분 멈추면 끊긴 것으로 본다.
PUT_TIMEOUT_SEC = 120.0

# 다시 부를 만한 상태. 토큰 요청과 세션 열기만 다시 부른다 — 둘 다 아무것도 만들지 않는다.
TRANSIENT_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3


class YoutubeError(RuntimeError):
    pass


class ReconnectNeeded(YoutubeError):
    """refresh token 이 더는 통하지 않는다. 사람이 [채널 연결] 을 다시 눌러야 한다.

    🔴 앱이 **테스트 상태**면 refresh token 이 7일 뒤 만료된다(Google OAuth 정책). 개발 중에는 이게
    주 1회 일어나는 정상 경로다 — 원인을 모르면 업로드가 "갑자기 고장 난" 것으로 읽힌다.
    """


@dataclass(frozen=True)
class Grant:
    refresh_token: str
    scopes: str


def configured(cfg: config.Config) -> bool:
    return bool(cfg.youtube_client_id and cfg.youtube_client_secret)


def authorize_url(cfg: config.Config, state: str) -> str:
    """Google 동의 화면 주소.

    - `access_type=offline` + `prompt=consent`: refresh token 은 **처음 동의할 때만** 온다. 이미 동의한
      계정이 다시 연결하면 빠지는데, 그러면 저장할 것이 없다. 매번 동의 화면을 띄워 매번 받는다.
    - `include_granted_scopes=false`: 예전에 다른 범위를 줬더라도 이번 토큰은 이번에 요청한 범위만 갖게 한다.
    """
    query = urllib.parse.urlencode(
        {
            "client_id": cfg.youtube_client_id,
            "redirect_uri": cfg.youtube_redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "false",
            "state": state,
        }
    )
    return f"{AUTH_URL}?{query}"


def exchange_code(cfg: config.Config, code: str) -> Grant:
    """동의 화면이 돌려준 code 를 토큰으로 바꾼다."""
    payload = _post_form(
        TOKEN_URL,
        {
            "code": code,
            "client_id": cfg.youtube_client_id,
            "client_secret": cfg.youtube_client_secret,
            "redirect_uri": cfg.youtube_redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        raise YoutubeError("refresh token 을 받지 못했다 — 동의 화면을 끝까지 거치지 않았을 수 있다. 다시 연결하세요")
    scopes = str(payload.get("scope") or "")
    # 🔴 동의 화면에서 범위별 체크를 풀 수 있다. 업로드 권한 없이 연결된 채 두면 첫 업로드에서야 403 이 난다.
    if UPLOAD_SCOPE not in scopes.split():
        raise YoutubeError("동의 화면에서 '유튜브 동영상 관리' 권한을 체크하지 않았다. 다시 연결해 체크하세요")
    return Grant(refresh_token=refresh_token, scopes=scopes)


def access_token(cfg: config.Config, refresh_token: str) -> str:
    """refresh token 으로 1시간짜리 access token 을 받는다. 업로드마다 새로 받는다 — 들고 있을 이유가 없다."""
    try:
        payload = _post_form(
            TOKEN_URL,
            {
                "client_id": cfg.youtube_client_id,
                "client_secret": cfg.youtube_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    except _TokenRefused as exc:
        if exc.error == "invalid_grant":
            raise ReconnectNeeded(
                "유튜브 채널 연결이 만료됐거나 해제됐다. 스튜디오 홈에서 다시 연결하세요"
                " (앱이 테스트 상태면 연결은 7일마다 만료된다)"
            ) from exc
        raise
    token = payload.get("access_token")
    if not token:
        raise YoutubeError(f"access token 이 응답에 없다: {sorted(payload)}")
    return str(token)


def revoke(token: str) -> bool:
    """연결을 끊을 때 Google 쪽 권한도 거둔다. 실패해도 우리 쪽 삭제는 그대로 한다 — 성공 여부만 돌려준다.

    개발자 정책 III.D.2: 사용자가 앱 안에서 연결을 끊으면 그 데이터를 지워야 한다. 토큰을 지우는 게
    우리 몫이고, 거두기는 Google 계정 화면에 이 앱이 남아 있지 않게 하려는 것이다.
    """
    try:
        _post_form(REVOKE_URL, {"token": token}, attempts=1)
        return True
    except YoutubeError as exc:
        logging.warning("YouTube token revoke failed: %s", exc)
        return False


def upload(token: str, path: Path, metadata: dict) -> dict:
    """영상을 올리고 video 리소스(id · snippet · status)를 돌려준다.

    🔴 **바이트 PUT 은 다시 보내지 않는다.** 응답만 잃고 실제로는 올라간 경우 다시 보내면 같은 영상이
    채널에 둘 생긴다. 실패하면 잡이 실패로 끝나고, 사람이 채널을 확인한 뒤 다시 누른다.
    TODO: 큰 파일이 생기면 `Content-Range: bytes */N` 으로 상태를 물어 이어 올린다(resumable 의 본뜻).
    지금 클립은 60초 이하·수십 MB 라 한 번에 보낸다.
    """
    size = path.stat().st_size
    if size == 0:
        raise YoutubeError(f"빈 파일이다: {path.name}")
    session = _open_session(token, size, metadata)
    started = time.monotonic()
    with path.open("rb") as body:
        request = urllib.request.Request(
            session, data=body, method="PUT",
            headers={"Content-Length": str(size), "Content-Type": "video/mp4"},
        )
        try:
            with urllib.request.urlopen(request, timeout=PUT_TIMEOUT_SEC) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise _api_error(exc) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise YoutubeError(
                f"업로드 중 연결이 끊겼다({exc}). 채널에 올라갔는지 확인한 뒤 다시 누르세요 — 바로 다시 보내면 둘이 될 수 있다"
            ) from exc
    logging.info("YouTube upload %d bytes in %.1fs", size, time.monotonic() - started)
    try:
        video = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise YoutubeError(f"업로드 응답이 JSON 이 아니다: {raw[:200]!r}") from exc
    if not isinstance(video, dict) or not video.get("id"):
        raise YoutubeError(f"업로드 응답에 영상 id 가 없다: {raw[:200]!r}")
    return video


def _open_session(token: str, size: int, metadata: dict) -> str:
    """resumable 업로드 세션을 연다. 응답의 Location 이 바이트를 보낼 주소다."""
    query = urllib.parse.urlencode({"uploadType": "resumable", "part": "snippet,status"})
    body = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = urllib.request.Request(
            f"{UPLOAD_URL}?{query}", data=body, method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(size),
                "X-Upload-Content-Type": "video/mp4",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=INIT_TIMEOUT_SEC) as response:
                location = response.headers.get("Location")
            break
        except urllib.error.HTTPError as exc:
            if attempt == MAX_ATTEMPTS or exc.code not in TRANSIENT_STATUS:
                raise _api_error(exc) from exc
            time.sleep(attempt)
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == MAX_ATTEMPTS:
                raise YoutubeError(f"유튜브에 닿지 못했다: {exc}") from exc
            time.sleep(attempt)
    if not location:
        raise YoutubeError("업로드 세션 주소(Location)가 응답에 없다")
    return location


# 유튜브가 돌려주는 이유(error.errors[].reason) 가운데 사람이 할 일이 있는 것. 원문은 뒤에 그대로 붙인다 —
# 번역은 추측이고 진짜 원인은 원문에 있다(ytdlp.py 와 같은 판단).
REASONS = {
    "quotaExceeded": "오늘 쓸 수 있는 API 쿼터를 다 썼다. 태평양 시간 자정(한국 오후 4~5시)에 초기화된다",
    "uploadLimitExceeded": "이 채널의 업로드 한도를 넘었다. 유튜브가 채널마다 거는 한도라 시간이 지나야 풀린다",
    "youtubeSignupRequired": "이 Google 계정에 유튜브 채널이 없다. youtube.com 에서 채널을 만든 뒤 다시 연결하세요",
    "invalidTitle": "제목을 유튜브가 받지 않았다(100자 이하, < > 없이)",
    "invalidDescription": "설명을 유튜브가 받지 않았다(5000바이트 이하, < > 없이)",
    "forbidden": "이 채널로는 할 수 없는 일이다. 연결한 계정과 채널을 확인하세요",
    # ---- 댓글(2026-11-01)
    "commentsDisabled": "이 영상은 댓글이 꺼져 있다",
    "videoNotFound": "유튜브에서 이 영상을 찾지 못했다. 지워졌거나 비공개일 수 있다",
    "commentNotFound": "그 댓글이 유튜브에 없다. 지워졌을 수 있다",
    "commentThreadNotFound": "그 댓글이 유튜브에 없다. 지워졌을 수 있다",
    "parentCommentNotFound": "답글을 달 댓글이 유튜브에 없다. 지워졌을 수 있다",
    "ineligibleAccount": "이 계정으로는 댓글을 쓸 수 없다. 유튜브에서 채널 상태를 확인하세요",
}

# 🔴 범위가 모자라다(동의 화면에서 댓글 권한을 풀었거나, 댓글 기능 전에 연결했다). 403 이지만 사람이 할 일은
# "다시 연결" 이라 ReconnectNeeded 로 낸다.
SCOPE_MISSING = "insufficientPermissions"


def _api_error(exc: urllib.error.HTTPError) -> YoutubeError:
    detail = exc.read().decode("utf-8", "replace")
    reason = message = ""
    try:
        error = json.loads(detail).get("error") or {}
        message = str(error.get("message") or "")
        reasons = [str(item.get("reason")) for item in error.get("errors") or [] if isinstance(item, dict)]
        reason = next((r for r in reasons if r in REASONS), reasons[0] if reasons else "")
    except (json.JSONDecodeError, AttributeError):
        message = detail[:300]
    if reason == SCOPE_MISSING or (exc.code == 403 and "insufficient authentication scopes" in message):
        return ReconnectNeeded(
            "연결할 때 받은 권한으로는 할 수 없다. 스튜디오 홈에서 유튜브를 다시 연결하고 동의 화면의 권한을 모두"
            f" 체크하세요 ({message})"
        )
    hint = REASONS.get(reason)
    # 🔴 401 이라고 다 만료가 아니다. 채널 없는 계정(youtubeSignupRequired)도 401 인데, 그걸 "다시 연결하라" 로
    # 읽히면 사람은 같은 계정으로 계속 다시 연결한다. 이유를 아는 것은 이유를 먼저 말한다.
    if exc.code == 401 and hint is None:
        return ReconnectNeeded(f"유튜브가 토큰을 받지 않았다(401). 채널을 다시 연결하세요 — {message}")
    original = f"YouTube {exc.code} {reason}: {message}".strip()
    return YoutubeError(f"{hint} ({original})" if hint else original)


# ---------------------------------------------------------------- 댓글 · 채널 (2026-11-01)

# 쪽마다 최대 100개(API 상한). 실측 원티드 영상 댓글은 편당 2~29개라 쪽 하나로 끝난다.
COMMENT_PAGE_SIZE = 100


@dataclass(frozen=True)
class Comment:
    """최상위 댓글 하나. 🔴 작성자 **이름은 싣지 않는다** — 응답에 오지만 여기서 버린다(tease §3-5)."""

    id: str
    text: str
    author_channel_id: str | None
    published_at: str


@dataclass(frozen=True)
class CommentThread:
    """스레드 하나의 머리. `channel_id` 는 **영상 주인 채널**이다 — 답글을 달아도 되는지 이것으로 본다."""

    id: str
    video_id: str
    channel_id: str | None
    text: str


def comment_threads(token: str, video_id: str, max_pages: int) -> tuple[list[Comment], str | None, bool]:
    """영상의 최상위 댓글(새것부터, `order=time`) · 영상 주인 채널 id · 쪽 상한에서 멈췄는지.

    답글(replies)은 받지 않는다 — `part=snippet` 만. 본문은 `textFormat=plainText` 로 받는다(HTML 아님).
    """
    comments: list[Comment] = []
    owner: str | None = None
    page_token: str | None = None
    for _ in range(max_pages):
        params = {
            "part": "snippet", "videoId": video_id, "maxResults": str(COMMENT_PAGE_SIZE),
            "textFormat": "plainText", "order": "time",
        }
        if page_token:
            params["pageToken"] = page_token
        payload = _api_get(token, "commentThreads", params)
        for item in payload.get("items") or []:
            snippet = item.get("snippet") or {}
            owner = owner or snippet.get("channelId")
            top = snippet.get("topLevelComment") or {}
            top_snippet = top.get("snippet") or {}
            text = str(top_snippet.get("textDisplay") or "").strip()
            if not top.get("id") or not text:
                continue
            comments.append(
                Comment(
                    id=str(top["id"]),
                    text=text,
                    author_channel_id=(top_snippet.get("authorChannelId") or {}).get("value"),
                    published_at=str(top_snippet.get("publishedAt") or ""),
                )
            )
        page_token = payload.get("nextPageToken")
        if not page_token:
            return comments, owner, False
    return comments, owner, True


def comment_thread(token: str, thread_id: str) -> CommentThread | None:
    """스레드 하나를 다시 읽는다. 지워졌으면 None. 답글을 달기 **직전**에 부른다(영상 주인 확인)."""
    payload = _api_get(token, "commentThreads", {"part": "snippet", "id": thread_id, "textFormat": "plainText"})
    items = payload.get("items") or []
    if not items:
        return None
    snippet = items[0].get("snippet") or {}
    top_snippet = (snippet.get("topLevelComment") or {}).get("snippet") or {}
    return CommentThread(
        id=str(items[0].get("id") or thread_id),
        video_id=str(snippet.get("videoId") or ""),
        channel_id=snippet.get("channelId"),
        text=str(top_snippet.get("textDisplay") or ""),
    )


def my_channel(token: str) -> tuple[str, str | None] | None:
    """이 토큰의 채널(id, 이름). 계정에 채널이 없으면 None. 댓글 권한(force-ssl)이 있어야 부를 수 있다."""
    payload = _api_get(token, "channels", {"part": "snippet", "mine": "true"})
    items = payload.get("items") or []
    if not items or not items[0].get("id"):
        return None
    return str(items[0]["id"]), (items[0].get("snippet") or {}).get("title")


def reply(token: str, parent_id: str, text: str) -> dict:
    """최상위 댓글에 답글을 단다. comment 리소스(id · snippet)를 돌려준다.

    🔴 **다시 보내지 않는다.** 응답만 잃고 실제로는 달렸으면 같은 답글이 둘 생긴다 — 업로드 바이트 PUT 과
    같은 판단이다. 실패하면 사람이 유튜브에서 확인한 뒤 다시 누른다.
    """
    body = json.dumps({"snippet": {"parentId": parent_id, "textOriginal": text}}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{API_URL}/comments?part=snippet", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT_SEC) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise _api_error(exc) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise YoutubeError(
            f"답글을 보내는 중 연결이 끊겼다({exc}). 유튜브에서 달렸는지 확인한 뒤 다시 누르세요 — 바로 다시 보내면 둘이 될 수 있다"
        ) from exc
    try:
        comment = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise YoutubeError(f"답글 응답이 JSON 이 아니다: {raw[:200]!r}") from exc
    if not isinstance(comment, dict) or not comment.get("id"):
        raise YoutubeError(f"답글 응답에 댓글 id 가 없다: {raw[:200]!r}")
    return comment


def comment_url(video_id: str, comment_id: str) -> str:
    """그 댓글이 맨 위에 보이는 영상 주소. 🔴 주소는 여기서만 짓는다(upload._view 와 같은 판단)."""
    return f"https://www.youtube.com/watch?v={video_id}&lc={comment_id}"


def _api_get(token: str, resource: str, params: dict) -> dict:
    """Data API GET. 아무것도 만들지 않으니 일시 오류(429·5xx)에 다시 부른다."""
    url = f"{API_URL}/{resource}?{urllib.parse.urlencode(params)}"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = urllib.request.Request(url, method="GET", headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=API_TIMEOUT_SEC) as response:
                raw = response.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            if attempt == MAX_ATTEMPTS or exc.code not in TRANSIENT_STATUS:
                raise _api_error(exc) from exc
            time.sleep(attempt)
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == MAX_ATTEMPTS:
                raise YoutubeError(f"유튜브에 닿지 못했다: {exc}") from exc
            time.sleep(attempt)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise YoutubeError(f"유튜브 응답이 JSON 이 아니다: {raw[:200]!r}") from exc
    if not isinstance(payload, dict):
        raise YoutubeError(f"유튜브 응답이 객체가 아니다: {raw[:200]!r}")
    return payload


class _TokenRefused(YoutubeError):
    """토큰 엔드포인트가 4xx 로 거절했다. `error` 는 OAuth 오류 코드(invalid_grant 등)."""

    def __init__(self, status: int, error: str, description: str) -> None:
        super().__init__(f"Google OAuth {status} {error}: {description}".strip())
        self.error = error


def _post_form(url: str, fields: dict, attempts: int = MAX_ATTEMPTS) -> dict:
    body = urllib.parse.urlencode(fields).encode("ascii")
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(request, timeout=TOKEN_TIMEOUT_SEC) as response:
                raw = response.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            if attempt < attempts and exc.code in TRANSIENT_STATUS:
                time.sleep(attempt)
                continue
            try:
                parsed = json.loads(detail)
            except json.JSONDecodeError:
                parsed = {}
            # 🔴 오류 본문만 싣는다. 요청 본문에는 client_secret·토큰이 있어 예외 메시지에 넣지 않는다.
            raise _TokenRefused(
                exc.code, str(parsed.get("error") or ""), str(parsed.get("error_description") or detail[:200])
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == attempts:
                raise YoutubeError(f"Google 에 닿지 못했다: {exc}") from exc
            time.sleep(attempt)
    if not raw.strip():
        # 거두기(revoke)는 성공하면 빈 본문이다.
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise YoutubeError(f"Google 응답이 JSON 이 아니다: {raw[:200]!r}") from exc
    if not isinstance(payload, dict):
        raise YoutubeError(f"Google 응답이 객체가 아니다: {raw[:200]!r}")
    return payload
