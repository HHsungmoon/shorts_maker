"""AI 초점(pipeline/focus.py) — 장면마다 사람 상자 → 창. tease §5-10 "포커스 규칙".

모델 응답은 클립 4(잡소리 ep.48, 4:18–4:48)에 gemini-3.6-flash 를 저해상도로 실제로 부른 값이다(2026-11-07, 장면마다
프레임 세 장). 기대값은 사람이 같은 장면을 직접 맞춘 상자다:

    장면 5(3명)   x 0.157 · w 0.66 · y 0.07
    장면 4(2명)   x 0.45  · w 0.54 · y 0.01   오른쪽 사람이 말한다
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shorts_maker.adapters import ffmpeg, gemini
from shorts_maker.pipeline import focus

from ..support import DbTestCase, make_config

W, H = 1920, 1080
SLOT = 854

_WIDE = [
    {"box_2d": [216, 185, 923, 451], "head_2d": [216, 204, 340, 285]},
    {"box_2d": [209, 345, 755, 513], "head_2d": [209, 379, 332, 439]},
    {"box_2d": [211, 584, 946, 780], "head_2d": [211, 668, 371, 764]},
]
_CLOSE = [{"box_2d": [53, 362, 1000, 804], "head_2d": [52, 483, 501, 680]}]
_TWO = [
    {"box_2d": [119, 90, 999, 429], "head_2d": [118, 163, 426, 313]},
    {"box_2d": [84, 610, 997, 903], "head_2d": [83, 679, 496, 814]},
]
CLIP4 = {
    "scenes": [
        {"scene": 1, "people": _WIDE, "speaker": 1},
        {"scene": 2, "people": _CLOSE, "speaker": 0},
        {"scene": 3, "people": _WIDE, "speaker": 1},
        {"scene": 4, "people": _TWO, "speaker": 1},
        {"scene": 5, "people": _WIDE, "speaker": 1},
        {"scene": 6, "people": _CLOSE, "speaker": 0},
    ]
}


def height(box: dict) -> float:
    return box["w"] * W * SLOT / 1080 / H


class ParseTest(unittest.TestCase):
    def test_clip_4_is_read_whole(self):
        scenes = focus.parse(json.dumps(CLIP4), 6)
        self.assertEqual([3, 1, 3, 2, 3, 1], [len(s["people"]) for s in scenes])
        self.assertEqual(1, scenes[3]["speaker"])
        self.assertAlmostEqual(0.61, scenes[3]["people"][1]["box"]["x0"])

    def test_scene_numbers_outside_the_request_are_dropped(self):
        raw = {"scenes": [{"scene": 7, "people": _CLOSE, "speaker": 0}, {"scene": 1, "people": _CLOSE, "speaker": 0},
                          {"scene": 1, "people": _WIDE, "speaker": 0}]}
        scenes = focus.parse(json.dumps(raw), 2)
        # 같은 번호가 또 오면 앞의 것. 응답에 없는 장면은 None(창을 그대로 둔다).
        self.assertEqual(1, len(scenes[0]["people"]))
        self.assertIsNone(scenes[1])

    def test_a_bad_box_drops_the_person_and_the_speaker_follows(self):
        people = [{"box_2d": [500, 600, 400, 700], "head_2d": [0, 0, 10, 10]}] + _TWO  # ymin > ymax
        raw = {"scenes": [{"scene": 1, "people": people, "speaker": 2}]}
        scene = focus.parse(json.dumps(raw), 1)[0]
        self.assertEqual(2, len(scene["people"]))
        # 응답의 2번(오른쪽 사람)은 남은 사람 안에서 1번이다.
        self.assertEqual(1, scene["speaker"])
        raw["scenes"][0]["speaker"] = 0  # 뺀 사람을 가리키면 모른다
        self.assertIsNone(focus.parse(json.dumps(raw), 1)[0]["speaker"])

    def test_a_bad_head_falls_back_to_the_top_of_the_body(self):
        raw = {"scenes": [{"scene": 1, "people": [{"box_2d": [200, 300, 1000, 500], "head_2d": [1]}], "speaker": -1}]}
        person = focus.parse(json.dumps(raw), 1)[0]["people"][0]
        self.assertEqual(0.2, person["head"]["y0"])
        self.assertEqual((0.3, 0.5), (person["head"]["x0"], person["head"]["x1"]))

    def test_an_answer_that_is_not_json_is_an_error(self):
        with self.assertRaises(focus.FocusError):
            focus.parse("{scenes:", 1)
        with self.assertRaises(focus.FocusError):
            focus.parse("[]", 1)


class FrameTest(unittest.TestCase):
    def framed(self, number: int, speaker: int | None = -1) -> tuple[dict, str]:
        scene = focus.parse(json.dumps(CLIP4), 6)[number - 1]
        chosen = scene["speaker"] if speaker == -1 else speaker
        return focus.frame(scene["people"], chosen, SLOT, W, H)

    def test_three_people_are_all_in_with_even_margins(self):
        box, how = self.framed(5)
        self.assertEqual("group", how)
        self.assertAlmostEqual(0.157, box["x"], delta=0.01)
        self.assertAlmostEqual(0.66, box["w"], delta=0.01)
        self.assertAlmostEqual(0.07, box["y"], delta=0.02)
        # 좌우 여백이 같다 — 사람이 짚은 "왼쪽은 여백이 거의 없고 오른쪽은 많다" 의 반대.
        self.assertAlmostEqual(0.185 - box["x"], box["x"] + box["w"] - 0.780, places=3)

    def test_two_people_focus_on_the_speaker(self):
        box, how = self.framed(4)
        self.assertEqual("speaker", how)
        self.assertAlmostEqual(0.45, box["x"], delta=0.03)
        self.assertAlmostEqual(0.54, box["w"], delta=0.02)
        # 오른쪽 사람은 다 들어오고(원본 오른쪽 끝에 붙는다), 왼쪽 사람의 얼굴은 빠진다.
        self.assertEqual(1.0, round(box["x"] + box["w"], 5))
        self.assertLess(box["x"], 0.61)
        self.assertGreater(box["x"], 0.313)

    def test_two_people_without_a_speaker_are_both_in(self):
        box, how = self.framed(4, speaker=None)
        self.assertEqual("group", how)
        # 둘을 다 담기엔 칸 폭이 모자라 가장 큰 창을 둘의 가운데에 둔다.
        self.assertAlmostEqual(1080 / 854 * 1080 / 1920, box["w"], places=4)
        self.assertAlmostEqual((0.09 + 0.903) / 2, box["x"] + box["w"] / 2, places=4)

    def test_one_person_is_centered_on_them(self):
        box, how = self.framed(2)
        self.assertEqual("one", how)
        # 클로즈업은 사람 폭 × 1.8 이 원본 높이 전체를 넘어 가장 큰 창이 된다 — 가운데만 사람에게로 옮긴다.
        self.assertAlmostEqual(1080 / 854 * 1080 / 1920, box["w"], places=4)
        self.assertAlmostEqual((0.583 + 0.5815) / 2, box["x"] + box["w"] / 2, places=4)
        self.assertEqual(0.0, box["y"])

    def test_a_small_person_is_not_blown_up_past_the_soft_limit(self):
        person = {"box": {"x0": 0.70, "y0": 0.5, "x1": 0.75, "y1": 0.7}, "head": {"x0": 0.71, "y0": 0.5, "x1": 0.74, "y1": 0.55}}
        box, _ = focus.frame([person], None, SLOT, W, H)
        self.assertAlmostEqual(1080 / (1920 * 1.2), box["w"], places=4)
        self.assertLessEqual(box["y"] + height(box), 1.0 + 1e-5)  # 소수 5자리 반올림 — 저장할 때 fit_window 가 다시 맞춘다

    def test_nobody_leaves_the_window_alone(self):
        self.assertEqual((None, "none"), focus.frame([], None, SLOT, W, H))


class SourceTimeTest(unittest.TestCase):
    def test_parts_are_laid_end_to_end(self):
        ranges = [(100.0, 120.0), (600.0, 610.0)]
        self.assertEqual(105.0, focus.source_time(ranges, 5.0))
        self.assertEqual(603.0, focus.source_time(ranges, 23.0))
        self.assertAlmostEqual(609.95, focus.source_time(ranges, 30.0))


class RunTest(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = make_config(Path(self._tmp.name))
        self.source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status)"
            " values ('대담', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE') returning id"
        ).fetchone()["id"]
        self.conn.commit()
        self.frames = mock.patch.object(ffmpeg, "frame_jpeg", return_value=b"jpeg").start()
        self.addCleanup(mock.patch.stopall)

    def run_focus(self, windows: list[dict]) -> dict:
        return focus.run(
            self.conn, self.cfg, source_id=self.source_id, clip_id=4, source_path="a.mp4",
            ranges=[(258.04, 288.04)], layout={"slot_height": SLOT, "windows": windows}, src_w=W, src_h=H,
        )

    def answer(self, scenes: list[dict]) -> tuple[str, dict, int]:
        usage = {"input_tokens": 5075, "output_tokens": 993, "thinking_tokens": 1848, "total_tokens": 7916,
                 "cached_tokens": None, "attempts": 1}
        return json.dumps({"scenes": scenes}), usage, 16896

    def calls(self) -> list[dict]:
        return list(self.conn.execute("select params, error, input_tokens from stage_calls where stage = 'focus' order by id"))

    def test_person_windows_are_left_out_of_the_request(self):
        windows = [
            {"start": 0.0, "x": 0.18, "y": 0.0, "w": 0.71, "by": "auto"},
            {"start": 8.266, "x": 0.3, "y": 0.0, "w": 0.6, "by": "person"},
            {"start": 10.935, "x": 0.18, "y": 0.0, "w": 0.71, "by": "auto"},
        ]
        reply = self.answer([{"scene": 1, "people": _WIDE, "speaker": 1}, {"scene": 2, "people": _TWO, "speaker": 1}])
        with mock.patch.object(gemini, "generate_json", return_value=reply) as model:
            out = self.run_focus(windows)
        images = model.call_args.args[3]
        # 사람이 잡은 장면 2 는 보내지 않는다 — 남은 둘이 모델에게는 장면 1 · 2 다.
        self.assertEqual(["Scene 1, frame A", "Scene 1, frame B", "Scene 1, frame C", "Scene 2, frame A"],
                         [label for label, _ in images][:4])
        self.assertEqual(6, len(images))
        # 프레임은 장면의 20 · 50 · 80% 자리, 원본의 초다.
        self.assertAlmostEqual(258.04 + 8.266 * 0.5, self.frames.call_args_list[1].args[1], places=3)
        self.assertEqual(["ai", "person", "ai"], [w["by"] for w in out["windows"]])
        self.assertEqual(windows[1], out["windows"][1])
        self.assertEqual(["group", "person", "speaker"], [s["how"] for s in out["scenes"]])
        params = self.calls()[0]["params"]
        self.assertEqual(6, params["frames"])
        self.assertEqual(["group", "speaker"], [s["how"] for s in params["framed"]])

    def test_everyone_set_by_a_person_needs_no_call(self):
        windows = [{"start": 0.0, "x": 0.2, "y": 0.0, "w": 0.6, "by": "person"}]
        with mock.patch.object(gemini, "generate_json") as model:
            out = self.run_focus(windows)
        model.assert_not_called()
        self.frames.assert_not_called()
        self.assertFalse(out["called"])
        self.assertEqual([], self.calls())

    def test_a_scene_without_people_keeps_its_window(self):
        windows = [{"start": 0.0, "x": 0.14, "y": 0.0, "w": 0.71, "by": "auto"}]
        with mock.patch.object(gemini, "generate_json", return_value=self.answer([{"scene": 1, "people": [], "speaker": -1}])):
            out = self.run_focus(windows)
        self.assertEqual(windows, out["windows"])
        self.assertEqual("none", out["scenes"][0]["how"])

    def test_a_broken_answer_is_asked_once_more(self):
        windows = [{"start": 0.0, "x": 0.14, "y": 0.0, "w": 0.71, "by": "auto"}]
        good = self.answer([{"scene": 1, "people": _CLOSE, "speaker": 0}])
        with mock.patch.object(gemini, "generate_json", side_effect=[("{", good[1], 10), good]) as model:
            out = self.run_focus(windows)
        self.assertEqual(2, model.call_count)
        self.assertEqual("ai", out["windows"][0]["by"])
        calls = self.calls()
        self.assertIn("JSON", calls[0]["error"])
        self.assertIsNone(calls[1]["error"])

    def test_a_failure_is_recorded_and_the_windows_stay(self):
        windows = [{"start": 0.0, "x": 0.14, "y": 0.0, "w": 0.71, "by": "auto"}]
        with (
            mock.patch.object(gemini, "generate_json", side_effect=gemini.GeminiError("할당량")),
            self.assertRaises(focus.FocusError),
        ):
            self.run_focus(windows)
        self.assertIn("할당량", self.calls()[0]["error"])


if __name__ == "__main__":
    unittest.main()
