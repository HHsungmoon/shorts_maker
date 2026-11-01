"""유튜브 댓글 → 질문 · 감상 (tease §3-5 · §3-6, 2026-11-01).

🔴 지키는 것: ① 댓글 권한이 있는 연결에서만 돈다 — 없으면 네트워크 전에 이유를 말한다 ② **문장 끝**이 질문 꼴인
것만 질문, 나머지(너무 긴 글 포함)는 감상 — 영상 주인의 글은 어느 쪽도 아니다 ③ 몇 번 가져와도 같다(댓글 id),
다시 가져오면 갈래를 다시 매기고 안 묶인 옛 질문은 뺀다 ④ 작성자 이름·채널 id 는 어디에도 남지 않는다
⑤ 성공도 실패도 `stage_calls` 에 남는다.
"""

import unittest

import tempfile
from pathlib import Path
from unittest import mock

from shorts_maker.adapters import youtube
from shorts_maker.answers import comments, livechat
from shorts_maker.pipeline import youtube_channel

from ..support import DbTestCase, make_config

FULL_GRANT = youtube.Grant(refresh_token="refresh-1", scopes=f"{youtube.UPLOAD_SCOPE} {youtube.COMMENT_SCOPE}")


def c(comment_id: str, text: str, author: str | None = "UCviewer") -> youtube.Comment:
    return youtube.Comment(id=comment_id, text=text, author_channel_id=author, published_at="2026-11-01T00:00:00Z")


class CommentsTestCase(DbTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = make_config(Path(self._tmp.name), youtube_client_id="cid", youtube_client_secret="secret")
        self.source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, youtube_id)"
            " values ('강연', 'LECTURE', 'a.mp4', 'sha256:a', 'vid1') returning id"
        ).fetchone()["id"]
        youtube_channel.save_channel(self.conn, FULL_GRANT)
        youtube_channel.note_channel(self.conn, "UCowner", "내 채널")
        self.conn.commit()

    def run_import(self, found: list[youtube.Comment], owner: str = "UCowner", truncated: bool = False) -> dict:
        with mock.patch.object(youtube, "access_token", return_value="access"), \
                mock.patch.object(youtube, "comment_threads", return_value=(found, owner, truncated)) as threads:
            result = comments.import_comments(self.conn, self.cfg, self.source_id)
        self.threads = threads
        return result

    def questions(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "select text, origin, external_id, viewer_id, asked_at_sec from questions order by id"
        )]


