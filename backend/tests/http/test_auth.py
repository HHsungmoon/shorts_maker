"""로그인 — 세션 토큰과 무차별 대입 방어.

여기서 틀리면 조용히 뚫린다. 서명이 헐거워도, 만료를 안 봐도, 잠금이 안 걸려도
화면상으로는 똑같이 "로그인 됨"이라 눈으로는 알 수 없다.
"""

import time
import unittest
from unittest import mock

from shorts_maker.http import auth

PASSWORD = "correct-horse-battery-staple"


class SessionTokenTest(unittest.TestCase):
    def test_accepts_a_token_it_issued(self):
        token = auth.issue(PASSWORD, ttl_hours=12)
        self.assertTrue(auth.verify(PASSWORD, token))

    def test_rejects_a_token_signed_with_another_password(self):
        # 비밀번호를 바꾸면 기존 세션이 전부 끊겨야 한다. 유출됐을 때 이게 유일한 회수 수단이다.
        token = auth.issue(PASSWORD, ttl_hours=12)
        self.assertFalse(auth.verify("something-else", token))

    def test_rejects_an_expired_token(self):
        token = auth.issue(PASSWORD, ttl_hours=12)
        expires, _, signature = token.partition(".")
        self.assertGreater(int(expires), time.time())
        # 시계를 만료 이후로 옮긴다. 서명은 그대로 유효하므로 만료 검사만 보는 테스트다.
        with mock.patch.object(auth.time, "time", return_value=int(expires) + 1):
            self.assertFalse(auth.verify(PASSWORD, token))

    def test_rejects_a_tampered_expiry(self):
        # 🔴 서명이 만료시각을 덮지 않으면 브라우저에서 숫자만 늘려 세션을 영구화할 수 있다.
        token = auth.issue(PASSWORD, ttl_hours=12)
        expires, _, signature = token.partition(".")
        forged = f"{int(expires) + 999999}.{signature}"
        self.assertFalse(auth.verify(PASSWORD, forged))

    def test_rejects_malformed_tokens(self):
        for bad in ("", ".", "abc", "abc.def", "123", f"{int(time.time()) + 60}."):
            with self.subTest(token=bad):
                self.assertFalse(auth.verify(PASSWORD, bad))

    def test_the_signing_key_is_not_the_password(self):
        # 서명값에서 비밀번호를 곧장 얻을 수 있으면 안 된다.
        self.assertNotIn(PASSWORD.encode(), auth._key(PASSWORD))


class ThrottleTest(unittest.TestCase):
    def setUp(self):
        auth.reset_throttle()

    tearDown = setUp

    def test_a_correct_password_passes(self):
        self.assertTrue(auth.check(PASSWORD, PASSWORD))
        self.assertEqual(auth.locked_for(), 0)

    def test_locks_out_after_repeated_failures(self):
        for _ in range(auth.MAX_FAILURES):
            self.assertFalse(auth.check(PASSWORD, "wrong"))
        self.assertGreater(auth.locked_for(), 0)

    def test_a_success_clears_the_failure_count(self):
        # 하루 종일 오타를 냈다고 해서 그 뒤의 정상 사용이 잠기면 안 된다.
        for _ in range(auth.MAX_FAILURES - 1):
            auth.check(PASSWORD, "wrong")
        self.assertTrue(auth.check(PASSWORD, PASSWORD))
        for _ in range(auth.MAX_FAILURES - 1):
            auth.check(PASSWORD, "wrong")
        self.assertEqual(auth.locked_for(), 0)

    def test_the_lockout_expires(self):
        for _ in range(auth.MAX_FAILURES):
            auth.check(PASSWORD, "wrong")
        with mock.patch.object(auth.time, "time", return_value=time.time() + auth.LOCKOUT_SEC + 1):
            self.assertEqual(auth.locked_for(), 0)


if __name__ == "__main__":
    unittest.main()
