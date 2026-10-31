"""라이브 채팅 다시보기 → 질문 (tease §3-5).

🔴 지키는 것: ① 안내 계정·운영자의 글은 질문이 아니다(다시보기엔 표식이 빠져 온다) ② 몇 번 가져와도
같다(메시지 id) ③ 작성자 이름·채널 id 는 어디에도 남지 않고, 받은 파일도 지운다 ④ 성공도 실패도
`stage_calls` 에 남는다.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shorts_maker.adapters import ytdlp
from shorts_maker.answers import livechat

from ..support import DbTestCase, make_config


def msg(msg_id: str, text: str, author: str = "UCviewer", offset: float = 60.0, staff: bool = False):
    return ytdlp.ChatMessage(id=msg_id, offset_sec=offset, text=text, author_id=author, staff=staff)


class PickTest(unittest.TestCase):
    def test_keeps_real_questions_in_chat_order(self):
        kept, skipped = livechat.pick([
            msg("1", "쏘카 채용 시, 잦은 이직이 결격사유가 될까요?"),
            msg("2", "안녕하세요 다들 반갑습니다"),
            msg("3", "iOS 개발자도 뽑나요"),
        ])
        self.assertEqual([m.id for m in kept], ["1", "3"])
        self.assertEqual(skipped, {"not_question": 1})

    def test_a_repeating_announcer_is_dropped_even_without_a_badge(self):
        # 🔴 쏘카 LIVE 실측: 안내 계정이 채널 주인과 다르고 표식도 없었다. 안내 문장이 질문 꼴이다.
        announce = "궁금한 점을 채팅창에 남겨주시면 잠시 후 답변해드리겠습니다"
        messages = [msg(f"a{i}", announce, author="UCstaff") for i in range(livechat.ANNOUNCER_REPEATS)]
        messages.append(msg("a-q", "이력서는 자유양식인가요?", author="UCstaff"))
        messages.append(msg("v1", "코딩테스트는 어떤 언어로 보나요?"))
        kept, skipped = livechat.pick(messages)
        self.assertEqual([m.id for m in kept], ["v1"])
        self.assertEqual(skipped["announcer"], livechat.ANNOUNCER_REPEATS + 1)

    def test_a_viewer_asking_twice_is_one_question_and_not_an_announcer(self):
        kept, skipped = livechat.pick([msg("1", "연봉은 어떻게 되나요?"), msg("2", "연봉은 어떻게 되나요?")])
        self.assertEqual([m.id for m in kept], ["1"])
        self.assertEqual(skipped, {"duplicate": 1})

    def test_staff_replies_short_and_me_too_are_dropped(self):
        kept, skipped = livechat.pick([
            msg("1", "공지: 언제 시작하나요?", staff=True),
            msg("2", "@경진김 이력서는 자유양식인가요? 네 맞습니다"),
            msg("3", "언제?"),
            msg("4", "(저도 궁금합니다)"),
        ])
        self.assertEqual(kept, [])
        self.assertEqual(skipped, {"staff": 1, "reply": 1, "short": 1, "me_too": 1})

    def test_viewer_id_hides_the_channel(self):
        hashed = livechat.viewer_id("UCsomeone")
        self.assertTrue(hashed.startswith("yt:"))
        self.assertNotIn("UCsomeone", hashed)
        self.assertEqual(hashed, livechat.viewer_id("UCsomeone"))


class ImportTest(DbTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.cfg = make_config(self.root)
        self.source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status, youtube_id)"
            " values ('쏘카 LIVE', 'LECTURE', 'a.mp4', 'sha256:c', 'DONE', 'zScahh0UTQc') returning id"
        ).fetchone()["id"]
        self.conn.commit()

    def fake_fetch(self, messages):
        """파일을 실제로 만들고(지워지는지 보려고) 파싱은 주어진 메시지로 바꾼다."""
        self.chat_file = self.root / "chat.live_chat.json"

        def fetch(cfg, video_id):
            self.chat_file.write_text("이름이 든 원본", encoding="utf-8")
            return self.chat_file, "UCowner"

        return (
            mock.patch.object(ytdlp, "fetch_live_chat", side_effect=fetch),
            mock.patch.object(ytdlp, "parse_live_chat", return_value=messages),
        )

    def run_import(self, messages):
        fetch, parse = self.fake_fetch(messages)
        with fetch, parse:
            return livechat.import_replay(self.conn, self.cfg, self.source_id)

    def test_questions_land_unclustered_with_their_time_and_no_name(self):
        result = self.run_import([msg("m1", "잦은 이직이 결격사유가 될까요?", offset=788), msg("m2", "안녕하세요")])
        self.assertEqual((result["messages"], result["inserted"], result["already"]), (2, 1, 0))
        row = self.conn.execute("select * from questions").fetchone()
        self.assertEqual((row["origin"], row["external_id"], row["asked_at_sec"]), ("live_chat", "m1", 788))
        self.assertIsNone(row["cluster_id"])
        self.assertNotIn("UCviewer", row["viewer_id"])
        # 🔴 이름이 든 파일은 남지 않는다.
        self.assertFalse(self.chat_file.exists())

    def test_importing_twice_adds_nothing(self):
        messages = [msg("m1", "잦은 이직이 결격사유가 될까요?")]
        self.run_import(messages)
        again = self.run_import(messages)
        self.assertEqual((again["inserted"], again["already"]), (0, 1))
        self.assertEqual(self.conn.execute("select count(*) as n from questions").fetchone()["n"], 1)

    def test_the_stage_is_recorded(self):
        self.run_import([msg("m1", "잦은 이직이 결격사유가 될까요?")])
        call = self.conn.execute("select * from stage_calls where stage = 'livechat'").fetchone()
        self.assertEqual((call["source_id"], call["error"]), (self.source_id, None))
        self.assertEqual(call["params"]["inserted"], 1)

    def test_a_video_without_a_replay_records_the_failure(self):
        with mock.patch.object(ytdlp, "fetch_live_chat", side_effect=ytdlp.NoLiveChat("다시보기가 없습니다")):
            with self.assertRaises(ytdlp.NoLiveChat):
                livechat.import_replay(self.conn, self.cfg, self.source_id)
        call = self.conn.execute("select error from stage_calls where stage = 'livechat'").fetchone()
        self.assertIn("NoLiveChat", call["error"])

    def test_a_source_without_a_youtube_id_is_refused_before_any_download(self):
        self.conn.execute("update sources set youtube_id = null")
        with mock.patch.object(ytdlp, "fetch_live_chat") as fetch:
            with self.assertRaises(livechat.LiveChatError):
                livechat.import_replay(self.conn, self.cfg, self.source_id)
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
