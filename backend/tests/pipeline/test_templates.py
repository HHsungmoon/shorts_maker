"""템플릿 검증과 배치(pipeline/templates.py) — DB 없이 돈다.

지키는 것: ① 시드 템플릿 둘이 원티드 숏폼에서 잰 자리에 블록을 놓는가 ② 글자가 안전 영역(120~1480)을 넘는
템플릿을 저장 단계에서 막는가 ③ 칸 높이 상한이 템플릿마다 계산되는가.
"""

import json
import re
import unittest
from pathlib import Path

from shorts_maker.pipeline import templates

MIGRATION = Path(templates.__file__).parents[1] / "db" / "migrations" / "019_clip_layout.sql"


def seed_specs() -> dict[str, dict]:
    """마이그레이션 019 의 시드 spec. 🔴 SQL 의 JSON 리터럴을 그대로 읽는다 — 테스트에 따로 적으면 둘이 어긋나도 모른다."""
    text = MIGRATION.read_text(encoding="utf-8")
    found = re.findall(r"\('([^']+)', 'card', 'seed', '(\{.*?\})'\)", text, flags=re.DOTALL)
    return {name: json.loads(body) for name, body in found}


def card(**slot) -> dict:
    return {
        "background": "#FFFFFF",
        "anchor_y": 790,
        "blocks": [
            {"type": "badge", "height": 80, "gap": 30},
            {"type": "headline", "size": 70, "line_height": 95, "line_gap": 10, "max_chars": 15, "gap": 40,
             "lines": [{"color": "#111111"}, {"color": "#FFFFFF", "band": "#111111"}]},
            {"type": "slot", "min": 608, "max": 900, "default": 608, "gap": 40} | slot,
            {"type": "credit", "height": 50, "size": 40, "text": "출처 : {channel}"},
        ],
    }


class SeedTest(unittest.TestCase):
    def test_both_seeds_are_valid(self):
        specs = seed_specs()
        self.assertEqual({"왓츠인 마이 AI 카드", "잡소리 카드"}, set(specs))
        for name, spec in specs.items():
            with self.subTest(name=name):
                self.assertEqual((608, 900), templates.slot_limits(templates.validate(spec)))

    def test_the_jobsori_layout_lands_where_the_real_short_has_it(self):
        # 잡소리 숏폼 실측(1080×1920): 배지 150–230 · 칸 504–1357. 같은 규칙으로 10px 안팎이어야 한다.
        spec = templates.validate(seed_specs()["잡소리 카드"])
        boxes = templates.stack(spec, 853)
        self.assertAlmostEqual(150, boxes["badge"].top, delta=10)
        self.assertAlmostEqual(504, boxes["slot"].top, delta=12)
        self.assertAlmostEqual(1357, boxes["slot"].bottom, delta=12)

    def test_the_whats_in_my_ai_slot_matches_too(self):
        # 왓츠인 마이 AI 실측: 칸 614–1222(16:9).
        spec = templates.validate(seed_specs()["왓츠인 마이 AI 카드"])
        slot = templates.stack(spec, 608)["slot"]
        self.assertAlmostEqual(614, slot.top, delta=10)
        self.assertAlmostEqual(1222, slot.bottom, delta=10)


class StackTest(unittest.TestCase):
    def test_the_middle_of_the_whole_stack_sits_on_the_anchor(self):
        spec = templates.validate(card())
        for slot in (608, 700, 900):
            boxes = templates.stack(spec, slot)
            top, bottom = boxes["badge"].top, boxes["credit"].bottom
            self.assertAlmostEqual(790, (top + bottom) / 2, delta=1)

    def test_the_last_gap_does_not_count(self):
        # 마지막 블록 뒤 여백이 덩어리 높이에 들어가면 가운데가 그만큼 위로 어긋난다.
        spec = card()
        spec["blocks"][-1]["gap"] = 200
        self.assertEqual(0, templates.validate(spec)["blocks"][-1]["gap"])


class SafeZoneTest(unittest.TestCase):
    def test_the_slot_limit_comes_from_the_safe_zone(self):
        # 덩어리 위끝 ≥ 120 → 440 + S ≤ 2 × (790 − 120) → S ≤ 900. 적힌 max 를 1080 으로 올려도 900 에서 멈춘다.
        spec = templates.validate(card(max=1080))
        self.assertEqual(900, templates.slot_limits(spec)[1])

    def test_dropping_the_badge_leaves_room_for_a_taller_slot(self):
        spec = card(max=1080)
        spec["blocks"] = spec["blocks"][1:]
        self.assertGreater(templates.slot_limits(templates.validate(spec))[1], 900)

    def test_a_template_whose_text_cannot_fit_is_refused(self):
        spec = card()
        spec["blocks"][0]["height"] = 300
        spec["blocks"][1]["line_height"] = 200
        with self.assertRaises(templates.TemplateError):
            templates.validate(spec)


class ValidationTest(unittest.TestCase):
    def test_colors_must_be_hex(self):
        spec = card()
        spec["background"] = "white"
        with self.assertRaises(templates.TemplateError):
            templates.validate(spec)

    def test_a_slot_is_required(self):
        spec = card()
        spec["blocks"] = [b for b in spec["blocks"] if b["type"] != "slot"]
        with self.assertRaises(templates.TemplateError):
            templates.validate(spec)

    def test_a_block_cannot_appear_twice(self):
        spec = card()
        spec["blocks"].append(dict(spec["blocks"][0]))
        with self.assertRaises(templates.TemplateError):
            templates.validate(spec)

    def test_the_default_slot_must_be_inside_its_range(self):
        with self.assertRaises(templates.TemplateError):
            templates.validate(card(default=950))

    def test_credit_text_fills_in_the_channel(self):
        spec = templates.validate(card())
        self.assertEqual("출처 : 채널 원티드", templates.credit_text(spec, " 채널 원티드 "))
        self.assertEqual("", templates.credit_text(spec, None))


if __name__ == "__main__":
    unittest.main()
