"""LLM 출력 검증 — judge 판정과 후보 계획.

여기 있는 건 전부 **순수 함수**다. 모델 품질이 아니라 "모델이 이상한 걸 줬을 때 코드가 막는가"를
본다(§12: LLM 출력은 항상 검증한다).
"""

import json
import unittest

from shorts_maker.answers import judge, routing
from shorts_maker.pipeline import ranking, subtitles


def line(number: int, segment_id: int, utterance_idx: int, start: float, end: float) -> dict:
    return {
        "line": number, "segment_id": segment_id, "segment_idx": segment_id, "description": "설명",
        "chunk_id": 1, "utterance_idx": utterance_idx,
        "start_sec": start, "end_sec": end, "text": f"{number}번 발화",
    }


# 구간 1 은 0~4번, 구간 2 는 5~9번.
LINES = [line(n, 1 if n < 5 else 2, n, n * 5.0, n * 5.0 + 5.0) for n in range(10)]


def plan(candidates: list, answerable: bool = True) -> str:
    return json.dumps({"answerable": answerable, "reason": "이유", "candidates": candidates})


def candidate(parts: list, label: str = "single") -> dict:
    return {"label": label, "reason": "r",
            "parts": [{"start_line": a, "end_line": b} for a, b in parts]}


class PlanValidationTest(unittest.TestCase):
    def test_a_valid_plan_survives(self):
        got = ranking.parse_answer(plan([candidate([(0, 2)]), candidate([(0, 1), (6, 8)], "combo")]), LINES)
        self.assertTrue(got.answerable)
        self.assertEqual(len(got.candidates), 2)
        self.assertEqual(len(got.candidates[1].parts), 2)

    def test_unanswerable_needs_no_candidates(self):
        got = ranking.parse_answer(plan([], answerable=False), LINES)
        self.assertFalse(got.answerable)
        self.assertEqual(got.reason, "이유")

    def test_a_line_that_does_not_exist_is_refused(self):
        # 🔴 없는 번호를 그대로 쓰면 초를 되찾을 때 KeyError 로 죽거나, 더 나쁘게는 엉뚱한 곳이 잘린다.
        with self.assertRaises(ranking.RankingError):
            ranking.parse_answer(plan([candidate([(0, 99)])]), LINES)

    def test_a_reversed_range_is_refused(self):
        with self.assertRaises(ranking.RankingError):
            ranking.parse_answer(plan([candidate([(5, 2)])]), LINES)

    def test_a_part_spanning_two_segments_is_refused(self):
        # 🔴 한 조각은 시간상 연속이어야 한다. 구간을 넘나들면 조각 **안에서** 점프가 생기고,
        # 그 점프는 브릿지 카드 없이 붙어 시청자에게는 말이 튀는 것으로만 보인다.
        with self.assertRaises(ranking.RankingError):
            ranking.parse_answer(plan([candidate([(3, 6)])]), LINES)

    def test_overlapping_parts_are_refused(self):
        with self.assertRaises(ranking.RankingError):
            ranking.parse_answer(plan([candidate([(0, 3), (2, 4)], "combo")]), LINES)

    def test_parts_are_sorted_into_time_order(self):
        got = ranking.parse_answer(plan([candidate([(6, 8), (0, 1)], "combo")]), LINES)
        self.assertEqual([p.start_line for p in got.candidates[0].parts], [0, 6])

    def test_too_many_parts_are_refused(self):
        with self.assertRaises(ranking.RankingError):
            ranking.parse_answer(plan([candidate([(0, 0), (2, 2), (4, 4), (6, 6)], "combo")]), LINES)

    def test_answerable_without_candidates_is_an_error(self):
        with self.assertRaises(ranking.RankingError):
            ranking.parse_answer(plan([]), LINES)

    def test_broken_json_is_an_error(self):
        with self.assertRaises(ranking.RankingError):
            ranking.parse_answer("not json", LINES)


class AnswerPromptTest(unittest.TestCase):
    def test_the_prompt_carries_the_budget_and_the_question(self):
        prompt = ranking.build_answer_prompt("연봉은?", LINES, 30.0, None)
        self.assertIn("연봉은?", prompt)
        self.assertIn("30초", prompt)

    def test_segment_headers_separate_the_lines(self):
        # 구간 머리글이 없으면 모델이 "같은 구간 안" 이라는 규칙을 지킬 근거가 없다.
        prompt = ranking.build_answer_prompt("질문", LINES, 30.0, None)
        self.assertEqual(prompt.count("[구간 "), 2)

    def test_an_empty_candidate_set_is_refused(self):
        with self.assertRaises(ranking.RankingError):
            ranking.build_answer_prompt("질문", [], 30.0, None)


