"""jev 엔진의 후보 만들기 — 위치 찾기 → 범위 뽑기 → 판정 → 고르기.

Jev 는 부르지 않는다(mock). 여기서 보는 건 판정 품질이 아니라 **코드가 지키는 규칙**이다:
범위가 같은 구간 안·예산 이내·앞을 가리키지 않는 첫머리인가, 겹치는 후보를 하나만 남기는가,
판정이 실패해도 나머지로 가는가, 기록이 남는가.
"""

import dataclasses
import json
import unittest
from unittest import mock

from shorts_maker.adapters import jev
from shorts_maker.answers import answer, judge, proposals, routing

from .test_answer import COMBO, SINGLE, TIGHT, AnswerTestCase, verdict


def line(
    n: int, text: str, segment: int = 1, start: float | None = None, length: float = 5.0,
    chunk: int = 1, utterance: int | None = None,
) -> dict:
    begin = n * length if start is None else start
    return {
        "line": n, "segment_id": segment, "segment_idx": segment - 1, "description": None,
        "chunk_id": chunk, "utterance_idx": n if utterance is None else utterance,
        "start_sec": begin, "end_sec": begin + length, "text": text, "shoulder": False,
    }


def judged(passed: bool, score: int, p_answers: float = 0.8) -> judge.Judged:
    v = judge.Verdict(passed, passed, score, "대역", p_standalone=0.9 if passed else 0.1, p_answers=p_answers)
    return judge.Judged(v, {"input_tokens": 1, "output_tokens": 1, "model": "jev-1.14.0"}, 1, "{}", "{}")


class LocateRequestTest(unittest.TestCase):
    LINES = [line(0, "가입니다", 1), line(1, "나입니다", 1), line(2, "다입니다", 2)]

    def test_every_segment_gets_a_where_and_a_has_question(self):
        _, questions, covers = proposals.locate_request("질문", self.LINES)
        self.assertEqual(set(questions), {"exists", "has_0", "where_0", "has_1", "where_1"})
        self.assertEqual(set(questions["where_0"]["criteria"]), {"L000", "L001"})
        self.assertEqual(covers["where_1"], [2])

    def test_the_transcript_carries_line_ids_and_no_segment_summaries(self):
        # 🔴 판단할 것은 요약이 아니라 실제 대사다.
        lines = [dict(self.LINES[0], description="요약문[표식]")]
        state, _, _ = proposals.locate_request("질문", lines)
        self.assertIn("L000| 가입니다", state["transcript"])
        self.assertNotIn("[표식]", state["transcript"])

    def test_a_segment_over_the_choice_limit_is_split_into_windows(self):
        lines = [line(n, f"{n}입니다") for n in range(proposals.MAX_CHOICES + 3)]
        _, questions, covers = proposals.locate_request("질문", lines)
        self.assertIn("where_0_0", questions)
        self.assertEqual(len(covers["where_0_1"]), 3)
        self.assertLessEqual(len(questions["where_0_0"]["criteria"]), proposals.MAX_CHOICES)


class LocatedTest(unittest.TestCase):
    LINES = [line(0, "가", 1), line(1, "나", 1), line(2, "다", 1), line(5, "라", 2)]

    def answers(self, has0=0.8, has1=0.4) -> dict:
        return {
            "exists": {"noul": 0.9},
            "has_0": {"noul": has0}, "has_1": {"noul": has1},
            "where_0": {"probabilities": {"L000": 0.1, "L001": 0.6, "L002": 0.3}},
            "where_1": {"probabilities": {"L005": 1.0}},
        }

    def covers(self):
        return {"has_0": [0, 1, 2], "where_0": [0, 1, 2], "has_1": [5], "where_1": [5]}

    def test_a_line_weighs_its_segment_times_its_share(self):
        located = proposals.read_located(self.answers(), self.covers(), self.LINES)
        self.assertAlmostEqual(located.line_weight[1], 0.8 * 0.6)
        self.assertAlmostEqual(located.line_weight[5], 0.4 * 1.0)

    def test_anchors_skip_lines_crowding_one_already_taken(self):
        located = proposals.read_located(self.answers(), self.covers(), self.LINES)
        # 1(0.48) → 5(0.40) → 2 는 1 과 한 줄 차이라 건너뛴다 → 0 도 붙어 있다
        self.assertEqual(proposals.anchors_of(located, self.LINES, limit=4), [1, 5])

    def test_anchors_far_lighter_than_the_best_are_not_taken(self):
        # 답이 없는 구간의 줄로 개수를 채우면 찌꺼기 후보가 자리를 차지한다(ANCHOR_FLOOR 실측).
        located = proposals.read_located(self.answers(has1=0.05), self.covers(), self.LINES)
        self.assertEqual(proposals.anchors_of(located, self.LINES, limit=4), [1])


