"""관리자 로그인 (비밀번호 1개 + 서명된 세션 쿠키).

Spring 프록시 뒤에 있을 때는 이 파일이 필요 없었다 — backend 가 이미 REALM_ADMIN 을
확인했고, 이 서비스는 도커 네트워크 안에서만 닿았다. 독립하면서 그 전제가 사라졌다.

**회원가입은 없다.** 계정이 아니라 **역할이 둘**이다(2026-09-16).

  `admin`    — 전부 할 수 있다. `SHORTS_ADMIN_PASSWORD`
  `readonly` — **보기만** 한다. `SHORTS_READONLY_PASSWORD`

🔴 보기 전용을 만든 이유는 편의가 아니다. 해커톤 심사처럼 여럿이 스튜디오를 들여다보는 동안
누군가 [답하기]를 누르면 Gemini 하루 할당량이 타고 실데이터가 바뀐다. 구경은 열되 행동은 막는
자리가 필요했다. 무엇이 행동인지는 **HTTP 메서드**로 가른다(http/deps.py) — 엔드포인트마다
적으면 새로 생긴 것을 반드시 빠뜨린다.

사용자 개념(가입·소유자)은 여전히 없다. 넣는 순간 모든 테이블에 소유자 스코프가 필요해지고,
워커 1개짜리 잡 큐가 사용자 간 공유 병목이 된다(설계 문서 §13).

라이브러리를 쓰지 않는 이유: 서명 토큰은 stdlib hmac 으로 20줄이면 끝나고, 이 레포는
같은 이유로 .env 파서도 직접 두고 있다(config.py).
"""

import base64
import hmac
import threading
import time
from hashlib import sha256

COOKIE_NAME = "sm_session"

# 🔴 무차별 대입은 실제 위협이다. 창이 없으면 초당 수천 번 시도로 짧은 비밀번호는 며칠이면 뚫린다.
#
# **실패는 IP 별로 센다**(2026-09-16). 예전에는 프로세스 전역 카운터 하나였고, 1인용 도구라는
# 전제에서는 그 절충이 맞았다. 보기 전용 비밀번호를 **화면에 공개**하면서 전제가 바뀌었다 —
# 서로 모르는 여러 명이 동시에 로그인한다. 전역 카운터면 **남 다섯 명의 오타가 나를 잠근다**:
# 복사하다 끝에 붙은 공백 하나면 실패 한 번이고, 다섯 번이면 60초 동안 아무도(관리자 포함)
# 못 들어온다. 심사 도중 가장 먼저 터질 자리였다.
#
# IP 를 돌려가며 두드리는 공격은 **전역 백스톱**이 받는다 — 창 안 전체 실패가 GLOBAL_MAX_FAILURES
# 를 넘으면 그때는 다 같이 잠근다. 평범한 오타로는 닿지 않는 값이라 사람을 막지 않는다.
#
# 세는 방식도 바꿨다: 성공할 때까지 누적하는 대신 **창 안의 실패**만 본다. 하루 종일 드문드문
# 틀린 것이 쌓여 잠기는 일이 없다.
#
# 상태는 프로세스 메모리에만 있다 — 재기동하면 풀린다. 재기동을 일으킬 수 있는 공격자라면
# 이미 더 큰 문제가 있다.
MAX_FAILURES = 5          # 한 IP 가 창 안에 낼 수 있는 실패
WINDOW_SEC = 60           # 실패를 세는 창. 지나간 실패는 잊는다
LOCKOUT_SEC = 60
GLOBAL_MAX_FAILURES = 50  # 창 안 전체 실패. IP 를 바꿔 가며 두드리는 경우의 백스톱
MAX_TRACKED_IPS = 4096    # 메모리 상한

# 🔴 uvicorn 의 동기 엔드포인트는 스레드풀에서 병렬로 돈다 — 카운터를 락 없이 건드리면
# 동시에 들어온 시도가 서로의 증가를 덮어써 잠금이 늦게 걸린다.
_lock = threading.Lock()
_failures: dict[str, list[float]] = {}  # ip → 최근 실패 시각
_locked_until: dict[str, float] = {}  # ip → 잠금이 풀리는 시각
_global_failures: list[float] = []
_global_locked_until = 0.0


def _key(password: str) -> bytes:
    """서명 키를 비밀번호에서 파생한다.

    비밀번호를 그대로 키로 쓰지 않는 이유: 쿠키에 실려 나가는 서명값에서 비밀번호 자체를
    공격할 여지를 남기지 않기 위해서다. 파생이라 **비밀번호를 바꾸면 기존 세션이 전부
    무효**가 된다 — 유출됐을 때 원하는 동작이다.
    """
    return hmac.new(b"shorts_maker/session-v1", password.encode("utf-8"), sha256).digest()


