"""자막 큐 생성과 ASS 출력 검증 (문서 §9-9).

여기서 지키는 건 두 가지다 — **시각이 클립 상대 초인가**(절대 초를 쓰면 자막이 통째로
어긋난다), 그리고 **ASS 특수문자가 제대로 처리되는가**(잘못하면 텍스트가 화면에서 사라지거나
제어문자가 글자로 찍힌다). 둘 다 결과물을 눈으로 볼 때까지 모른다.
"""

import json
import unittest

from shorts_maker.pipeline import subtitles as sub


def utterance(idx, start, end, text, words=None):
    return {
        "idx": idx,
        "start_sec": start,
        "end_sec": end,
        "text": text,
        "words": json.dumps(words, ensure_ascii=False) if words else None,
    }


def word(start, end, text):
    return {"start": start, "end": end, "text": text}


class BuildCuesTest(unittest.TestCase):
    def test_times_are_relative_to_clip_start(self):
        cues = sub.build_cues(
            [utterance(0, 1200.0, 1203.0, "연민은 수치심을 모른다")], clip_start=1200.0, clip_end=1260.0
        )
        self.assertEqual(cues[0].start, 0.0)
        self.assertEqual(cues[0].end, 3.0)

    def test_skips_utterances_outside_the_clip(self):
        cues = sub.build_cues(
            [
                utterance(0, 1100.0, 1105.0, "앞"),
                utterance(1, 1210.0, 1213.0, "안"),
                utterance(2, 1300.0, 1305.0, "뒤"),
            ],
            clip_start=1200.0,
            clip_end=1260.0,
        )
        self.assertEqual([c.text for c in cues], ["안"])

    def test_splits_long_utterance_by_words(self):
        # 발화 하나가 11초짜리면 자막 한 장으로는 못 쓴다 — word 타임스탬프로 쪼갠다.
        words = [word(1200.0 + i * 0.5, 1200.5 + i * 0.5, f"단어{i} ") for i in range(24)]
        cues = sub.build_cues(
            [utterance(0, 1200.0, 1212.0, "".join(w["text"] for w in words), words)],
            clip_start=1200.0,
            clip_end=1260.0,
        )
        self.assertGreater(len(cues), 1)
        for cue in cues:
            self.assertLessEqual(cue.end - cue.start, sub.MAX_SEC + 0.01)

    def test_falls_back_to_whole_utterance_without_words(self):
        cues = sub.build_cues(
            [utterance(0, 1200.0, 1202.0, "단어 정보 없음")], clip_start=1200.0, clip_end=1260.0
        )
        self.assertEqual([c.text for c in cues], ["단어 정보 없음"])

    def test_clamps_to_clip_bounds(self):
        cues = sub.build_cues(
            [utterance(0, 1198.0, 1205.0, "걸친 발화")], clip_start=1200.0, clip_end=1203.0
        )
        self.assertGreaterEqual(cues[0].start, 0.0)
        self.assertLessEqual(cues[0].end, 3.0)

    def test_extends_very_short_cue_to_readable_length(self):
        cues = sub.build_cues(
            [utterance(0, 1200.0, 1200.2, "예")], clip_start=1200.0, clip_end=1260.0
        )
        self.assertGreaterEqual(cues[0].end - cues[0].start, sub.MIN_SEC)


class WrapTest(unittest.TestCase):
    def test_keeps_short_text_on_one_line(self):
        self.assertEqual(sub.wrap("짧은 문장"), "짧은 문장")

    def test_breaks_at_a_space_near_the_middle(self):
        wrapped = sub.wrap("자존심이 상한 사람은 어떻게 대응할 거냐면")
        self.assertIn("\\N", wrapped)
        self.assertNotIn(" \\N", wrapped)


