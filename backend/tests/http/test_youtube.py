"""유튜브 채널 연결 · 업로드 API(`http/youtube.py`) — 경계(tease §7-5).

🔴 지키는 것: ① 콜백은 GET 인데 행동이라 메서드로 안 갈린다 — state 와 역할을 그 자리에서 본다
② refresh token 은 **어떤 응답에도** 실리지 않는다(보기 전용도 읽는다) ③ 스튜디오 주소의 호스트가
콜백 주소와 다르면 동의 화면에 보내기 전에 막는다(세션 쿠키가 따라가지 않는다) ④ 이 라우터는 스튜디오
라우터 밑에 붙어 그 인증을 받는다.
"""

import dataclasses
import unittest
import urllib.parse
from unittest import mock

from shorts_maker.adapters import youtube
from shorts_maker.db import store
from shorts_maker.http import deps, studio
from shorts_maker.http import youtube as youtube_routes
from shorts_maker.pipeline import youtube_channel

from .test_api_auth import READONLY, ApiAuthTestCase, _routes

SECRET_REFRESH = "refresh-token-that-must-never-leave"


class YoutubeApiTestCase(ApiAuthTestCase):
    def setUp(self):
        super().setUp()
        deps.cfg = dataclasses.replace(
            deps.cfg,
            youtube_client_id="cid",
            youtube_client_secret="client-secret",
            # TestClient 의 호스트에 맞춘다. 다른 호스트는 HostTest 가 따로 본다.
            youtube_redirect_uri="http://testserver/api/youtube/callback",
        )

    def connect_channel(self) -> None:
        with store.connect(deps.cfg.database_url) as conn:
            youtube_channel.save_channel(conn, youtube.Grant(refresh_token=SECRET_REFRESH, scopes=youtube.UPLOAD_SCOPE))
            conn.commit()

    def stored_token(self) -> str | None:
        with store.connect(deps.cfg.database_url) as conn:
            row = conn.execute("select refresh_token from youtube_channel").fetchone()
        return row["refresh_token"] if row else None

    def seed_clip(self) -> tuple[int, int]:
        with store.connect(deps.cfg.database_url) as conn:
            source_id = conn.execute(
                "insert into sources (title, content_type, path, fingerprint, status, youtube_id)"
                " values ('강연', 'LECTURE', 'a.mp4', 'sha256:y', 'DONE', 'abcDEF12345') returning id"
            ).fetchone()["id"]
            chunk_id = conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path)"
                " values (%s, 0, 0, 600, 'c.wav') returning id", (source_id,)
            ).fetchone()["id"]
            segment_id = conn.execute(
                "insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx,"
                " end_utterance_idx) values (%s, 0, 0, 100, 0, 3) returning id", (chunk_id,)
            ).fetchone()["id"]
            run_id = conn.execute(
                "insert into runs (source_id) values (%s) returning id", (source_id,)
            ).fetchone()["id"]
            clip_id = conn.execute(
                "insert into clips (run_id, segment_id, start_sec, end_sec, title)"
                " values (%s, %s, 61, 90, '제목') returning id", (run_id, segment_id)
            ).fetchone()["id"]
            conn.commit()
        return source_id, clip_id

    def callback(self, **query):
        return self.client.get(f"/api/youtube/callback?{urllib.parse.urlencode(query)}", follow_redirects=False)

    def assert_back_home(self, response, result: str) -> None:
        self.assertEqual(response.status_code, 303, response.text)
        location = urllib.parse.urlsplit(response.headers["location"])
        self.assertEqual(location.path, "/studio")
        self.assertEqual(urllib.parse.parse_qs(location.query)["youtube"], [result])


class MountTest(YoutubeApiTestCase):
    def test_every_route_sits_under_the_studio_router(self):
        # 🔴 앱에 직접 붙으면 `/api` 도 인증도 없이 열린다. 스튜디오 라우터 밑에서 `/api/...` 로 보여야 한다.
        studio_paths = {(route.path, method) for route in _routes(studio.router.routes) for method in route.methods}
        for route in youtube_routes.router.routes:
            for method in route.methods:
                with self.subTest(method=method, path=route.path):
                    self.assertIn(("/api" + route.path, method), studio_paths)

    def test_nothing_is_open_without_a_login(self):
        for method, path in (("GET", "/api/youtube"), ("GET", "/api/youtube/callback"), ("DELETE", "/api/youtube")):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.client.request(method, path).status_code, 401)


class ConnectTest(YoutubeApiTestCase):
    def test_hands_back_the_consent_url_with_a_fresh_state(self):
        self.login()
        response = self.client.post("/api/youtube/connect")
        self.assertEqual(response.status_code, 200, response.text)
        url = urllib.parse.urlsplit(response.json()["url"])
        self.assertEqual(url.netloc, "accounts.google.com")
        state = urllib.parse.parse_qs(url.query)["state"][0]
        self.assertTrue(youtube_channel.take_state(state))

    def test_missing_settings_say_where_to_put_them(self):
        deps.cfg = dataclasses.replace(deps.cfg, youtube_client_id="")
        self.login()
        response = self.client.post("/api/youtube/connect")
        self.assertEqual(response.status_code, 400)
        self.assertIn(".env", response.json()["detail"])

    def test_a_studio_on_another_host_is_stopped_before_the_consent_screen(self):
        # 🔴 127.0.0.1 로 열어 두고 localhost 로 돌아오면 쿠키가 없어 401 — 동의까지 마친 뒤에 터진다.
        deps.cfg = dataclasses.replace(deps.cfg, youtube_redirect_uri="http://localhost:8100/api/youtube/callback")
        self.login()
        response = self.client.post("/api/youtube/connect")
        self.assertEqual(response.status_code, 400)
        self.assertIn("http://localhost:8100", response.json()["detail"])