def _sign(key: bytes, message: str) -> str:
    digest = hmac.new(key, message.encode("ascii"), sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


ADMIN = "admin"
READONLY = "readonly"
ROLES = (ADMIN, READONLY)


def issue(password: str, ttl_hours: int, role: str = ADMIN) -> str:
    """`<역할>.<만료 epoch>.<서명>` 형태의 세션 토큰.

    🔴 **역할이 서명 안에 들어간다.** 쿠키 문자열의 `readonly` 를 `admin` 으로 고치면 서명이
    깨진다. 게다가 역할마다 서명 키가 다르다 — 키는 그 역할의 비밀번호에서 파생된다.

    형식이 2026-09-16 에 바뀌었다(예전에는 `<만료>.<서명>`). 옛 쿠키는 verify 에서 떨어지고
    사용자는 다시 로그인한다 — 세션 하나 끊기는 쪽이 역할 없는 토큰을 관리자로 읽는 쪽보다 낫다.
    """
    if role not in ROLES:
        raise ValueError(f"없는 역할이다: {role}")
    expires = int(time.time()) + ttl_hours * 3600
    return f"{role}.{expires}.{_sign(_key(password), f'{role}.{expires}')}"


def verify(password: str, token: str, role: str = ADMIN) -> bool:
    """이 토큰이 **그 역할로** 유효한가."""
    parts = token.split(".")
    if len(parts) != 3:
        return False
    got_role, head, signature = parts
    if got_role != role or not head.isdigit():
        return False
    # 서명을 먼저 본다. 만료 검사를 먼저 하면 위조 토큰과 만료 토큰의 응답 시간이 갈린다.
    if not hmac.compare_digest(signature, _sign(_key(password), f"{got_role}.{head}")):
        return False
    return int(head) > time.time()


def role_of(admin_password: str, readonly_password: str, token: str | None) -> str | None:
    """이 쿠키가 말하는 역할. 없거나 위조면 None.

    🔴 관리자를 먼저 본다. 두 비밀번호를 같게 설정한 실수에서도 권한이 낮은 쪽으로 새지 않는다.
    """
    if not token:
        return None
    if admin_password and verify(admin_password, token, ADMIN):
        return ADMIN
    if readonly_password and verify(readonly_password, token, READONLY):
        return READONLY
    return None


def _prune(now: float) -> None:
    """창을 벗어난 기록을 버린다. 부르는 쪽이 `_lock` 을 쥐고 있어야 한다."""
    global _global_locked_until
    for ip in list(_failures):
        fresh = [t for t in _failures[ip] if now - t < WINDOW_SEC]
        if fresh:
            _failures[ip] = fresh
        else:
            del _failures[ip]
    for ip in list(_locked_until):
        if _locked_until[ip] <= now:
            del _locked_until[ip]
    _global_failures[:] = [t for t in _global_failures if now - t < WINDOW_SEC]
    if _global_locked_until <= now:
        _global_locked_until = 0.0
    # 🔴 무한히 자라는 dict 를 프로세스에 두지 않는다. X-Real-IP 는 nginx 가 덮어써서 위조할 수
    # 없으니 실제로는 방문자 수만큼만 자라지만, 상한 없는 메모리는 그 자체로 버그다.
    if len(_failures) > MAX_TRACKED_IPS:
        _failures.clear()


def locked_for(ip: str) -> int:
    """이 IP 가 지금 잠겨 있으면 남은 초, 아니면 0. 전역 백스톱이 걸렸으면 모두가 잠긴다."""
    now = time.time()
    with _lock:
        _prune(now)
        until = max(_locked_until.get(ip, 0.0), _global_locked_until)
    remaining = until - now
    return max(0, int(remaining)) if remaining > 0 else 0


def _record_failure(ip: str) -> None:
    """부르는 쪽이 `_lock` 을 쥔다."""
    global _global_locked_until
    now = time.time()
    _prune(now)
    _failures.setdefault(ip, []).append(now)
    _global_failures.append(now)
    if len(_failures[ip]) >= MAX_FAILURES:
        _locked_until[ip] = now + LOCKOUT_SEC
        _failures.pop(ip, None)
    if len(_global_failures) >= GLOBAL_MAX_FAILURES:
        _global_locked_until = now + LOCKOUT_SEC
        _global_failures.clear()


def _forget(ip: str) -> None:
    """성공한 IP 의 실패 기록을 지운다. 부르는 쪽이 `_lock` 을 쥔다."""
    _failures.pop(ip, None)
    _locked_until.pop(ip, None)


def _matches(secret: str, attempt: str) -> bool:
    """🔴 `==` 로 비교하면 일치하는 접두사 길이만큼 시간이 달라져 한 글자씩 복원된다.

    앞뒤 공백을 뗀 값도 받아 준다. 비밀번호를 **화면에서 복사해 붙이는** 사용이 기본이 되면서
    끝에 붙은 공백 하나가 실패로 세는 일이 실제로 생긴다 — 그 실패는 보안이 아니라 잠금만 채운다.
    """
    return hmac.compare_digest(attempt, secret) or hmac.compare_digest(attempt.strip(), secret)


def check(password: str, attempt: str, ip: str) -> bool:
    """비밀번호를 확인하고 그 IP 의 실패 기록을 갱신한다. 잠긴 동안에는 호출하지 않는다."""
    with _lock:
        if _matches(password, attempt):
            _forget(ip)
            return True
        _record_failure(ip)
        return False


def authenticate(admin_password: str, readonly_password: str, attempt: str, ip: str) -> str | None:
    """비밀번호로 역할을 정한다. 맞으면 역할, 아니면 None.

    🔴 실패는 **한 번만** 센다. 두 비밀번호를 각각 `check` 로 확인하면 틀린 입력 하나가
    카운터를 둘 올려 잠금이 절반 속도로 찬다.
    """
    with _lock:
        if admin_password and _matches(admin_password, attempt):
            _forget(ip)
            return ADMIN
        if readonly_password and _matches(readonly_password, attempt):
            _forget(ip)
            return READONLY
        _record_failure(ip)
        return None


def reset_throttle() -> None:
    """테스트 전용 — 모듈 전역 상태가 테스트 간에 새지 않게 한다."""
    global _global_locked_until
    with _lock:
        _failures.clear()
        _locked_until.clear()
        _global_failures.clear()
        _global_locked_until = 0.0
