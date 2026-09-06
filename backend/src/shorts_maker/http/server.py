"""앱 조립과 **공개** 라우트 — /auth/** · /health · 프론트 서빙 (문서 §10 C · §13).

원래는 Spring 백엔드만 호출하는 내부 API 였다 — 관리자 인증은 backend 가 이미 했고
이 서비스는 도커 네트워크 안에서만 닿았다. **독립 서비스가 되면서 그 전제가 사라졌다.**
이제 브라우저가 직접 붙으므로 여기서 인증한다(auth.py — 비밀번호 1개 + 세션 쿠키).

인증이 필요한 `/api/**` 는 전부 `studio.router` 에 있고, 그 라우터가 **라우터 레벨**로
`require_auth` 를 건다(deps.py). 이 파일에는 인증 **없이** 열려야 하는 것만 남는다 — 로그인
자체, 헬스체크, 정적 프론트. 여기에 `/api/...` 라우트를 직접 등록하면 인증이 빠진다.
`tests/test_api_auth.py` 가 "앱의 모든 /api/** 라우트는 스튜디오 라우터 소속" 을 확인한다.

🔴 그 전제를 코드가 강제한다: 루프백이 아닌 주소에 바인딩하면서 SHORTS_ADMIN_PASSWORD
가 없으면 **기동을 거부한다**(check_binding). 설정 실수로 무인증 인스턴스가 외부에
열리는 경로를 막는다.
"""

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

# 🔴 모듈을 별칭으로 들여온다. 아래 `/debug` 라우트 함수 이름이 `debug_page` 라서 그대로 import 하면
# 함수 정의가 모듈 전역 이름을 덮어써 `debug_page.INDEX_HTML` 이 AttributeError 가 된다.
from ..answers import clusters
from . import auth, deps, studio, watch
from . import debug_page as debug_html
from ..config import LOOPBACK
from ..db import store
from .deps import connect

app = FastAPI(title="shorts_maker", docs_url="/docs")


# ---------------------------------------------------------------- 로그인

class LoginIn(BaseModel):
    password: str


@app.post("/auth/login")
def login(body: LoginIn, response: Response) -> dict:
    cfg = deps.cfg
    if not cfg.admin_password:
        # 로컬 개발 모드. 로그인 화면을 띄울 이유가 없으므로 프론트가 이 응답을 보고 건너뛴다.
        return {"ok": True, "authRequired": False}
    locked = auth.locked_for()
    if locked:
        raise HTTPException(429, f"시도가 너무 많습니다. {locked}초 뒤에 다시 시도하세요.")
    if not auth.check(cfg.admin_password, body.password):
        raise HTTPException(401, "비밀번호가 맞지 않습니다")
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.issue(cfg.admin_password, cfg.session_ttl_hours),
        max_age=cfg.session_ttl_hours * 3600,
        # 🔴 httponly: 쿠키를 JS 에서 못 읽게 한다. XSS 가 나도 세션이 바로 새지는 않는다
        # (admin-web 은 localStorage 였고, 그건 스크립트가 그대로 읽어간다).
        httponly=True,
        # lax: 외부 사이트에서 넘어온 POST 에 쿠키가 실리지 않는다 → CSRF 의 기본 경로가 막힌다.
        samesite="lax",
        secure=cfg.cookie_secure,
        path="/",
    )
    return {"ok": True, "authRequired": True}


@app.post("/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/auth/me")
def me(request: Request) -> dict:
    """프론트가 로그인 화면을 띄울지 결정하는 용도. 401 을 쓰지 않는다 —
    "로그인 안 됨"은 정상 상태이고, 에러로 만들면 콘솔이 매번 빨개진다."""
    cfg = deps.cfg
    if not cfg.admin_password:
        return {"authenticated": True, "authRequired": False}
    token = request.cookies.get(auth.COOKIE_NAME)
    return {
        "authenticated": bool(token and auth.verify(cfg.admin_password, token)),
        "authRequired": True,
    }


