"""미디어 삭제·경로 가드 검증.

🔴 삭제 API 는 경로 탈출이 곧 임의 파일 삭제다. 그리고 파생물을 안 지우면 화면에서만
사라지고 디스크는 그대로 차 있어 아무도 눈치채지 못한다. 둘 다 여기서 고정한다.
"""

import tempfile
import unittest
from pathlib import Path

from shorts_maker import media
from shorts_maker.db import store

from .support import make_config


class MediaTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        root = Path(self._dir.name)
        self.cfg = make_config(root)
        self.cfg.source_dir.mkdir(parents=True)
        self.cfg.work_dir.mkdir(parents=True, exist_ok=True)
        self.conn = store.connect(self.cfg.db_path)
        store.apply_schema(self.conn)
        self.outside = root / "precious.txt"
        self.outside.write_text("건드리면 안 됨")

    def tearDown(self) -> None:
        self.conn.close()
        self._dir.cleanup()

    def video(self, name: str, size: int = 1024) -> Path:
        path = self.cfg.source_dir / name
        path.write_bytes(b"\0" * size)
        return path

    def register(self, path: Path) -> int:
        cursor = self.conn.execute(
            """insert into sources (title, content_type, path, duration_sec, fingerprint)
               values ('강연', 'LECTURE', ?, 1800, ?)""",
            (str(path), f"sha256:{path.name}"),
        )
        self.conn.commit()
        return cursor.lastrowid


class ResolveTest(MediaTestCase):
    def test_rejects_path_separators(self):
        for name in ("../precious.txt", "sub/x.mp4", "a\\b.mp4", "..", ""):
            with self.subTest(name=name):
                with self.assertRaises(media.MediaError):
                    media.resolve(self.cfg, name)

    def test_accepts_a_plain_filename(self):
        self.assertEqual(media.resolve(self.cfg, "a.mp4"), (self.cfg.source_dir / "a.mp4").resolve())


class ListingTest(MediaTestCase):
    def test_shows_unregistered_files_too(self):
        # scp 로 올려두고 등록 안 한 파일도 디스크를 먹는다 — 목록에서 빠지면 안 된다.
        self.video("orphan.mp4")
        registered = self.video("known.mp4")
        self.register(registered)
        items = {i["name"]: i for i in media.listing(self.conn, self.cfg)}
        self.assertIsNone(items["orphan.mp4"]["sourceId"])
        self.assertIsNotNone(items["known.mp4"]["sourceId"])

    def test_ignores_non_video_files(self):
        (self.cfg.source_dir / "notes.txt").write_text("x")
        self.video("a.mp4")
        self.assertEqual([i["name"] for i in media.listing(self.conn, self.cfg)], ["a.mp4"])


class DeleteTest(MediaTestCase):
    def test_removes_the_file_and_reports_freed_space(self):
        self.video("a.mp4", size=4096)
        result = media.delete(self.conn, self.cfg, "a.mp4")
        self.assertFalse((self.cfg.source_dir / "a.mp4").exists())
        self.assertEqual(result.freed_bytes, 4096)

    def test_removes_derived_files_and_rows(self):
        path = self.video("a.mp4")
        source_id = self.register(path)
        chunk_file = self.cfg.work_dir / "source1_chunk0.wav"
        chunk_file.write_bytes(b"\0" * 512)
        chunk_id = self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path) values (?, 0, 0, 1800, ?)",
            (source_id, str(chunk_file)),
        ).lastrowid
        segment_id = self.conn.execute(
            """insert into segments (chunk_id, idx, start_sec, end_sec,
                                     start_utterance_idx, end_utterance_idx)
               values (?, 0, 0, 300, 0, 5)""",
            (chunk_id,),
        ).lastrowid
        run_id = self.conn.execute(
            "insert into runs (source_id) values (?)", (source_id,)
        ).lastrowid
        clip_dir = self.cfg.work_dir / "clips"
        clip_dir.mkdir(parents=True)
        clip_file = clip_dir / "clip001.mp4"
        clip_file.write_bytes(b"\0" * 256)
        (clip_dir / "clip001.ass").write_text("자막")
        preview_dir = self.cfg.work_dir / "previews"
        preview_dir.mkdir(parents=True)
        preview = preview_dir / f"segment{segment_id:04d}.mp4"
        preview.write_bytes(b"\0" * 128)
        self.conn.execute(
            "insert into clips (run_id, segment_id, start_sec, end_sec, path) values (?, ?, 0, 45, ?)",
            (run_id, segment_id, str(clip_file)),
        )
        self.conn.commit()

        result = media.delete(self.conn, self.cfg, "a.mp4")

        for leftover in (path, chunk_file, clip_file, preview, clip_dir / "clip001.ass"):
            with self.subTest(path=leftover.name):
                self.assertFalse(leftover.exists(), f"{leftover} 가 남았다")
        counts = store.row_counts(self.conn)
        for table in ("sources", "chunks", "segments", "runs", "clips"):
            self.assertEqual(counts[table], 0, table)
        self.assertEqual(result.source_id, source_id)

    def test_never_touches_files_outside_the_managed_directories(self):
        # DB 에 이상한 경로가 들어 있어도 밖을 지우면 안 된다.
        path = self.video("a.mp4")
        source_id = self.register(path)
        self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path) values (?, 0, 0, 10, ?)",
            (source_id, str(self.outside)),
        )
        self.conn.commit()
        media.delete(self.conn, self.cfg, "a.mp4")
        self.assertTrue(self.outside.exists(), "관리 디렉토리 밖 파일이 지워졌다")

    def test_deletes_an_unregistered_file_without_a_db_row(self):
        self.video("orphan.mp4")
        result = media.delete(self.conn, self.cfg, "orphan.mp4")
        self.assertIsNone(result.source_id)
        self.assertFalse((self.cfg.source_dir / "orphan.mp4").exists())


if __name__ == "__main__":
    unittest.main()
