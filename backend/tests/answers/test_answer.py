"""[답하기] DAG — 순서, 실패 복구, best-of-3 선택.

Gemini 는 부르지 않는다(mock). 여기서 보는 건 답변 품질이 아니라 **배선**이다:
단계가 순서대로 기록되는가, 실패하면 되돌아오는가, 셋 중 어느 것을 고르는가.
"""

import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shorts_maker.adapters import gemini
from shorts_maker.answers import answer, clusters, judge, retrieval, routing
from shorts_maker.pipeline import cutting, ranking, render

from ..support import DbTestCase, make_config


def _decision(route: str, selected: list[int] | None = None) -> routing.Decision:
    """라우팅 대역. `selected` 기본값은 None 이라 rank 경로에서는 쓰이지 않는다."""
    return routing.Decision(route=route, selected=selected, reason="대역")


def usage() -> dict:
    return {"input_tokens": 10, "output_tokens": 5, "thinking_tokens": 0,
            "total_tokens": 15, "cached_tokens": 0, "attempts": 1}


class AnswerTestCase(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = dataclasses.replace(make_config(Path(self._tmp.name)), teaser_max_sec=30.0)

        self.source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status, published, context)"
            " values ('채용설명회', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE', true, '개요') returning id"
        ).fetchone()["id"]
        chunk_id = self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path)"
            " values (%s, 0, 0, 600, 'c.wav') returning id", (self.source_id,)
        ).fetchone()["id"]
        # 발화 12개, 각 5초. 구간 둘로 나뉜다.
        for idx in range(12):
            self.conn.execute(
                "insert into utterances (chunk_id, idx, start_sec, end_sec, text)"
                " values (%s, %s, %s, %s, %s)",
                (chunk_id, idx, idx * 5, idx * 5 + 5, f"{idx}번째 발화입니다"),
            )
        self.segments = []
        for position, (lo, hi) in enumerate([(0, 5), (6, 11)]):
            self.segments.append(self.conn.execute(
                """insert into segments (chunk_id, idx, start_sec, end_sec, description,
                                         start_utterance_idx, end_utterance_idx)
                   values (%s, %s, %s, %s, %s, %s, %s) returning id""",
                (chunk_id, position, lo * 5, hi * 5 + 5, f"구간 {position} 설명", lo, hi),
            ).fetchone()["id"])
        self.cluster_id = self.conn.execute(
            "insert into question_clusters (source_id, canonical_text) values (%s, %s) returning id",
            (self.source_id, "마케터 인재상이 궁금합니다"),
        ).fetchone()["id"]
        self.conn.commit()

    def status(self) -> str:
        return self.conn.execute(
            "select status from question_clusters where id = %s", (self.cluster_id,)
        ).fetchone()["status"]

    def stages(self) -> list[str]:
        return [r["stage"] for r in self.conn.execute("select stage from stage_calls order by id")]

    def patched(self, plan: dict, verdicts: list[dict], route: str = routing.RANK):
        """LLM 셋을 전부 대역으로. rank 는 후보 계획, judge 는 후보마다 하나씩 순서대로."""
        import json

        judged = iter(verdicts)

        def fake_judge(cfg, question, part_texts):
            return judge.parse(json.dumps(next(judged))), usage(), 5

        return (
            mock.patch.object(routing, "decide", return_value=_decision(route)),
            mock.patch.object(gemini, "generate_json",
                              return_value=(json.dumps(plan, ensure_ascii=False), usage(), 7)),
            mock.patch.object(judge, "judge", side_effect=fake_judge),
            mock.patch.object(render, "run_for_clip", return_value=Path("clips/clip001.mp4")),
        )

    def run_answer(self, plan: dict, verdicts: list[dict], route: str = routing.RANK) -> dict:
        patches = self.patched(plan, verdicts, route)
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return answer.run(self.conn, self.cfg, self.cluster_id)


SINGLE = {"label": "single", "reason": "직접 답한다", "parts": [{"start_line": 0, "end_line": 2}]}
COMBO = {"label": "combo", "reason": "두 곳을 잇는다",
         "parts": [{"start_line": 0, "end_line": 1}, {"start_line": 7, "end_line": 8}]}
TIGHT = {"label": "tight", "reason": "핵심만", "parts": [{"start_line": 1, "end_line": 1}]}


def verdict(standalone=True, answers=True, score=80, reason="괜찮다") -> dict:
    return {"standalone": standalone, "answers": answers, "score": score, "reason": reason}


