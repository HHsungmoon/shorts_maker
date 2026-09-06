"""주제 분할이 중간부터 이어가는가.

🔴 LLM 호출이 청크당 한 번이고 무료 등급은 **하루 20회**다. 2026-09-06에 4조각 중 2조각에서
할당량이 떨어져 멈췄는데, 그때 처음부터 다시 하면 남은 할당량을 앞부분에 다 쓴다.
그래서 끝난 청크는 건너뛴다 — 단, 번호가 이어져야 하므로 **빈 청크 뒤는 전부 다시 만든다.**
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shorts_maker.pipeline import segmentation

from ..support import DbTestCase, make_config


class ResumeTest(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = make_config(Path(self._tmp.name))
        self.source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status)"
            " values ('강연', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE') returning id"
        ).fetchone()["id"]
        self.chunks = []
        for position in range(3):
            chunk_id = self.conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path)"
                " values (%s, %s, %s, %s, %s) returning id",
                (self.source_id, position, position * 100, position * 100 + 100, f"c{position}.wav"),
            ).fetchone()["id"]
            self.chunks.append(chunk_id)
            for idx in range(4):
                self.conn.execute(
                    "insert into utterances (chunk_id, idx, start_sec, end_sec, text)"
                    " values (%s, %s, %s, %s, %s)",
                    (chunk_id, idx, position * 100 + idx * 10, position * 100 + idx * 10 + 10, "발화"),
                )
        self.conn.commit()

    def seed(self, chunk_id: int, start_idx: int, count: int = 2) -> None:
        """이미 끝난 청크를 흉내낸다."""
        for offset in range(count):
            self.conn.execute(
                """insert into segments (chunk_id, idx, start_sec, end_sec, description,
                                         start_utterance_idx, end_utterance_idx)
                   values (%s, %s, %s, %s, %s, %s, %s)""",
                (chunk_id, start_idx + offset, offset * 10, offset * 10 + 10, "기존 구간", 0, 1),
            )
        self.conn.commit()

    def fake_run_for_chunk(self):
        """호출된 청크를 기록하고 구간 2개를 만든다. 실제 LLM 은 부르지 않는다."""
        called: list[int] = []

        def run(conn, cfg, chunk_id, force, idx_offset=0):
            called.append(chunk_id)
            for offset in range(2):
                conn.execute(
                    """insert into segments (chunk_id, idx, start_sec, end_sec, description,
                                             start_utterance_idx, end_utterance_idx)
                       values (%s, %s, %s, %s, %s, %s, %s)""",
                    (chunk_id, idx_offset + offset, offset * 10, offset * 10 + 10, "새 구간", 0, 1),
                )
            conn.commit()
            return [object(), object()]

        return called, run

    def idx_by_chunk(self) -> dict:
        rows = self.conn.execute(
            """select sg.chunk_id, array_agg(sg.idx order by sg.idx) as idxs from segments sg
               join chunks ch on ch.id = sg.chunk_id where ch.source_id = %s group by sg.chunk_id""",
            (self.source_id,),
        ).fetchall()
        return {r["chunk_id"]: r["idxs"] for r in rows}

    def test_finished_chunks_are_skipped(self):
        self.seed(self.chunks[0], 0)
        self.seed(self.chunks[1], 2)
        called, run = self.fake_run_for_chunk()
        with mock.patch.object(segmentation, "run_for_chunk", side_effect=run):
            segmentation.run_for_source(self.conn, self.cfg, self.source_id)
        # 마지막 청크만 새로 돌았다 — 앞의 둘은 LLM 을 부르지 않는다.
        self.assertEqual(called, [self.chunks[2]])

    def test_numbering_continues_from_what_was_already_there(self):
        self.seed(self.chunks[0], 0)
        self.seed(self.chunks[1], 2)
        called, run = self.fake_run_for_chunk()
        with mock.patch.object(segmentation, "run_for_chunk", side_effect=run):
            segmentation.run_for_source(self.conn, self.cfg, self.source_id)
        by_chunk = self.idx_by_chunk()
        self.assertEqual(by_chunk[self.chunks[2]], [4, 5])
        # 🔴 소스 전체에서 번호가 유일해야 rank 가 지목한 구간을 되찾을 수 있다.
        everything = [idx for idxs in by_chunk.values() for idx in idxs]
        self.assertEqual(sorted(everything), list(range(len(everything))))

    def test_a_gap_forces_everything_after_it_to_be_redone(self):
        # 가운데가 비면 번호를 이어 붙일 수 없다 — 뒤쪽은 버리고 다시 만든다.
        self.seed(self.chunks[0], 0)
        self.seed(self.chunks[2], 99)   # 번호가 어긋난 채 남아 있는 상태
        called, run = self.fake_run_for_chunk()
        with mock.patch.object(segmentation, "run_for_chunk", side_effect=run):
            segmentation.run_for_source(self.conn, self.cfg, self.source_id)
        self.assertEqual(called, [self.chunks[1], self.chunks[2]])
        everything = sorted(idx for idxs in self.idx_by_chunk().values() for idx in idxs)
        self.assertEqual(everything, list(range(len(everything))))

    def test_force_redoes_everything(self):
        self.seed(self.chunks[0], 0)
        self.seed(self.chunks[1], 2)
        called, run = self.fake_run_for_chunk()
        with mock.patch.object(segmentation, "run_for_chunk", side_effect=run):
            segmentation.run_for_source(self.conn, self.cfg, self.source_id, force=True)
        self.assertEqual(called, self.chunks)

    def test_a_failure_partway_keeps_what_finished(self):
        # 할당량이 떨어져 멈춘 상황. 앞의 결과가 남아야 다시 눌렀을 때 이어갈 수 있다.
        called, run = self.fake_run_for_chunk()

        def fail_on_third(conn, cfg, chunk_id, force, idx_offset=0):
            if chunk_id == self.chunks[2]:
                raise segmentation.SegmentationError("할당량 초과")
            return run(conn, cfg, chunk_id, force, idx_offset)

        with mock.patch.object(segmentation, "run_for_chunk", side_effect=fail_on_third):
            with self.assertRaises(segmentation.SegmentationError):
                segmentation.run_for_source(self.conn, self.cfg, self.source_id)
        self.conn.rollback()
        by_chunk = self.idx_by_chunk()
        self.assertEqual(sorted(by_chunk), sorted(self.chunks[:2]))
        self.assertEqual(by_chunk[self.chunks[0]], [0, 1])
        self.assertEqual(by_chunk[self.chunks[1]], [2, 3])


if __name__ == "__main__":
    unittest.main()