class RangesTest(unittest.TestCase):
    def test_a_start_pointing_back_is_never_offered(self):
        lines = [
            line(0, "취업률이 높습니다."),
            line(1, "그래서 인문계열도 마찬가지입니다."),
            line(2, "유지취업률도 1위입니다."),
        ]
        starts = {start for start, _ in proposals.ranges_around(2, lines, 40.0)}
        self.assertNotIn(1, starts)
        self.assertIn(0, starts)

    def test_a_start_mid_sentence_is_never_offered(self):
        # 앞 발화가 문장을 끝내지 않았다 → 1번 줄은 문장 한복판이다.
        lines = [line(0, "저희 학교는"), line(1, "취업률이 높습니다."), line(2, "1위입니다.")]
        starts = {start for start, _ in proposals.ranges_around(2, lines, 40.0)}
        self.assertNotIn(1, starts)

    def test_every_range_fits_the_budget_and_the_floor(self):
        lines = [line(n, f"{n}번째입니다.") for n in range(12)]
        for start, end in proposals.ranges_around(6, lines, 20.0):
            length = lines[end]["end_sec"] - lines[start]["start_sec"]
            self.assertGreaterEqual(length, proposals.MIN_SEC)
            self.assertLessEqual(length, 20.0)

    def test_a_range_reaches_back_across_a_segment_boundary_when_the_talk_continues(self):
        # 🔴 실측: "이 논술전형 선발할 때 …" 가 앞 구간 끝이라 잘려, 논술 기준이 교과 질문의 1등이 됐다.
        lines = [line(0, "이 논술전형 선발할 때 기준을 적용합니다.", 1), line(1, "7 이내면 됩니다.", 2),
                 line(2, "한국사는 4등급입니다.", 2)]
        starts = {start for start, _ in proposals.ranges_around(1, lines, 40.0)}
        self.assertIn(0, starts)

    def test_a_range_never_crosses_a_gap_or_another_chunk(self):
        # 발화 번호가 건너뛰었거나(고르지 않은 구간이 사이에 있다) 청크가 다르면 시간상 점프다.
        gap = [line(0, "앞입니다.", 1, utterance=0), line(1, "답입니다.", 2, utterance=5),
               line(2, "이어집니다.", 2, utterance=6)]
        other_chunk = [line(0, "앞입니다.", 1, chunk=1), line(1, "답입니다.", 2, chunk=2, utterance=0),
                       line(2, "이어집니다.", 2, chunk=2, utterance=1)]
        for lines in (gap, other_chunk):
            with self.subTest():
                self.assertTrue(all(start >= 1 for start, _ in proposals.ranges_around(1, lines, 40.0)))

    def test_with_nowhere_good_to_cut_the_anchor_alone_is_offered(self):
        # 발화 하나가 예산보다 길다 — 범위를 하나도 못 내면 그 자리의 답이 사라진다.
        lines = [line(0, "아주 긴 발화입니다.", length=50.0)]
        self.assertEqual(proposals.ranges_around(0, lines, 40.0), [(0, 0)])


def span(start: int, end: int, anchor: int | None = None, length: float = 5.0) -> proposals.Span:
    return proposals.Span([(start, end)], start if anchor is None else anchor, [
        dataclasses.replace(_PART, start_sec=start * length, end_sec=(end + 1) * length)
    ])


_PART = proposals.cutting.PartSpec(1, 1, 0, 0, 0.0, 5.0, "대사")


