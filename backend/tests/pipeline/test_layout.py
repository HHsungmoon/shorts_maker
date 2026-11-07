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

    def scenes(self, *starts: float, **box) -> list[dict]:
        base = {"x": 0.1, "y": 0.0, "w": 0.6, "by": "person"} | box
        return [base | {"start": start} for start in starts]

    def test_each_scene_keeps_its_own_window_and_the_ends_are_filled(self):
        # 클립 4 의 경계(8.27 · 10.94 …). end 는 다음 장면의 start 다 — 화면이 보낸 end 는 믿지 않는다.
        windows = self.scenes(0, 8.266, 10.935)
        windows[1] |= {"x": 0.3, "end": 99}
        out = layout.validate(draft(windows=windows), spec(), W, H, duration=30.0)["windows"]
        self.assertEqual([0.0, 8.266, 10.935], [w["start"] for w in out])
        self.assertEqual([8.266, 10.935, None], [w["end"] for w in out])
        self.assertAlmostEqual(0.3, out[1]["x"], places=3)

    def test_the_first_scene_starts_at_zero(self):
        with self.assertRaises(layout.LayoutError):
            self.check(draft(windows=self.scenes(1.0, 5.0)))

    def test_scenes_out_of_order_or_too_close_are_refused_instead_of_merged(self):
        # 🔴 조용히 합치면 사람이 나눈 경계가 저장하는 순간 사라진다.
        for starts in ((0, 5.0, 4.0), (0, 5.0, 5.1)):
            with self.subTest(starts=starts), self.assertRaises(layout.LayoutError):
                self.check(draft(windows=self.scenes(*starts)))

    def test_a_scene_past_the_clip_end_is_refused(self):
        with self.assertRaises(layout.LayoutError):
            layout.validate(draft(windows=self.scenes(0, 29.9)), spec(), W, H, duration=30.0)

    def test_every_scene_is_fitted_to_the_one_slot(self):
        # 칸 높이는 클립에 하나 — 칸이 길어지면 모든 장면의 창이 원본 안으로 함께 줄어든다.
        out = self.check(draft(slot_height=900, windows=self.scenes(0, 10, w=1.0)))
        for window in out["windows"]:
            self.assertLessEqual(layout.window_height(window, 900, W, H), 1.0 + 1e-6)


    def test_the_slot_must_stay_in_the_template_range(self):
        with self.assertRaises(layout.LayoutError):
            self.check(draft(slot_height=1000))

    def test_an_odd_slot_height_becomes_even(self):
        # yuv420p 는 짝수 크기만 받는다.
        self.assertEqual(0, self.check(draft(slot_height=701))["slot_height"] % 2)

    def test_a_half_rounds_up_like_the_editor(self):
        # 편집 화면은 Math.round(853 / 2) * 2 = 854 다. 서버가 852 로 내리면 같은 템플릿이 열 때마다 2px 달라진다.
        self.assertEqual(854, layout._even(853))
        self.assertEqual(702, layout._even(701))

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


class SplitTest(unittest.TestCase):
    def test_new_scenes_inherit_the_box_covering_their_middle(self):
        # 경계가 8.0 → 8.3 으로 조금 옮겨져도 두 번째 장면은 옛 두 번째 상자를 물려받는다(시작 시각으로 고르면 첫 상자다).
        # 옛 창(8~30초)의 98% 가 새 장면 안이라 1:1 — 사람이 잡은 상자로 남는다.
        old = [
            {"start": 0, "end": 8.0, "x": 0.1, "y": 0, "w": 0.5, "by": "auto"},
            {"start": 8.0, "end": None, "x": 0.4, "y": 0, "w": 0.5, "by": "person"},
        ]
        out = layout.split(old, [0.0, 8.3], 30.0)
        self.assertEqual([0.1, 0.4], [w["x"] for w in out])
        self.assertEqual(["auto", "person"], [w["by"] for w in out])
        self.assertEqual([8.3, None], [w["end"] for w in out])

    def test_a_person_box_spread_over_several_scenes_goes_back_to_auto(self):
        # 클립 4(2026-11-07): 창 하나일 때 클립 전체에 잡은 상자가 장면 여섯에 사람 것으로 퍼져 AI 가 못 고쳤다.
        # 좌표는 초기값으로 물려주고 by 만 내린다.
        old = [{"start": 0, "end": None, "x": 0.18025, "y": 0, "w": 0.71136, "by": "person"}]
        out = layout.split(old, [0.0, 8.266, 10.935, 13.563, 19.277, 25.158], 30.0)
        self.assertEqual({"auto"}, {w["by"] for w in out})
        self.assertEqual({0.18025}, {w["x"] for w in out})
        # 한 장면을 둘로 나눈 것도 같다 — 옛 창의 절반씩이라 어느 쪽과도 1:1 이 아니다.
        halves = layout.split([old[0] | {"end": None}], [0.0, 15.0], 30.0)
        self.assertEqual(["auto", "auto"], [w["by"] for w in halves])

    def test_window_at_picks_the_scene_of_the_moment(self):
        windows = [{"start": 0, "x": 0}, {"start": 8.3, "x": 1}]
        self.assertEqual(0, layout.window_at(windows, 8.29)["x"])
        self.assertEqual(1, layout.window_at(windows, 8.3)["x"])


class GeometryTest(unittest.TestCase):
    def test_the_crop_is_even_and_inside_the_source(self):
        value = layout.validate(draft(slot_height=852), spec(), W, H)
        crop = layout.crop_px(value["windows"][0], 852, W, H)
        self.assertEqual(0, crop["w"] % 2)
        self.assertEqual(0, crop["h"] % 2)
        self.assertLessEqual(crop["x"] + crop["w"], W)
        self.assertLessEqual(crop["y"] + crop["h"], H)

    def test_each_scene_has_its_own_crop_and_the_worst_upscale_counts(self):
        windows = [
            {"start": 0, "x": 0.0, "y": 0.0, "w": 1.0},
            {"start": 8.3, "x": 0.3, "y": 0.2, "w": 0.4},
        ]
        value = layout.validate(draft(windows=windows), spec(), W, H)
        geometry = layout.geometry(spec(), value, W, H)
        self.assertEqual([0.0, 8.3], [c["start"] for c in geometry["crops"]])
        self.assertGreater(geometry["crops"][0]["w"], geometry["crops"][1]["w"])
        self.assertAlmostEqual(1080 / geometry["crops"][1]["w"], geometry["upscale"], places=2)
        self.assertTrue(any("장면 2" in w for w in geometry["warnings"]))

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
