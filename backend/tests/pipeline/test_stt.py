"""whisper 출력 → utterances 변환·저장 검증.

whisper 모델은 부르지 않는다(transcribe 를 가짜로 바꾼다). 여기서 검증하는 건 모델 품질이 아니라
**시간 축 변환**이다 — 청크 로컬 초를 소스 절대 초로 올리는 이 한 줄이 틀리면 [7] 이 원본에서
엉뚱한 데를 자르고, 결과물을 볼 때까지 아무도 모른다.

🔴 저장 경로(run_for_chunk)도 여기서 돈다. 순수 함수만 보던 시절, Postgres 전환에서 `executemany`
가 커서의 메서드라는 걸 놓쳐 전사가 통째로 실패했는데 테스트는 전부 통과했다(2026-09-06).
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from shorts_maker.db import store
from shorts_maker.pipeline import stt

from ..support import DbTestCase, make_config


def word(start, end, text, probability=0.9):
    return SimpleNamespace(start=start, end=end, word=text, probability=probability)


def segment(start, end, text, words=None, avg_logprob=-0.3, no_speech_prob=0.01):
    return SimpleNamespace(
        start=start, end=end, text=text, words=words or [],
        avg_logprob=avg_logprob, no_speech_prob=no_speech_prob,
    )


class ToUtteranceRowsTest(unittest.TestCase):
    def test_shifts_to_source_absolute_seconds(self):
        rows, skipped = stt.to_utterance_rows([segment(0.0, 2.0, " 안녕하세요 ")], chunk_start_sec=1200)
        self.assertEqual(skipped, 0)
        self.assertEqual(rows[0]["start_sec"], 1200.0)
        self.assertEqual(rows[0]["end_sec"], 1202.0)

    def test_strips_text(self):
        rows, _ = stt.to_utterance_rows([segment(0.0, 2.0, "  니체는  ")], chunk_start_sec=0)
        self.assertEqual(rows[0]["text"], "니체는")

    def test_shifts_word_timestamps_too(self):
        rows, _ = stt.to_utterance_rows(
            [segment(1.0, 3.0, "니체", words=[word(1.0, 1.5, "니"), word(1.5, 3.0, "체")])],
            chunk_start_sec=600,
        )
        # jsonb 컬럼이라 행에는 리스트가 그대로 들어간다(Jsonb 래핑은 insert 시점). 문자열이 아니다.
        words = rows[0]["words"]
        self.assertEqual([w["start"] for w in words], [601.0, 601.5])
        self.assertEqual([w["end"] for w in words], [601.5, 603.0])

    def test_skips_blank_text(self):
        rows, skipped = stt.to_utterance_rows(
            [segment(0.0, 1.0, "   "), segment(1.0, 2.0, "실제 발화")], chunk_start_sec=0
        )
        self.assertEqual(skipped, 1)
        self.assertEqual(len(rows), 1)

    def test_skips_non_positive_duration(self):
        # whisper 는 무음에서 길이 0 짜리를 뱉는다. 그대로 넣으면 utterances 의 check 에 걸려
        # STT 전체가 죽는다.
        rows, skipped = stt.to_utterance_rows(
            [segment(5.0, 5.0, "영초"), segment(9.0, 8.0, "역전")], chunk_start_sec=0
        )
        self.assertEqual((len(rows), skipped), (0, 2))

    def test_indices_stay_contiguous_after_skipping(self):
        rows, _ = stt.to_utterance_rows(
            [segment(0.0, 1.0, "하나"), segment(1.0, 1.0, ""), segment(2.0, 3.0, "둘")],
            chunk_start_sec=0,
        )
        self.assertEqual([r["idx"] for r in rows], [0, 1])

    def test_keeps_quality_signals(self):
        rows, _ = stt.to_utterance_rows(
            [segment(0.0, 1.0, "환각 의심", avg_logprob=-1.8, no_speech_prob=0.7)], chunk_start_sec=0
        )
        self.assertAlmostEqual(rows[0]["avg_logprob"], -1.8)
        self.assertAlmostEqual(rows[0]["no_speech_prob"], 0.7)

    def test_words_are_null_when_absent(self):
        rows, _ = stt.to_utterance_rows([segment(0.0, 1.0, "말")], chunk_start_sec=0)
        self.assertIsNone(rows[0]["words"])


class CheckLanguageTest(unittest.TestCase):
    def test_accepts_supported_codes(self):
        for code in stt.LANGUAGES:
            with self.subTest(code=code):
                self.assertEqual(stt.check_language(code), code)

    def test_treats_empty_as_auto_detect(self):
        for blank in (None, ""):
            with self.subTest(blank=repr(blank)):
                self.assertIsNone(stt.check_language(blank))

    def test_rejects_unknown_codes(self):
        # 🔴 whisper 는 모르는 코드를 받으면 에러를 낸다. 전사를 몇 분 돌린 뒤가 아니라
        # 등록 시점에 걸러야 한다.
        for code in ("kr", "korean", "KO", "en-US", "xx"):
            with self.subTest(code=code):
                with self.assertRaises(stt.SttError):
                    stt.check_language(code)


class RunForChunkTest(DbTestCase):
    """전사 결과가 실제로 utterances 에 들어가는가. whisper 는 부르지 않는다."""

    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.cfg = make_config(root)
        self.cfg.work_dir.mkdir(parents=True)
        (self.cfg.work_dir / "chunk.wav").write_bytes(b"RIFF")
        source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, language)"
            " values ('강연', 'LECTURE', 'a.mp4', 'sha256:a', 'ko') returning id"
        ).fetchone()["id"]
        self.chunk_id = self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path)"
            " values (%s, 0, 600, 900, 'chunk.wav') returning id",
            (source_id,),
        ).fetchone()["id"]
        self.conn.commit()

    def run_stt(self, segments, force=False):
        result = stt.Transcription(
            rows=stt.to_utterance_rows(segments, chunk_start_sec=600)[0],
            skipped=0, load_ms=3, transcribe_ms=12, language="ko", language_probability=0.99, model="small",
        )
        with mock.patch.object(stt, "transcribe", return_value=result):
            return stt.run_for_chunk(self.conn, self.cfg, self.chunk_id, None, force)

    def utterances(self):
        return [dict(r) for r in self.conn.execute(
            "select * from utterances where chunk_id = %s order by idx", (self.chunk_id,)
        )]

    def test_rows_land_in_the_database_with_words_as_json(self):
        self.run_stt([
            segment(0.0, 2.0, "니체는", words=[word(0.0, 1.0, "니체는")]),
            segment(2.0, 4.0, "말했다"),
        ])
        rows = self.utterances()
        self.assertEqual([r["text"] for r in rows], ["니체는", "말했다"])
        # 소스 절대 초로 올라와야 한다.
        self.assertEqual([r["start_sec"] for r in rows], [600.0, 602.0])
        # jsonb 라 리스트가 그대로 돌아온다 — 문자열이 아니다.
        self.assertEqual(rows[0]["words"], [{"start": 600.0, "end": 601.0, "text": "니체는", "probability": 0.9}])
        self.assertIsNone(rows[1]["words"])

    def test_the_call_is_recorded_with_its_parameters(self):
        # 규약: 모든 단계는 stage_calls 에. params 는 jsonb 라 dict 로 돌아온다.
        self.run_stt([segment(0.0, 1.0, "말")])
        call = self.conn.execute("select * from stage_calls where stage = 'stt'").fetchone()
        self.assertEqual(call["model"], "faster-whisper:small")
        self.assertEqual(call["params"]["language"], "ko")
        self.assertEqual(call["latency_ms"], 12)

    def test_a_second_run_needs_force_and_then_replaces(self):
        self.run_stt([segment(0.0, 1.0, "처음")])
        with self.assertRaises(stt.SttError):
            self.run_stt([segment(0.0, 1.0, "두번째")])
        self.run_stt([segment(0.0, 1.0, "두번째")], force=True)
        self.assertEqual([r["text"] for r in self.utterances()], ["두번째"])


if __name__ == "__main__":
    unittest.main()