class SelectTest(unittest.TestCase):
    def test_a_passing_candidate_outranks_a_higher_scoring_failure(self):
        picked = proposals.select([(span(0, 2), judged(False, 95)), (span(5, 7), judged(True, 60))], 1)
        self.assertEqual(picked[0][0].ranges, [(5, 7)])

    def test_heavy_overlap_keeps_only_the_better_one(self):
        picked = proposals.select([
            (span(0, 4), judged(True, 90)),
            (span(1, 4), judged(True, 85)),   # 4줄 중 4줄이 겹친다
            (span(8, 10), judged(True, 70)),
        ], 5)
        self.assertEqual([s.ranges for s, _ in picked], [[(0, 4)], [(8, 10)]])

    def test_the_limit_is_respected(self):
        items = [(span(n * 10, n * 10 + 2), judged(True, 50 + n)) for n in range(8)]
        self.assertEqual(len(proposals.select(items, 5)), 5)

    def test_once_something_passes_non_answers_are_left_out(self):
        not_an_answer = judge.Judged(
            judge.Verdict(True, False, 5, "대역", p_standalone=0.9, p_answers=0.02), {}, 1, "{}", "{}"
        )
        answers_but_leans_back = judge.Judged(
            judge.Verdict(False, True, 40, "대역", p_standalone=0.3, p_answers=0.8), {}, 1, "{}", "{}"
        )
        picked = proposals.select([
            (span(0, 2), judged(True, 80)), (span(10, 12), not_an_answer), (span(20, 22), answers_but_leans_back),
        ], 5)
        self.assertEqual([s.ranges for s, _ in picked], [[(0, 2)], [(20, 22)]])

    def test_when_nothing_passes_the_best_are_still_returned(self):
        # 🔴 answer._pick 과 같은 규칙 — 사람이 읽고 판단할 수 있다.
        picked = proposals.select([(span(0, 2), judged(False, 40)), (span(5, 7), judged(False, 30))], 5)
        self.assertEqual(len(picked), 2)


class CombosTest(unittest.TestCase):
    def test_pairs_come_out_in_time_order_within_budget(self):
        later, earlier = span(10, 11), span(0, 1)
        combos = proposals.combos_of([later, earlier], [], 40.0)
        self.assertEqual(combos[0].ranges, [(0, 1), (10, 11)])

    def test_pairs_over_budget_or_overlapping_are_not_made(self):
        self.assertEqual(proposals.combos_of([span(0, 5), span(10, 15)], [], 40.0), [])  # 30 + 30초
        self.assertEqual(proposals.combos_of([span(0, 2), span(2, 3)], [], 40.0), [])    # 2번 줄이 겹친다


