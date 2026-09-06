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


if __name__ == "__main__":
    unittest.main()