class HappyPathTest(AnswerTestCase):
    def test_the_stages_are_recorded_in_order(self):
        # 🔴 계측이 빠지면 어디서 느린지·비싼지를 사후에 알 수 없다(규약: 모든 단계는 stage_calls).
        self.run_answer(
            {"answerable": True, "reason": "있다", "candidates": [SINGLE, COMBO, TIGHT]},
            [verdict(score=60), verdict(score=90), verdict(score=70)],
        )
        stages = self.stages()
        self.assertEqual(stages.count("rank"), 1)
        self.assertEqual(stages.count("judge"), 3)
        self.assertLess(stages.index("rank"), stages.index("judge"))

    def test_the_highest_scoring_passing_candidate_wins(self):
        result = self.run_answer(
            {"answerable": True, "reason": "있다", "candidates": [SINGLE, COMBO, TIGHT]},
            [verdict(score=60), verdict(score=90), verdict(score=70)],
        )
        self.assertEqual(result["label"], "combo")
        self.assertEqual(result["parts"], 2)
        parts = self.conn.execute(
            "select count(*) as n from clip_parts where clip_id = %s", (result["clipId"],)
        ).fetchone()["n"]
        self.assertEqual(parts, 2)

    def test_a_failing_candidate_never_wins_over_a_passing_one(self):
        # 🔴 점수만 보면 안 된다 — 자립하지 않는 클립은 점수가 높아도 숏폼으로 못 쓴다.
        result = self.run_answer(
            {"answerable": True, "reason": "있다", "candidates": [SINGLE, COMBO, TIGHT]},
            [verdict(score=50), verdict(standalone=False, score=99), verdict(score=40)],
        )
        self.assertEqual(result["label"], "single")

    def test_when_nothing_passes_the_best_still_goes_to_review_with_a_warning(self):
        # 무한 재시도 대신 사람의 눈으로 넘긴다.
        result = self.run_answer(
            {"answerable": True, "reason": "있다", "candidates": [SINGLE, COMBO, TIGHT]},
            [verdict(answers=False, score=30), verdict(answers=False, score=55),
             verdict(standalone=False, score=20)],
        )
        self.assertEqual(self.status(), "REVIEW")
        review = self.conn.execute(
            "select verdict, reviewer from clip_reviews where clip_id = %s", (result["clipId"],)
        ).fetchone()
        self.assertEqual((review["verdict"], review["reviewer"]), ("NG", "llm"))

    def test_a_passing_winner_is_recorded_as_an_llm_ok(self):
        result = self.run_answer(
            {"answerable": True, "reason": "있다", "candidates": [SINGLE]}, [verdict()]
        )
        review = self.conn.execute(
            "select verdict, reviewer from clip_reviews where clip_id = %s", (result["clipId"],)
        ).fetchone()
        self.assertEqual((review["verdict"], review["reviewer"]), ("OK", "llm"))

    def test_the_cluster_ends_in_review_and_points_at_the_run(self):
        result = self.run_answer(
            {"answerable": True, "reason": "있다", "candidates": [SINGLE]}, [verdict()]
        )
        row = self.conn.execute(
            "select status, run_id from question_clusters where id = %s", (self.cluster_id,)
        ).fetchone()
        self.assertEqual((row["status"], row["run_id"]), ("REVIEW", result["runId"]))

    def test_every_candidate_is_kept_in_the_run_for_the_creator_to_see(self):
        result = self.run_answer(
            {"answerable": True, "reason": "있다", "candidates": [SINGLE, COMBO, TIGHT]},
            [verdict(score=60), verdict(score=90), verdict(score=70)],
        )
        ranked = self.conn.execute(
            "select ranked from runs where id = %s", (result["runId"],)
        ).fetchone()["ranked"]
        self.assertEqual(len(ranked["candidates"]), 3)
        self.assertEqual([c["won"] for c in ranked["candidates"]], [False, True, False])

    def test_the_clip_is_linked_to_the_cluster(self):
        result = self.run_answer(
            {"answerable": True, "reason": "있다", "candidates": [SINGLE]}, [verdict()]
        )
        linked = self.conn.execute(
            "select question_cluster_id, total_sec from clips where id = %s", (result["clipId"],)
        ).fetchone()
        self.assertEqual(linked["question_cluster_id"], self.cluster_id)
        self.assertEqual(linked["total_sec"], result["totalSec"])