class JudgeParsingTest(unittest.TestCase):
    def test_a_verdict_passes_only_when_both_gates_are_true(self):
        self.assertTrue(judge.Verdict(True, True, 10, "r").passed)
        for standalone, answers in ((True, False), (False, True), (False, False)):
            with self.subTest(standalone=standalone, answers=answers):
                self.assertFalse(judge.Verdict(standalone, answers, 99, "r").passed)

    def test_scores_outside_the_range_are_clamped_not_rejected(self):
        # 판정 내용은 쓸 만한데 점수만 튀는 경우가 있다 — 판정 전체를 버리기엔 아깝다.
        self.assertEqual(judge.parse('{"standalone": true, "answers": true, "score": 250, "reason": "r"}').score, 100)
        self.assertEqual(judge.parse('{"standalone": true, "answers": true, "score": -5, "reason": "r"}').score, 0)

    def test_a_missing_field_is_an_error(self):
        with self.assertRaises(judge.JudgeError):
            judge.parse('{"standalone": true, "answers": true}')

    def test_broken_json_is_an_error(self):
        with self.assertRaises(judge.JudgeError):
            judge.parse("nope")

    def test_a_combo_prompt_tells_the_judge_about_the_jump(self):
        # 🔴 모르면 "말이 갑자기 바뀐다" 를 자립성 실패로 읽고 조합을 전부 떨어뜨린다.
        one = judge.build_prompt("질문", ["하나"])
        many = judge.build_prompt("질문", ["하나", "둘"])
        self.assertNotIn("이어집니다", one)
        self.assertIn("이어집니다", many)
        self.assertIn("1번째 조각", many)

    def test_an_empty_clip_cannot_be_judged(self):
        with self.assertRaises(judge.JudgeError):
            judge.build_prompt("질문", [])


class RoutingTest(unittest.TestCase):
    def test_a_valid_route_parses(self):
        self.assertEqual(routing.parse('{"route": "retrieval", "reason": "r"}')[0], routing.RETRIEVAL)

    def test_an_unknown_route_is_an_error(self):
        for raw in ('{"route": "search", "reason": "r"}', '{"reason": "r"}', "nope"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    routing.parse(raw)

    def test_the_rule_of_thumb_separates_the_obvious_cases(self):
        # LLM 과 나란히 기록해 "규칙만으로 충분했나" 를 나중에 데이터로 답한다.
        for text in ("연봉은 얼마인가요?", "면접 절차가 어떻게 되나요", "재택근무 되나요"):
            self.assertEqual(routing.rule_of_thumb(text), routing.RETRIEVAL, text)
        for text in ("핵심만 30초로", "재밌는 부분"):
            self.assertEqual(routing.rule_of_thumb(text), routing.RANK, text)


class PartSubtitleTest(unittest.TestCase):
    """🔴 조각을 한 번에 이어붙여 렌더하므로 자막 시각도 이어붙인 타임라인 기준이어야 한다.
    조각별 상대 초를 그대로 쓰면 두 번째 조각부터 전부 어긋난다."""

    def utterance(self, start: float, end: float, text: str) -> dict:
        return {"idx": 0, "start_sec": start, "end_sec": end, "text": text, "words": None}

    def test_the_second_part_is_shifted_by_the_first_and_the_bridge(self):
        cues = subtitles.build_part_cues(
            [[self.utterance(100, 106, "첫")], [self.utterance(300, 307, "둘")]],
            [(100, 106), (300, 307)], bridge_sec=0.4,
        )
        self.assertEqual(cues[0].start, 0.0)
        self.assertEqual(cues[-1].start, 6.4)   # 6초 + 브릿지 0.4초

    def test_a_bridge_card_is_inserted_between_parts_only(self):
        cues = subtitles.build_part_cues(
            [[self.utterance(0, 5, "첫")], [self.utterance(60, 65, "둘")], [self.utterance(120, 125, "셋")]],
            [(0, 5), (60, 65), (120, 125)],
        )
        bridges = [c for c in cues if "이어집니다" in c.text]
        self.assertEqual(len(bridges), 2)

    def test_the_bridge_names_the_position_in_the_original(self):
        # 분만 쓰면 같은 분 안의 점프가 "0분에서" 가 되어 이상하다.
        cues = subtitles.build_part_cues(
            [[self.utterance(0, 5, "첫")], [self.utterance(752, 755, "둘")]], [(0, 5), (752, 755)]
        )
        self.assertIn("12:32", next(c.text for c in cues if "이어집니다" in c.text))

    def test_a_single_part_gets_no_bridge(self):
        cues = subtitles.build_part_cues([[self.utterance(0, 5, "첫")]], [(0, 5)])
        self.assertEqual([c.text for c in cues], ["첫"])

    def test_mismatched_counts_are_refused(self):
        with self.assertRaises(ValueError):
            subtitles.build_part_cues([[self.utterance(0, 5, "첫")]], [(0, 5), (10, 15)])


if __name__ == "__main__":
    unittest.main()
