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
from datetime import datetime, timezone

import psycopg
from fastapi import Header, HTTPException, Request

from .. import config, jobs
from . import auth
from ..db import store

cfg = config.load()

# 🔴 워커 1개. 동시 1건은 정책이 아니라 구조다(jobs.py) — 큐를 두 개 만들면 그 구조가 깨진다.
queue = jobs.JobQueue()


# 🔴 **읽기로 치는 메서드.** 나머지는 전부 행동이다.
#
# 역할을 엔드포인트마다 적지 않는 이유는 라우터 레벨 인증과 같다 — 적는 방식은 새로 생긴 것을
# 반드시 빠뜨린다. 메서드로 가르면 새 엔드포인트가 자동으로 옳은 쪽에 들어가고, 라우트 테이블을
# 순회하는 테스트가 그걸 증명한다(tests/http/test_api_auth.py).
#
# GET 인데 실제로는 행동인 것이 하나 있다: 구간 미리보기는 없으면 ffmpeg 로 만든다. 그건 그
# 자리에서 따로 막는다(http/studio.py).
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def require_auth(request: Request, x_shorts_token: str | None = Header(default=None)) -> None:
    """세션 쿠키 **또는** X-Shorts-Token 중 하나면 통과한다. 역할에 따라 행동을 막는다.

    쿠키는 브라우저용, 토큰은 CLI·스크립트 같은 기계 클라이언트용이다. 둘 다 설정돼
    있지 않으면 인증하지 않는다 — 루프백 전용 로컬 개발 모드이고, 그 상태로 외부에
    열리는 것은 server.check_binding 이 막는다.

    🔴 역할이 둘이다(2026-09-16). `admin` 은 전부, `readonly` 는 **읽기만** 한다. 읽기와 행동은
    `SAFE_METHODS` 로 가른다. 보기 전용이 행동을 부르면 401 이 아니라 **403** 이다 — 로그인은
    돼 있으니 다시 로그인하라고 하면 안 된다.

    엔드포인트마다 `Depends` 를 붙이지 않는다. `studio.router` 가 **라우터 레벨**로
    걸어서, 거기 등록되는 모든 라우트가 자동으로 이 검사를 거친다(update_plan D6).
    """
    if not cfg.admin_password and not cfg.api_token:
        # 로컬 개발 모드. 🔴 보기 전용 비밀번호만 넣는 것으로는 인증이 켜지지 않는다 —
        # 그 조합은 "관리자 비밀번호 없이 공개" 와 같고, check_binding 이 애초에 기동을 막는다.
        request.state.role = auth.ADMIN
        return
    if cfg.api_token and x_shorts_token and hmac.compare_digest(x_shorts_token, cfg.api_token):
        # 기계 클라이언트(CLI·배포 스크립트)는 전권이다. 사람이 브라우저로 쓰는 길이 아니다.
        request.state.role = auth.ADMIN
        return
    role = auth.role_of(
        cfg.admin_password, cfg.readonly_password, request.cookies.get(auth.COOKIE_NAME)
    )
    if role is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    if role != auth.ADMIN and request.method.upper() not in SAFE_METHODS:
        raise HTTPException(
            status_code=403, detail="보기 전용으로 로그인했습니다. 이 동작은 관리자만 할 수 있습니다."
        )
    request.state.role = role


def current_role(request: Request) -> str:
    """이 요청의 역할. `require_auth` 를 통과한 뒤에만 뜻이 있다."""
    return getattr(request.state, "role", auth.ADMIN)


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
    """잡을 띄운다. 이미 도는 게 있으면 **409** 다.

    🔴 거절을 엔드포인트마다 적지 않고 여기 한 곳에 둔다 — 실행 엔드포인트가 열두 개이고,
    적는 방식은 새로 생긴 것을 반드시 빠뜨린다(라우터 레벨 인증과 같은 이유).

    409 를 고른 이유: 요청 자체는 옳고 **지금 상태와 충돌**할 뿐이다. 400 이면 사용자가 잘못
    보낸 것처럼 읽히고, 503 이면 서버가 고장난 것처럼 읽힌다.
    """
    try:
        return queue.submit(kind, target, fn).as_dict()
    except jobs.Busy as busy:
        running = busy.running
        started = datetime.fromisoformat(running.created_at)
        seconds = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
        raise HTTPException(
            status_code=409,
            detail=(
                f"다른 작업이 실행 중입니다 — {running.kind}({running.target}), {seconds}초 경과. "
                "한 번에 하나만 돌릴 수 있습니다. 끝난 뒤에 다시 눌러 주세요."
            ),
        ) from busy
