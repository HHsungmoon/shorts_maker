"""등록·청크 추출.

- 입력 경로 가드: C3 에서 관리자 페이지가 이 함수를 그대로 쓴다. 그때 뚫리면 관리자가 준
  문자열로 서버의 아무 파일이나 읽히므로, 방어선을 테스트로 고정한다.
- 원본 상태 전이(RUNNING → DONE/FAILED)와 청크 **교체**(M0, 2026-09-06).
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from psycopg.types.json import Jsonb

from shorts_maker.adapters import ffmpeg
from shorts_maker.pipeline import ingest
from shorts_maker.db import store

from ..support import DbTestCase, make_config


class ResolveSourcePathTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        root = Path(self._dir.name)
        self.source_dir = root / "sources"
        self.source_dir.mkdir()
        (self.source_dir / "ok.mp4").write_bytes(b"x")
        (self.source_dir / "nested").mkdir()
        (self.source_dir / "nested" / "deep.mp4").write_bytes(b"x")
        self.outside = root / "secret.txt"
        self.outside.write_bytes(b"x")

        self.cfg = make_config(root, source_dir=self.source_dir)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_accepts_relative_path_inside_root(self):
        self.assertEqual(
            ingest.resolve_source_path(self.cfg, "ok.mp4"), (self.source_dir / "ok.mp4").resolve()
        )

    def test_accepts_nested_path(self):
        ingest.resolve_source_path(self.cfg, "nested/deep.mp4")

    def test_rejects_absolute_path_outside_root(self):
        with self.assertRaises(ingest.IngestError):
            ingest.resolve_source_path(self.cfg, str(self.outside))

    def test_rejects_parent_traversal(self):
        with self.assertRaises(ingest.IngestError):
            ingest.resolve_source_path(self.cfg, "../secret.txt")

    def test_rejects_traversal_that_returns_inside(self):
        # 밖으로 나갔다 들어오는 경로도 결국 안이면 통과해야 한다 — 과잉 차단이 아닌지 확인.
        ingest.resolve_source_path(self.cfg, "nested/../ok.mp4")

    def test_rejects_missing_file(self):
        with self.assertRaises(ingest.IngestError):
            ingest.resolve_source_path(self.cfg, "nope.mp4")


class _IngestDbCase(DbTestCase):
    """원본 파일과 비운 테스트 DB(`self.conn`, Postgres) 를 갖춘 케이스. ffmpeg 는 부르지 않는다 —
    여기서 보는 건 행의 상태다. ingest 함수들은 안에서 commit 하는데 같은 연결이라 그대로 보인다."""

    def setUp(self) -> None:
        super().setUp()
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        root = Path(self._dir.name)
        self.cfg = make_config(root)
        self.cfg.source_dir.mkdir(parents=True)
        self.cfg.work_dir.mkdir(parents=True)
        self.video = self.cfg.source_dir / "a.mp4"
        self.video.write_bytes(b"video-a")
        patcher = mock.patch.object(ffmpeg, "duration_sec", return_value=120.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def source(self, source_id: int) -> dict:
        return self.conn.execute("select * from sources where id = %s", (source_id,)).fetchone()


class SourceLifecycleTest(_IngestDbCase):
    """등록은 RUNNING → DONE/FAILED 다. 다운로드가 수 분인데 행이 그동안 없으면 화면에도 없고,
    서버가 죽었을 때 정리할 대상도 없다."""

    def test_begin_creates_a_running_row_before_the_file_is_hashed(self):
        begun = ingest.begin_source(self.conn, self.cfg, self.video, "강연", "LECTURE", None, None)
        row = self.source(begun.source_id)
        self.assertFalse(begun.reused)
        self.assertEqual(row["status"], "RUNNING")
        self.assertTrue(row["fingerprint"].startswith("pending:"))
        self.assertIsNone(row["duration_sec"])
        self.assertEqual(row["path"], "a.mp4")  # 상대경로

    def test_finish_fills_duration_and_fingerprint_and_marks_done(self):
        begun = ingest.begin_source(self.conn, self.cfg, self.video, "강연", "LECTURE", None, None)
        ingest.finish_source(self.conn, self.cfg, begun.source_id, self.video)
        row = self.source(begun.source_id)
        self.assertEqual(row["status"], "DONE")
        self.assertEqual(row["duration_sec"], 120.0)
        self.assertTrue(row["fingerprint"].startswith("sha256:"))

    def test_a_failure_while_finishing_is_recorded_not_hidden(self):
        begun = ingest.begin_source(self.conn, self.cfg, self.video, "강연", "LECTURE", None, None)
        with mock.patch.object(ffmpeg, "duration_sec", side_effect=RuntimeError("ffprobe 죽음")):
            with self.assertRaises(RuntimeError):
                ingest.finish_source(self.conn, self.cfg, begun.source_id, self.video)
        row = self.source(begun.source_id)
        self.assertEqual(row["status"], "FAILED")
        self.assertIn("ffprobe 죽음", row["error"])

    def test_a_failed_row_is_taken_over_by_the_next_attempt_with_the_same_id(self):
        # 지우고 새로 만들면 id 가 바뀐다 — 화면이 가리키던 곳이 사라진다.
        first = ingest.begin_source(self.conn, self.cfg, self.video, "강연", "LECTURE", None, None)
        ingest.fail_source(self.conn, first.source_id, "네트워크")
        second = ingest.begin_source(self.conn, self.cfg, self.video, "강연 2", "LECTURE", None, None)
        self.assertEqual(second.source_id, first.source_id)
        self.assertFalse(second.reused)
        row = self.source(second.source_id)
        self.assertEqual((row["status"], row["error"], row["title"]), ("RUNNING", None, "강연 2"))

    def test_a_done_row_is_reused_not_recreated(self):
        first = ingest.begin_source(self.conn, self.cfg, self.video, "강연", "LECTURE", None, None)
        ingest.finish_source(self.conn, self.cfg, first.source_id, self.video)
        second = ingest.begin_source(self.conn, self.cfg, self.video, "다른 제목", "LECTURE", None, None)
        self.assertTrue(second.reused)
        self.assertEqual(second.source_id, first.source_id)
        self.assertEqual(self.source(first.source_id)["title"], "강연")  # 덮어쓰지 않는다

    def test_the_same_content_under_another_name_is_refused_and_leaves_no_row(self):
        ingest.add_source(self.conn, self.cfg, "a.mp4", "강연", "LECTURE", None, None)
        copy = self.cfg.source_dir / "b.mp4"
        copy.write_bytes(b"video-a")
        with self.assertRaises(ingest.IngestError):
            ingest.add_source(self.conn, self.cfg, "b.mp4", "복사본", "LECTURE", None, None)
        # FAILED 로 남기면 목록에 실체 없는 행이 생긴다.
        self.assertEqual(store.row_counts(self.conn)["sources"], 1)

    def test_add_source_refuses_a_path_that_is_already_done(self):
        ingest.add_source(self.conn, self.cfg, "a.mp4", "강연", "LECTURE", None, None)
        with self.assertRaises(ingest.IngestError):
            ingest.add_source(self.conn, self.cfg, "a.mp4", "강연", "LECTURE", None, None)


class ChunkReplaceTest(_IngestDbCase):
    """LECTURE 는 소스당 청크 1개. "다시 추출"은 옆에 하나 더 만드는 게 아니라 **교체**다."""

    def setUp(self) -> None:
        super().setUp()
        self.source_id = ingest.add_source(self.conn, self.cfg, "a.mp4", "강연", "LECTURE", None, None)

        def fake_extract(src: str, out: str, start: float, end: float) -> None:
            Path(out).write_bytes(b"wav")

        patcher = mock.patch.object(ffmpeg, "extract_audio", side_effect=fake_extract)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _grow_a_tree(self, chunk_id: int) -> Path:
        """청크 아래에 발화·구간·run·클립(+파일)을 심는다. 교체가 이걸 다 치워야 한다."""
        self.conn.execute(
            "insert into utterances (chunk_id, idx, start_sec, end_sec, text) values (%s, 0, 0, 5, '안녕')",
            (chunk_id,),
        )
        segment_id = self.conn.execute(
            "insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx, end_utterance_idx)"
            " values (%s, 0, 0, 5, 0, 0) returning id",
            (chunk_id,),
        ).fetchone()["id"]
        run_id = self.conn.execute(
            "insert into runs (source_id, status, ranked) values (%s, 'DONE', %s) returning id",
            (self.source_id, Jsonb({})),
        ).fetchone()["id"]
        clip_file = self.cfg.work_dir / "clips" / "clip001.mp4"
        clip_file.parent.mkdir(parents=True, exist_ok=True)
        clip_file.write_bytes(b"mp4")
        self.conn.execute(
            "insert into clips (run_id, segment_id, start_sec, end_sec, path, rendered) values (%s, %s, 0, 5, %s, true)",
            (run_id, segment_id, self.cfg.store_work(clip_file)),
        )
        self.conn.commit()
        return clip_file

    def test_a_second_chunk_is_refused_without_replace(self):
        ingest.add_chunk(self.conn, self.cfg, self.source_id, 0, 60)
        with self.assertRaises(ingest.IngestError):
            ingest.add_chunk(self.conn, self.cfg, self.source_id, 0, 90)
        self.assertEqual(store.row_counts(self.conn)["chunks"], 1)

    def test_replace_keeps_the_idx_and_removes_everything_below(self):
        first = ingest.add_chunk(self.conn, self.cfg, self.source_id, 0, 60)
        clip_file = self._grow_a_tree(first)

        second = ingest.add_chunk(self.conn, self.cfg, self.source_id, 0, 90, replace=True)

        counts = store.row_counts(self.conn)
        self.assertEqual(counts["chunks"], 1)
        self.assertEqual((counts["utterances"], counts["segments"], counts["runs"], counts["clips"]), (0, 0, 0, 0))
        row = self.conn.execute("select * from chunks where id = %s", (second,)).fetchone()
        self.assertEqual((row["idx"], row["end_sec"]), (0, 90.0))
        self.assertFalse(clip_file.exists())
        # 새 wav 는 옛것과 같은 이름이다 — 파생물 정리가 그것까지 지우면 안 된다.
        self.assertTrue(self.cfg.work_file(row["path"]).exists())

    def test_a_failed_extraction_leaves_the_old_chunk_intact(self):
        # 몇 분짜리 STT 를 실패한 재추출 때문에 잃지 않는다.
        first = ingest.add_chunk(self.conn, self.cfg, self.source_id, 0, 60)
        self._grow_a_tree(first)
        with mock.patch.object(ffmpeg, "extract_audio", side_effect=ffmpeg.FfmpegError("디스크 꽉")):
            with self.assertRaises(ffmpeg.FfmpegError):
                ingest.add_chunk(self.conn, self.cfg, self.source_id, 0, 90, replace=True)
        counts = store.row_counts(self.conn)
        self.assertEqual((counts["chunks"], counts["utterances"], counts["runs"]), (1, 1, 1))



class PlanChunksTest(unittest.TestCase):
    """🔴 청크는 **메모리 상한** 때문에 나눈다. 95분을 한 번에 전사하다 컨테이너 한도(2.93GB)를
    넘어 OOM 으로 죽은 게 이 함수가 생긴 이유다(2026-09-06 실측: 30분 = 1.4GB)."""

    def test_a_short_range_stays_one_piece(self):
        self.assertEqual(ingest.plan_chunks(0, 600, 1500), [(0.0, 600.0)])
        self.assertEqual(ingest.plan_chunks(0, 1500, 1500), [(0.0, 1500.0)])

    def test_a_range_that_does_not_start_at_zero_is_respected(self):
        # 사용자가 정하는 건 범위다 — 영상 전체가 아니라 "20분부터 60분까지" 를 고를 수 있다.
        ranges = ingest.plan_chunks(1200, 3600, 1500)
        self.assertEqual(ranges[0][0], 1200)
        self.assertEqual(ranges[-1][1], 3600)
        self.assertEqual(len(ranges), 2)

    def test_pieces_are_even_rather_than_max_sized(self):
        # 🔴 95분을 30분 상한으로 나누면 30/30/30/5 가 아니라 24×4 다. 마지막만 짧으면 그 조각의
        # 전사가 유난히 빨리 끝나 진행률이 거짓말을 한다.
        ranges = ingest.plan_chunks(0, 5736, 1800)
        self.assertEqual(len(ranges), 4)
        lengths = [end - start for start, end in ranges]
        self.assertLess(max(lengths), 1800)
        self.assertLess(max(lengths) - min(lengths), 1.0)

    def test_a_long_video_is_split_into_even_pieces(self):
        ranges = ingest.plan_chunks(0, 5736, 1500)
        self.assertEqual(len(ranges), 4)
        for start, end in ranges:
            self.assertLessEqual(end - start, 1500)
        # 마지막만 짧은 것보다 고르게 나뉘어야 STT 시간이 예측 가능하다.
        lengths = [end - start for start, end in ranges]
        self.assertLess(max(lengths) - min(lengths), 1.0)

    def test_the_pieces_cover_everything_without_gaps_or_overlap(self):
        ranges = ingest.plan_chunks(0, 5736, 1500)
        self.assertEqual(ranges[0][0], 0.0)
        self.assertEqual(ranges[-1][1], 5736)
        for (_, end), (next_start, _) in zip(ranges, ranges[1:]):
            self.assertEqual(end, next_start)

    def test_boundaries_snap_to_nearby_silence(self):
        # 고정 길이로 자르면 문장 한복판에서 끊겨 그 발화가 양쪽에서 반토막 난다.
        plain = ingest.plan_chunks(0, 5736, 1500)
        snapped = ingest.plan_chunks(0, 5736, 1500, [(1420.0, 1424.0)])
        self.assertNotEqual(plain[0][1], snapped[0][1])
        self.assertEqual(snapped[0][1], 1422.0)

    def test_silence_too_far_from_the_target_is_ignored(self):
        # 멀리서 당기면 청크 길이가 들쭉날쭉해져 메모리 상한의 의미가 사라진다.
        plain = ingest.plan_chunks(0, 5736, 1500)
        far = ingest.plan_chunks(0, 5736, 1500, [(100.0, 140.0)])
        self.assertEqual(plain, far)

    def test_the_last_boundary_never_exceeds_the_duration(self):
        # 🔴 반올림하면 round(5736.048617, 3) = 5736.049 로 길이를 넘어 add_chunk 가 거부한다.
        for duration in (5736.048617, 1500.0004, 3000.9999):
            with self.subTest(duration=duration):
                ranges = ingest.plan_chunks(0, duration, 1500)
                self.assertLessEqual(ranges[-1][1], duration)
                self.assertEqual(ranges[-1][1], duration)

    def test_an_empty_range_is_refused(self):
        for start, end in ((0, 0), (100, 100), (500, 100)):
            with self.subTest(start=start, end=end):
                with self.assertRaises(ingest.IngestError):
                    ingest.plan_chunks(start, end, 1500)


class AddChunksTest(_IngestDbCase):
    """소스 하나 = 사용자에게는 "구간 추출" 한 번. 조각이 몇 개인지는 내부 사정이다."""

    def setUp(self) -> None:
        super().setUp()

        def fake_extract(src: str, out: str, start: float, end: float) -> None:
            Path(out).write_bytes(b"wav")

        patcher = mock.patch.object(ffmpeg, "extract_audio", side_effect=fake_extract)
        patcher.start()
        self.addCleanup(patcher.stop)
        silence = mock.patch.object(ffmpeg, "detect_silences", return_value=[])
        silence.start()
        self.addCleanup(silence.stop)

    def source_of(self, duration: float) -> int:
        with mock.patch.object(ffmpeg, "duration_sec", return_value=duration):
            return ingest.add_source(self.conn, self.cfg, "a.mp4", "강연", "LECTURE", None, None)

    def chunks(self, source_id: int) -> list[dict]:
        return [
            dict(r) for r in self.conn.execute(
                "select idx, start_sec, end_sec, path from chunks where source_id = %s order by idx",
                (source_id,),
            )
        ]

    def test_a_short_video_makes_one_chunk(self):
        source_id = self.source_of(600)
        self.assertEqual(len(ingest.add_chunks(self.conn, self.cfg, source_id)), 1)

    def test_a_long_video_makes_several_with_distinct_files(self):
        source_id = self.source_of(5736)
        made = ingest.add_chunks(self.conn, self.cfg, source_id)
        self.assertEqual(len(made), 4)
        rows = self.chunks(source_id)
        self.assertEqual([r["idx"] for r in rows], [0, 1, 2, 3])
        # 🔴 파일명이 겹치면 뒤 조각이 앞 조각을 덮어써 전사가 통째로 틀어진다.
        self.assertEqual(len({r["path"] for r in rows}), 4)

    def test_running_it_twice_is_refused_without_replace(self):
        source_id = self.source_of(5736)
        ingest.add_chunks(self.conn, self.cfg, source_id)
        with self.assertRaises(ingest.IngestError):
            ingest.add_chunks(self.conn, self.cfg, source_id)
        self.assertEqual(len(self.chunks(source_id)), 4)

    def test_replace_starts_over_instead_of_appending(self):
        source_id = self.source_of(5736)
        ingest.add_chunks(self.conn, self.cfg, source_id)
        ingest.add_chunks(self.conn, self.cfg, source_id, replace=True)
        self.assertEqual([r["idx"] for r in self.chunks(source_id)], [0, 1, 2, 3])

    def test_a_source_without_a_duration_is_refused(self):
        source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status)"
            " values ('x', 'LECTURE', 'a.mp4', 'sha256:x', 'RUNNING') returning id"
        ).fetchone()["id"]
        self.conn.commit()
        with self.assertRaises(ingest.IngestError):
            ingest.add_chunks(self.conn, self.cfg, source_id)



class SourceLockTest(_IngestDbCase):
    """🔴 전사가 도는 중에 재분할이 들어오면 청크가 사라져 그 전사가 외래키 위반으로 죽는다.

    2026-09-06에 실제로 당했다 — 4조각 전사 중에 화면에서 "다시 추출" 을 눌렀다. API 는 잡 큐가
    워커 하나라 동시 실행이 없지만 CLI 는 그 큐를 우회하므로 DB 에 건다.
    """

    def setUp(self) -> None:
        super().setUp()

        def fake_extract(src: str, out: str, start: float, end: float) -> None:
            Path(out).write_bytes(b"wav")

        for target, kwargs in (("extract_audio", {"side_effect": fake_extract}),
                               ("detect_silences", {"return_value": []})):
            patcher = mock.patch.object(ffmpeg, target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        with mock.patch.object(ffmpeg, "duration_sec", return_value=1200.0):
            self.source_id = ingest.add_source(
                self.conn, self.cfg, "a.mp4", "강연", "LECTURE", None, None
            )

    def test_rechunking_is_refused_while_another_connection_holds_the_source(self):
        # 다른 연결이 잡고 있는 상태를 흉내낸다 — 어드바이저리 락은 **세션** 단위다.
        with store.connect(self.url) as other:
            with store.source_lock(other, self.source_id, "전사"):
                with self.assertRaises(store.SourceBusy):
                    ingest.add_chunks(self.conn, self.cfg, self.source_id)
        # 락이 풀리면 다시 된다.
        self.assertEqual(len(ingest.add_chunks(self.conn, self.cfg, self.source_id)), 1)

    def test_the_lock_is_released_even_when_the_body_raises(self):
        with self.assertRaises(ValueError):
            with store.source_lock(self.conn, self.source_id):
                raise ValueError("실패")
        # 🔴 락은 커밋/롤백으로 풀리지 않는다. finally 에서 풀지 않으면 그 세션이 영원히 잡고 있다.
        with store.connect(self.url) as other:
            self.assertFalse(store.source_is_busy(other, self.source_id))

    def test_busy_is_visible_to_another_connection(self):
        # 화면이 버튼을 미리 잠그는 근거다.
        with store.connect(self.url) as other:
            self.assertFalse(store.source_is_busy(other, self.source_id))
            with store.source_lock(self.conn, self.source_id):
                self.assertTrue(store.source_is_busy(other, self.source_id))
            self.assertFalse(store.source_is_busy(other, self.source_id))


if __name__ == "__main__":
    unittest.main()
