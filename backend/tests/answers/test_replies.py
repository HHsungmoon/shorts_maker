"""유튜브 댓글 질문에 답글 (tease §7-5, 2026-11-01).

🔴 지키는 것: ① 영상 주인 채널로만 단다 — 남의 영상이면 아무것도 보내지 않는다 ② 같은 댓글에 두 번 달지
않는다 ③ 보내다 실패하면 다시 보내지 않고 FAILED 로 남긴다 ④ 초안의 시각은 클립 조각에서, Shorts 주소는
비공개가 아닐 때만 ⑤ 성공도 거절도 `stage_calls` 에 남는다.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import psycopg

from shorts_maker.adapters import youtube
from shorts_maker.answers import clusters, replies
from shorts_maker.pipeline import youtube_channel

from ..support import DbTestCase, make_config

FULL_GRANT = youtube.Grant(refresh_token="refresh-1", scopes=f"{youtube.UPLOAD_SCOPE} {youtube.COMMENT_SCOPE}")


class StampTest(unittest.TestCase):
    def test_minutes_and_hours(self):
        self.assertEqual(replies.stamp(754.9), "12:34")
        self.assertEqual(replies.stamp(5), "0:05")
        # 쏘카 LIVE 는 96분이다 — 유튜브는 h:mm:ss 도 링크로 바꾼다.
        self.assertEqual(replies.stamp(3600 + 62), "1:01:02")


class ReplyTestCase(DbTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = make_config(Path(self._tmp.name), youtube_client_id="cid", youtube_client_secret="secret")
        execute = self.conn.execute
        self.source_id = execute(
            "insert into sources (title, content_type, path, fingerprint, youtube_id)"
            " values ('강연', 'LECTURE', 'a.mp4', 'sha256:a', 'vid1') returning id"
        ).fetchone()["id"]
        chunk_id = execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path) values (%s, 0, 0, 900, 'c.wav') returning id",
            (self.source_id,),
        ).fetchone()["id"]
        segment_id = execute(
            """insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx, end_utterance_idx)
               values (%s, 0, 700, 800, 0, 3) returning id""",
            (chunk_id,),
        ).fetchone()["id"]
        self.cluster_id = execute(
            "insert into question_clusters (source_id, canonical_text, status) values (%s, '면접은 몇 번?', 'PUBLISHED')"
            " returning id",
            (self.source_id,),
        ).fetchone()["id"]
        run_id = execute(
            "insert into runs (source_id, criteria_prompt) values (%s, '면접은 몇 번?') returning id", (self.source_id,)
        ).fetchone()["id"]
        self.clip_id = execute(
            """insert into clips (run_id, segment_id, start_sec, end_sec, title, question_cluster_id)
               values (%s, %s, 754.9, 790, '면접은 세 번 봅니다', %s) returning id""",
            (run_id, segment_id, self.cluster_id),
        ).fetchone()["id"]
        execute(
            """insert into clip_parts (clip_id, ordinal, segment_id, start_sec, end_sec, start_utterance_idx, end_utterance_idx)
               values (%s, 0, %s, 754.9, 790, 0, 3)""",
            (self.clip_id, segment_id),
        )
        self.question_id = execute(
            """insert into questions (source_id, text, viewer_id, origin, external_id, cluster_id)
               values (%s, '면접 몇 번 보나요?', 'yt:x', 'comment', 'c1', %s) returning id""",
            (self.source_id, self.cluster_id),
        ).fetchone()["id"]
        youtube_channel.save_channel(self.conn, FULL_GRANT)
        youtube_channel.note_channel(self.conn, "UCowner", "내 채널")
        self.conn.commit()

    def post(self, owner: str = "UCowner", thread_found: bool = True, reply=None, text: str = "12:34부터 답이 나와요"):
        thread = youtube.CommentThread(id="c1", video_id="vid1", channel_id=owner, text="면접 몇 번 보나요?")
        reply = reply or mock.DEFAULT
        with mock.patch.object(youtube, "access_token", return_value="access"), \
                mock.patch.object(youtube, "comment_thread", return_value=thread if thread_found else None), \
                mock.patch.object(youtube, "reply", return_value={"id": "c1.r1", "snippet": {
                    "authorChannelId": {"value": owner}}}) as sent:
            if reply is not mock.DEFAULT:
                sent.side_effect = reply
            try:
                return replies.post(self.conn, self.cfg, self.question_id, text)
            finally:
                self.sent = sent

    def reply_rows(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("select * from question_replies order by id")]


class DraftTest(ReplyTestCase):
    def test_the_draft_carries_the_title_and_the_moment_in_the_video(self):
        found = replies.draft(self.conn, self.cfg, self.question_id)
        self.assertEqual(found["text"], "면접은 세 번 봅니다\n영상 12:34부터 답이 나와요.")
        self.assertIsNone(found["blocked"])
        self.assertEqual(found["channel"]["channel_title"], "내 채널")

    def test_a_shared_short_adds_its_link_but_a_private_one_does_not(self):
        insert = """insert into clip_uploads (clip_id, title, privacy, status, youtube_video_id)
                    values (%s, 't', %s, 'DONE', %s)"""
        self.conn.execute(insert, (self.clip_id, "private", "priv1"))
        self.assertNotIn("shorts", replies.draft(self.conn, self.cfg, self.question_id)["text"])
        self.conn.execute(insert, (self.clip_id, "unlisted", "open1"))
        self.assertIn("https://youtube.com/shorts/open1", replies.draft(self.conn, self.cfg, self.question_id)["text"])

    def test_a_combined_clip_lists_every_part(self):
        self.conn.execute(
            """insert into clip_parts (clip_id, ordinal, segment_id, start_sec, end_sec, start_utterance_idx, end_utterance_idx)
               select clip_id, 1, segment_id, 3700, 3720, 0, 1 from clip_parts where clip_id = %s""",
            (self.clip_id,),
        )
        self.assertIn("영상 12:34 · 1:01:40 에", replies.draft(self.conn, self.cfg, self.question_id)["text"])

    def test_says_why_it_cannot_reply(self):
        self.conn.execute("update question_clusters set status = 'OPEN'")
        self.assertIn("숏폼이 없습니다", replies.draft(self.conn, self.cfg, self.question_id)["blocked"])
        self.conn.execute("update question_clusters set status = 'REVIEW'")
        youtube_channel.save_channel(self.conn, youtube.Grant(refresh_token="r", scopes=youtube.UPLOAD_SCOPE))
        self.assertIn("다시 연결", replies.draft(self.conn, self.cfg, self.question_id)["blocked"])

    def test_a_question_from_our_page_cannot_be_replied_to(self):
        self.conn.execute("update questions set origin = 'page', external_id = null")
        self.assertIn("유튜브 댓글", replies.draft(self.conn, self.cfg, self.question_id)["blocked"])

    def test_an_unknown_question_is_not_found(self):
        with self.assertRaises(replies.QuestionNotFound):
            replies.draft(self.conn, self.cfg, 999)


class PostTest(ReplyTestCase):
    def test_a_reply_is_sent_under_the_comment_and_recorded(self):
        result = self.post()
        self.sent.assert_called_once_with("access", "c1", "12:34부터 답이 나와요")
        self.assertEqual((result["status"], result["url"]), ("DONE", "https://www.youtube.com/watch?v=vid1&lc=c1.r1"))
        row = self.reply_rows()[0]
        self.assertEqual((row["clip_id"], row["channel_id"], row["parent_comment_id"]), (self.clip_id, "UCowner", "c1"))
        call = self.conn.execute("select * from stage_calls where stage = 'reply'").fetchone()
        self.assertEqual((call["error"], call["params"]["comment_id"]), (None, "c1.r1"))
        # 질문 탭의 질문 줄이 그 답글로 간다.
        listed = clusters.questions_of(self.conn, self.cluster_id)[0]["reply"]
        self.assertEqual((listed["status"], listed["url"]), ("DONE", result["url"]))

    def test_someone_elses_video_is_refused_before_anything_is_sent(self):
        # 🔴 개발 중 개인 채널로 원티드 영상에 공개 답글을 남기는 사고를 막는다.
        with self.assertRaises(replies.NotReady) as caught:
            self.post(owner="UCwanted")
        self.assertIn("영상 주인", str(caught.exception))
        self.sent.assert_not_called()
        self.assertEqual(self.reply_rows(), [])
        call = self.conn.execute("select error from stage_calls where stage = 'reply'").fetchone()
        self.assertIn("NotReady", call["error"])

    def test_a_deleted_comment_is_refused(self):
        with self.assertRaises(replies.NotReady):
            self.post(thread_found=False)
        self.sent.assert_not_called()

    def test_the_same_comment_is_not_replied_to_twice(self):
        self.post()
        with self.assertRaises(replies.NotReady):
            self.post()
        self.assertEqual(len(self.reply_rows()), 1)

    def test_a_failed_send_is_recorded_and_can_be_retried_by_hand(self):
        with self.assertRaises(youtube.YoutubeError):
            self.post(reply=youtube.YoutubeError("연결이 끊겼다"))
        self.sent.assert_called_once()
        row = self.reply_rows()[0]
        self.assertEqual(row["status"], "FAILED")
        self.assertIn("연결이 끊겼다", row["error"])
        # 사람이 유튜브에서 확인한 뒤 다시 누르면 된다 — 실패한 행은 막지 않는다.
        self.assertEqual(self.post()["status"], "DONE")

    def test_the_database_refuses_a_second_live_reply_to_one_comment(self):
        # 화면 검사를 지나친 두 요청이 겹쳐도 유일 인덱스가 막는다.
        insert = """insert into question_replies (question_id, parent_comment_id, video_id, text, status)
                    values (%s, 'c1', 'vid1', '답', %s)"""
        self.conn.execute(insert, (self.question_id, "FAILED"))
        self.conn.execute(insert, (self.question_id, "POSTING"))
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.conn.execute(insert, (self.question_id, "POSTING"))

    def test_empty_or_long_text_is_refused_without_sending(self):
        with self.assertRaises(replies.InvalidText):
            replies.post(self.conn, self.cfg, self.question_id, "   ")
        with self.assertRaises(replies.InvalidText):
            replies.post(self.conn, self.cfg, self.question_id, "가" * (replies.TEXT_MAX + 1))

    def test_an_unknown_channel_is_looked_up_before_the_owner_check(self):
        self.conn.execute("update youtube_channel set channel_id = null, channel_title = null")
        self.conn.commit()
        with mock.patch.object(youtube, "my_channel", return_value=("UCowner", "내 채널")) as mine:
            self.assertEqual(self.post()["status"], "DONE")
        mine.assert_called_once_with("access")
        self.assertEqual(youtube_channel.channel(self.conn)["channel_id"], "UCowner")
