"""관리자 기준 (프롬프트 2층, `shorts_maker/standards.py`).

🔴 여기서 지키는 것 셋:
  1. **append-only.** 고칠 때마다 행이 쌓이고 최신이 이긴다 — "지난주 클립은 어떤 기준으로 뽑혔나" 에
     답할 수 있어야 한다
  2. **넣는 곳과 안 넣는 곳.** 순위·후보·자르기·판정 점수에는 들어가고, 구간 분할·답 구간 찾기·
     영상 개요에는 **절대 안 들어간다.** 구간은 모든 기준이 재사용하는 중립 자산이고, "어디에 답이
     있나" 와 "이 영상이 무엇인가" 는 사실 판단이다. 누가 나중에 무심코 넣으면 이 테스트가 막는다
  3. **판정에서는 점수에만.** 두 관문에 섞이면 선호가 숨은 탈락 조건이 된다
"""

import inspect
import unittest

import psycopg

from shorts_maker import standards
from shorts_maker.answers import judge, routing
from shorts_maker.pipeline import cutting, overview, ranking, segmentation

from .support import DbTestCase

MARK = "숫자·조건·기한이 있는 발화를 우선한다 [기준표식]"


class StoreTest(DbTestCase):
    def test_nothing_saved_means_no_standard(self):
        self.assertIsNone(standards.load(self.conn))
        self.assertIsNone(standards.latest(self.conn))

    def test_the_latest_save_wins(self):
        standards.save(self.conn, "처음 기준")
        standards.save(self.conn, "고친 기준")
        self.assertEqual(standards.load(self.conn), "고친 기준")

    def test_history_is_kept_not_overwritten(self):
        # 🔴 제자리 UPDATE 면 "어떤 기준으로 뽑혔나" 가 사라진다.
        standards.save(self.conn, "처음 기준")
        standards.save(self.conn, "고친 기준")
        n = self.conn.execute("select count(*) as n from admin_standards").fetchone()["n"]
        self.assertEqual(n, 2)

    def test_a_blank_save_clears_the_standard(self):
        # 지우는 방법은 빈 글을 저장하는 것이다. 행이 없는 것과 같게 읽힌다.
        standards.save(self.conn, "기준")
        standards.save(self.conn, "   ")
        self.assertIsNone(standards.load(self.conn))
        self.assertEqual(standards.latest(self.conn)["body"], "")

    def test_surrounding_whitespace_is_trimmed(self):
        standards.save(self.conn, "  기준  \n")
        self.assertEqual(standards.load(self.conn), "기준")

    def test_over_the_cap_is_refused_before_writing(self):
        """🔴 기준은 모든 순위·판정 호출에 붙는다. 판정 프롬프트보다 길면 본문을 덮어 버린다."""
        with self.assertRaises(standards.StandardError):
            standards.save(self.conn, "가" * (standards.MAX_CHARS + 1))
        n = self.conn.execute("select count(*) as n from admin_standards").fetchone()["n"]
        self.assertEqual(n, 0)

    def test_exactly_at_the_cap_is_allowed(self):
        standards.save(self.conn, "가" * standards.MAX_CHARS)
        self.assertEqual(len(standards.load(self.conn)), standards.MAX_CHARS)

    def test_the_database_enforces_the_cap_too(self):
        # 코드를 우회해 넣어도 막힌다. 두 값이 어긋나면 이 테스트가 먼저 안다.
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.conn.execute(
                "insert into admin_standards (body) values (%s)", ("가" * (standards.MAX_CHARS + 1),)
            )
        self.conn.rollback()


class BlockTest(unittest.TestCase):
    def test_an_empty_standard_leaves_no_trace(self):
        # 🔴 빈 지시문을 남기면 모델이 그걸 해석한다. 줄 자체를 뺀다.
        for empty in (None, "", "   \n"):
            with self.subTest(empty=empty):
                self.assertEqual(standards.block(empty), "")
                self.assertEqual(standards.score_block(empty), "")

    def test_the_block_calls_it_a_preference(self):
        text = standards.block(MARK)
        self.assertIn(MARK, text)
        self.assertIn("선호", text)

    def test_the_block_says_the_request_wins_on_conflict(self):
        # 채널 공통 기준보다 이번에 콕 집어 요청한 것이 더 구체적이다.
        self.assertIn("이번 요청을 따른다", standards.block(MARK))

    def test_the_score_block_keeps_the_gates_out(self):
        text = standards.score_block(MARK)
        self.assertIn("score 에만", text)
        self.assertIn("standalone", text)
        self.assertIn("반영하지 않는다", text)


