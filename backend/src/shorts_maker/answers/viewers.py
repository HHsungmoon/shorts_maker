"""익명 시청자 — 쿠키 발급과 공개 쓰기 레이트리밋 (tease §7-4).

시청자에게는 계정이 없다. 첫 방문에 UUID 쿠키를 주고 그게 `viewer_id` 다. 좋아요 중복을
막고("한 사람 한 표"), 질문이 누구 것인지 표시하는 데만 쓴다.

🔴 **완벽하지 않다.** 쿠키를 지우면 새 사람이 된다 — 좋아요를 연타로 늘릴 수 있다.
데모와 초기 서비스에서는 받아들이는 절충이고, 제출 문서에도 이 한계를 적는다. 본격
운영은 로그인이든 기기 지문이든 다른 얘기다.

레이트리밋은 프로세스 메모리에 있다(로그인 잠금과 같은 방식 — `http/auth.py`). 재기동하면
풀리고 워커가 여럿이 되면 워커마다 따로 센다. 지금은 프로세스가 하나라 맞고, 여럿이 되는
순간 Postgres 나 Redis 로 옮겨야 한다 — 그때 이 주석을 근거로 옮긴다.
"""

import time
import uuid

from fastapi import HTTPException, Request, Response

# 세션 쿠키가 `sm_session` 이라 접두사를 맞췄다. 🔴 제품명(TEASE)을 쿠키에 넣지 않는다 —
# 이름은 바뀔 수 있고 쿠키 이름이 바뀌면 그날 이후 방문자가 전부 새 사람이 된다.
COOKIE_NAME = "sm_viewer"

# 1년. 좋아요 중복 방지가 쿠키 수명만큼만 유지된다 — 짧게 잡을 이유가 없다.
COOKIE_MAX_AGE = 365 * 24 * 3600

# 분당 허용치. IP 는 그 3배 — 학교·회사처럼 여러 사람이 한 주소를 쓰는 경우를 막지 않으면서
# 쿠키를 지워가며 도는 한 사람은 걸리게 하는 지점이다.
LIMITS = {"question": 5, "like": 30}
IP_MULTIPLIER = 3
WINDOW_SEC = 60

# (종류, 키) → 최근 요청 시각들. 창을 벗어난 값은 셀 때 버린다.
_hits: dict[tuple[str, str], list[float]] = {}


def _client_ip(request: Request) -> str:
    """🔴 `X-Real-IP` 만 믿는다. nginx 가 이 헤더를 **덮어쓰기** 때문이다
    (`proxy_set_header X-Real-IP $remote_addr`).

    `X-Forwarded-For` 는 쓰지 않는다 — nginx 설정이 `$proxy_add_x_forwarded_for` 라
    클라이언트가 보낸 값 뒤에 덧붙이는 형태고, 그러면 첫 항목을 아무나 위조해 IP 한도를
    피해 갈 수 있다. 프록시 없이 직접 띄운 경우(로컬)는 소켓 주소로 떨어진다.
    """
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    return request.client.host if request.client else "unknown"


def viewer_id(request: Request, response: Response) -> str:
    """쿠키에서 시청자 id 를 읽고, 없으면 발급한다.

    `/api/watch/**` 라우터에 라우터 레벨로 걸려 있어서 **모든** 공개 요청이 이걸 지난다 —
    목록만 봐도 쿠키가 생긴다. FastAPI 가 요청당 의존성 결과를 캐시하므로, 핸들러가
    `Depends(viewer_id)` 로 다시 받아도 한 번만 돈다.

    쿠키 값이 UUID 모양이 아니면 새로 발급한다. 남이 넣은 임의 문자열이 `viewer_id` 로
    DB 에 들어가는 걸 막는다 — 길이 제한이 없으면 그대로 저장된다.
    """
    found = request.cookies.get(COOKIE_NAME)
    if found:
        try:
            return str(uuid.UUID(found))
        except ValueError:
            pass
    issued = str(uuid.uuid4())
    response.set_cookie(
        COOKIE_NAME,
        issued,
        max_age=COOKIE_MAX_AGE,
        # JS 가 못 읽는다. 시청자 화면은 "내가 좋아요 했나"를 서버 응답(likedByMe)으로 받는다.
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        path="/",
    )
    return issued


def _cookie_secure() -> bool:
    # 지연 import — deps 가 이 모듈을 부르지 않지만, 설정은 호출 시점에 읽어야 테스트의 교체가 반영된다.
    from ..http import deps

    return deps.cfg.cookie_secure


def _count(kind: str, key: str, now: float) -> int:
    stamps = [t for t in _hits.get((kind, key), []) if now - t < WINDOW_SEC]
    _hits[(kind, key)] = stamps
    return len(stamps)


def check_rate(request: Request, viewer: str, kind: str) -> None:
    """한도를 넘으면 429. 넘지 않으면 이번 요청을 기록한다.

    🔴 쓰기 엔드포인트에서만 부른다. 읽기까지 세면 목록을 새로고침하는 것만으로 막힌다.
    """
    limit = LIMITS[kind]
    now = time.monotonic()
    ip = _client_ip(request)
    if _count(kind, f"v:{viewer}", now) >= limit or _count(kind, f"i:{ip}", now) >= limit * IP_MULTIPLIER:
        raise HTTPException(429, "너무 잦습니다. 잠시 뒤에 다시 시도하세요.")
    _hits[(kind, f"v:{viewer}")].append(now)
    _hits[(kind, f"i:{ip}")].append(now)


def reset_limits() -> None:
    """테스트 전용 — 모듈 전역 카운터가 테스트 간에 새지 않게 한다."""
    _hits.clear()