class JevEngineTest(AnswerTestCase):
    """answer.run 이 jev 엔진으로 끝까지 가는가 — 기록, 후보 수, 실패 처리."""

    def setUp(self) -> None:
        super().setUp()
        self.cfg = dataclasses.replace(self.cfg, answer_engine="jev", answer_candidates=5)
        self.befores: list[str | None] = []

    def locate(self, exists: float = 0.9):
        """구간 0 의 2번 줄을 답의 핵심으로 짚는 위치 찾기 대역."""

        def fake(cfg, state, questions, timeout=None):
            answers = {"exists": {"type": "noul", "noul": exists}}
            for name, q in questions.items():
                if name.startswith("has_"):
                    answers[name] = {"type": "noul", "noul": 0.8 if name == "has_0" else 0.2}
                elif name.startswith("where_"):
                    keys = list(q["criteria"])
                    probabilities = {k: (0.9 if k == "L002" else 0.1 / max(1, len(keys) - 1)) for k in keys}
                    answers[name] = {"type": "choice", "choice": max(probabilities, key=probabilities.get),
                                     "confidence": 0.9, "probabilities": probabilities}
            checked = jev.validate(questions, answers)
            return checked, {"input_tokens": 300, "output_tokens": 20, "model": "jev-1.14.0"}, 4, json.dumps(answers)

        return mock.patch.object(jev, "system_one", side_effect=fake)

    def judge_by_text(self, fail_on: str | None = None):
        """2번 발화가 들어 있으면 합격. `fail_on` 이 들어 있으면 그 판정은 오류로 끝난다."""

        def fake(cfg, question, part_texts, standard=None, before=None):
            self.befores.append(before)
            text = " ".join(part_texts)
            if fail_on and fail_on in text:
                raise jev.JevError("일시 장애")
            return judged("2번째" in text, 50 + len(part_texts) * 10)

        return mock.patch.object(judge, "judge_jev", side_effect=fake)

    def run_jev(self, *patches) -> dict:
        for patch in (mock.patch.object(routing, "decide", return_value=routing.Decision(
                routing.RETRIEVAL, [0, 1], "대역")), *patches):
            patch.start()
            self.addCleanup(patch.stop)
        return answer.run(self.conn, self.cfg, self.cluster_id)

    def candidates(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("select * from run_candidates order by ordinal")]

    def test_it_goes_to_review_with_up_to_five_candidates(self):
        result = self.run_jev(self.locate(), self.judge_by_text())
        self.assertTrue(result["answerable"])
        self.assertEqual(self.status(), "REVIEW")
        self.assertTrue(1 <= len(self.candidates()) <= 5)
        self.assertEqual(sum(c["recommended"] for c in result["candidates"]), 1)

    def test_locate_and_every_judgement_are_recorded_as_jev(self):
        self.run_jev(self.locate(), self.judge_by_text())
        rows = [dict(r) for r in self.conn.execute("select stage, model, params from stage_calls order by id")]
        self.assertEqual(rows[0]["stage"], "locate")
        self.assertEqual(rows[0]["params"]["anchors"][0]["line"], 2)
        judges = [r for r in rows if r["stage"] == "judge"]
        self.assertTrue(judges)
        self.assertTrue(all(r["model"] == "jev-1.14.0" for r in judges))
        # 🔴 합격선을 다시 잡으려면 원래 확률이 있어야 한다.
        self.assertTrue(all("p_answers" in r["params"] for r in judges))

    def test_the_recommendation_contains_the_located_answer(self):
        result = self.run_jev(self.locate(), self.judge_by_text())
        pick = next(c for c in result["candidates"] if c["recommended"])
        row = self.conn.execute("select parts, standalone from run_candidates where id = %s", (pick["id"],)).fetchone()
        self.assertTrue(row["standalone"])
        self.assertIn("2번째", " ".join(p["text"] for p in row["parts"]))

    def test_no_two_candidates_mostly_overlap(self):
        self.run_jev(self.locate(), self.judge_by_text())
        sets = [
            {(p["chunk_id"], u) for p in c["parts"] for u in range(p["start_utterance_idx"], p["end_utterance_idx"] + 1)}
            for c in self.candidates()
        ]
        for i, a in enumerate(sets):
            for b in sets[i + 1:]:
                self.assertLess(len(a & b) / min(len(a), len(b)), proposals.OVERLAP)

    def test_the_judge_is_shown_what_came_right_before(self):
        # 🔴 앞 대사를 모르면 "무엇에 대한 숫자인지" 빠진 범위를 걸러낼 수 없다(judge.jev_state 실측).
        self.run_jev(self.locate(), self.judge_by_text())
        # 2번 줄 둘레의 범위는 0·1번 줄에서도 시작한다 — 1번에서 시작하면 앞 대사는 0번이다.
        self.assertIn("0번째 발화입니다", self.befores)

    def test_a_low_answer_probability_is_unanswerable_without_judging(self):
        with mock.patch.object(answer.retrieval, "suggest_elsewhere", return_value=None):
            result = self.run_jev(self.locate(exists=0.05), self.judge_by_text())
        self.assertFalse(result["answerable"])
        self.assertEqual(self.status(), "UNANSWERABLE")
        self.assertNotIn("judge", self.stages())

    def test_one_failed_judgement_does_not_sink_the_answer(self):
        result = self.run_jev(self.locate(), self.judge_by_text(fail_on="0번째"))
        self.assertTrue(result["answerable"])
        errors = self.conn.execute(
            "select count(*) as n from stage_calls where stage = 'judge' and error is not null"
        ).fetchone()["n"]
        self.assertGreater(errors, 0)

    def test_the_rank_route_still_goes_through_gemini(self):
        # 🔴 "핵심만 30초" 에는 답의 핵심 줄이 없다 — proposals 머리 주석.
        plan = {"answerable": True, "reason": "있다", "candidates": [SINGLE, COMBO, TIGHT]}
        with mock.patch.object(proposals, "propose", side_effect=AssertionError("jev 로 가면 안 된다")):
            result = self.run_answer(plan, [verdict(), verdict(), verdict()], route=routing.RANK)
        self.assertTrue(result["answerable"])


if __name__ == "__main__":
    unittest.main()