class WhereItGoesTest(unittest.TestCase):
    """🔴 넣는 곳과 안 넣는 곳. 이 테스트가 없으면 몇 달 뒤 누군가 "기준을 구간 분할에도 넣자" 를
    무심코 하고, 그 순간 구간이 중립 자산이 아니게 된다."""

    SEGMENTS = [{"idx": 0, "start_sec": 0, "end_sec": 30, "description": "설명"}]
    LINES = [{
        "line": 0, "segment_id": 1, "segment_idx": 0, "description": "설명", "chunk_id": 1,
        "utterance_idx": 0, "start_sec": 0.0, "end_sec": 5.0, "text": "발화",
    }]
    UTTERANCES = [{"idx": 0, "start_sec": 0.0, "end_sec": 5.0, "text": "발화"}]

    # ---- 들어가는 곳 ----

    def test_the_criteria_rank_carries_it(self):
        self.assertIn(MARK, ranking.build_prompt(self.SEGMENTS, None, None, MARK))

    def test_the_answer_candidates_carry_it(self):
        self.assertIn(MARK, ranking.build_answer_prompt("질문", self.LINES, 30.0, None, 3, MARK))

    def test_the_cut_carries_it(self):
        self.assertIn(MARK, cutting.build_prompt(self.UTTERANCES, "설명", None, MARK))

    def test_the_judge_carries_it_for_the_score_only(self):
        prompt = judge.build_prompt("질문", ["대사"], MARK)
        self.assertIn(MARK, prompt)
        self.assertIn("score 에만", prompt)

    def test_the_criteria_rank_puts_the_standard_before_this_runs_criteria(self):
        # 공통이 먼저, 이번 기준이 뒤 — 부딪히면 뒤의 구체적인 요청이 이긴다고 머리에 적혀 있다.
        prompt = ranking.build_prompt(self.SEGMENTS, None, "이번 기준 [이번표식]", MARK)
        self.assertLess(prompt.index(MARK), prompt.index("[이번표식]"))

    # ---- 안 들어가는 곳 ----

    def test_segmentation_cannot_take_a_standard(self):
        """🔴 구간은 캐시해서 **모든 기준이 재사용하는 중립 자산**이다. 취향이 섞이면 기준을 바꿀
        때마다 전사부터 다시 해야 한다."""
        self.assertNotIn("standard", inspect.signature(segmentation.build_prompt).parameters)
        self.assertNotIn("관리자 기준", segmentation.build_prompt(self.UTTERANCES, None))

    def test_answer_location_cannot_take_a_standard(self):
        """🔴 "어디에 답이 있나" 는 사실 판단이다. 기준이 섞이면 선호하지 않는 구간이 검색에서
        사라져 답할 수 있는 질문을 못 답하게 된다."""
        self.assertNotIn("standard", inspect.signature(routing.build_prompt).parameters)
        self.assertNotIn("standard", inspect.signature(routing.decide).parameters)

    def test_the_overview_draft_cannot_take_a_standard(self):
        """🔴 영상 개요는 "이 영상이 무엇인가" 라는 **사실**이다(프롬프트 3층).

        취향이 섞이면 그 취향이 3층으로 위장해 이후 모든 호출에 따라붙고, 그때는 어느 층에서
        왔는지 아무도 모른다 — 2층은 화면에서 고치고 지울 수 있지만 3층에 스며든 것은 못 뺀다.
        """
        self.assertNotIn("standard", inspect.signature(overview.build_prompt).parameters)
        self.assertNotIn("관리자 기준", overview.build_prompt("제목", ["구간 요약"]))

    # ---- 비었을 때 ----

    def test_without_a_standard_every_prompt_is_as_before(self):
        # 🔴 기준 기능을 넣었다고 기존 결과가 흔들리면 안 된다.
        header = "관리자 기준"
        self.assertNotIn(header, ranking.build_prompt(self.SEGMENTS, None, None))
        self.assertNotIn(header, ranking.build_answer_prompt("질문", self.LINES, 30.0, None))
        self.assertNotIn(header, cutting.build_prompt(self.UTTERANCES, "설명", None))
        self.assertNotIn(header, judge.build_prompt("질문", ["대사"]))


if __name__ == "__main__":
    unittest.main()
