"""URL 가드 검증.

🔴 서버가 임의 URL 을 받게 하면 내부망(postgres:5432, 메타데이터 서버)을 찌를 수 있다.
호스트 제한이 그 통로를 닫는 유일한 장치라 여기서 고정한다.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shorts_maker.adapters import ytdlp

from ..support import make_config


class CheckUrlTest(unittest.TestCase):
    def test_accepts_youtube(self):
        for url in (
            "https://www.youtube.com/watch?v=s7Cuv-ErQHk",
            "https://youtu.be/s7Cuv-ErQHk",
            "https://m.youtube.com/watch?v=s7Cuv-ErQHk",
        ):
            with self.subTest(url=url):
                self.assertEqual(ytdlp.check_url(url), url)

    def test_rejects_other_hosts(self):
        for url in (
            "https://evil.example.com/x.mp4",
            "http://169.254.169.254/latest/meta-data/",
            "http://postgres:5432/",
            "https://youtube.com.evil.example.com/watch?v=1",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ytdlp.DownloadError):
                    ytdlp.check_url(url)

    def test_rejects_non_http_schemes(self):
        for url in ("file:///etc/passwd", "ftp://youtube.com/x", "gopher://youtube.com/"):
            with self.subTest(url=url):
                with self.assertRaises(ytdlp.DownloadError):
                    ytdlp.check_url(url)

    def test_video_id_pattern_rejects_path_tricks(self):
        # 파일명이 id 로 지어지므로 여기가 뚫리면 경로 탈출이 된다.
        for bad in ("../../etc/passwd", "a/b", "with space", ""):
            with self.subTest(bad=bad):
                self.assertIsNone(ytdlp.ID_PATTERN.match(bad))


class ParseVideoIdTest(unittest.TestCase):
    """사람이 손으로 채우는 자리(파일로 올린 원본의 유튜브 id)를 받아 준다.

    🔴 URL 을 그대로 저장하면 임베드가 **조용히** 깨진다 — 플레이어는 id 만 받고, 잘못된 값도
    화면에서는 빈 사각형으로만 보인다. 그래서 저장 전에 id 를 뽑아낸다.
    """

    def test_accepts_a_bare_id_and_trims_it(self):
        self.assertEqual("MVqTWMg4n0o", ytdlp.parse_video_id("  MVqTWMg4n0o "))

    def test_pulls_the_id_out_of_every_youtube_url_shape(self):
        for url in (
            "https://youtu.be/MVqTWMg4n0o",
            "https://www.youtube.com/watch?v=MVqTWMg4n0o",
            "https://www.youtube.com/watch?v=MVqTWMg4n0o&t=30s",
            "https://m.youtube.com/watch?v=MVqTWMg4n0o",
            "https://www.youtube.com/embed/MVqTWMg4n0o",
            "https://www.youtube.com/shorts/MVqTWMg4n0o",
        ):
            with self.subTest(url=url):
                self.assertEqual("MVqTWMg4n0o", ytdlp.parse_video_id(url))

    def test_rejects_other_hosts_and_junk(self):
        for bad in ("https://evil.example.com/watch?v=MVqTWMg4n0o", "with space", "", "../../etc"):
            with self.subTest(bad=bad):
                with self.assertRaises(ytdlp.DownloadError):
                    ytdlp.parse_video_id(bad)


class BlockedHintTest(unittest.TestCase):
    """🔴 데이터센터 IP 차단을 영상 문제로 오해하지 않게 한다 (2026-09-17 운영에서 겪었다).

    같은 영상이 맥에서는 받아지고 서버에서만 `Video unavailable` 로 죽었다. 원문만 보여주면
    사람은 영상을 의심하고 다른 URL 을 계속 넣어 본다. 반대로 표식을 넓게 잡으면 진짜로 없는
    영상까지 "IP 문제" 로 안내해 반대 방향으로 헤매게 되므로, 둘 다 여기서 고정한다.
    """

    def test_recognises_the_block_signatures(self):
        for stderr in (
            "ERROR: [youtube] zScahh0UTQc: Video unavailable",
            "ERROR: [youtube] xyz: Sign in to confirm you're not a bot",
            "ERROR: [youtube] xyz: This content isn't available, try again later",
        ):
            with self.subTest(stderr=stderr):
                self.assertTrue(ytdlp._blocked(stderr))

    def test_leaves_unrelated_failures_alone(self):
        for stderr in (
            "ERROR: unable to write file: No space left on device",
            "ERROR: ffmpeg not found",
        ):
            with self.subTest(stderr=stderr):
                self.assertFalse(ytdlp._blocked(stderr))

def chat_line(msg_id: str, offset_ms, text_runs: list[dict], author: str = "UCviewer", badges=None) -> str:
    """다시보기 파일의 한 줄 — 실제 구조(쏘카 LIVE, 2026-10-31)를 그대로 줄인 것."""
    renderer = {
        "id": msg_id,
        "message": {"runs": text_runs},
        "authorName": {"simpleText": "실명이 들어오는 자리"},
        "authorExternalChannelId": author,
        "timestampUsec": "1690000000000000",
    }
    if badges:
        renderer["authorBadges"] = [{"liveChatAuthorBadgeRenderer": {"icon": {"iconType": b}}} for b in badges]
    return json.dumps({"replayChatItemAction": {
        "actions": [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": renderer}}}],
        "videoOffsetTimeMsec": offset_ms,
    }}, ensure_ascii=False)


class ParseLiveChatTest(unittest.TestCase):
    def parse(self, *lines: str, owner: str = "UCowner") -> list[ytdlp.ChatMessage]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.live_chat.json"
            path.write_text("\n".join(lines), encoding="utf-8")
            return ytdlp.parse_live_chat(path, owner)

    def test_reads_text_offset_and_author(self):
        [message] = self.parse(chat_line("m1", "788000", [{"text": "이직이 결격사유가 될까요?"}]))
        self.assertEqual(message, ytdlp.ChatMessage(
            id="m1", offset_sec=788.0, text="이직이 결격사유가 될까요?", author_id="UCviewer", staff=False,
        ))

    def test_emoji_pieces_are_dropped_and_text_is_joined(self):
        [message] = self.parse(chat_line("m1", "0", [
            {"text": "슈퍼앱 "}, {"emoji": {"emojiId": "🚘", "shortcuts": [":car:"]}}, {"text": "가능성 궁금해요 "},
        ]))
        self.assertEqual(message.text, "슈퍼앱 가능성 궁금해요")

    def test_the_owner_and_badged_staff_are_marked(self):
        owner, moderator, viewer = self.parse(
            chat_line("m1", "0", [{"text": "a"}], author="UCowner"),
            chat_line("m2", "0", [{"text": "b"}], author="UCmod", badges=["MODERATOR"]),
            chat_line("m3", "0", [{"text": "c"}], author="UCv", badges=["VERIFIED"]),
        )
        self.assertEqual((owner.staff, moderator.staff, viewer.staff), (True, True, False))

    def test_other_items_and_broken_lines_are_skipped(self):
        engagement = json.dumps({"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": {
            "liveChatViewerEngagementMessageRenderer": {"id": "e1"}}}}], "videoOffsetTimeMsec": "0"}})
        messages = self.parse(engagement, "{not json", "", chat_line("m1", "-500", [{"text": "질문 있어요?"}]))
        self.assertEqual([m.id for m in messages], ["m1"])
        # 방송 전 대기실 채팅은 0 초 밑으로 오지 않게 한다.
        self.assertEqual(messages[0].offset_sec, 0.0)


class FetchLiveChatTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = make_config(Path(self._tmp.name))

    def test_no_replay_file_says_why(self):
        # 라이브가 아니었던 영상: yt-dlp 는 성공하지만 채팅 파일을 만들지 않는다.
        with mock.patch.object(ytdlp, "_run", return_value="UCowner\n"):
            with self.assertRaises(ytdlp.NoLiveChat):
                ytdlp.fetch_live_chat(self.cfg, "abcDEF12345")

    def test_returns_the_file_and_the_channel(self):
        def fake_run(args, timeout):
            target = Path(args[args.index("-o") + 1] + ".live_chat.json")
            target.write_text(chat_line("m1", "0", [{"text": "x"}]), encoding="utf-8")
            return "UCowner\n"

        with mock.patch.object(ytdlp, "_run", side_effect=fake_run):
            path, owner = ytdlp.fetch_live_chat(self.cfg, "abcDEF12345")
        self.assertTrue(path.is_file())
        self.assertEqual(owner, "UCowner")

    def test_a_strange_id_never_reaches_the_command(self):
        with mock.patch.object(ytdlp, "_run") as run:
            with self.assertRaises(ytdlp.DownloadError):
                ytdlp.fetch_live_chat(self.cfg, "../../etc")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
