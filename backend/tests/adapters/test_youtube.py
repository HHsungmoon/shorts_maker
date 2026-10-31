"""유튜브 어댑터 — 토큰과 업로드.

🔴 여기서 지키는 건 셋이다. ① **업로드 권한 없이 연결되지 않는다** — 동의 화면에서 체크를 풀면 첫
업로드에서야 403 이 난다. ② **만료는 "다시 연결하라" 로 읽힌다** — 테스트 상태 앱은 7일마다 일어난다.
③ **바이트 PUT 은 다시 보내지 않는다** — 응답만 잃은 경우 같은 영상이 채널에 둘 생긴다.
"""

import email.message
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

from shorts_maker.adapters import youtube

from ..support import make_config


def _http_error(code: int, payload: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.invalid", code, "x", email.message.Message(), io.BytesIO(json.dumps(payload).encode())
    )


class _Response:
    def __init__(self, payload: dict | None = None, location: str | None = None):
        self._body = json.dumps(payload).encode() if payload is not None else b""
        self.headers = email.message.Message()
        if location:
            self.headers["Location"] = location

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class YoutubeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.cfg = make_config(self.root, youtube_client_id="client-id", youtube_client_secret="client-secret")
        sleep = mock.patch("time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)


class AuthorizeUrlTest(YoutubeTestCase):
    def test_asks_for_the_upload_scope_only_and_a_refresh_token(self):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(youtube.authorize_url(self.cfg, "s1")).query)
        # 🔴 개발자 정책 III.D.2 — 지금 쓰는 범위만.
        self.assertEqual(query["scope"], [youtube.UPLOAD_SCOPE])
        # refresh token 은 처음 동의할 때만 온다. 매번 동의 화면을 띄워 매번 받는다.
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["prompt"], ["consent"])
        self.assertEqual(query["state"], ["s1"])
        self.assertEqual(query["redirect_uri"], [self.cfg.youtube_redirect_uri])

    def test_the_secret_never_goes_into_the_browser(self):
        self.assertNotIn("client-secret", youtube.authorize_url(self.cfg, "s1"))


class ExchangeCodeTest(YoutubeTestCase):
    def test_a_grant_carries_the_refresh_token_and_scopes(self):
        payload = {"access_token": "a", "refresh_token": "r", "scope": youtube.UPLOAD_SCOPE}
        with mock.patch("urllib.request.urlopen", return_value=_Response(payload)) as urlopen:
            grant = youtube.exchange_code(self.cfg, "code-1")
        self.assertEqual(grant, youtube.Grant(refresh_token="r", scopes=youtube.UPLOAD_SCOPE))
        sent = urllib.parse.parse_qs(urlopen.call_args.args[0].data.decode())
        self.assertEqual(sent["code"], ["code-1"])
        self.assertEqual(sent["grant_type"], ["authorization_code"])

    def test_an_unticked_upload_scope_is_refused(self):
        # 🔴 동의 화면에서 범위별 체크를 풀 수 있다. 업로드 없이 연결된 채로 두면 첫 업로드에서야 터진다.
        payload = {"access_token": "a", "refresh_token": "r", "scope": "openid"}
        with mock.patch("urllib.request.urlopen", return_value=_Response(payload)):
            with self.assertRaises(youtube.YoutubeError):
                youtube.exchange_code(self.cfg, "code-1")

    def test_a_missing_refresh_token_is_refused(self):
        payload = {"access_token": "a", "scope": youtube.UPLOAD_SCOPE}
        with mock.patch("urllib.request.urlopen", return_value=_Response(payload)):
            with self.assertRaises(youtube.YoutubeError):
                youtube.exchange_code(self.cfg, "code-1")


class AccessTokenTest(YoutubeTestCase):
    def test_an_expired_grant_asks_to_reconnect(self):
        refused = _http_error(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
        with mock.patch("urllib.request.urlopen", side_effect=[refused]):
            with self.assertRaises(youtube.ReconnectNeeded) as caught:
                youtube.access_token(self.cfg, "old")
        self.assertIn("7일", str(caught.exception))

    def test_the_secret_is_not_in_the_error_message(self):
        # 요청 본문에는 client_secret·토큰이 있다. 예외 메시지는 잡 오류로 화면에 그대로 뜬다.
        refused = _http_error(401, {"error": "invalid_client", "error_description": "Unauthorized"})
        with mock.patch("urllib.request.urlopen", side_effect=[refused]):
            with self.assertRaises(youtube.YoutubeError) as caught:
                youtube.access_token(self.cfg, "refresh-secret")
        self.assertNotIn("client-secret", str(caught.exception))
        self.assertNotIn("refresh-secret", str(caught.exception))

    def test_a_server_hiccup_is_retried(self):
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=[_http_error(503, {}), _Response({"access_token": "fresh"})],
        ) as urlopen:
            self.assertEqual(youtube.access_token(self.cfg, "r"), "fresh")
        self.assertEqual(urlopen.call_count, 2)


