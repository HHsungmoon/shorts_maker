"""유튜브 채널 연결 — 무엇을 저장하고 언제 거절하나(tease §7-5).

🔴 지키는 것: ① 밖으로 나가는 채널 정보에 refresh token 이 없다 ② state 는 한 번 쓰면 사라지고 10분이면
만료된다(CSRF) ③ Google 이 거절한 연결은 저장하지 않는다 ④ 끊을 때 Google 이 답하지 않아도 우리 쪽은 지운다.
"""

import tempfile
from pathlib import Path
from unittest import mock

from shorts_maker.adapters import youtube
from shorts_maker.pipeline import youtube_channel

from ..support import DbTestCase, make_config

GRANT = youtube.Grant(refresh_token="refresh-1", scopes=youtube.UPLOAD_SCOPE)


class ChannelTestCase(DbTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = make_config(
            Path(self._tmp.name),
            youtube_client_id="cid",
            youtube_client_secret="secret",
            youtube_redirect_uri="http://localhost:8100/api/youtube/callback",
        )


class StoreTest(ChannelTestCase):
    def test_the_channel_view_never_carries_the_token(self):
        youtube_channel.save_channel(self.conn, GRANT)
        self.assertNotIn("refresh_token", youtube_channel.channel(self.conn))
        self.assertEqual(youtube_channel.refresh_token(self.conn), "refresh-1")

    def test_nothing_connected_reads_as_none(self):
        self.assertIsNone(youtube_channel.channel(self.conn))
        self.assertIsNone(youtube_channel.refresh_token(self.conn))
        self.assertIsNone(youtube_channel.forget_channel(self.conn))

    def test_reconnecting_replaces_and_forgets_the_old_channel_name(self):
        # 새 토큰이 같은 채널이라는 보장이 없다(동의 화면에서 채널을 고른다). 틀린 이름을 보이지 않는다.
        youtube_channel.save_channel(self.conn, GRANT)
        youtube_channel.note_channel(self.conn, "UCold", "옛 채널")
        youtube_channel.save_channel(self.conn, youtube.Grant(refresh_token="refresh-2", scopes=youtube.UPLOAD_SCOPE))
        found = youtube_channel.channel(self.conn)
        self.assertEqual((found["channel_id"], found["channel_title"]), (None, None))
        self.assertEqual(youtube_channel.refresh_token(self.conn), "refresh-2")
        self.assertEqual(self.conn.execute("select count(*) as n from youtube_channel").fetchone()["n"], 1)


class StartTest(ChannelTestCase):
    def test_hands_out_a_consent_url_carrying_a_live_state(self):
        url = youtube_channel.start(self.cfg, "localhost")
        self.assertTrue(url.startswith(youtube.AUTH_URL))
        state = url.split("state=")[1].split("&")[0]
        self.assertTrue(youtube_channel.take_state(state))

    def test_missing_settings_are_refused(self):
        cfg = make_config(Path(self._tmp.name))
        with self.assertRaises(youtube_channel.ChannelError):
            youtube_channel.start(cfg, "localhost")

    def test_another_host_is_refused_but_another_port_is_fine(self):
        # 🔴 쿠키는 호스트별이고 포트는 가르지 않는다 — vite(5173)에서 열어도 8100 콜백에 쿠키가 실린다.
        with self.assertRaises(youtube_channel.ChannelError) as caught:
            youtube_channel.check_host(self.cfg, "127.0.0.1")
        self.assertIn("http://localhost:8100", str(caught.exception))
        youtube_channel.check_host(self.cfg, "localhost")


class StateTest(ChannelTestCase):
    def test_a_state_works_once(self):
        state = youtube_channel.issue_state()
        self.assertTrue(youtube_channel.take_state(state))
        self.assertFalse(youtube_channel.take_state(state))

    def test_unknown_or_missing_states_are_refused(self):
        self.assertFalse(youtube_channel.take_state("forged"))
        self.assertFalse(youtube_channel.take_state(None))
        self.assertFalse(youtube_channel.take_state(""))

    def test_a_state_expires(self):
        now = 1000.0
        with mock.patch("time.monotonic", side_effect=lambda: now):
            state = youtube_channel.issue_state()
            now += youtube_channel.STATE_TTL_SEC + 1
            self.assertFalse(youtube_channel.take_state(state))


class FinishTest(ChannelTestCase):
    def test_a_good_code_is_saved(self):
        with mock.patch.object(youtube, "exchange_code", return_value=GRANT) as exchange:
            youtube_channel.finish(self.conn, self.cfg, "code-1")
        exchange.assert_called_once_with(self.cfg, "code-1")
        self.assertEqual(youtube_channel.refresh_token(self.conn), "refresh-1")

    def test_a_refused_code_saves_nothing(self):
        # 업로드 범위를 체크하지 않았거나 code 가 만료됐다. 반쯤 연결된 채로 두면 첫 업로드에서야 터진다.
        with mock.patch.object(youtube, "exchange_code", side_effect=youtube.YoutubeError("업로드 권한이 없다")):
            with self.assertRaises(youtube_channel.ChannelError) as caught:
                youtube_channel.finish(self.conn, self.cfg, "code-1")
        self.assertIn("업로드 권한", str(caught.exception))
        self.assertIsNone(youtube_channel.channel(self.conn))


class DisconnectTest(ChannelTestCase):
    def test_forgets_locally_even_when_google_does_not_answer(self):
        youtube_channel.save_channel(self.conn, GRANT)
        self.conn.commit()
        with mock.patch.object(youtube, "revoke", return_value=False) as revoke:
            result = youtube_channel.disconnect(self.conn)
        self.assertEqual(result, {"disconnected": True, "revoked": False})
        revoke.assert_called_once_with("refresh-1")
        self.assertIsNone(youtube_channel.channel(self.conn))

    def test_nothing_to_disconnect_calls_nobody(self):
        with mock.patch.object(youtube, "revoke") as revoke:
            self.assertEqual(youtube_channel.disconnect(self.conn), {"disconnected": False, "revoked": False})
        revoke.assert_not_called()
