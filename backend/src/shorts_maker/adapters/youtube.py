"""유튜브 — OAuth 토큰과 영상 업로드(tease §7-5, 2026-10-31).

하는 일은 넷뿐이다: 동의 화면 주소 만들기 · code 를 토큰으로 바꾸기 · refresh token 으로 access token
받기 · 영상 올리기(resumable 업로드 두 단계: 세션 열기 → 바이트 PUT).

🔴 **SDK(google-api-python-client)를 쓰지 않는다.** 위 넷이 POST/PUT 몇 번이고, SDK 를 써도 런타임
동작은 같다 — 규약상 "코드가 줄어든다" 는 채택 근거가 아니다. jev.py 처럼 stdlib urllib 로 직접 부른다.

🔴 범위는 `youtube.upload` 하나다. 개발자 정책 III.D.2 — 지금 쓰는 범위만 요청한다. 그래서 이 토큰으로는
channels.list 를 못 부르고, 채널 이름은 업로드 응답에서 알게 된다(pipeline/upload.py).

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

UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
SCOPES = (UPLOAD_SCOPE,)

# 토큰 요청은 몇백 ms 다. 업로드 세션 열기도 메타데이터 POST 하나라 짧다.
TOKEN_TIMEOUT_SEC = 15.0
INIT_TIMEOUT_SEC = 30.0
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
    - `include_granted_scopes=false`: 예전에 다른 범위를 줬더라도 이번 토큰은 업로드 하나만 갖게 한다.
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
            raise _upload_error(exc) from exc
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
                raise _upload_error(exc) from exc
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
    "forbidden": "이 채널에 올릴 권한이 없다. 연결한 계정과 채널을 확인하세요",
}


def _upload_error(exc: urllib.error.HTTPError) -> YoutubeError:
    detail = exc.read().decode("utf-8", "replace")
    reason = message = ""
    try:
        error = json.loads(detail).get("error") or {}
        message = str(error.get("message") or "")
        reasons = [str(item.get("reason")) for item in error.get("errors") or [] if isinstance(item, dict)]
        reason = next((r for r in reasons if r in REASONS), reasons[0] if reasons else "")
    except (json.JSONDecodeError, AttributeError):
        message = detail[:300]
    hint = REASONS.get(reason)
    # 🔴 401 이라고 다 만료가 아니다. 채널 없는 계정(youtubeSignupRequired)도 401 인데, 그걸 "다시 연결하라" 로
    # 읽히면 사람은 같은 계정으로 계속 다시 연결한다. 이유를 아는 것은 이유를 먼저 말한다.
    if exc.code == 401 and hint is None:
        return ReconnectNeeded(f"유튜브가 토큰을 받지 않았다(401). 채널을 다시 연결하세요 — {message}")
    original = f"YouTube {exc.code} {reason}: {message}".strip()
    return YoutubeError(f"{hint} ({original})" if hint else original)


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
