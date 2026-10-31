"""유튜브 API — 채널 연결과 Shorts 업로드(tease §7-5, 2026-10-31). **인증 필요.**

🔴 이 라우터는 **스튜디오 라우터의 하위 라우터**다. `studio.py` 가 `router.include_router(youtube.router)`
로 붙이고, 그때 스튜디오 라우터의 `/api` 접두사와 `require_auth` 가 이 라우트들에 함께 붙는다. 그래서
여기에는 접두사도 인증도 적지 않는다. 🔴 앱(`server.py`)에 **직접** 붙이면 인증 없이 열린다 —
`tests/http/test_api_auth.py` 가 `/api` 밖 라우트를, `tests/http/test_youtube.py` 가 이 라우터의 소속을 본다.

도메인 로직은 없다. 무엇을 저장하고 언제 거절하나는 `pipeline/youtube_channel.py`(연결) 와
`pipeline/upload.py`(업로드) 가 정하고, 여기는 HTTP 로 옮기기만 한다.

🔴 콜백은 **GET 인데 행동이다**(토큰을 저장한다). Google 이 브라우저를 GET 으로 돌려보내니 메서드를
바꿀 수 없고, 그래서 `deps.SAFE_METHODS` 로 갈리지 않는다. 구간 미리보기처럼 그 자리에서 막는다 —
① state 가 이 서버가 방금 만든 것인지(CSRF) ② 관리자인지. state 는 관리자만 부를 수 있는 POST 에서만
나오므로 ②는 이중 울타리다. ①을 먼저 본다 — 틀린 state 는 홈으로 돌려보낸다(303).
"""

import urllib.parse
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from . import auth, deps
from .deps import connect, submit
from ..pipeline import upload, youtube_channel

# 🔴 접두사·의존성을 두지 않는다(모듈 머리 주석). 스튜디오 라우터에 붙는 순간 둘 다 생긴다.
router = APIRouter()


class YoutubeUploadIn(BaseModel):
    """유튜브에 올릴 값. 대화상자에서 사람이 고친 뒤의 것이다.

    🔴 정확한 한도(제목 100자 · 설명 5000바이트 · < > 금지)는 `upload.check_metadata` 가 본다. 여기
    상한은 터무니없는 입력만 막는다 — 잘라서 받으면 사람이 쓴 적 없는 제목이 채널에 올라간다(III.C.3).
    공개 범위는 기본값을 두지 않는다. 고르는 것 자체가 정책상 요구다(III.E.3).
    """

    title: str = Field(max_length=1000)
    description: str = Field(default="", max_length=20000)
    privacy: Literal["private", "unlisted", "public"]


def _back_home(result: str, reason: str | None = None) -> RedirectResponse:
    # 홈이 이 값을 읽어 안내를 띄우고 주소에서 지운다(StudioHome). 이유는 짧게 — 주소창에 남는 글이다.
    query = {"youtube": result}
    if reason:
        query["reason"] = reason[:300]
    return RedirectResponse(f"/studio?{urllib.parse.urlencode(query)}", status_code=303)


# ---------------------------------------------------------------- 채널 연결

@router.get("/youtube")
def youtube_state() -> dict:
    """연결 상태. 🔴 refresh token 은 싣지 않는다 — 보기 전용도 읽는 응답이다."""
    with connect() as conn:
        found = youtube_channel.channel(conn)
    return {
        "configured": youtube_channel.configured(deps.cfg),
        "redirectUri": deps.cfg.youtube_redirect_uri,
        "channel": found,
    }


@router.post("/youtube/connect")
def youtube_connect(request: Request) -> dict:
    """동의 화면 주소를 돌려준다. 화면이 그 주소로 이동한다."""
    try:
        return {"url": youtube_channel.start(deps.cfg, request.url.hostname)}
    except youtube_channel.ChannelError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/youtube/callback")
def youtube_callback(
    request: Request, state: str | None = None, code: str | None = None, error: str | None = None,
) -> RedirectResponse:
    """Google 이 돌려보내는 자리. 결과는 홈으로 넘겨 안내한다 — JSON 을 그대로 띄우면 사람이 갈 곳을 잃는다."""
    if not youtube_channel.take_state(state):
        return _back_home("failed", "연결 요청이 만료됐거나 이 서버가 만든 것이 아닙니다. 다시 연결하세요")
    if deps.current_role(request) != auth.ADMIN:
        raise HTTPException(403, "보기 전용에서는 채널을 연결할 수 없습니다")
    if error:
        # access_denied — 동의 화면에서 [취소] 를 눌렀다.
        return _back_home("denied", error)
    if not code:
        return _back_home("failed", "Google 이 code 를 주지 않았습니다")
    with connect() as conn:
        try:
            youtube_channel.finish(conn, deps.cfg, code)
        except youtube_channel.ChannelError as exc:
            return _back_home("failed", str(exc))
        conn.commit()
    return _back_home("connected")


@router.delete("/youtube")
def youtube_disconnect() -> dict:
    """연결을 끊는다. 올린 기록은 남는다(`youtube_channel.disconnect`)."""
    with connect() as conn:
        return youtube_channel.disconnect(conn)


# ---------------------------------------------------------------- 업로드

@router.get("/clips/{clip_id}/youtube")
def youtube_upload_form(clip_id: int) -> dict:
    """업로드 대화상자에 채울 값과 이 클립을 올린 기록."""
    with connect() as conn:
        try:
            defaults = upload.defaults(conn, clip_id)
        except upload.ClipNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        return {
            "defaults": defaults,
            "uploads": upload.history(conn, clip_id),
            "channel": youtube_channel.channel(conn),
            "configured": youtube_channel.configured(deps.cfg),
        }


@router.post("/clips/{clip_id}/youtube")
def youtube_upload(clip_id: int, body: YoutubeUploadIn) -> dict:
    """연결된 채널에 Shorts 로 올린다. 잡이다(워커 1개 — 도는 잡이 있으면 409).

    🔴 사람이 누를 때만 온다. 이 요청 하나가 개발자 정책이 말하는 "명시적 동의" 다(III.I.2).
    """
    try:
        title, description, privacy = upload.check_metadata(body.title, body.description, body.privacy)
    except upload.UploadError as exc:
        raise HTTPException(422, str(exc)) from exc
    with connect() as conn:
        try:
            upload.check_ready(conn, deps.cfg, clip_id)
        except upload.ClipNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except upload.NotReady as exc:
            raise HTTPException(409, str(exc)) from exc

    def work() -> dict:
        with connect() as conn:
            return upload.run(conn, deps.cfg, clip_id, title, description, privacy)

    return submit("upload", f"clip {clip_id}", work)