@app.get("/health")
def health() -> dict:
    """무인증. 도커 헬스체크와 nginx 가 부른다.

    🔴 설정값을 여기 담지 않는다. backend 프록시 뒤에 있을 때는 이 응답이 도커 네트워크
    밖으로 안 나갔지만 지금은 인터넷에 열려 있다 — 모델명·스키마 버전은 공격자에게
    먼저 주지 않는다. 화면이 쓰는 값은 /api/status 로 옮겼다.
    """
    return {"ok": True}


# ---------------------------------------------------------------- 인증 필요한 API

# 🔴 라우터는 아래 프론트 catch-all **앞에** 붙여야 한다. FastAPI 는 등록 순서대로 매칭한다.
app.include_router(studio.router)

# 🔴 시청자 API. **인증이 없다** — 인터넷에 그대로 열린다. 경로는 전부 `/api/watch/` 로 시작하고,
# 읽기는 발행된 것만 준다(watch.py 머리 주석). 스튜디오 엔드포인트를 실수로 여기 넣으면
# 무인증으로 새므로, `tests/http/test_api_auth.py` 가 두 라우터의 경로 접두사를 검사한다.
app.include_router(watch.router)


# ---------------------------------------------------------------- 프론트

# 🔴 이 아래 라우트들은 경로를 통째로 삼키므로 **반드시 API 라우트 뒤에** 등록돼야 한다.
# 위로 올리면 /api/** 가 전부 프론트로 빨려 들어간다.

@app.get("/debug", response_class=HTMLResponse)
def debug_page() -> str:
    """node 빌드 없이 파이프라인 상태를 보는 화면. `web/dist` 가 없을 때 `/` 도 여기로 온다."""
    return debug_html.INDEX_HTML


def _spa_file(rel: str) -> Path | None:
    """web/dist 안의 실제 파일만 돌려준다. 없으면 None.

    🔴 `..` 로 디렉터리를 빠져나가는 요청을 막는다 — 정적 서빙에서 이 검사를 빼면
    서버의 아무 파일이나 읽힌다.
    """
    root = deps.cfg.web_dir.resolve()
    candidate = (root / rel).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str) -> Any:
    index = deps.cfg.web_dir / "index.html"
    if not index.is_file():
        # 아직 프론트를 빌드하지 않았다. 루트만 개발용 화면으로 받아주고 나머지는 404 다.
        if full_path in ("", "index.html"):
            return HTMLResponse(debug_html.INDEX_HTML)
        raise HTTPException(404, "not found")
    # 없는 API 경로가 index.html 로 200 을 받으면 프론트가 HTML 을 JSON 으로 파싱하다
    # 엉뚱한 곳에서 죽는다. 여기서 404 로 끊는다.
    if full_path.startswith(("api/", "auth/", "health")):
        raise HTTPException(404, "not found")
    found = _spa_file(full_path) if full_path else None
    # 클라이언트 라우팅(/login 등)은 파일이 없다 — index.html 을 주고 브라우저가 처리한다.
    return FileResponse(found or index)


def check_binding() -> None:
    cfg = deps.cfg
    if cfg.api_host not in LOOPBACK and not cfg.admin_password:
        raise SystemExit(
            f"거부: {cfg.api_host} 에 바인딩하려면 SHORTS_ADMIN_PASSWORD 가 필요하다.\n"
            "브라우저가 직접 붙는 서비스라 비밀번호 없이 외부에 열면 무인증으로 노출된다.\n"
            "  openssl rand -base64 24"
        )


def serve() -> None:
    import uvicorn

    check_binding()
    with connect() as conn:
        store.apply_schema(conn)
        # 재기동 시 매달린 잡 정리 — 워커는 메모리에만 있으므로 RUNNING 은 이미 죽은 것이다.
        # sources 의 RUNNING 은 다운로드·등록 잡이 만든다(ingest.begin_source).
        conn.execute("update sources set status = 'FAILED', error = '재기동으로 중단됨' where status = 'RUNNING'")
        conn.execute("update runs set status = 'FAILED', error = '재기동으로 중단됨' where status = 'RUNNING'")
        # 클러스터의 IN_PROGRESS 도 같은 이유로 죽은 것이다 — 크리에이터가 다시 [답하기] 를 누를 수 있게 되돌린다.
        clusters.reopen_stuck(conn)
        conn.commit()
    uvicorn.run(app, host=deps.cfg.api_host, port=deps.cfg.api_port, log_level="info")