class UnanswerableTest(AnswerTestCase):
    def test_an_unanswerable_question_stops_before_cutting(self):
        result = self.run_answer({"answerable": False, "reason": "그 얘기가 없다", "candidates": []}, [])
        self.assertFalse(result["answerable"])
        self.assertEqual(self.status(), "UNANSWERABLE")
        self.assertEqual(self.conn.execute("select count(*) as n from clips").fetchone()["n"], 0)
        self.assertNotIn("judge", self.stages())

    def test_a_weak_retrieval_score_stops_before_the_rank_call(self):
        # 검색이 바닥이면 rank 를 부를 이유가 없다 — 비용을 아끼는 게 아니라 없는 답을 만들지 않는 것이다.
        found = retrieval.Candidates(segments=[], best_score=0.1)
        with (
            # selected=None 은 "판정을 못 읽었다" 다 — 그때만 임베딩 top-k 로 물러난다.
            mock.patch.object(routing, "decide", return_value=_decision(routing.RETRIEVAL, None)),
            mock.patch.object(retrieval, "candidates", return_value=found),
            mock.patch.object(retrieval, "suggest_elsewhere", return_value=None),
            mock.patch.object(gemini, "generate_json", autospec=True) as llm,
        ):
            result = answer.run(self.conn, self.cfg, self.cluster_id)
        self.assertFalse(result["answerable"])
        self.assertEqual(self.status(), "UNANSWERABLE")
        llm.assert_not_called()

    def test_a_better_match_elsewhere_is_remembered_as_a_suggestion(self):
        other = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status)"
            " values ('ep.2', 'LECTURE', 'b.mp4', 'sha256:b', 'DONE') returning id"
        ).fetchone()["id"]
        self.conn.commit()
        found = retrieval.Candidates(segments=[], best_score=0.2)
        with (
            mock.patch.object(routing, "decide", return_value=_decision(routing.RETRIEVAL, None)),
            mock.patch.object(retrieval, "candidates", return_value=found),
            mock.patch.object(retrieval, "suggest_elsewhere", return_value=other),
        ):
            result = answer.run(self.conn, self.cfg, self.cluster_id)
        self.assertEqual(result["suggestedSourceId"], other)
        self.assertEqual(
            self.conn.execute(
                "select suggested_source_id from question_clusters where id = %s", (self.cluster_id,)
            ).fetchone()["suggested_source_id"],
            other,
        )


