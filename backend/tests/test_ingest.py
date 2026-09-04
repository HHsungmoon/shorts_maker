"""입력 경로 가드 검증.

C3 에서 관리자 페이지가 이 함수를 그대로 쓴다. 그때 뚫리면 관리자가 준 문자열로 서버의
아무 파일이나 읽히므로, 방어선을 지금 테스트로 고정한다.
"""

import tempfile
import unittest
from pathlib import Path

from shorts_maker import ingest

from .support import make_config


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


if __name__ == "__main__":
    unittest.main()
