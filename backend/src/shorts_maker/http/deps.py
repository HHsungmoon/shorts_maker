"""프로세스 전역 상태와 FastAPI 의존성 — 라우터들이 공유하는 것.

`server.py`(앱 조립 · 공개 라우트) 와 `studio.py`(인증 필요한 `/api/**`) 가 둘 다 설정과
잡 큐, 인증 의존성을 쓴다. 한쪽 모듈에 두면 다른 쪽이 그 모듈을 import 해야 하고, 앱을
조립하는 `server.py` 는 라우터 모듈을 import 하므로 순환이 된다. 그래서 셋을 여기로 뺐다.

🔴 `cfg` 는 **호출 시점에** 읽어야 한다(`deps.cfg`). `from .deps import cfg` 로 값을 복사해
가면 테스트가 `deps.cfg` 를 갈아끼워도 그 모듈에는 반영되지 않는다 — test_api_auth.py 가
그 방식으로 앱을 테스트한다.
"""

import hmac
from contextlib import AbstractContextManager

import psycopg
from fastapi import Header, HTTPException, Request

from .. import config, jobs
from . import auth
from ..db import store

cfg = config.load()

# 🔴 워커 1개. 동시 1건은 정책이 아니라 구조다(jobs.py) — 큐를 두 개 만들면 그 구조가 깨진다.
queue = jobs.JobQueue()


def require_auth(request: Request, x_shorts_token: str | None = Header(default=None)) -> None:
    """세션 쿠키 **또는** X-Shorts-Token 중 하나면 통과한다.

    쿠키는 브라우저용, 토큰은 CLI·스크립트 같은 기계 클라이언트용이다. 둘 다 설정돼
    있지 않으면 인증하지 않는다 — 루프백 전용 로컬 개발 모드이고, 그 상태로 외부에
    열리는 것은 server.check_binding 이 막는다.

    엔드포인트마다 `Depends` 를 붙이지 않는다. `studio.router` 가 **라우터 레벨**로
    걸어서, 거기 등록되는 모든 라우트가 자동으로 이 검사를 거친다(update_plan D6).
    """
    if not cfg.admin_password and not cfg.api_token:
        return
    if cfg.api_token and x_shorts_token and hmac.compare_digest(x_shorts_token, cfg.api_token):
        return
    if cfg.admin_password:
        token = request.cookies.get(auth.COOKIE_NAME)
        if token and auth.verify(cfg.admin_password, token):
            return
    raise HTTPException(status_code=401, detail="로그인이 필요합니다")


def connect() -> AbstractContextManager[psycopg.Connection]:
    """`with connect() as conn:` — 풀에서 연결을 빌려 정상 종료면 commit, 예외면 rollback.

    `store.connect` 가 이미 컨텍스트 매니저라 그대로 돌려준다. `cfg` 는 여기서 호출 시점에
    읽는다(모듈 docstring) — 테스트가 `deps.cfg` 를 갈아끼우면 다음 호출부터 그 DB 로 간다.
    """
    return store.connect(cfg.database_url)


def rows(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[dict]:
    # 풀이 dict_row 로 열어 두어 행이 이미 dict 다. 복사해서 호출부가 키를 덧붙여도 안전하게 한다.
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def submit(kind: str, target: str, fn) -> dict:
    return queue.submit(kind, target, fn).as_dict()
