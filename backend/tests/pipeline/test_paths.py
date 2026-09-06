"""DB 에 저장되는 파일 경로가 **상대경로**인가.

절대경로를 넣던 시절에는 레포 디렉터리를 옮기는 것만으로 원본·청크·클립 다섯 행이 전부 깨졌다
(2026-09-04). 호스트(`~/dev/...`)와 컨테이너(`/sources`, `/data/work`)는 애초에 경로가 다르므로,
DB 를 그대로 들고 다니려면 저장 시점에 접두어를 벗겨야 한다. 옛 행의 절대경로는 그대로
읽혀야 한다 — 폴백까지 여기서 고정한다.
"""

import tempfile
import unittest
from pathlib import Path

from shorts_maker.pipeline import media
from shorts_maker.db import store

from ..support import DbTestCase, make_config


class StoreAndResolveTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self.cfg = make_config(self.root)
        self.cfg.source_dir.mkdir(parents=True)
        (self.cfg.work_dir / "clips").mkdir(parents=True)

    def tearDown(self):
        self._dir.cleanup()

    def test_source_is_stored_relative_to_source_dir(self):
        stored = self.cfg.store_source(self.cfg.source_dir / "a.mp4")
        self.assertEqual(stored, "a.mp4")
        self.assertEqual(self.cfg.source_file(stored), self.cfg.source_dir / "a.mp4")

    def test_work_files_keep_their_subdirectory(self):
        stored = self.cfg.store_work(self.cfg.work_dir / "clips" / "clip001.mp4")
        self.assertEqual(stored, "clips/clip001.mp4")
        self.assertEqual(self.cfg.work_file(stored), self.cfg.work_dir / "clips" / "clip001.mp4")

    def test_the_stored_value_survives_a_relocation(self):
        # 같은 상대경로를 다른 root 의 Config 로 읽어도 그 root 아래를 가리켜야 한다.
        stored = self.cfg.store_source(self.cfg.source_dir / "a.mp4")
        elsewhere = make_config(self.root / "moved")
        self.assertEqual(elsewhere.source_file(stored), elsewhere.source_dir / "a.mp4")

    def test_an_absolute_path_is_read_as_is(self):
        # 상대경로 전환 이전에 만든 행. 조용히 root 아래로 붙여 엉뚱한 파일을 가리키면 안 된다.
        outside = self.root / "legacy.mp4"
        self.assertEqual(self.cfg.source_file(str(outside)), outside)
        self.assertEqual(self.cfg.work_file(str(outside)), outside)

    def test_a_file_outside_the_root_is_not_made_relative(self):
        outside = self.root / "elsewhere.mp4"
        self.assertEqual(self.cfg.store_source(outside), str(outside.resolve()))


class DeleteFindsRelativeRowsTest(DbTestCase):
    """삭제가 상대경로로 등록된 원본을 찾아 DB 행까지 지우는가.

    여기서 못 찾으면 파일만 지워지고 행은 남는다 — media.find_source_id 가 경고한 최악의 상태.
    DB 는 비운 테스트 Postgres(`self.conn`), 파일은 임시 디렉터리.
    """

    def setUp(self):
        super().setUp()
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.cfg = make_config(Path(self._dir.name))
        self.cfg.source_dir.mkdir(parents=True)
        self.cfg.work_dir.mkdir(parents=True)

    def test_relative_row_is_found_and_removed(self):
        (self.cfg.source_dir / "a.mp4").write_bytes(b"\0")
        self.conn.execute(
            """insert into sources (title, content_type, path, fingerprint)
               values ('강연', 'LECTURE', 'a.mp4', 'sha256:a')"""
        )
        self.conn.commit()
        result = media.delete(self.conn, self.cfg, "a.mp4")
        self.assertIsNotNone(result.source_id)
        self.assertEqual(store.row_counts(self.conn)["sources"], 0)

    def test_listing_marks_a_relative_row_as_registered(self):
        (self.cfg.source_dir / "a.mp4").write_bytes(b"\0")
        self.conn.execute(
            """insert into sources (title, content_type, path, fingerprint)
               values ('강연', 'LECTURE', 'a.mp4', 'sha256:a')"""
        )
        self.conn.commit()
        items = media.listing(self.conn, self.cfg)
        self.assertIsNotNone(items[0]["sourceId"])
