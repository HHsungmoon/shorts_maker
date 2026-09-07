"""렌더 — 조각 수에 따른 분기와 계측.

ffmpeg 은 부르지 않는다(mock). 실제 필터 그래프가 도는지는 합성 영상으로 따로 확인했다
(7.40초 = 4 + 브릿지 0.4 + 3, 1080x1920). 여기서 보는 건 **어느 길로 가는가**와
**무엇을 기록하는가**다.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shorts_maker.adapters import ffmpeg
from shorts_maker.pipeline import render

from ..support import DbTestCase, make_config


class RenderTestCase(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = make_config(Path(self._tmp.name))
        self.cfg.source_dir.mkdir(parents=True, exist_ok=True)
        (self.cfg.source_dir / "a.mp4").write_bytes(b"video")

        self.source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status)"
            " values ('강연', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE') returning id"
        ).fetchone()["id"]
        self.chunk_id = self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path)"
            " values (%s, 0, 0, 3000, 'c.wav') returning id", (self.source_id,)
        ).fetchone()["id"]
        for idx in range(8):
            self.conn.execute(
                "insert into utterances (chunk_id, idx, start_sec, end_sec, text)"
                " values (%s, %s, %s, %s, %s)",
                (self.chunk_id, idx, idx * 100, idx * 100 + 10, f"{idx}번 발화"),
            )
        self.segment_id = self.conn.execute(
            """insert into segments (chunk_id, idx, start_sec, end_sec, description,
                                     start_utterance_idx, end_utterance_idx)
               values (%s, 0, 0, 800, '설명', 0, 7) returning id""", (self.chunk_id,)
        ).fetchone()["id"]
        self.run_id = self.conn.execute(
            "insert into runs (source_id) values (%s) returning id", (self.source_id,)
        ).fetchone()["id"]
        self.conn.commit()

        # 자막 필터·폰트 확인은 렌더의 관심사가 아니다 — 여기서는 있다고 본다.
        for name, value in (("has_filter", True), ("font_available", True)):
            patcher = mock.patch.object(ffmpeg, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def clip(self, start: float, end: float, parts: list[tuple[float, float]] | None,
             total_sec: float | None = None) -> int:
        clip_id = self.conn.execute(
            """insert into clips (run_id, segment_id, start_sec, end_sec, total_sec)
               values (%s, %s, %s, %s, %s) returning id""",
            (self.run_id, self.segment_id, start, end, total_sec),
        ).fetchone()["id"]
        for ordinal, (piece_start, piece_end) in enumerate(parts or []):
            self.conn.execute(
                """insert into clip_parts (clip_id, ordinal, segment_id, start_sec, end_sec,
                                           start_utterance_idx, end_utterance_idx)
                   values (%s, %s, %s, %s, %s, 0, 1)""",
                (clip_id, ordinal, self.segment_id, piece_start, piece_end),
            )
        self.conn.commit()
        return clip_id

    def recorded(self) -> dict:
        return self.conn.execute(
            "select params from stage_calls where stage = 'render' order by id desc limit 1"
        ).fetchone()["params"]


class BranchTest(RenderTestCase):
    def test_a_single_part_clip_takes_the_original_path(self):
        clip_id = self.clip(0, 30, [(0.0, 30.0)])
        with (
            mock.patch.object(ffmpeg, "render_vertical") as single,
            mock.patch.object(ffmpeg, "render_parts") as combined,
        ):
            render.run_for_clip(self.conn, self.cfg, clip_id, force=True)
        single.assert_called_once()
        combined.assert_not_called()

    def test_a_clip_with_no_parts_also_takes_the_original_path(self):
        # 기존 경로(기준 rank → cut)가 만든 클립은 clip_parts 가 없다. 그대로 돌아야 한다.
        clip_id = self.clip(0, 30, None)
        with (
            mock.patch.object(ffmpeg, "render_vertical") as single,
            mock.patch.object(ffmpeg, "render_parts") as combined,
        ):
            render.run_for_clip(self.conn, self.cfg, clip_id, force=True)
        single.assert_called_once()
        combined.assert_not_called()

    def test_several_parts_are_concatenated(self):
        clip_id = self.clip(0, 700, [(0.0, 20.0), (600.0, 610.0)], total_sec=30.0)
        with (
            mock.patch.object(ffmpeg, "render_vertical") as single,
            mock.patch.object(ffmpeg, "render_parts") as combined,
        ):
            render.run_for_clip(self.conn, self.cfg, clip_id, force=True)
        single.assert_not_called()
        combined.assert_called_once()
        # 조각의 시간 범위가 순서대로 넘어가야 한다.
        self.assertEqual(combined.call_args.args[2], [(0.0, 20.0), (600.0, 610.0)])

    def test_rendering_marks_the_clip_and_stores_a_relative_path(self):
        clip_id = self.clip(0, 30, [(0.0, 30.0)])
        with mock.patch.object(ffmpeg, "render_vertical"):
            render.run_for_clip(self.conn, self.cfg, clip_id, force=True)
        row = self.conn.execute(
            "select rendered, path from clips where id = %s", (clip_id,)
        ).fetchone()
        self.assertTrue(row["rendered"])
        # 🔴 DB 에는 work_dir 기준 상대경로만 들어간다(config.store_work).
        self.assertEqual(row["path"], f"clips/clip{clip_id:03d}.mp4")

    def test_an_already_rendered_clip_needs_force(self):
        clip_id = self.clip(0, 30, [(0.0, 30.0)])
        with mock.patch.object(ffmpeg, "render_vertical"):
            render.run_for_clip(self.conn, self.cfg, clip_id, force=True)
            with self.assertRaises(render.RenderError):
                render.run_for_clip(self.conn, self.cfg, clip_id, force=False)


class InstrumentationTest(RenderTestCase):
    def test_a_combined_clip_records_its_real_length_not_the_envelope(self):
        # 🔴 조합 클립의 start/end 는 봉투다(첫 조각 시작 ~ 마지막 조각 끝). 12:30 과 41:00 을 이은
        # 30초짜리를 28분으로 기록하면 §7 의 시간 실측이 통째로 거짓이 된다.
        clip_id = self.clip(0, 700, [(0.0, 20.0), (600.0, 610.0)], total_sec=30.0)
        with mock.patch.object(ffmpeg, "render_parts"):
            render.run_for_clip(self.conn, self.cfg, clip_id, force=True)
        params = self.recorded()
        self.assertEqual(params["duration_sec"], 30.0)
        self.assertEqual(params["parts"], 2)

    def test_a_single_part_clip_falls_back_to_the_span(self):
        clip_id = self.clip(0, 30, [(0.0, 30.0)])
        with mock.patch.object(ffmpeg, "render_vertical"):
            render.run_for_clip(self.conn, self.cfg, clip_id, force=True)
        params = self.recorded()
        self.assertEqual(params["duration_sec"], 30.0)
        self.assertEqual(params["parts"], 1)

    def test_the_call_is_tied_to_its_source_and_run(self):
        clip_id = self.clip(0, 30, [(0.0, 30.0)])
        with mock.patch.object(ffmpeg, "render_vertical"):
            render.run_for_clip(self.conn, self.cfg, clip_id, force=True)
        row = self.conn.execute(
            "select source_id, run_id from stage_calls where stage = 'render'"
        ).fetchone()
        self.assertEqual((row["source_id"], row["run_id"]), (self.source_id, self.run_id))


class CombinedSubtitleTest(RenderTestCase):
    def test_subtitles_are_offset_onto_the_joined_timeline(self):
        # 조각을 한 번에 이어붙이므로 자막도 이어붙인 뒤 기준이어야 한다. 조각별 상대 초를 그대로
        # 쓰면 두 번째 조각부터 전부 어긋난다.
        clip_id = self.clip(0, 700, [(0.0, 20.0), (600.0, 620.0)], total_sec=40.0)
        with mock.patch.object(ffmpeg, "render_parts") as combined:
            render.run_for_clip(self.conn, self.cfg, clip_id, force=True)
        subtitle_path = Path(combined.call_args.kwargs["subtitle_path"])
        text = subtitle_path.read_text(encoding="utf-8")
        # 브릿지 카드가 자막 파일 안에 들어 있다 — 별도 필터를 쓰지 않는다.
        self.assertIn("이어집니다", text)
        # 두 번째 조각의 자막은 20초 + 브릿지 뒤로 밀려 있어야 한다(0:00:20.40 이후).
        self.assertIn("0:00:20.40", text)

    def test_no_subtitles_means_no_file_is_passed(self):
        clip_id = self.clip(0, 700, [(0.0, 20.0), (600.0, 610.0)], total_sec=30.0)
        with mock.patch.object(ffmpeg, "render_parts") as combined:
            render.run_for_clip(self.conn, self.cfg, clip_id, force=True, burn_subtitles=False)
        self.assertIsNone(combined.call_args.kwargs["subtitle_path"])



class LegacyClipLengthTest(DbTestCase):
    """기존 경로(기준 rank → cut)가 만든 클립도 `total_sec` 을 갖는가.

    🔴 시청자 화면이 이 값으로 길이를 보여준다. null 이면 길이가 통째로 안 나오고, 읽는 쪽마다
    "null 이면 end - start" 폴백을 써야 하는데 그 폴백은 **조합 클립에서 틀린다**(봉투가 나온다).
    """

    def test_the_backfill_only_touches_clips_without_parts(self):
        source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status)"
            " values ('강연', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE') returning id"
        ).fetchone()["id"]
        chunk_id = self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path)"
            " values (%s, 0, 0, 3000, 'c.wav') returning id", (source_id,)
        ).fetchone()["id"]
        segment_id = self.conn.execute(
            "insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx,"
            " end_utterance_idx) values (%s, 0, 0, 800, 0, 7) returning id", (chunk_id,)
        ).fetchone()["id"]
        run_id = self.conn.execute(
            "insert into runs (source_id) values (%s) returning id", (source_id,)
        ).fetchone()["id"]

        # 조각 없는 단일 컷 — 봉투가 곧 길이다.
        plain = self.conn.execute(
            "insert into clips (run_id, segment_id, start_sec, end_sec) values (%s, %s, 10, 42)"
            " returning id", (run_id, segment_id)
        ).fetchone()["id"]
        # 조합 클립 — 봉투(12:30~41:00)와 실제 길이(30초)가 다르다.
        # 🔴 run 을 따로 만든다. `uq_clips_run_segment` 가 한 run 안에서 같은 구간의 클립을 하나로
        # 막는다 — 버튼을 두 번 눌러 중복이 쌓이는 걸 막는 제약이라 여기서도 지켜야 한다.
        other_run = self.conn.execute(
            "insert into runs (source_id) values (%s) returning id", (source_id,)
        ).fetchone()["id"]
        combo = self.conn.execute(
            "insert into clips (run_id, segment_id, start_sec, end_sec) values (%s, %s, 750, 2460)"
            " returning id", (other_run, segment_id)
        ).fetchone()["id"]
        for ordinal, (start, end) in enumerate([(750, 770), (2450, 2460)]):
            self.conn.execute(
                "insert into clip_parts (clip_id, ordinal, segment_id, start_sec, end_sec,"
                " start_utterance_idx, end_utterance_idx) values (%s, %s, %s, %s, %s, 0, 1)",
                (combo, ordinal, segment_id, start, end),
            )
        self.conn.commit()

        # 003 마이그레이션과 같은 문장.
        self.conn.execute(
            """update clips c set total_sec = c.end_sec - c.start_sec
               where c.total_sec is null
                 and not exists (select 1 from clip_parts p where p.clip_id = c.id)"""
        )
        self.conn.commit()
        lengths = {
            r["id"]: r["total_sec"]
            for r in self.conn.execute("select id, total_sec from clips")
        }
        self.assertEqual(lengths[plain], 32.0)
        # 🔴 조합 클립은 그대로 null 이어야 한다 — 봉투(1710초)를 길이로 적으면 거짓이 된다.
        self.assertIsNone(lengths[combo])


if __name__ == "__main__":
    unittest.main()
