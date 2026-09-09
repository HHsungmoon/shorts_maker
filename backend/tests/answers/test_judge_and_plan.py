"""LLM 출력 검증 — judge 판정과 후보 계획.

여기 있는 건 전부 **순수 함수**다. 모델 품질이 아니라 "모델이 이상한 걸 줬을 때 코드가 막는가"를
본다(§12: LLM 출력은 항상 검증한다).
"""

import json
import unittest

from shorts_maker.answers import judge, routing
from shorts_maker.pipeline import cutting, ranking, subtitles


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
    """경로 판정 + 구간 선택. 한 번의 LLM 호출이 둘 다 한다(routing 모듈 머리 주석).

    🔴 여기서 지키는 것은 **`selected` 의 세 가지 뜻이 섞이지 않는 것**이다:
      - 번호 목록: 그 구간들의 대사를 다음 단계에 넘긴다
      - `[]`: 모델이 "이 영상엔 없다" 고 답했다 → 답변 불가
      - `None`: 판정을 못 읽었다 → 임베딩 top-k 폴백
    셋을 합치면 파싱 실패가 잘못된 시청자 안내로 바뀐다.
    """

    ALLOWED = {0, 1, 2, 7, 22}

    def parse(self, raw: str):
        return routing.parse(raw, self.ALLOWED)

    def test_a_valid_route_parses(self):
        self.assertEqual(self.parse('{"route": "retrieval", "segments": [], "reason": "r"}').route,
                         routing.RETRIEVAL)

    def test_an_unknown_route_is_an_error(self):
        for raw in ('{"route": "search", "segments": [], "reason": "r"}', '{"reason": "r"}', "nope"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    self.parse(raw)

    def test_the_chosen_segments_come_back_in_the_order_given(self):
        # 관련 높은 순으로 온다고 보고 앞에서 자른다 — 시간 순 정렬은 호출부의 일이다.
        decision = self.parse('{"route": "retrieval", "segments": [22, 7, 1], "reason": "r"}')
        self.assertEqual(decision.selected, [22, 7, 1])

    def test_a_number_outside_the_listing_is_dropped(self):
        # 🔴 화이트리스트. 없는 번호로 대사를 찾으면 다음 단계가 엉뚱한 구간을 자른다.
        self.assertEqual(self.parse('{"route": "retrieval", "segments": [7, 999], "reason": "r"}').selected, [7])

    def test_all_numbers_outside_the_listing_is_an_error_not_an_empty_answer(self):
        """🔴 빈 배열로 두면 "이 영상엔 없다" 로 읽힌다 — 모델이 엉뚱한 번호를 댄 것이
        시청자에게 잘못된 안내로 바뀐다. 파싱 실패로 올려 폴백을 타게 한다."""
        with self.assertRaises(ValueError):
            self.parse('{"route": "retrieval", "segments": [998, 999], "reason": "r"}')

    def test_an_honest_empty_answer_survives(self):
        # 모델이 처음부터 빈 배열을 준 것은 판정이다 — 버리지 않는다.
        self.assertEqual(self.parse('{"route": "retrieval", "segments": [], "reason": "없다"}').selected, [])

    def test_duplicates_collapse(self):
        self.assertEqual(self.parse('{"route": "retrieval", "segments": [7, 7, 1], "reason": "r"}').selected, [7, 1])

    def test_too_many_segments_are_capped(self):
        # 상한이 있는 이유는 비용이 아니라 다음 단계의 정밀도다(MAX_SEGMENTS 주석).
        allowed = set(range(20))
        decision = routing.parse(
            '{"route": "retrieval", "segments": %s, "reason": "r"}' % list(range(20)), allowed
        )
        self.assertEqual(len(decision.selected), routing.MAX_SEGMENTS)

    def test_the_listing_shows_every_segment_with_its_number(self):
        segments = [{"idx": 0, "description": "인사"}, {"idx": 7, "description": "마이크로서비스"}]
        prompt = routing.build_prompt("기술 스택은?", segments)
        self.assertIn("[0] 인사", prompt)
        self.assertIn("[7] 마이크로서비스", prompt)
        self.assertIn("기술 스택은?", prompt)

    def test_a_prompt_without_segments_is_refused(self):
        # 구간이 없으면 고를 것이 없다. 호출부가 먼저 걸러야 한다.
        with self.assertRaises(ValueError):
            routing.build_prompt("질문", [])

    def test_the_rule_of_thumb_separates_the_obvious_cases(self):
        # LLM 과 나란히 기록해 "규칙만으로 충분했나" 를 나중에 데이터로 답한다.
        for text in ("연봉은 얼마인가요?", "면접 절차가 어떻게 되나요", "재택근무 되나요"):
            self.assertEqual(routing.rule_of_thumb(text), routing.RETRIEVAL, text)
        for text in ("핵심만 30초로", "재밌는 부분"):
            self.assertEqual(routing.rule_of_thumb(text), routing.RANK, text)


class LeadInTest(unittest.TestCase):
    """🔴 앞을 가리키며 시작하는 컷의 시작점을 당긴다 (cutting.lead_in).

    실측 근거(2026-09-09, "개발 직군의 주요 기술 스택"): 후보 **셋이 전부** 자립성 판정에서
    떨어졌고 이유가 같았다 — "저희는 **이런 서비스들**을 …", "**이렇게** 수많은 AI 프로젝트들이 …".
    검색도 rank 도 맞는 대사를 골랐는데 시작점이 한 발화 늦었다. 프롬프트에는 이미
    "지시대명사로 시작하지 않는다" 가 있었고 지켜지지 않았다 — 그래서 코드가 고친다.
    """

    def lines(self, *texts: str, seconds: float = 5.0, segment_id: int = 1) -> list[dict]:
        return [
            {
                "line": n, "segment_id": segment_id, "segment_idx": 0, "description": "",
                "chunk_id": 1, "utterance_idx": n,
                "start_sec": n * seconds, "end_sec": (n + 1) * seconds, "text": text,
            }
            for n, text in enumerate(texts)
        ]

    def pull(self, lines: list[dict], start: int, end: int, budget: float = 30.0):
        return cutting.lead_in([ranking.Part(start, end)], lines, budget)[0]

    def test_a_clip_starting_with_a_backward_reference_reaches_back(self):
        lines = self.lines(
            "저희 서비스는 차량 제어부터 개인화까지 여러 가지가 있습니다",
            "저희는 이런 서비스들을 개발하기 위해 마이크로서비스 아키텍처를 씁니다",
        )
        self.assertEqual(self.pull(lines, 1, 1).start_line, 0)

    def test_a_self_contained_opening_is_left_alone(self):
        # 🔴 필요 없을 때 당기면 예산만 먹고 답이 짧아진다.
        lines = self.lines(
            "앞 문장입니다",
            "쏘카는 마이크로서비스 아키텍처를 씁니다",
        )
        self.assertEqual(self.pull(lines, 1, 1).start_line, 1)

    def test_a_demonstrative_deep_in_the_sentence_does_not_trigger(self):
        """클립 한복판의 지시어는 정상이다 — 선행사가 클립 안에 있다."""
        lines = self.lines(
            "앞 문장입니다",
            "쏘카는 마이크로서비스 아키텍처를 쓰고 있는데 이런 구조가 개발 속도를 높입니다",
        )
        self.assertEqual(self.pull(lines, 1, 1).start_line, 1)

    def test_it_stops_at_the_segment_boundary(self):
        # 🔴 조각은 한 구간 안에 있어야 한다(parse_answer 불변식). 넘으면 뒤에서 조용히 어긋난다.
        lines = self.lines("다른 구간의 마지막 말") + [
            {
                "line": 1, "segment_id": 2, "segment_idx": 1, "description": "",
                "chunk_id": 1, "utterance_idx": 1, "start_sec": 5.0, "end_sec": 10.0,
                "text": "이런 서비스들을 만듭니다",
            }
        ]
        self.assertEqual(self.pull(lines, 1, 1).start_line, 1)

    def test_it_never_eats_the_budget(self):
        """🔴 맥락 때문에 답을 잘라내지 않는다.

        앞머리가 붙고 답이 반이 된 클립보다, 시작이 조금 어색해도 답이 온전한 편이 낫다.
        남은 어색함은 판정이 잡고 크리에이터가 화면에서 읽는다.
        """
        lines = self.lines("앞 발화", "이런 것들을 만듭니다", seconds=20.0)
        self.assertEqual(self.pull(lines, 1, 1, budget=30.0).start_line, 1)

    def test_it_gives_up_rather_than_landing_in_mid_sentence(self):
        """🔴 좋은 자리를 못 찾으면 그냥 둔다.

        실측에서 지시어만 피하다 "해나가고 있고" 에서 시작한 적이 있다 — 앞을 가리키지는 않지만
        문장 한복판이라 더 나빴다. 어중간하게 당기느니 원래대로 두고 판정에 맡긴다.
        """
        lines = self.lines("그래서 하나", "그리고 둘", "그런데 셋", "이렇게 만듭니다", seconds=2.0)
        self.assertEqual(self.pull(lines, 3, 3).start_line, 3)

    def test_it_only_reaches_back_so_far(self):
        # 좋은 자리가 상한 밖이면 포기한다 — 그 위로는 30초 안에 답이 들어갈 자리가 없다.
        lines = self.lines(
            "깨끗한 시작입니다", "그래서 하나", "그리고 둘", "그런데 셋", "이렇게 넷", seconds=2.0,
        )
        self.assertEqual(self.pull(lines, 4, 4).start_line, 4)

    def test_it_lands_where_the_previous_utterance_finished_a_sentence(self):
        # 앞 발화가 문장을 끝냈으면 거기서부터가 새 문장이다.
        lines = self.lines(
            "쏘카는 여러 서비스를 운영하고 있습니다",
            "차량 제어와 안전을 다룹니다",
            "이런 서비스들을 만들기 위해",
            seconds=3.0,
        )
        self.assertEqual(self.pull(lines, 2, 2).start_line, 1)

    def test_the_second_part_of_a_combo_is_fixed_too(self):
        """🔴 조합은 조각 사이에 점프가 있어서 **두 번째 조각도 그 자리에서 새로 시작**한다.

        실측의 combo 가 정확히 그랬다 — 두 조각 다 지시어로 시작했다.
        """
        lines = self.lines(
            "첫 조각 앞", "첫 조각 본문입니다", "둘째 조각 앞 맥락", "이렇게 만들고 있습니다",
            seconds=3.0,
        )
        parts = cutting.lead_in([ranking.Part(1, 1), ranking.Part(3, 3)], lines, 30.0)
        self.assertEqual([(p.start_line, p.end_line) for p in parts], [(1, 1), (2, 3)])

    def test_it_never_overlaps_the_part_before_it(self):
        # 겹치면 같은 대사가 두 번 나오고 자막 타임라인이 어긋난다.
        lines = self.lines("앞", "첫 조각", "이런 것들을 만듭니다", seconds=3.0)
        parts = cutting.lead_in([ranking.Part(0, 1), ranking.Part(2, 2)], lines, 30.0)
        self.assertEqual([(p.start_line, p.end_line) for p in parts], [(0, 1), (2, 2)])

    def test_the_markers_cover_what_the_measurement_found(self):
        # 실측에서 실제로 걸린 두 문장. 목록을 줄일 때 이 둘이 빠지면 안 된다.
        self.assertTrue(cutting.needs_lead_in("저희는 이런 서비스들을 1년으로 개발하기 위해서"))
        self.assertTrue(cutting.needs_lead_in("이렇게 수많은 AI 프로젝트들이 원활하게"))


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
