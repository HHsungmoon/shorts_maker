"""whisper 출력 → utterances 변환 검증.

whisper 없이 도는 순수 함수만 다룬다. 여기서 검증하는 건 모델 품질이 아니라
**시간 축 변환**이다 — 청크 로컬 초를 소스 절대 초로 올리는 이 한 줄이 틀리면
[7] 이 원본에서 엉뚱한 데를 자르고, 결과물을 볼 때까지 아무도 모른다.
"""

import json
import unittest
from types import SimpleNamespace

from shorts_maker import stt


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
        words = json.loads(rows[0]["words"])
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


if __name__ == "__main__":
    unittest.main()


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
