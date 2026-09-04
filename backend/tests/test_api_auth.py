"""API 경계 — 무엇이 인증 없이 열려 있는가.

이 파일이 지키는 것은 하나다: **backend(Spring) 프록시가 사라진 뒤에도 /api/** 가 닫혀
있는가.** 예전에는 도커 네트워크가 울타리였고 코드는 열려 있어도 괜찮았다. 지금은
코드가 유일한 울타리다.
"""

import dataclasses
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from shorts_maker import api, auth, config
from shorts_maker.db import store

PASSWORD = "test-admin-password"
TOKEN = "test-machine-token"


class ApiAuthTestCase(unittest.TestCase):
    """cfg 를 갈아끼운 앱으로 테스트한다.

    라우트들이 모듈 전역 `api.cfg` 를 호출 시점에 읽으므로 이 교체가 실제 경로에 반영된다.
    """

    admin_password = PASSWORD
    api_token = ""

    def setUp(self):
        auth.reset_throttle()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        base = dataclasses.replace(
            config.load(),
            gemini_api_key="",
            db_path=root / "test.db",
            work_dir=root / "work",
            source_dir=root / "sources",
            web_dir=root / "nonexistent-dist",
            admin_password=self.admin_password,
            api_token=self.api_token,
            cookie_secure=False,
        )
        with store.connect(base.db_path) as conn:
            store.apply_schema(conn)
        self._saved = api.cfg
        api.cfg = base
        self.client = TestClient(api.app)

    def tearDown(self):
        api.cfg = self._saved
        self._tmp.cleanup()
        auth.reset_throttle()

    def login(self) -> None:
        response = self.client.post("/auth/login", json={"password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)


class ClosedByDefaultTest(ApiAuthTestCase):
    def test_api_is_refused_without_credentials(self):
        for path in ("/api/sources", "/api/status", "/api/media", "/api/jobs"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)

    def test_writes_are_refused_too(self):
        # 조회만 막고 실행을 열어두면 남이 서버에서 STT 와 Gemini 를 돌릴 수 있다.
        response = self.client.post("/api/sources/1/rank", json={"criteria": "x"})
        self.assertEqual(response.status_code, 401)

    def test_health_stays_public(self):
        # 도커 헬스체크와 nginx 가 자격증명 없이 부른다.
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_health_leaks_no_configuration(self):
        # 🔴 예전 /health 는 모델명·스키마 버전을 담았다. 그때는 도커 네트워크 안이었지만
        # 지금은 인터넷에 열려 있다.
        body = self.client.get("/health").json()
        for leaked in ("geminiKey", "geminiModel", "whisperModel", "schema"):
            self.assertNotIn(leaked, body)

    def test_a_session_cookie_opens_the_api(self):
        self.login()
        self.assertEqual(self.client.get("/api/sources").status_code, 200)

    def test_logout_closes_it_again(self):
        self.login()
        self.client.post("/auth/logout")
        self.assertEqual(self.client.get("/api/sources").status_code, 401)

    def test_a_forged_cookie_does_not_open_it(self):
        self.client.cookies.set(auth.COOKIE_NAME, "99999999999.not-a-real-signature")
        self.assertEqual(self.client.get("/api/sources").status_code, 401)


class LoginTest(ApiAuthTestCase):
    def test_a_wrong_password_is_refused(self):
        self.assertEqual(
            self.client.post("/auth/login", json={"password": "wrong"}).status_code, 401
        )

    def test_repeated_failures_get_locked_out(self):
        for _ in range(auth.MAX_FAILURES):
            self.client.post("/auth/login", json={"password": "wrong"})
        # 🔴 잠긴 뒤에는 **올바른 비밀번호도** 거부해야 한다. 안 그러면 잠금이 무의미하다.
        response = self.client.post("/auth/login", json={"password": PASSWORD})
        self.assertEqual(response.status_code, 429)

    def test_me_reports_the_state_without_erroring(self):
        self.assertEqual(
            self.client.get("/auth/me").json(), {"authenticated": False, "authRequired": True}
        )
        self.login()
        self.assertEqual(
            self.client.get("/auth/me").json(), {"authenticated": True, "authRequired": True}
        )

    def test_the_cookie_is_not_readable_by_scripts(self):
        self.login()
        header = self.client.post("/auth/login", json={"password": PASSWORD}).headers["set-cookie"]
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=lax", header)


class MachineTokenTest(ApiAuthTestCase):
    api_token = TOKEN

    def test_a_valid_token_opens_the_api(self):
        response = self.client.get("/api/sources", headers={"X-Shorts-Token": TOKEN})
        self.assertEqual(response.status_code, 200)

    def test_a_wrong_token_does_not(self):
        response = self.client.get("/api/sources", headers={"X-Shorts-Token": "nope"})
        self.assertEqual(response.status_code, 401)

    def test_the_cookie_still_works_alongside_it(self):
        self.login()
        self.assertEqual(self.client.get("/api/sources").status_code, 200)


class LocalDevModeTest(ApiAuthTestCase):
    """비밀번호도 토큰도 없는 상태 = 루프백 전용 로컬 개발. 인증하지 않는다."""

    admin_password = ""
    api_token = ""

    def test_the_api_is_open(self):
        self.assertEqual(self.client.get("/api/sources").status_code, 200)

    def test_the_frontend_is_told_no_login_is_needed(self):
        self.assertEqual(
            self.client.get("/auth/me").json(), {"authenticated": True, "authRequired": False}
        )

    def test_binding_beyond_loopback_is_refused_in_this_state(self):
        # 🔴 이 조합(인증 없음 + 외부 바인딩)이 사고 경로다. 기동 자체를 막는다.
        api.cfg = dataclasses.replace(api.cfg, api_host="0.0.0.0")
        with self.assertRaises(SystemExit):
            api.check_binding()

    def test_loopback_binding_is_allowed(self):
        api.cfg = dataclasses.replace(api.cfg, api_host="127.0.0.1")
        api.check_binding()


class SpaRoutingTest(ApiAuthTestCase):
    def test_unknown_api_paths_do_not_fall_through_to_the_frontend(self):
        # 🔴 catch-all 이 /api/** 를 삼키면 없는 엔드포인트가 index.html 로 200 을 받고,
        # 프론트는 HTML 을 JSON 으로 파싱하다 엉뚱한 곳에서 죽는다.
        self.login()
        for path in ("/api/nope", "/auth/nope"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)


if __name__ == "__main__":
    unittest.main()