class SegmentSelectionTest(AnswerTestCase):
    """🔴 구간을 LLM 이 고른다 (2026-09-09, update_plan M5 실측).

    임베딩 top-k 로는 답이 담긴 구간이 44개 중 29위여서 못 찾았다. 그래서 라우팅 호출이 구간 목록을
    함께 보고 고른다. 여기서 보는 건 그 선택이 **다음 단계에 제대로 전달되는가** 다.
    """

    def rank_prompt(self, llm) -> str:
        """rank 호출에 실제로 들어간 프롬프트."""
        self.assertTrue(llm.call_args_list, "rank 가 불리지 않았다")
        return llm.call_args_list[0].args[1]

    def test_only_the_chosen_segments_reach_the_rank_call(self):
        import json

        plan = {"answerable": True, "reason": "있다", "candidates": [SINGLE]}
        with (
            mock.patch.object(routing, "decide", return_value=_decision(routing.RETRIEVAL, [1])),
            mock.patch.object(gemini, "generate_json",
                              return_value=(json.dumps(plan, ensure_ascii=False), usage(), 7)) as llm,
            mock.patch.object(judge, "judge", return_value=(judge.parse(json.dumps(verdict())), usage(), 5)),
            mock.patch.object(render, "run_for_clip", return_value=Path("clips/clip001.mp4")),
        ):
            answer.run(self.conn, self.cfg, self.cluster_id)
        prompt = self.rank_prompt(llm)
        self.assertIn("[구간 1]", prompt)
        self.assertNotIn("[구간 0]", prompt)

    def test_the_chosen_segments_are_put_back_in_time_order(self):
        """🔴 모델은 관련 높은 순으로 준다. 다음 단계는 시간 순을 전제한다 —
        조합 후보의 조각이 시간 순이어야 하고(parse_answer) 발화 번호도 시간 순으로 매겨진다."""
        import json

        plan = {"answerable": True, "reason": "있다", "candidates": [SINGLE]}
        with (
            # 1번 구간이 더 관련 높다고 답한 경우.
            mock.patch.object(routing, "decide", return_value=_decision(routing.RETRIEVAL, [1, 0])),
            mock.patch.object(gemini, "generate_json",
                              return_value=(json.dumps(plan, ensure_ascii=False), usage(), 7)) as llm,
            mock.patch.object(judge, "judge", return_value=(judge.parse(json.dumps(verdict())), usage(), 5)),
            mock.patch.object(render, "run_for_clip", return_value=Path("clips/clip001.mp4")),
        ):
            answer.run(self.conn, self.cfg, self.cluster_id)
        prompt = self.rank_prompt(llm)
        self.assertLess(prompt.index("[구간 0]"), prompt.index("[구간 1]"))

    def test_an_empty_selection_is_unanswerable_without_calling_rank(self):
        # 모델이 구간 목록 전체를 보고 "없다" 고 답했다. 없는 답을 만들려고 rank 를 부를 이유가 없다.
        decision = _decision(routing.RETRIEVAL, [])
        decision.reason = "복지에 대한 내용이 이 영상에 없습니다"
        with (
            mock.patch.object(routing, "decide", return_value=decision),
            mock.patch.object(retrieval, "suggest_elsewhere", return_value=None),
            mock.patch.object(gemini, "generate_json", autospec=True) as llm,
        ):
            result = answer.run(self.conn, self.cfg, self.cluster_id)
        self.assertFalse(result["answerable"])
        self.assertEqual(self.status(), "UNANSWERABLE")
        llm.assert_not_called()
        # 🔴 모델이 말한 이유가 그대로 남아야 한다 — 크리에이터가 화면에서 읽는 문장이다.
        self.assertIn("복지", result["reason"])

    def test_an_empty_selection_never_falls_back_to_embeddings(self):
        """🔴 "없다" 는 판정이다. 폴백을 타면 약한 신호로 억지 답을 만들게 된다."""
        with (
            mock.patch.object(routing, "decide", return_value=_decision(routing.RETRIEVAL, [])),
            mock.patch.object(retrieval, "suggest_elsewhere", return_value=None),
            mock.patch.object(retrieval, "candidates", autospec=True) as fallback,
            mock.patch.object(gemini, "generate_json", autospec=True),
        ):
            answer.run(self.conn, self.cfg, self.cluster_id)
        fallback.assert_not_called()

    def test_a_suggestion_failure_does_not_trap_the_cluster(self):
        """🔴 안내는 부수 정보다. 임베딩 할당량이 떨어졌다고 답변 불가를 기록조차 못 하면
        클러스터가 IN_PROGRESS 에 갇혀 크리에이터가 다시 누를 수도 없다."""
        with (
            mock.patch.object(routing, "decide", return_value=_decision(routing.RETRIEVAL, [])),
            mock.patch.object(retrieval, "suggest_elsewhere",
                              side_effect=gemini.GeminiError("할당량 소진")),
        ):
            result = answer.run(self.conn, self.cfg, self.cluster_id)
        self.assertFalse(result["answerable"])
        self.assertEqual(self.status(), "UNANSWERABLE")
        self.assertIsNone(result["suggestedSourceId"])

    def test_the_rank_route_still_sees_every_segment(self):
        # 크리에이터의 "핵심만 30초로" 경로다. 좁히지 않는다 — 찾을 대상이 없으니 전체가 후보다.
        import json

        plan = {"answerable": True, "reason": "있다", "candidates": [SINGLE]}
        with (
            mock.patch.object(routing, "decide", return_value=_decision(routing.RANK, [])),
            mock.patch.object(gemini, "generate_json",
                              return_value=(json.dumps(plan, ensure_ascii=False), usage(), 7)) as llm,
            mock.patch.object(judge, "judge", return_value=(judge.parse(json.dumps(verdict())), usage(), 5)),
            mock.patch.object(render, "run_for_clip", return_value=Path("clips/clip001.mp4")),
        ):
            answer.run(self.conn, self.cfg, self.cluster_id)
        prompt = self.rank_prompt(llm)
        self.assertIn("[구간 0]", prompt)
        self.assertIn("[구간 1]", prompt)