class UploadTest(YoutubeTestCase):
    def setUp(self):
        super().setUp()
        self.clip = self.root / "clip.mp4"
        self.clip.write_bytes(b"\x00" * 1024)

    def test_opens_a_session_then_puts_the_bytes(self):
        video = {"id": "vid1", "snippet": {"channelId": "UC1", "channelTitle": "내 채널"}}
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=[_Response(location="https://upload.example/session"), _Response(video)],
        ) as urlopen:
            result = youtube.upload("token", self.clip, {"snippet": {"title": "제목"}})
        self.assertEqual(result["id"], "vid1")
        init, put = (call.args[0] for call in urlopen.call_args_list)
        self.assertEqual(init.get_method(), "POST")
        self.assertIn("uploadType=resumable", init.full_url)
        self.assertEqual(init.get_header("X-upload-content-length"), "1024")
        self.assertEqual(json.loads(init.data)["snippet"]["title"], "제목")
        self.assertEqual(put.get_method(), "PUT")
        self.assertEqual(put.full_url, "https://upload.example/session")
        self.assertEqual(put.get_header("Content-length"), "1024")

    def test_the_bytes_are_not_sent_twice(self):
        # 🔴 응답만 잃고 실제로는 올라갔을 수 있다. 다시 보내면 같은 영상이 둘이 된다.
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=[_Response(location="https://upload.example/s"), urllib.error.URLError("reset"), _Response({"id": "x"})],
        ) as urlopen:
            with self.assertRaises(youtube.YoutubeError) as caught:
                youtube.upload("token", self.clip, {})
        self.assertEqual(urlopen.call_count, 2)
        self.assertIn("확인", str(caught.exception))

    def test_a_quota_error_says_what_to_do(self):
        quota = _http_error(403, {"error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}})
        with mock.patch("urllib.request.urlopen", side_effect=[quota]):
            with self.assertRaises(youtube.YoutubeError) as caught:
                youtube.upload("token", self.clip, {})
        self.assertIn("자정", str(caught.exception))
        self.assertIn("quotaExceeded", str(caught.exception))

    def test_an_account_without_a_channel_says_so(self):
        signup = _http_error(401, {"error": {"code": 401, "errors": [{"reason": "youtubeSignupRequired"}]}})
        with mock.patch("urllib.request.urlopen", side_effect=[signup]):
            with self.assertRaises(youtube.YoutubeError) as caught:
                youtube.upload("token", self.clip, {})
        # 🔴 401 이지만 "다시 연결하라" 가 아니다 — 같은 계정으로 다시 연결해도 채널은 안 생긴다.
        self.assertNotIsInstance(caught.exception, youtube.ReconnectNeeded)
        self.assertIn("채널이 없다", str(caught.exception))

    def test_a_response_without_an_id_is_an_error(self):
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=[_Response(location="https://upload.example/s"), _Response({"kind": "youtube#video"})],
        ):
            with self.assertRaises(youtube.YoutubeError):
                youtube.upload("token", self.clip, {})

    def test_an_empty_file_is_refused_before_any_request(self):
        empty = self.root / "empty.mp4"
        empty.write_bytes(b"")
        with mock.patch("urllib.request.urlopen") as urlopen:
            with self.assertRaises(youtube.YoutubeError):
                youtube.upload("token", empty, {})
        urlopen.assert_not_called()


class RevokeTest(YoutubeTestCase):
    def test_a_failed_revoke_does_not_raise(self):
        # 우리 쪽 삭제는 거두기의 성공과 무관하게 한다(http/studio.youtube_disconnect).
        with mock.patch("urllib.request.urlopen", side_effect=[urllib.error.URLError("down")]):
            self.assertFalse(youtube.revoke("r"))

    def test_an_empty_success_body_is_fine(self):
        with mock.patch("urllib.request.urlopen", return_value=_Response()):
            self.assertTrue(youtube.revoke("r"))


if __name__ == "__main__":
    unittest.main()
