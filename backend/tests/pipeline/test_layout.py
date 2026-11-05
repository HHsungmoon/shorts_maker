"""클립 편집 명세(pipeline/layout.py) — 기본값 · 검증 · 창 계산. DB 없이 돈다.

🔴 창은 원본 크기에 대한 **비율**이고 높이는 칸 비율에서 나온다. 여기서 틀리면 미리보기의 상자와 렌더 결과가
다른 곳을 자른다.
"""

import unittest

from shorts_maker.pipeline import layout, templates

from .test_templates import card

W, H = 1920, 1080


def spec(**slot) -> dict:
    return templates.validate(card(**slot))


def draft(**overrides) -> dict:
    base = layout.default(
        spec(), template_id=1, series_id=1, title="삼성 출신 면접관이 말하는 합격하는 답변 공개",
        channel="채널 원티드", src_w=W, src_h=H,
    )
    return base | overrides


class DefaultTest(unittest.TestCase):
    def test_a_16_9_slot_shows_the_whole_frame(self):
        # 칸 608 은 정확한 16:9(607.5)보다 0.5px 길다 — 그래서 좌우가 합쳐 1.6px 깎인다(폭 0.99918). 그만큼은 원본 전체다.
        window = draft()["windows"][0]
        self.assertAlmostEqual(1.0, window["w"], delta=0.001)
        self.assertAlmostEqual(0.0, window["x"], delta=0.001)
        self.assertEqual(0.0, window["y"])
        self.assertEqual("auto", window["by"])

    def test_a_taller_slot_crops_the_sides_around_the_middle(self):
        # 칸 852(약 5:4) → 창 높이가 원본 전체면 폭은 1080 × 1080/852 = 1369px = 0.713.
        base = layout.default(
            spec(default=852), template_id=1, series_id=None, title=None, channel=None, src_w=W, src_h=H
        )
        window = base["windows"][0]
        self.assertAlmostEqual(0.713, window["w"], places=3)
        self.assertAlmostEqual((1 - window["w"]) / 2, window["x"], places=4)
        self.assertEqual(0.0, window["y"])

    def test_the_title_is_folded_into_two_lines(self):
        self.assertEqual(["삼성 출신 면접관이 말하는", "합격하는 답변 공개"], draft()["headline"])

    def test_the_credit_uses_the_channel(self):
        self.assertEqual("출처 : 채널 원티드", draft()["credit"])

    def test_subtitles_start_on(self):
        # P5(원본 자막 판정) 전까지는 켜기가 기본이다. 사람이 끈다.
        self.assertTrue(draft()["subtitles"])


class ValidateTest(unittest.TestCase):
    def check(self, value: dict) -> dict:
        return layout.validate(value, spec(), W, H)

    def test_a_default_round_trips(self):
        value = draft()
        self.assertEqual(value, self.check(value))

    def test_a_slight_overflow_from_dragging_is_pulled_back_inside(self):
        value = draft(windows=[{"x": 0.004, "y": -0.003, "w": 1.0, "by": "person"}])
        window = self.check(value)["windows"][0]
        self.assertGreaterEqual(window["x"], 0.0)
        self.assertEqual(0.0, window["y"])
        self.assertLessEqual(window["x"] + window["w"], 1.0)
        self.assertEqual("person", window["by"])

    def test_a_window_with_no_width_is_refused(self):
        with self.assertRaises(layout.LayoutError):
            self.check(draft(windows=[{"x": 0.2, "y": 0.2, "w": 0}]))

    def test_a_window_too_narrow_to_recognize_is_refused(self):
        # 폭 300px 을 1080 으로 늘리면 3.6배 — 상한 3배를 넘는다.
        with self.assertRaises(layout.LayoutError):
            self.check(draft(windows=[{"x": 0.2, "y": 0.2, "w": 300 / W}]))

    def test_one_window_for_now(self):
        window = draft()["windows"][0]
        with self.assertRaises(layout.LayoutError):
            self.check(draft(windows=[window, window]))

    def test_the_slot_must_stay_in_the_template_range(self):
        with self.assertRaises(layout.LayoutError):
            self.check(draft(slot_height=1000))

    def test_an_odd_slot_height_becomes_even(self):
        # yuv420p 는 짝수 크기만 받는다.
        self.assertEqual(0, self.check(draft(slot_height=701))["slot_height"] % 2)

    def test_a_taller_slot_shrinks_a_full_width_window_instead_of_overflowing(self):
        value = draft(slot_height=900)  # 창은 기본값(원본 전체 폭)이다 — 칸이 길어지면 높이가 원본을 넘는다.
        window = self.check(value)["windows"][0]
        self.assertLessEqual(layout.window_height(window, 900, W, H), 1.0 + 1e-6)
        self.assertAlmostEqual(0.5, window["x"] + window["w"] / 2, places=4)

    def test_headline_whitespace_is_collapsed_and_missing_lines_are_padded(self):
        out = self.check(draft(headline=["  면접관이   말하는 "]))
        self.assertEqual(["면접관이 말하는", ""], out["headline"])

    def test_a_runaway_headline_is_refused(self):
        with self.assertRaises(layout.LayoutError):
            self.check(draft(headline=["가" * 41, ""]))

    def test_by_values_are_checked(self):
        with self.assertRaises(layout.LayoutError):
            self.check(draft(subtitles_by="robot"))


class GeometryTest(unittest.TestCase):
    def test_the_crop_is_even_and_inside_the_source(self):
        value = layout.validate(draft(slot_height=852), spec(), W, H)
        crop = layout.crop_px(value["windows"][0], 852, W, H)
        self.assertEqual(0, crop["w"] % 2)
        self.assertEqual(0, crop["h"] % 2)
        self.assertLessEqual(crop["x"] + crop["w"], W)
        self.assertLessEqual(crop["y"] + crop["h"], H)

    def test_upscaling_is_warned(self):
        value = layout.validate(draft(windows=[{"x": 0.3, "y": 0.3, "w": 0.4}]), spec(), W, H)
        geometry = layout.geometry(spec(), value, W, H)
        self.assertGreater(geometry["upscale"], layout.SOFT_UPSCALE)
        self.assertTrue(any("배" in w for w in geometry["warnings"]))

    def test_a_full_width_16_9_window_is_not_upscaled(self):
        geometry = layout.geometry(spec(), draft(), W, H)
        self.assertLess(geometry["upscale"], 1)
        self.assertEqual([], geometry["warnings"])

    def test_no_series_is_warned_when_the_template_has_a_badge(self):
        geometry = layout.geometry(spec(), draft(series_id=None), W, H)
        self.assertTrue(any("시리즈" in w for w in geometry["warnings"]))


if __name__ == "__main__":
    unittest.main()
