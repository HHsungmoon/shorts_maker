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
        # 형식은 `<역할>.<만료>.<서명>` 이다(2026-09-16).
        _, expires, _ = token.split(".")
        self.assertGreater(int(expires), time.time())
        # 시계를 만료 이후로 옮긴다. 서명은 그대로 유효하므로 만료 검사만 보는 테스트다.
        with mock.patch.object(auth.time, "time", return_value=int(expires) + 1):
            self.assertFalse(auth.verify(PASSWORD, token))

    def test_rejects_a_tampered_expiry(self):
        # 🔴 서명이 만료시각을 덮지 않으면 브라우저에서 숫자만 늘려 세션을 영구화할 수 있다.
        token = auth.issue(PASSWORD, ttl_hours=12)
        role, expires, signature = token.split(".")
        forged = f"{role}.{int(expires) + 999999}.{signature}"
        self.assertFalse(auth.verify(PASSWORD, forged))

    def test_rejects_malformed_tokens(self):
        for bad in ("", ".", "abc", "abc.def", "123", f"{int(time.time()) + 60}."):
            with self.subTest(token=bad):
                self.assertFalse(auth.verify(PASSWORD, bad))

    def test_the_signing_key_is_not_the_password(self):
        # 서명값에서 비밀번호를 곧장 얻을 수 있으면 안 된다.
        self.assertNotIn(PASSWORD.encode(), auth._key(PASSWORD))


class RoleTokenTest(unittest.TestCase):
    """🔴 역할이 서명 안에 들어간다 (2026-09-16).

    보기 전용 세션을 관리자로 올릴 수 있으면 역할을 나눈 의미가 없다. 막는 방법은 둘이다 —
    역할이 서명 대상에 포함되고, 역할마다 서명 키가 다르다(키는 그 역할의 비밀번호에서 파생).
    """

    def test_a_token_verifies_only_as_the_role_it_was_issued_for(self):
        token = gen = auth.issue("admin-pw", 1, auth.READONLY)
        self.assertTrue(auth.verify("admin-pw", gen, auth.READONLY))
        self.assertFalse(auth.verify("admin-pw", token, auth.ADMIN))

    def test_editing_the_role_breaks_the_signature(self):
        token = auth.issue("ro-pw", 1, auth.READONLY)
        self.assertFalse(auth.verify("ro-pw", token.replace("readonly.", "admin.", 1), auth.ADMIN))

    def test_role_of_picks_the_role_from_the_right_password(self):
        admin_token = auth.issue("admin-pw", 1, auth.ADMIN)
        ro_token = auth.issue("ro-pw", 1, auth.READONLY)
        self.assertEqual(auth.role_of("admin-pw", "ro-pw", admin_token), auth.ADMIN)
        self.assertEqual(auth.role_of("admin-pw", "ro-pw", ro_token), auth.READONLY)
        self.assertIsNone(auth.role_of("admin-pw", "ro-pw", None))
        self.assertIsNone(auth.role_of("admin-pw", "ro-pw", "garbage"))

    def test_a_readonly_token_is_not_accepted_when_only_admin_is_configured(self):
        # 보기 전용 비밀번호를 지우면 그 세션들도 같이 죽어야 한다.
        ro_token = auth.issue("ro-pw", 1, auth.READONLY)
        self.assertIsNone(auth.role_of("admin-pw", "", ro_token))

    def test_the_old_two_part_token_is_rejected(self):
        expires = int(time.time()) + 3600
        legacy = f"{expires}.{auth._sign(auth._key('admin-pw'), str(expires))}"
        self.assertIsNone(auth.role_of("admin-pw", "ro-pw", legacy))

    def test_an_unknown_role_cannot_be_issued(self):
        with self.assertRaises(ValueError):
            auth.issue("admin-pw", 1, "superuser")


class AuthenticateTest(unittest.TestCase):
    def setUp(self):
        auth.reset_throttle()
        self.addCleanup(auth.reset_throttle)

    def test_each_password_maps_to_its_role(self):
        self.assertEqual(auth.authenticate("admin-pw", "ro-pw", "admin-pw"), auth.ADMIN)
        self.assertEqual(auth.authenticate("admin-pw", "ro-pw", "ro-pw"), auth.READONLY)

    def test_a_wrong_password_is_no_role(self):
        self.assertIsNone(auth.authenticate("admin-pw", "ro-pw", "nope"))

    def test_one_wrong_attempt_counts_once_not_twice(self):
        """🔴 두 번 세면 보기 전용 비밀번호가 있다는 이유만으로 잠금이 절반 속도로 찬다."""
        for _ in range(auth.MAX_FAILURES - 1):
            auth.authenticate("admin-pw", "ro-pw", "nope")
        self.assertEqual(auth.locked_for(), 0)
        auth.authenticate("admin-pw", "ro-pw", "nope")
        self.assertGreater(auth.locked_for(), 0)


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
