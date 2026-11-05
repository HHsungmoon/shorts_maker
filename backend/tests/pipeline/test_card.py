"""카드의 글자 층(pipeline/card.py) — ASS 를 어떻게 쓰는가와 캐시 이름. ffmpeg 은 부르지 않는다.

실제로 그려지는 모양은 2026-11-05 에 소스 4(잡소리)로 PNG · 영상을 뽑아 눈으로 확인했다(설계안 v3 의 잡소리 측정과
배지 · 칸 위치가 맞았다). 여기서는 그 모양을 만드는 **입력**이 바뀌지 않게 지킨다.
"""

import tempfile
import unittest
from pathlib import Path

from shorts_maker.pipeline import card, layout, templates

from ..support import make_config
from .test_templates import card as card_spec


def spec() -> dict:
    return templates.validate(card_spec())


def draft(**overrides) -> dict:
    return layout.default(
        spec(), template_id=1, series_id=1, title="면접관이 말하는 합격 답변", channel="채널 원티드",
        src_w=1920, src_h=1080,
    ) | overrides


class ColorTest(unittest.TestCase):
    def test_colors_are_written_blue_green_red(self):
        # 🔴 ASS 는 &HAABBGGRR 이다. #FFD400(노랑)을 그대로 옮기면 하늘색이 된다.
        self.assertEqual("&H0000D4FF", card.ass_color("#FFD400"))


class AssTest(unittest.TestCase):
    def test_colors_are_not_moved_into_tv_range(self):
        # 🔴 YCbCr Matrix 가 없으면 libass 가 색을 16–235 로 옮긴다(#FFD400 → #EBC610, 2026-11-05 실측).
        self.assertIn("YCbCr Matrix: None", card.build_ass(spec(), draft(), "잡소리", False))

    def test_the_series_name_is_drawn_when_there_is_no_badge_image(self):
        text = card.build_ass(spec(), draft(), "잡소리", False)
        self.assertIn("잡소리", text)
        self.assertIn("Style: Badge", text)

    def test_a_badge_image_replaces_the_text_badge(self):
        text = card.build_ass(spec(), draft(), "잡소리", True)
        self.assertNotIn("Style: Badge", text)

    def test_no_series_means_no_badge(self):
        self.assertNotIn("Style: Badge", card.build_ass(spec(), draft(), None, False))

    def test_lines_sit_in_the_middle_of_their_blocks(self):
        value = draft(headline=["첫 줄", "둘째 줄"])
        boxes = templates.stack(spec(), value["slot_height"])
        text = card.build_ass(spec(), value, "잡소리", False)
        self.assertIn(f"\\pos(540,{boxes['badge'].middle})", text)
        self.assertIn(f"\\pos(540,{boxes['credit'].middle})", text)
        # 두 번째 줄 = 위끝 + 첫 줄 높이 + 줄 간격 + 반 줄
        second = boxes["headline"].top + 95 + 10 + 95 // 2
        self.assertIn(f"\\pos(540,{second})", text)

    def test_an_empty_line_draws_nothing(self):
        text = card.build_ass(spec(), draft(headline=["한 줄만", ""]), "잡소리", False)
        self.assertIn("Style: Headline0", text)
        self.assertNotIn("Style: Headline1", text)

    def test_braces_in_copy_do_not_become_override_tags(self):
        text = card.build_ass(spec(), draft(headline=["{\\b1}굵게", ""]), "잡소리", False)
        self.assertIn("\\{", text)


class CacheNameTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = make_config(Path(self._tmp.name))

    def test_only_hash_names_are_served(self):
        # 🔴 화면이 고른 이름으로 파일을 읽는다 — work_dir 밖을 가리키는 이름은 받지 않는다.
        (self.cfg.work_dir / card.CARD_DIR).mkdir(parents=True)
        good = "0" * card.NAME_LENGTH + ".png"
        (self.cfg.work_dir / card.CARD_DIR / good).write_bytes(b"png")
        self.assertIsNotNone(card.card_file(self.cfg, good))
        for name in ("../secret.png", "a" * card.NAME_LENGTH + ".ass", "x.png", "G" * card.NAME_LENGTH + ".png"):
            with self.subTest(name=name):
                self.assertIsNone(card.card_file(self.cfg, name))

    def test_the_same_input_finds_the_same_file(self):
        self.assertIsNone(card.lookup(self.cfg, spec(), draft(), "잡소리", None))
        digest, _, _ = card._plan(spec(), draft(), "잡소리", None)
        (self.cfg.work_dir / card.CARD_DIR).mkdir(parents=True)
        (self.cfg.work_dir / card.CARD_DIR / f"{digest}.png").write_bytes(b"png")
        self.assertIsNotNone(card.lookup(self.cfg, spec(), draft(), "잡소리", None))
        # 카피가 바뀌면 다른 파일이다.
        self.assertIsNone(card.lookup(self.cfg, spec(), draft(headline=["다른 카피", ""]), "잡소리", None))


if __name__ == "__main__":
    unittest.main()
