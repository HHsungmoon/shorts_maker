"""[3] 주제 분할의 프롬프트 조립과 응답 검증.

Gemini 없이 도는 순수 함수만 다룬다. 여기서 지키는 건 §12 의 "LLM 출력은 항상 검증한다"
— 특히 **발화가 조용히 사라지지 않는 것**이다. 빈틈 하나를 놓치면 그 구간은 rank 후보에서
빠지는데, 결과물만 봐서는 원래 없던 건지 잃어버린 건지 알 수 없다.
"""

import json
import unittest

from shorts_maker.pipeline import segmentation as seg


def payload(*triples):
    return json.dumps(
        {"segments": [{"start_idx": s, "end_idx": e, "summary": t} for s, e, t in triples]}
    )


class BuildPromptTest(unittest.TestCase):
    UTTERANCES = [
        {"idx": 0, "text": "연민은 수치심을 모른다"},
        {"idx": 1, "text": "칸트는 다르게 본다"},
        {"idx": 2, "text": "도덕법칙으로 받아들일 만한 것"},
    ]

    def test_includes_every_utterance(self):
        prompt = seg.build_prompt(self.UTTERANCES, context=None)
        for u in self.UTTERANCES:
            self.assertIn(f"[{u['idx']}] {u['text']}", prompt)

    def test_includes_context_when_given(self):
        prompt = seg.build_prompt(self.UTTERANCES, context="  백승영 교수의 니체 강연  ")
        self.assertIn("백승영 교수의 니체 강연", prompt)

    def test_omits_context_block_when_blank(self):
        for blank in (None, "", "   "):
            with self.subTest(blank=repr(blank)):
                # §6-3 과 같은 이유 — 빈 지시문을 남기면 모델이 그걸 해석한다.
                self.assertNotIn("강연 개요:", seg.build_prompt(self.UTTERANCES, blank))

    def test_states_the_index_range(self):
        prompt = seg.build_prompt(self.UTTERANCES, None)
        self.assertIn("0번에서 시작", prompt)
        self.assertIn("2번에서 끝", prompt)

    def test_forbids_evaluation(self):
        # 🔴 §3: 이 단계가 평가를 하면 Segment 가 중립 자산이 아니게 되고, 기준만 바꿔
        # 다시 돌리는 §6-2 가 깨진다. 프롬프트가 그걸 금지하고 있어야 한다.
        prompt = seg.build_prompt(self.UTTERANCES, None)
        self.assertIn("평가", prompt)
        self.assertIn("내용만 적는다", prompt)

    def test_rejects_empty_utterances(self):
        with self.assertRaises(seg.SegmentationError):
            seg.build_prompt([], None)


class ParseResponseTest(unittest.TestCase):
    def test_accepts_contiguous_full_coverage(self):
        specs = seg.parse_response(payload((0, 3, "동정 비판"), (4, 9, "칸트의 선의지")), max_idx=9)
        self.assertEqual([(s.start_idx, s.end_idx) for s in specs], [(0, 3), (4, 9)])
        self.assertEqual(specs[1].summary, "칸트의 선의지")

    def test_sorts_out_of_order_segments(self):
        specs = seg.parse_response(payload((4, 9, "뒤"), (0, 3, "앞")), max_idx=9)
        self.assertEqual([s.summary for s in specs], ["앞", "뒤"])

    def test_rejects_gap(self):
        # 5번 발화가 어디에도 없다 — 조용히 잃는 대신 실패해야 한다.
        with self.assertRaises(seg.SegmentationError) as ctx:
            seg.parse_response(payload((0, 4, "a"), (6, 9, "b")), max_idx=9)
        self.assertIn("이어지지 않는다", str(ctx.exception))

    def test_rejects_overlap(self):
        with self.assertRaises(seg.SegmentationError):
            seg.parse_response(payload((0, 5, "a"), (4, 9, "b")), max_idx=9)

    def test_rejects_not_starting_at_zero(self):
        with self.assertRaises(seg.SegmentationError) as ctx:
            seg.parse_response(payload((1, 9, "a")), max_idx=9)
        self.assertIn("0번 발화", str(ctx.exception))

    def test_rejects_not_ending_at_max(self):
        with self.assertRaises(seg.SegmentationError) as ctx:
            seg.parse_response(payload((0, 7, "a")), max_idx=9)
        self.assertIn("마지막 발화", str(ctx.exception))

    def test_rejects_index_beyond_range(self):
        # 존재하지 않는 발화를 지목하는 경우 — §12 의 화이트리스트 검증.
        with self.assertRaises(seg.SegmentationError) as ctx:
            seg.parse_response(payload((0, 42, "a")), max_idx=9)
        self.assertIn("범위를 벗어났다", str(ctx.exception))

    def test_rejects_reversed_range(self):
        with self.assertRaises(seg.SegmentationError):
            seg.parse_response(payload((5, 2, "a")), max_idx=9)

    def test_rejects_blank_summary(self):
        with self.assertRaises(seg.SegmentationError):
            seg.parse_response(payload((0, 9, "   ")), max_idx=9)

    def test_rejects_empty_list(self):
        with self.assertRaises(seg.SegmentationError):
            seg.parse_response(json.dumps({"segments": []}), max_idx=9)

    def test_rejects_broken_json(self):
        with self.assertRaises(seg.SegmentationError):
            seg.parse_response("{not json", max_idx=9)

    def test_rejects_missing_field(self):
        with self.assertRaises(seg.SegmentationError):
            seg.parse_response(json.dumps({"segments": [{"start_idx": 0, "summary": "a"}]}), max_idx=9)

    def test_accepts_single_segment_covering_everything(self):
        specs = seg.parse_response(payload((0, 9, "전체가 한 주제")), max_idx=9)
        self.assertEqual(len(specs), 1)


if __name__ == "__main__":
    unittest.main()
