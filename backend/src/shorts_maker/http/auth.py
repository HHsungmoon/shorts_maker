"""관리자 로그인 (비밀번호 1개 + 서명된 세션 쿠키).

Spring 프록시 뒤에 있을 때는 이 파일이 필요 없었다 — backend 가 이미 REALM_ADMIN 을
확인했고, 이 서비스는 도커 네트워크 안에서만 닿았다. 독립하면서 그 전제가 사라졌다.

**회원가입은 없다.** 운영자 1명이 쓰는 도구라 비밀번호 하나면 충분하다. 사용자 개념을
넣는 순간 모든 테이블에 소유자 스코프가 필요해지고, 워커 1개짜리 잡 큐가 사용자 간
공유 병목이 된다(설계 문서 §13) — 두 번째 사용자가 실제로 생길 때 할 일이다.

라이브러리를 쓰지 않는 이유: 서명 토큰은 stdlib hmac 으로 20줄이면 끝나고, 이 레포는
같은 이유로 .env 파서도 직접 두고 있다(config.py).
"""

import base64
import hmac
import time
from hashlib import sha256

COOKIE_NAME = "sm_session"

# 🔴 비밀번호가 하나뿐이라 무차별 대입이 실제 위협이다. 창을 두지 않으면 초당 수천 번
# 시도로 짧은 비밀번호는 며칠이면 뚫린다.
#
# 실패 카운터는 IP 별이 아니라 **전역**이다. IP 를 돌려가며 우회하는 걸 막는 대신,
# 공격자가 운영자를 잠글 수 있다(DoS). 1인용 도구에서는 뚫리는 쪽이 더 나쁘고 창이
# 60초라 실사용에 걸리지 않아 이 절충을 택했다. 프로세스 메모리에만 있어서 재기동하면
# 풀린다 — 재기동을 유발할 수 있는 공격자라면 이미 더 큰 문제가 있다.
MAX_FAILURES = 5
LOCKOUT_SEC = 60

_failures = 0
_locked_until = 0.0


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


def issue(password: str, ttl_hours: int) -> str:
    """`<만료 epoch>.<서명>` 형태의 세션 토큰.

    담는 게 만료시각뿐이라 사용자 식별자가 없다 — 계정이 하나뿐이라 필요가 없다.
    """
    expires = int(time.time()) + ttl_hours * 3600
    return f"{expires}.{_sign(_key(password), str(expires))}"


def verify(password: str, token: str) -> bool:
    head, _, signature = token.partition(".")
    if not signature or not head.isdigit():
        return False
    # 서명을 먼저 본다. 만료 검사를 먼저 하면 위조 토큰과 만료 토큰의 응답 시간이 갈린다.
    if not hmac.compare_digest(signature, _sign(_key(password), head)):
        return False
    return int(head) > time.time()


def locked_for() -> int:
    """지금 잠겨 있으면 남은 초, 아니면 0."""
    remaining = _locked_until - time.time()
    return max(0, int(remaining)) if remaining > 0 else 0


def check(password: str, attempt: str) -> bool:
    """비밀번호를 확인하고 실패 카운터를 갱신한다. 잠긴 동안에는 호출하지 않는다."""
    global _failures, _locked_until
    # 🔴 == 로 비교하면 일치하는 접두사 길이만큼 시간이 달라져 한 글자씩 복원된다.
    if hmac.compare_digest(attempt, password):
        _failures = 0
        return True
    _failures += 1
    if _failures >= MAX_FAILURES:
        _locked_until = time.time() + LOCKOUT_SEC
        _failures = 0
    return False


def reset_throttle() -> None:
    """테스트 전용 — 모듈 전역 상태가 테스트 간에 새지 않게 한다."""
    global _failures, _locked_until
    _failures = 0
    _locked_until = 0.0