class ImportTest(CommentsTestCase):
    def test_questions_come_in_oldest_first_as_comment_questions(self):
        # 유튜브는 새것부터 준다.
        result = self.run_import([c("c2", "두 번째 질문인데 면접은 몇 번 보나요?"), c("c1", "자소서는\n몇 자까지 쓰나요?")])
        rows = self.questions()
        self.assertEqual([(r["text"], r["origin"], r["external_id"]) for r in rows],
                         [("자소서는 몇 자까지 쓰나요?", "comment", "c1"),
                          ("두 번째 질문인데 면접은 몇 번 보나요?", "comment", "c2")])
        self.assertIsNone(rows[0]["asked_at_sec"])
        self.assertEqual((result["comments"], result["inserted"]), (2, 2))
        self.assertEqual(self.threads.call_args.args, ("access", "vid1", comments.MAX_PAGES))

    def test_reviews_and_long_posts_go_to_the_reaction_list_and_the_owner_nowhere(self):
        result = self.run_import([
            c("c1", "궁금한 점을 댓글로 남겨 주시면 답해 드릴까요?", author="UCowner"),
            c("c2", "좋은 영상 감사합니다"),
            c("c3", "그래서 " * 200 + "어떻게 되나요?"),
            c("c4", "신입도 지원할 수 있나요?"),
        ])
        self.assertEqual([r["external_id"] for r in self.questions()], ["c4"])
        self.assertEqual(result["skipped"], {"staff": 1, "long": 1})
        self.assertEqual(result["reviews"], 2)
        kinds = {r["comment_id"]: r["kind"] for r in self.conn.execute("select comment_id, kind from source_comments")}
        self.assertEqual(kinds, {"c2": "review", "c3": "review", "c4": "question"})

    def test_a_question_word_in_the_middle_of_a_review_is_not_a_question(self):
        # 🔴 삼성(원본 4) 실측: 채팅 규칙은 `어떻게` 가 어디 있어도 질문으로 봐서 감상 12개가 질문으로 들어왔다.
        self.run_import([c("c1", "어떻게 답변을 준비해야 할지 조금은 알겠어요. 감사합니다")])
        self.assertEqual(self.questions(), [])
        self.assertEqual(comments.counts(self.conn, self.source_id)["reviews"], 1)

    def test_importing_again_takes_back_a_question_that_is_now_a_review(self):
        # 예전 규칙으로 들어온 질문을 흉내 낸다. 하나는 이미 묶였다 — 그건 사람이 본 것이라 두고 간다.
        cluster = self.conn.execute(
            "insert into question_clusters (source_id, canonical_text) values (%s, '묶음') returning id",
            (self.source_id,),
        ).fetchone()["id"]
        insert = """insert into questions (source_id, text, viewer_id, origin, external_id, cluster_id)
                    values (%s, %s, 'yt:x', 'comment', %s, %s)"""
        self.conn.execute(insert, (self.source_id, "어떤 답이 좋은지 알겠어요", "c1", None))
        self.conn.execute(insert, (self.source_id, "어떻게 할지 감이 와요", "c2", cluster))
        self.conn.commit()
        result = self.run_import([c("c1", "어떤 답이 좋은지 알겠어요"), c("c2", "어떻게 할지 감이 와요")])
        self.assertEqual(result["withdrawn"], 1)
        self.assertEqual([r["external_id"] for r in self.questions()], ["c2"])

    def test_importing_again_refreshes_the_text(self):
        # 🔴 개발자 정책 III.E.4 의 "갱신" — 고친 댓글은 고친 글로, 받은 때는 새로.
        self.run_import([c("c1", "좋았어요")])
        before = self.conn.execute("select fetched_at from source_comments").fetchone()["fetched_at"]
        self.run_import([c("c1", "정말 좋았어요")])
        row = self.conn.execute("select text, fetched_at from source_comments").fetchone()
        self.assertEqual(row["text"], "정말 좋았어요")
        self.assertGreaterEqual(row["fetched_at"], before)
        self.assertEqual(comments.counts(self.conn, self.source_id)["total"], 1)

    def test_importing_again_adds_nothing(self):
        self.run_import([c("c1", "신입도 지원할 수 있나요?")])
        result = self.run_import([c("c1", "신입도 지원할 수 있나요?"), c("c2", "경력은 몇 년부터인가요?")])
        self.assertEqual((result["inserted"], result["already"]), (1, 1))
        self.assertEqual(len(self.questions()), 2)

    def test_no_author_name_or_channel_id_is_kept(self):
        self.run_import([c("c1", "신입도 지원할 수 있나요?", author="UCsecret")])
        row = self.questions()[0]
        self.assertEqual(row["viewer_id"], livechat.viewer_id("UCsecret"))
        dump = repr([dict(r) for r in self.conn.execute("select * from questions")])
        dump += repr([dict(r) for r in self.conn.execute("select * from source_comments")])
        dump += repr([dict(r) for r in self.conn.execute("select params from stage_calls")])
        self.assertNotIn("UCsecret", dump)

    def test_says_whether_replies_can_go_under_this_video(self):
        # 🔴 답글은 영상 주인 채널로만 단다 — 가져오면서 미리 알린다.
        self.assertTrue(self.run_import([], owner="UCowner")["ownChannel"])
        self.assertFalse(self.run_import([], owner="UCwanted")["ownChannel"])

    def test_the_call_is_recorded(self):
        self.run_import([c("c1", "신입도 지원할 수 있나요?")], truncated=True)
        call = self.conn.execute("select * from stage_calls where stage = 'comment'").fetchone()
        self.assertEqual((call["source_id"], call["error"]), (self.source_id, None))
        self.assertEqual((call["params"]["inserted"], call["params"]["truncated"]), (1, True))


class RefuseTest(CommentsTestCase):
    def test_without_the_comment_scope_nothing_is_fetched(self):
        youtube_channel.save_channel(self.conn, youtube.Grant(refresh_token="r", scopes=youtube.UPLOAD_SCOPE))
        self.conn.commit()
        with mock.patch.object(youtube, "comment_threads") as threads:
            with self.assertRaises(comments.CommentError) as caught:
                comments.import_comments(self.conn, self.cfg, self.source_id)
        threads.assert_not_called()
        self.assertIn("다시 연결", str(caught.exception))

    def test_a_source_without_a_youtube_id_is_refused(self):
        self.conn.execute("update sources set youtube_id = null")
        self.conn.commit()
        with self.assertRaises(comments.CommentError):
            comments.import_comments(self.conn, self.cfg, self.source_id)

    def test_a_youtube_failure_is_recorded_and_raised(self):
        with mock.patch.object(youtube, "access_token", return_value="access"), \
                mock.patch.object(youtube, "comment_threads", side_effect=youtube.YoutubeError("이 영상은 댓글이 꺼져 있다")):
            with self.assertRaises(youtube.YoutubeError):
                comments.import_comments(self.conn, self.cfg, self.source_id)
        call = self.conn.execute("select error from stage_calls where stage = 'comment'").fetchone()
        self.assertIn("댓글이 꺼져", call["error"])


class IsQuestionTest(unittest.TestCase):
    """🔴 문장 **끝**만 본다. 애매하면 감상 쪽이다 — 질문으로 잘못 가면 [집계]·[답하기] 비용이 든다."""

    def test_question_endings(self):
        for text in ("면접은 몇 번 보나요?", "신입도 지원할 수 있나요", "2부도 있을까요ㅎㅎ", "경력은 몇 년부터인가요~~",
                     "무엇을 준비해야 합니까 😊", "좋은 영상 감사합니다. 혹시 자료도 받을 수 있을까요"):
            with self.subTest(text=text):
                self.assertTrue(comments.is_question(text))

    def test_reviews(self):
        for text in ("어떻게 답변을 준비해야 할지 조금은 알겠어요", "이런 영상 더 올려주세요!", "좋으니까 계속 볼게요",
                     "합격 팁 중 또 하나", "@작성자 맞나요?", ""):
            with self.subTest(text=text):
                self.assertFalse(comments.is_question(text))
