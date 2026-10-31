"""whisper 출력 → utterances 변환·저장 검증.

whisper 모델은 부르지 않는다(transcribe 를 가짜로 바꾼다). 여기서 검증하는 건 모델 품질이 아니라
**시간 축 변환**이다 — 청크 로컬 초를 소스 절대 초로 올리는 이 한 줄이 틀리면 [7] 이 원본에서
엉뚱한 데를 자르고, 결과물을 볼 때까지 아무도 모른다.

🔴 저장 경로(run_for_source → run_span)도 여기서 돈다. 순수 함수만 보던 시절, Postgres 전환에서 `executemany`
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


class NormalizeTermsTest(unittest.TestCase):
    """화면에서 온 용어 힌트를 정리한다. 🔴 넘치는 목록은 받아 두지 않고 거절한다 — whisper 가 앞쪽을 조용히 버린다."""

    def test_trims_drops_blanks_and_duplicates_keeping_order(self):
        self.assertEqual(
            stt.normalize_terms(["  서강대학교 ", "", "다전공", "서강대학교", "로욜라  국제대학"]),
            ["서강대학교", "다전공", "로욜라 국제대학"],
        )

    def test_a_comma_inside_a_term_is_refused(self):
        # 힌트는 용어를 쉼표로 이은 한 줄이라 용어 안의 쉼표는 용어를 둘로 쪼갠다.
        with self.assertRaises(stt.SttError):
            stt.normalize_terms(["숭문고, 광성고"])

    def test_a_list_longer_than_the_hint_limit_is_refused(self):
        terms = [f"고등학교{i:02d}" for i in range(40)]  # 6자 × 40 + 구분자 = 200자를 넘는다
        with self.assertRaises(stt.SttError):
            stt.normalize_terms(terms)

    def test_the_prompt_joins_terms_and_is_none_when_empty(self):
        self.assertEqual(stt.terms_prompt(["서강대학교", "다전공"]), "서강대학교, 다전공")
        self.assertIsNone(stt.terms_prompt([]))


class NormalizeFixesTest(unittest.TestCase):
    """교정 사전은 저장할 때마다 전사 전체에 다시 적용된다 — 몇 번을 적용해도 같은 결과여야 한다."""

    def test_trims_drops_blank_rows_and_keeps_order(self):
        fixes = stt.normalize_fixes([
            {"from": " 수능체제 ", "to": "수능최저"}, {"from": "", "to": ""}, {"from": "체저", "to": "최저"},
        ])
        self.assertEqual([{"from": "수능체제", "to": "수능최저"}, {"from": "체저", "to": "최저"}], fixes)

    def test_a_half_filled_row_is_refused(self):
        with self.assertRaises(stt.SttError):
            stt.normalize_fixes([{"from": "체저", "to": ""}])

    def test_a_fix_that_would_grow_on_every_apply_is_refused(self):
        # "최저" → "수능최저" 를 두 번 적용하면 "수능수능최저" 가 된다.
        with self.assertRaises(stt.SttError):
            stt.normalize_fixes([{"from": "최저", "to": "수능최저"}])

    def test_fixes_that_feed_each_other_are_refused(self):
        with self.assertRaises(stt.SttError):
            stt.normalize_fixes([{"from": "체저", "to": "최저"}, {"from": "최저", "to": "최소"}])

    def test_the_same_wrong_word_twice_is_refused(self):
        with self.assertRaises(stt.SttError):
            stt.normalize_fixes([{"from": "체저", "to": "최저"}, {"from": "체저", "to": "체제"}])


class FixWordsTest(unittest.TestCase):
    """자막은 단어 목록으로 만든다 — text 만 고치면 화면 자막은 틀린 그대로다."""

    def words(self, *pairs):
        return [{"start": s, "end": s + 1, "text": t, "probability": 0.9} for s, t in pairs]

    def test_fixes_inside_one_word_and_keeps_its_time(self):
        out = stt.fix_words(self.words((0, " 이"), (1, " 체저도")), "체저", "최저")
        self.assertEqual([" 이", " 최저도"], [w["text"] for w in out])
        self.assertEqual((1, 2), (out[1]["start"], out[1]["end"]))

    def test_a_match_across_words_merges_them_from_first_start_to_last_end(self):
        out = stt.fix_words(self.words((0, " 수능"), (1, " 체자"), (2, " 학력")), "수능 체자", "수능 최저")
        self.assertEqual([" 수능 최저", " 학력"], [w["text"] for w in out])
        self.assertEqual((0, 2), (out[0]["start"], out[0]["end"]))

    def test_two_matches_in_one_word_are_both_fixed(self):
        out = stt.fix_words(self.words((0, " 체저체저")), "체저", "최저")
        self.assertEqual([" 최저최저"], [w["text"] for w in out])

    def test_no_match_returns_the_words_untouched(self):
        words = self.words((0, " 논술"))
        self.assertEqual(words, stt.fix_words(words, "체저", "최저"))

    def test_applying_twice_gives_the_same_row(self):
        row = {"text": "수능체제학력 기준", "words": self.words((0, " 수능체제학력"), (1, " 기준"))}
        fixes = [{"from": "수능체제", "to": "수능최저"}]
        once = stt.apply_fixes(row, fixes)
        self.assertEqual("수능최저학력 기준", once["text"])
        self.assertEqual(" 수능최저학력", once["words"][0]["text"])
        self.assertEqual(once, stt.apply_fixes(once, fixes))


class UncoveredTest(unittest.TestCase):
    """🔴 같은 오디오는 한 번만 듣는다(tease §5-1) — 들은 범위를 빼고 남은 것만."""

    def test_nothing_heard_leaves_the_whole_range(self):
        self.assertEqual(stt.uncovered(0, 100, []), [(0, 100)])

    def test_heard_parts_are_cut_out(self):
        self.assertEqual(stt.uncovered(0, 100, [(10, 20), (50, 120)]), [(0, 10), (20, 50)])

    def test_a_sliver_left_by_rounding_is_not_worth_hearing(self):
        self.assertEqual(stt.uncovered(0, 100.0004, [(0, 100)]), [])


class RunForSourceTest(DbTestCase):
    """전사 결과가 보관되고(transcripts) 들은 범위가 남는가(stt_spans). whisper 는 부르지 않는다."""

    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.cfg = make_config(root)
        self.cfg.work_dir.mkdir(parents=True)
        (self.cfg.work_dir / "chunk.wav").write_bytes(b"RIFF")
        self.source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, language)"
            " values ('강연', 'LECTURE', 'a.mp4', 'sha256:a', 'ko') returning id"
        ).fetchone()["id"]
        self.chunk_id = self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path)"
            " values (%s, 0, 600, 900, 'chunk.wav') returning id",
            (self.source_id,),
        ).fetchone()["id"]
        self.conn.commit()

    def fake(self, segments):
        """transcribe 를 대신한다. 받은 시작 초(범위의 시작)로 whisper 의 로컬 초를 올린다 — 진짜와 같다."""
        def run(audio, start_sec, model, prompt, language):
            self.heard.append((audio, start_sec, prompt))
            return stt.Transcription(
                rows=stt.to_utterance_rows(segments, chunk_start_sec=start_sec)[0],
                skipped=0, load_ms=3, transcribe_ms=12, language="ko", language_probability=0.99, model="small",
            )
        self.heard = []
        return mock.patch.object(stt, "transcribe", side_effect=run)

    def run_stt(self, segments, prompt=None):
        with self.fake(segments):
            return stt.run_for_source(self.conn, self.cfg, self.source_id, None, prompt)

    def utterances(self):
        return [dict(r) for r in self.conn.execute(
            "select * from utterances where chunk_id = %s order by idx", (self.chunk_id,)
        )]

    def test_rows_are_kept_and_used_when_there_are_no_captions(self):
        self.run_stt([
            segment(0.0, 2.0, "니체는", words=[word(0.0, 1.0, "니체는")]),
            segment(2.0, 4.0, "말했다"),
        ])
        rows = self.utterances()
        self.assertEqual([r["text"] for r in rows], ["니체는", "말했다"])
        # 원본 절대 초로 올라와야 한다.
        self.assertEqual([r["start_sec"] for r in rows], [600.0, 602.0])
        # jsonb 라 리스트가 그대로 돌아온다 — 문자열이 아니다.
        self.assertEqual(rows[0]["words"], [{"start": 600.0, "end": 601.0, "text": "니체는", "probability": 0.9}])
        self.assertIsNone(rows[1]["words"])
        kept = self.conn.execute("select count(*) as n from transcripts where origin = 'whisper'").fetchone()["n"]
        self.assertEqual(kept, 2)
        span = self.conn.execute("select * from stt_spans").fetchone()
        self.assertEqual((span["start_sec"], span["end_sec"], span["model"]), (600, 900, "faster-whisper:small"))
        source = self.conn.execute("select caption_source from sources").fetchone()
        self.assertEqual(source["caption_source"], "whisper")

    def test_captions_in_use_stay_in_use(self):
        # 자막이 기본이다(tease §5-1). 음성 인식은 비교용으로 보관만 한다.
        from shorts_maker.pipeline import transcripts

        transcripts.store_rows(self.conn, self.source_id, "youtube", [
            {"start_sec": 600, "end_sec": 602, "text": "자막"}], replace=True)
        transcripts.use(self.conn, self.source_id, "youtube")
        self.conn.commit()
        self.run_stt([segment(0.0, 1.0, "음성")])
        self.assertEqual([r["text"] for r in self.utterances()], ["자막"])
        self.assertEqual(transcripts.counts(self.conn, self.source_id), {"youtube": 1, "whisper": 1})
        call = self.conn.execute("select params from stage_calls where stage = 'stt'").fetchone()
        self.assertFalse(call["params"]["used"])

    def test_the_same_audio_is_never_heard_twice(self):
        self.run_stt([segment(0.0, 1.0, "처음")])
        with self.fake([segment(0.0, 1.0, "두번째")]):
            with self.assertRaises(stt.SttError):
                stt.run_for_source(self.conn, self.cfg, self.source_id)
        self.assertEqual(self.heard, [])
        self.assertEqual([r["text"] for r in self.utterances()], ["처음"])

    def test_after_an_interruption_only_the_rest_is_heard(self):
        # 청크 둘 중 첫째만 들은 채 멈췄다.
        second = self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path) values (%s, 1, 900, 1200, 'chunk.wav')"
            " returning id",
            (self.source_id,),
        ).fetchone()["id"]
        self.conn.execute(
            "insert into stt_spans (source_id, start_sec, end_sec, model) values (%s, 600, 900, 'faster-whisper:small')",
            (self.source_id,),
        )
        self.conn.commit()
        self.run_stt([segment(0.0, 1.0, "둘째")])
        self.assertEqual([start for _, start, _ in self.heard], [900.0])
        self.assertEqual(stt.remaining(self.conn, self.source_id), [])
        self.assertIsNotNone(second)

    def test_a_partly_heard_chunk_hears_only_the_new_part_from_the_original(self):
        # 범위를 넓혀 다시 추출했다: 옛 범위 600~700 은 이미 들었다. 남은 700~900 만 원본에서 뽑아 듣는다.
        self.conn.execute(
            "insert into stt_spans (source_id, start_sec, end_sec, model) values (%s, 600, 700, 'faster-whisper:small')",
            (self.source_id,),
        )
        self.conn.commit()
        extracted = []

        def fake_extract(src, dst, start, end):
            extracted.append((start, end))
            Path(dst).write_bytes(b"RIFF")

        with mock.patch.object(stt.ffmpeg, "extract_audio", side_effect=fake_extract):
            self.run_stt([segment(0.0, 1.0, "새 부분")])
        self.assertEqual(extracted, [(700, 900)])
        self.assertEqual(self.heard[0][1], 700)
        self.assertEqual(self.utterances()[0]["start_sec"], 700.0)
        # 뽑은 임시 파일은 남지 않는다.
        self.assertEqual([p.name for p in self.cfg.work_dir.glob("*_stt_*.wav")], [])

    def test_the_call_is_recorded_with_its_parameters(self):
        # 규약: 모든 단계는 stage_calls 에. params 는 jsonb 라 dict 로 돌아온다.
        self.run_stt([segment(0.0, 1.0, "말")])
        call = self.conn.execute("select * from stage_calls where stage = 'stt'").fetchone()
        self.assertEqual(call["model"], "faster-whisper:small")
        self.assertEqual(call["params"]["language"], "ko")
        self.assertEqual(call["params"]["chunk_id"], self.chunk_id)
        self.assertEqual(call["params"]["span"], [600, 900])
        self.assertEqual(call["latency_ms"], 12)

    def test_the_source_terms_become_the_hint_unless_one_is_given(self):
        self.conn.execute("update sources set stt_terms = %s", (["서강대학교", "다전공"],))
        self.conn.commit()
        self.run_stt([])
        self.assertEqual(self.heard[0][2], "서강대학교, 다전공")
        self.assertEqual(
            self.conn.execute("select initial_prompt from stt_spans").fetchone()["initial_prompt"], "서강대학교, 다전공"
        )

    def test_a_given_hint_wins(self):
        # CLI --prompt 처럼 직접 준 힌트가 이긴다.
        self.conn.execute("update sources set stt_terms = %s", (["서강대학교"],))
        self.conn.commit()
        self.run_stt([], prompt="광성고")
        self.assertEqual(self.heard[0][2], "광성고")

    def test_the_source_fixes_are_applied_before_the_rows_land(self):
        self.conn.execute("""update sources set stt_fixes = '[{"from": "체저", "to": "최저"}]'""")
        self.conn.commit()
        self.run_stt([segment(0.0, 2.0, "이 체저도", words=[word(0.0, 1.0, " 이"), word(1.0, 2.0, " 체저도")])])
        row = self.utterances()[0]
        self.assertEqual("이 최저도", row["text"])
        self.assertEqual(" 최저도", row["words"][1]["text"])
        kept = self.conn.execute("select text from transcripts").fetchone()
        self.assertEqual("이 최저도", kept["text"])
        call = self.conn.execute("select params from stage_calls where stage = 'stt'").fetchone()
        self.assertEqual(1, call["params"]["fixed"])


if __name__ == "__main__":
    unittest.main()
