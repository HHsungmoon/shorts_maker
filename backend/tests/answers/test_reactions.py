"""시청자 반응 — 감상 댓글 AI 요약 (tease §3-6, 2026-11-01).

🔴 지키는 것: ① 감상이 없으면 부르지 않는다 ② 근거 번호는 화이트리스트로 검증하고 **개수는 코드가 센다**
③ 근거 없는 묶음은 버린다 ④ 요약 관점은 들어가고 관리자 기준은 안 들어간다 ⑤ 질문 갈래 댓글은 보내지 않는다
⑥ 성공도 실패도 `stage_calls` 에 남는다 ⑦ 요약 뒤 감상 수가 바뀌면 stale.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shorts_maker import standards
from shorts_maker.adapters import gemini
from shorts_maker.answers import reactions, report

from ..support import DbTestCase, make_config

USAGE = {"input_tokens": 10, "output_tokens": 5, "thinking_tokens": 0, "total_tokens": 15, "cached_tokens": 0,
         "attempts": 1}


class ParseTest(unittest.TestCase):
    REVIEWS = [{"id": 11, "text": "좋았어요"}, {"id": 12, "text": "소리가 작아요"}, {"id": 13, "text": "2부 해주세요"}]

    def parse(self, themes, overview="전반적으로 좋았다"):
        return reactions.parse(json.dumps({"overview": overview, "themes": themes}), self.REVIEWS)

    def test_numbers_become_comment_ids_and_bad_ones_are_dropped(self):
        body = self.parse([{"kind": "positive", "title": "유익", "detail": "d", "comments": [1, 1, 9, 0, True, "2"]}])
        self.assertEqual(body["themes"][0]["comments"], [11])

    def test_a_theme_without_evidence_or_with_an_unknown_kind_is_dropped(self):
        body = self.parse([
            {"kind": "positive", "title": "근거 없음", "detail": "", "comments": [99]},
            {"kind": "angry", "title": "모르는 종류", "detail": "", "comments": [1]},
            {"kind": "concern", "title": "음량", "detail": "", "comments": [2]},
        ])
        self.assertEqual([t["title"] for t in body["themes"]], ["음량"])

    def test_themes_are_ordered_by_kind_then_evidence(self):
        body = self.parse([
            {"kind": "request", "title": "2부", "detail": "", "comments": [3]},
            {"kind": "positive", "title": "하나", "detail": "", "comments": [1]},
            {"kind": "positive", "title": "둘", "detail": "", "comments": [1, 2]},
        ])
        self.assertEqual([t["title"] for t in body["themes"]], ["둘", "하나", "2부"])

    def test_an_empty_answer_is_an_error(self):
        with self.assertRaises(reactions.ReactionError):
            self.parse([], overview="")
        with self.assertRaises(reactions.ReactionError):
            reactions.parse("not json", self.REVIEWS)


class PromptTest(unittest.TestCase):
    def test_numbers_start_at_one_and_the_db_id_is_not_shown(self):
        prompt = reactions.build_prompt([{"id": 9041, "text": "좋았어요"}], None, None)
        self.assertIn("[1] 좋았어요", prompt)
        self.assertNotIn("9041", prompt)

    def test_an_empty_guide_and_context_leave_no_trace(self):
        prompt = reactions.build_prompt([{"id": 1, "text": "좋았어요"}], " ", "")
        self.assertNotIn("요약 관점", prompt)
        self.assertNotIn("영상 개요", prompt)

    def test_a_long_comment_is_cut(self):
        prompt = reactions.build_prompt([{"id": 1, "text": "가" * 2000}], None, None)
        self.assertIn("가" * reactions.COMMENT_MAX_CHARS + "…", prompt)


class SummarizeTest(DbTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = make_config(Path(self._tmp.name))
        self.source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, context)"
            " values ('강연', 'LECTURE', 'a.mp4', 'sha256:a', '삼성 면접 강연') returning id"
        ).fetchone()["id"]
        self.conn.commit()

    def comment(self, comment_id: str, text: str, kind: str = "review") -> int:
        return self.conn.execute(
            """insert into source_comments (source_id, comment_id, kind, text, viewer_id, published_at)
               values (%s, %s, %s, %s, 'yt:x', now()) returning id""",
            (self.source_id, comment_id, kind, text),
        ).fetchone()["id"]

    def run_summary(self, themes, overview="좋은 반응"):
        raw = json.dumps({"overview": overview, "themes": themes}, ensure_ascii=False)
        with mock.patch.object(gemini, "generate_json", return_value=(raw, USAGE, 7)) as llm:
            result = reactions.summarize(self.conn, self.cfg, self.source_id)
        self.llm = llm
        return result

    def test_no_reviews_means_no_call(self):
        self.comment("q1", "면접 몇 번 보나요?", kind="question")
        with mock.patch.object(gemini, "generate_json") as llm:
            with self.assertRaises(reactions.ReactionError):
                reactions.summarize(self.conn, self.cfg, self.source_id)
        llm.assert_not_called()

    def test_a_summary_is_stored_and_counted_by_code(self):
        first = self.comment("c1", "유익했어요")
        second = self.comment("c2", "정말 도움 됐어요")
        self.comment("q1", "면접 몇 번 보나요?", kind="question")
        standards.save(self.conn, "관리자기준표식")
        standards.save(self.conn, "요약관점표식", standards.REACTION)
        self.conn.commit()
        result = self.run_summary([{"kind": "positive", "title": "유익", "detail": "도움이 됐다", "comments": [1, 2]}])
        self.assertEqual((result["reviews"], result["themes"]), (2, 1))

        prompt = self.llm.call_args.args[1]
        self.assertIn("요약관점표식", prompt)
        self.assertIn("삼성 면접 강연", prompt)
        # 🔴 관리자 기준은 클립 선호다 — 요약에 들어가지 않는다. 질문 갈래도 보내지 않는다.
        self.assertNotIn("관리자기준표식", prompt)
        self.assertNotIn("면접 몇 번", prompt)

        shown = report.build(self.conn, self.cfg, self.source_id)["reactions"]
        self.assertEqual((shown["total"], shown["questions"], len(shown["reviews"])), (3, 1, 2))
        theme = shown["summary"]["themes"][0]
        self.assertEqual((theme["comments"], theme["count"], theme["label"]), ([first, second], 2, "좋았던 점"))
        self.assertFalse(shown["summary"]["stale"])
        self.assertEqual(shown["summary"]["overview"], "좋은 반응")

        call = self.conn.execute("select * from stage_calls where stage = 'reaction'").fetchone()
        self.assertEqual((call["error"], call["input_tokens"], call["params"]["reviews"]), (None, 10, 2))

    def test_a_new_review_after_the_summary_marks_it_stale(self):
        self.comment("c1", "유익했어요")
        self.conn.commit()
        self.run_summary([{"kind": "positive", "title": "유익", "detail": "", "comments": [1]}])
        self.comment("c2", "소리가 작아요")
        self.assertTrue(reactions.view(self.conn, self.source_id)["summary"]["stale"])

    def test_a_vanished_comment_drops_out_of_its_theme(self):
        self.comment("c1", "유익했어요")
        self.comment("c2", "좋았어요")
        self.conn.commit()
        self.run_summary([{"kind": "positive", "title": "유익", "detail": "", "comments": [1, 2]}])
        # 다시 가져오며 질문 갈래로 바뀐 댓글은 근거에서 빠진다.
        self.conn.execute("update source_comments set kind = 'question' where comment_id = 'c2'")
        theme = reactions.view(self.conn, self.source_id)["summary"]["themes"][0]
        self.assertEqual(theme["count"], 1)

    def test_an_unusable_answer_is_recorded_and_nothing_is_stored(self):
        self.comment("c1", "유익했어요")
        self.conn.commit()
        with self.assertRaises(reactions.ReactionError):
            self.run_summary([{"kind": "positive", "title": "유익", "detail": "", "comments": [42]}], overview="")
        self.assertIsNone(reactions.latest(self.conn, self.source_id))
        call = self.conn.execute("select error from stage_calls where stage = 'reaction'").fetchone()
        self.assertIn("ReactionError", call["error"])

    def test_a_gemini_failure_is_recorded_and_raised(self):
        self.comment("c1", "유익했어요")
        self.conn.commit()
        with mock.patch.object(gemini, "generate_json", side_effect=gemini.GeminiError("할당량")):
            with self.assertRaises(gemini.GeminiError):
                reactions.summarize(self.conn, self.cfg, self.source_id)
        call = self.conn.execute("select error from stage_calls where stage = 'reaction'").fetchone()
        self.assertIn("할당량", call["error"])

    def test_a_source_without_comments_reports_zero(self):
        shown = reactions.view(self.conn, self.source_id)
        self.assertEqual((shown["total"], shown["reviews"], shown["summary"]), (0, [], None))
