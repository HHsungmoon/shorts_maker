"""클립 안의 장면 경계(pipeline/scenes.py) — tease §5-10 "장면마다 창".

점수는 클립 4(잡소리 ep.48, 4:18–4:48) 실측값을 그대로 쓴다. ffmpeg 은 mock — 실제 점수는 2026-11-07 에 같은 구간으로
쟀다(컷 0.39~0.46, 컷 아닌 곳 최대 0.043, 30초에 0.69초).
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shorts_maker.adapters import ffmpeg
from shorts_maker.pipeline import scenes

from ..support import DbTestCase, make_config

# 클립 4 의 장면 점수(구간 안의 초, 점수) — 29.95 는 끝 한 프레임에 다음 장면이 들어온 것, 29.996 은 그 다음 프레임.
CLIP4 = [
    (8.266, 0.393), (10.935, 0.416), (13.563, 0.412), (19.277, 0.386), (25.158, 0.422),
    (29.954, 0.46), (29.996, 0.141),
]


class BoundariesTest(unittest.TestCase):
    def test_clip_4_splits_where_a_person_saw_the_cuts(self):
        starts, head, tail = scenes.boundaries([t for t, s in CLIP4 if s >= scenes.THRESHOLD], [], 30.0)
        self.assertEqual([0.0, 8.266, 10.935, 13.563, 19.277, 25.158], starts)
        self.assertIsNone(head)
        # 끝 0.046초(한 프레임)에 다음 장면 — 장면으로 나누지 않고 알린다.
        self.assertAlmostEqual(0.046, tail, places=3)

    def test_a_flash_right_after_a_cut_is_not_a_scene(self):
        starts, _, _ = scenes.boundaries([5.0, 5.2, 12.0], [], 30.0)
        self.assertEqual([0.0, 5.0, 12.0], starts)

    def test_a_cut_at_the_very_start_is_a_head_flash(self):
        starts, head, _ = scenes.boundaries([0.08, 10.0], [], 30.0)
        self.assertEqual([0.0, 10.0], starts)
        self.assertAlmostEqual(0.08, head)

    def test_part_joins_are_always_boundaries(self):
        # 조합 클립의 이음매는 원본의 다른 자리다. 바로 앞의 컷이 0.5초 안이면 이음매가 이긴다.
        starts, _, _ = scenes.boundaries([19.8], [20.0], 30.0)
        self.assertEqual([0.0, 20.0], starts)


class DetectTest(DbTestCase):
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

    def detect(self, ranges):
        return scenes.detect(
            self.conn, self.cfg, source_id=self.source_id, clip_id=4, source_path="a.mp4", ranges=ranges
        )

    def recorded(self) -> dict:
        return self.conn.execute(
            "select params, error from stage_calls where stage = 'scenes' order by id desc limit 1"
        ).fetchone()

    def test_cuts_are_split_from_weak_changes_and_recorded(self):
        scores = CLIP4 + [(3.39, 0.06)]  # 장면 안에서 자막 줄이 바뀐 정도 — 애매한 곳으로만 남는다
        with mock.patch.object(ffmpeg, "scene_scores", return_value=scores) as probe:
            out = self.detect([(258.0, 288.0)])
        probe.assert_called_once()
        self.assertEqual((258.0, 30.0), probe.call_args.args[1:3])
        self.assertEqual(6, len(out["starts"]))
        self.assertEqual([3.39, 29.996], sorted(w["t"] for w in out["weak"]))
        params = self.recorded()["params"]
        self.assertEqual(6, params["scenes"])
        self.assertEqual(scenes.THRESHOLD, params["threshold"])

    def test_parts_are_laid_end_to_end_without_the_bridge(self):
        # 조각 20초 + 10초. 두 번째 조각 안 3초의 컷은 클립 안 23초다. 브릿지는 클립 시간에 없다.
        with mock.patch.object(ffmpeg, "scene_scores", side_effect=[[(8.0, 0.4)], [(3.0, 0.4)]]):
            out = self.detect([(100.0, 120.0), (600.0, 610.0)])
        self.assertEqual([0.0, 8.0, 20.0, 23.0], out["starts"])
        self.assertEqual(30.0, out["duration"])

    def test_a_failure_is_recorded_too(self):
        with (
            mock.patch.object(ffmpeg, "scene_scores", side_effect=ffmpeg.FfmpegError("boom")),
            self.assertRaises(ffmpeg.FfmpegError),
        ):
            self.detect([(0.0, 30.0)])
        self.assertIn("boom", self.recorded()["error"])


if __name__ == "__main__":
    unittest.main()