class CallbackTest(YoutubeApiTestCase):
    def test_a_good_callback_stores_the_grant_once(self):
        self.login()
        state = youtube_channel.issue_state()
        grant = youtube.Grant(refresh_token="r-new", scopes=youtube.UPLOAD_SCOPE)
        with mock.patch.object(youtube, "exchange_code", return_value=grant) as exchange:
            self.assert_back_home(self.callback(state=state, code="c1"), "connected")
            # 뒤로 가기·새로고침으로 같은 주소를 다시 열어도 두 번 바꾸지 않는다.
            self.assert_back_home(self.callback(state=state, code="c1"), "failed")
        exchange.assert_called_once_with(deps.cfg, "c1")
        self.assertEqual(self.stored_token(), "r-new")

    def test_a_state_this_server_did_not_make_is_refused(self):
        # 🔴 CSRF — 남이 만든 동의 링크로 남의 채널이 우리 스튜디오에 연결되면 영상이 그쪽으로 올라간다.
        self.login()
        with mock.patch.object(youtube, "exchange_code") as exchange:
            self.assert_back_home(self.callback(state="forged", code="c1"), "failed")
            self.assert_back_home(self.callback(code="c1"), "failed")
        exchange.assert_not_called()
        self.assertIsNone(self.stored_token())

    def test_readonly_cannot_connect_even_with_a_live_state(self):
        # GET 이라 deps.SAFE_METHODS 를 통과한다. 그 자리에서 막는다.
        self.login(READONLY)
        state = youtube_channel.issue_state()
        with mock.patch.object(youtube, "exchange_code") as exchange:
            response = self.callback(state=state, code="c1")
        self.assertEqual(response.status_code, 403)
        exchange.assert_not_called()
        self.assertIsNone(self.stored_token())

    def test_a_cancelled_consent_goes_home_quietly(self):
        self.login()
        self.assert_back_home(self.callback(state=youtube_channel.issue_state(), error="access_denied"), "denied")
        self.assertIsNone(self.stored_token())

    def test_an_exchange_failure_goes_home_with_the_reason(self):
        self.login()
        state = youtube_channel.issue_state()
        with mock.patch.object(youtube, "exchange_code", side_effect=youtube.YoutubeError("업로드 권한이 체크되지 않았다")):
            response = self.callback(state=state, code="c1")
        self.assert_back_home(response, "failed")
        self.assertIn("업로드 권한", urllib.parse.unquote_plus(response.headers["location"]))


class TokenNeverLeavesTest(YoutubeApiTestCase):
    def test_no_response_carries_the_refresh_token(self):
        # 🔴 이 값만 있으면 그 채널에 영상을 올릴 수 있다. 보기 전용도 읽는 응답들이다.
        source_id, clip_id = self.seed_clip()
        self.connect_channel()
        self.login(READONLY)
        for path in ("/api/youtube", f"/api/clips/{clip_id}/youtube", f"/api/sources/{source_id}"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn(SECRET_REFRESH, response.text)
                self.assertNotIn("client-secret", response.text)

    def test_the_state_shows_the_channel_without_its_secret(self):
        self.connect_channel()
        self.login()
        body = self.client.get("/api/youtube").json()
        self.assertTrue(body["configured"])
        self.assertEqual(body["channel"]["scopes"], youtube.UPLOAD_SCOPE)


class DisconnectTest(YoutubeApiTestCase):
    def test_disconnect_forgets_locally_even_if_google_does_not_answer(self):
        self.connect_channel()
        self.login()
        with mock.patch.object(youtube, "revoke", return_value=False) as revoke:
            response = self.client.delete("/api/youtube")
        self.assertEqual(response.json(), {"disconnected": True, "revoked": False})
        revoke.assert_called_once_with(SECRET_REFRESH)
        self.assertIsNone(self.stored_token())


class UploadRequestTest(YoutubeApiTestCase):
    def test_privacy_has_no_default(self):
        # 🔴 개발자 정책 III.E.3 — 공개 범위는 사람이 고른다. 빠지면 서버가 대신 정하지 않는다.
        _, clip_id = self.seed_clip()
        self.login()
        response = self.client.post(f"/api/clips/{clip_id}/youtube", json={"title": "제목"})
        self.assertEqual(response.status_code, 422)

    def test_bad_values_are_refused_before_anything_runs(self):
        _, clip_id = self.seed_clip()
        self.connect_channel()
        self.login()
        response = self.client.post(f"/api/clips/{clip_id}/youtube", json={"title": "<제목>", "privacy": "private"})
        self.assertEqual(response.status_code, 422)

    def test_not_ready_is_409_and_says_why(self):
        _, clip_id = self.seed_clip()
        self.login()
        response = self.client.post(f"/api/clips/{clip_id}/youtube", json={"title": "제목", "privacy": "private"})
        self.assertEqual(response.status_code, 409)

    def test_a_missing_clip_is_404(self):
        self.connect_channel()
        self.login()
        response = self.client.post("/api/clips/9999/youtube", json={"title": "제목", "privacy": "private"})
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
