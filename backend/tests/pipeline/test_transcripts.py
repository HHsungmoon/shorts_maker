"""대사 두 층 — 보관(transcripts) · 쓰는 것(utterances). tease §5-1.

🔴 지키는 것: ① 쓰는 대사를 갈아 끼우면 구간·클립 조각의 발화 **번호**가 **시각**으로 다시 맞는다 — 번호로
대사를 읽는 컷·추천·답하기가 엉뚱한 문장을 읽지 않게 ② 분석 범위 밖 발화는 담지 않는다 ③ 보관한 대사는
옮겨 담아도 줄지 않는다.
"""

import unittest

from shorts_maker.pipeline import transcripts

from ..support import DbTestCase


def u(idx: int, start: float, end: float) -> dict:
    return {"idx": idx, "start_sec": start, "end_sec": end}


class SpanOfTest(unittest.TestCase):
    UTTERANCES = [u(0, 0, 4), u(1, 4, 9), u(2, 9.8, 12), u(3, 12, 20)]

    def test_utterances_whose_middle_is_inside_are_the_range(self):
        self.assertEqual(transcripts.span_of(self.UTTERANCES, 4, 12), (1, 2))

    def test_a_sentence_starting_a_little_early_still_belongs_to_the_later_segment(self):
        # 자막 문장이 구간 시작(10초)보다 0.2초 일찍 시작했다. 시작 초로 고르면 앞 구간으로 넘어간다.
        self.assertEqual(transcripts.span_of(self.UTTERANCES, 10, 20), (2, 3))

    def test_a_short_range_inside_one_long_utterance_takes_that_utterance(self):
        self.assertEqual(transcripts.span_of(self.UTTERANCES, 14, 15), (3, 3))

    def test_a_range_in_silence_takes_the_nearest(self):
        self.assertEqual(transcripts.span_of([u(0, 0, 2), u(1, 30, 32)], 27, 29), (1, 1))

    def test_no_utterances_cannot_be_mapped(self):
        self.assertIsNone(transcripts.span_of([], 0, 10))


class AssignToChunksTest(unittest.TestCase):
    def test_each_utterance_goes_where_it_starts_and_idx_restarts_per_chunk(self):
        chunks = [{"id": 1, "start_sec": 100, "end_sec": 200}, {"id": 2, "start_sec": 200, "end_sec": 300}]
        utterances = [{"start_sec": s, "end_sec": s + 5, "text": str(s)} for s in (50, 120, 198, 201, 400)]
        by_chunk, outside = transcripts.assign_to_chunks(utterances, chunks)
        self.assertEqual([(x["idx"], x["text"]) for x in by_chunk[1]], [(0, "120"), (1, "198")])
        self.assertEqual([(x["idx"], x["text"]) for x in by_chunk[2]], [(0, "201")])
        self.assertEqual(outside, 2)


class UseTest(DbTestCase):
    def setUp(self):
        super().setUp()
        self.source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status)"
            " values ('강연', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE') returning id"
        ).fetchone()["id"]
        self.chunk_id = self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path) values (%s, 0, 0, 100, 'c.wav') returning id",
            (self.source_id,),
        ).fetchone()["id"]
        # 음성 인식 대사: 문장이 잘게 나뉘어 있다. 구간 하나가 30~60초.
        transcripts.store_rows(self.conn, self.source_id, "whisper", [
            {"start_sec": s, "end_sec": s + 9, "text": f"whisper {s}", "words": None} for s in range(0, 100, 10)
        ], replace=False)
        transcripts.use(self.conn, self.source_id, "whisper")
        # 구간과 그 안의 클립 조각 — 번호는 음성 인식 대사 기준이다(30~60초 = 발화 3..5).
        self.segment_id = self.conn.execute(
            """insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx, end_utterance_idx)
               values (%s, 0, 30, 60, 3, 5) returning id""",
            (self.chunk_id,),
        ).fetchone()["id"]
        run_id = self.conn.execute(
            "insert into runs (source_id, criteria_prompt) values (%s, '기준') returning id", (self.source_id,)
        ).fetchone()["id"]
        clip_id = self.conn.execute(
            """insert into clips (run_id, segment_id, start_sec, end_sec) values (%s, %s, 40, 60) returning id""",
            (run_id, self.segment_id),
        ).fetchone()["id"]
        self.part_id = self.conn.execute(
            """insert into clip_parts (clip_id, ordinal, segment_id, start_sec, end_sec, start_utterance_idx, end_utterance_idx)
               values (%s, 0, %s, 40, 60, 4, 5) returning id""",
            (clip_id, self.segment_id),
        ).fetchone()["id"]
        self.conn.commit()

    def dialogue(self, table: str, row_id: int) -> list[str]:
        """번호 범위로 대사를 읽는다 — 컷·추천·답하기가 하는 그대로."""
        rng = self.conn.execute(
            f"select start_utterance_idx as a, end_utterance_idx as b from {table} where id = %s", (row_id,)
        ).fetchone()
        return [r["text"] for r in self.conn.execute(
            "select text from utterances where chunk_id = %s and idx between %s and %s order by idx",
            (self.chunk_id, rng["a"], rng["b"]),
        )]

    def test_swapping_the_transcript_keeps_segments_and_clip_parts_on_the_same_seconds(self):
        # 자막은 문장이 길다 — 같은 번호가 전혀 다른 시각을 가리키게 된다.
        transcripts.store_rows(self.conn, self.source_id, "youtube", [
            {"start_sec": s, "end_sec": s + 19, "text": f"caption {s}", "words": None} for s in range(0, 100, 20)
        ], replace=True)
        result = transcripts.use(self.conn, self.source_id, "youtube")
        self.assertEqual(self.dialogue("segments", self.segment_id), ["caption 40"])
        self.assertEqual(self.dialogue("clip_parts", self.part_id), ["caption 40"])
        self.assertEqual((result["segments"], result["clip_parts"]), (1, 1))
        source = self.conn.execute("select caption_source from sources").fetchone()
        self.assertEqual(source["caption_source"], "youtube")
        # 돌아오면 처음 번호로 돌아온다.
        transcripts.use(self.conn, self.source_id, "whisper")
        self.assertEqual(self.dialogue("segments", self.segment_id), ["whisper 30", "whisper 40", "whisper 50"])

    def test_the_kept_transcript_does_not_shrink_when_used(self):
        transcripts.use(self.conn, self.source_id, "whisper")
        self.assertEqual(transcripts.counts(self.conn, self.source_id), {"youtube": 0, "whisper": 10})

    def test_speech_outside_the_chunks_is_not_used_but_kept(self):
        transcripts.store_rows(self.conn, self.source_id, "youtube", [
            {"start_sec": 50, "end_sec": 52, "text": "안"}, {"start_sec": 500, "end_sec": 502, "text": "밖"},
        ], replace=True)
        result = transcripts.use(self.conn, self.source_id, "youtube")
        self.assertEqual((result["utterances"], result["outside"]), (1, 1))
        self.assertEqual(transcripts.counts(self.conn, self.source_id)["youtube"], 2)

    def test_switching_to_a_transcript_that_was_never_fetched_is_refused(self):
        with self.assertRaises(transcripts.TranscriptError):
            transcripts.switch(self.conn, self.source_id, "youtube")
        with self.assertRaises(transcripts.TranscriptError):
            transcripts.switch(self.conn, self.source_id, "manual")


if __name__ == "__main__":
    unittest.main()