class AssOutputTest(unittest.TestCase):
    def test_line_break_marker_is_not_double_escaped(self):
        # 🔴 escape 를 wrap 뒤에 부르면 `\\N` 이 되어 화면에 글자로 찍힌다. 실제로 한 번 겪었다.
        body = sub.to_ass([sub.Cue(0.0, 3.0, "자존심이 상한 사람은 어떻게 대응할 거냐면")])
        dialogue = [l for l in body.splitlines() if l.startswith("Dialogue")][0]
        self.assertIn("\\N", dialogue)
        self.assertNotIn("\\\\N", dialogue)

    def test_escapes_brace_that_would_swallow_the_rest(self):
        # ASS 에서 `{` 는 오버라이드 블록의 시작이라 그대로 두면 뒤 텍스트가 사라진다.
        body = sub.to_ass([sub.Cue(0.0, 2.0, "수식 {a}")])
        self.assertIn("\\{a\\}", body)

    def test_formats_timestamps_as_ass_expects(self):
        self.assertEqual(sub.timestamp(0.0), "0:00:00.00")
        self.assertEqual(sub.timestamp(65.25), "0:01:05.25")
        self.assertEqual(sub.timestamp(3725.5), "1:02:05.50")

    def test_places_subtitles_below_the_video_band(self):
        # 영상은 656~1264 를 차지한다. 마진이 그보다 위면 화면을 가린다.
        body = sub.to_ass([sub.Cue(0.0, 1.0, "a")])
        style = [l for l in body.splitlines() if l.startswith("Style:")][0]
        self.assertGreater(int(style.rsplit(",", 2)[-2]), 1264)

    def test_emits_one_dialogue_per_cue(self):
        body = sub.to_ass([sub.Cue(0.0, 1.0, "하나"), sub.Cue(1.0, 2.0, "둘")])
        self.assertEqual(len([l for l in body.splitlines() if l.startswith("Dialogue")]), 2)


class EditTest(unittest.TestCase):
    """사람이 고친 문구(tease §5-10 "자막 고치기") — 원본 시각과 원래 문구가 열쇠, 시간은 그대로."""

    def cues(self, clip_start=1200.0):
        return sub.build_cues(
            [utterance(0, 1201.0, 1203.0, "수능체제 기준으로"), utterance(1, 1204.0, 1206.0, "다음 말")],
            clip_start=clip_start, clip_end=1260.0,
        )

    def test_a_cue_remembers_where_its_first_word_was_in_the_source(self):
        # 구간 처음을 1202 로 다듬어 첫 줄이 잘려도 열쇠는 그대로 원본 시각이다(시작은 0 으로 잘린다).
        self.assertEqual([1201.0, 1204.0], [c.at for c in self.cues()])
        trimmed = self.cues(clip_start=1202.0)
        self.assertEqual((0.0, 1201.0), (trimmed[0].start, trimmed[0].at))

    def test_the_text_changes_and_the_time_does_not(self):
        out = sub.apply_edits(self.cues(), [{"at": 1201.0, "was": "수능체제 기준으로", "text": "수능최저 기준으로"}])
        self.assertEqual(["수능최저 기준으로", "다음 말"], [c.text for c in out])
        self.assertEqual((1.0, 3.0), (out[0].start, out[0].end))

    def test_an_emptied_line_is_dropped(self):
        out = sub.apply_edits(self.cues(), [{"at": 1204.0, "was": "다음 말", "text": ""}])
        self.assertEqual(["수능체제 기준으로"], [c.text for c in out])

    def test_an_edit_for_a_sentence_that_changed_is_not_used(self):
        # 대사를 갈아 끼워 같은 시각에 다른 문장이 왔다 — 시각만 보고 붙이면 엉뚱한 줄이 바뀐다.
        out = sub.apply_edits(self.cues(), [{"at": 1201.0, "was": "수능 체제 기준으로", "text": "엉뚱한 문구"}])
        self.assertEqual(["수능체제 기준으로", "다음 말"], [c.text for c in out])

    def test_the_editor_timeline_has_no_bridges(self):
        # 편집 화면의 재생 막대는 조각을 그냥 잇는다 — 브릿지 줄도 브릿지만큼 미는 것도 없다.
        parts = [[utterance(0, 0.0, 5.0, "첫")], [utterance(1, 600.0, 605.0, "둘")]]
        cues = sub.build_part_cues(parts, [(0.0, 10.0), (600.0, 610.0)], bridges=False)
        self.assertEqual([("첫", 0.0), ("둘", 10.0)], [(c.text, c.start) for c in cues])
        self.assertEqual([0.0, 600.0], [c.at for c in cues])


if __name__ == "__main__":
    unittest.main()