class FailureTest(AnswerTestCase):
    def test_a_failure_returns_the_cluster_to_open(self):
        # 🔴 IN_PROGRESS 로 남으면 크리에이터가 다시 누를 수 없다.
        with (
            mock.patch.object(routing, "decide", return_value=_decision(routing.RANK)),
            mock.patch.object(gemini, "generate_json", side_effect=gemini.GeminiError("과부하")),
        ):
            with self.assertRaises(gemini.GeminiError):
                answer.run(self.conn, self.cfg, self.cluster_id)
        self.assertEqual(self.status(), "OPEN")

    def test_the_reason_is_kept_on_the_run(self):
        with (
            mock.patch.object(routing, "decide", return_value=_decision(routing.RANK)),
            mock.patch.object(gemini, "generate_json", side_effect=gemini.GeminiError("과부하")),
        ):
            with self.assertRaises(gemini.GeminiError):
                answer.run(self.conn, self.cfg, self.cluster_id)
        row = self.conn.execute("select status, error from runs order by id desc limit 1").fetchone()
        self.assertEqual(row["status"], "FAILED")
        self.assertIn("과부하", row["error"])

    def test_a_render_failure_also_returns_to_open(self):
        patches = [
            mock.patch.object(routing, "decide", return_value=_decision(routing.RANK)),
            mock.patch.object(
                gemini, "generate_json",
                return_value=('{"answerable": true, "reason": "있다", "candidates": '
                              '[{"label": "single", "reason": "r", "parts": '
                              '[{"start_line": 0, "end_line": 2}]}]}', usage(), 7)),
            mock.patch.object(judge, "judge", return_value=(judge.Verdict(True, True, 80, "ok"), usage(), 5)),
            mock.patch.object(render, "run_for_clip", side_effect=render.RenderError("ffmpeg 죽음")),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with self.assertRaises(render.RenderError):
            answer.run(self.conn, self.cfg, self.cluster_id)
        self.assertEqual(self.status(), "OPEN")


class BudgetTest(AnswerTestCase):
    """🔴 LLM 이 말한 길이는 믿지 않는다 — 코드가 강제한다(tease §5-6)."""

    def lines(self):
        segments = retrieval.load_segments(self.conn, self.source_id)
        return ranking.number_lines(self.conn, segments)

    def parts_of(self, *ranges):
        lines = self.lines()
        return cutting.resolve_parts([ranking.Part(a, b) for a, b in ranges], lines), lines

    def test_a_fitting_set_is_left_alone(self):
        parts, lines = self.parts_of((0, 2), (7, 8))   # 15초 + 10초 = 25초
        kept = cutting.enforce_budget(parts, 30.0, lines)
        self.assertEqual(len(kept), 2)

    def test_trailing_parts_are_dropped_until_it_fits(self):
        # 앞이 대개 답의 핵심이라 뒤부터 떨어뜨린다.
        parts, lines = self.parts_of((0, 3), (6, 8))   # 20초 + 15초 = 35초
        kept = cutting.enforce_budget(parts, 30.0, lines)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].end_sec, 20.0)

    def test_a_single_oversized_part_is_trimmed_at_an_utterance_boundary(self):
        # 🔴 초 단위로 자르면 말 중간에서 끊긴다. 발화 경계가 곧 컷 지점이다.
        parts, lines = self.parts_of((0, 5))   # 30초를 넘는 한 덩어리
        kept = cutting.enforce_budget(parts, 22.0, lines)
        self.assertEqual(len(kept), 1)
        self.assertLessEqual(kept[0].end_sec - kept[0].start_sec, 22.0)
        self.assertEqual(kept[0].end_sec % 5, 0)   # 발화 경계

    def test_the_budget_is_applied_before_judging(self):
        # judge 는 실제로 나갈 대사를 봐야 한다. 예산 전 대사를 보면 판정이 거짓이 된다.
        seen: list[list[str]] = []

        def spy(cfg, question, part_texts):
            seen.append(part_texts)
            return judge.Verdict(True, True, 80, "ok"), usage(), 5

        patches = [
            mock.patch.object(routing, "decide", return_value=_decision(routing.RANK)),
            mock.patch.object(
                gemini, "generate_json",
                return_value=('{"answerable": true, "reason": "있다", "candidates": '
                              '[{"label": "single", "reason": "r", "parts": '
                              '[{"start_line": 0, "end_line": 5}]}]}', usage(), 7)),
            mock.patch.object(judge, "judge", side_effect=spy),
            mock.patch.object(render, "run_for_clip", return_value=Path("clips/clip001.mp4")),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        # 예산을 12초로 좁혀 30초짜리 후보가 반드시 잘리게 한다.
        result = answer.run(self.conn, dataclasses.replace(self.cfg, teaser_max_sec=12.0), self.cluster_id)
        self.assertLessEqual(result["totalSec"], 12.0)
        # 🔴 judge 는 **잘린 뒤**의 대사를 봐야 한다. 예산 전 대사를 보면 판정이 거짓이 된다.
        self.assertEqual(len(seen), 1)
        self.assertNotIn("5번째 발화", seen[0][0])
        self.assertIn("0번째 발화", seen[0][0])


if __name__ == "__main__":
    unittest.main()
