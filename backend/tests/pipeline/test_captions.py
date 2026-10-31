"""유튜브 자막 → 발화 (tease §5-1).

🔴 지키는 것: ① 발화가 whisper 경로와 같은 모양으로 청크에 담긴다(뒤 단계는 출처를 모른다) ② 군말·소리
표시는 빠지고 "네·예" 는 남는다 ③ 음성 인식 대사는 따로 남는다(자막이 덮지 않는다) ④ 성공도 실패도
`stage_calls` ⑤ 받은 파일은 남지 않는다.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shorts_maker.adapters import ytdlp
from shorts_maker.pipeline import captions, transcripts

from ..support import DbTestCase, make_config


def w(start: float, text: str, end: float | None = None, change: bool = False) -> ytdlp.CaptionWord:
    return ytdlp.CaptionWord(start=start, end=end if end is not None else start + 0.4, text=text, speaker_change=change)


class ToUtterancesTest(unittest.TestCase):
    def texts(self, words):
        return [u["text"] for u in captions.to_utterances(words)[0]]

    def test_a_sentence_ends_at_its_punctuation(self):
        self.assertEqual(
            self.texts([w(0, "예,"), w(0.4, " 반갑습니다."), w(0.8, "서강대학교"), w(1.2, " 입학처장입니다.")]),
            ["예, 반갑습니다.", "서강대학교 입학처장입니다."],
        )

    def test_a_long_pause_or_a_new_speaker_also_ends_it(self):
        self.assertEqual(self.texts([w(0, "그래서"), w(5, "다음은")]), ["그래서", "다음은"])
        self.assertEqual(self.texts([w(0, "질문"), w(0.4, "네", change=True)]), ["질문", "네"])

    def test_one_utterance_never_runs_past_the_limit(self):
        words = [w(i * 0.5, f"말{i}") for i in range(int(captions.MAX_UTTERANCE_SEC * 2) + 4)]
        for utterance in captions.to_utterances(words)[0]:
            self.assertLessEqual(utterance["end_sec"] - utterance["start_sec"], captions.MAX_UTTERANCE_SEC)

    def test_fillers_and_sound_tags_go_but_yes_stays(self):
        utterances, dropped = captions.to_utterances(
            [w(0, "어"), w(0.4, " 그"), w(0.8, " 음,"), w(1.2, " 네"), w(1.6, " [웃음]"), w(2.0, " 맞습니다.")]
        )
        self.assertEqual([u["text"] for u in utterances], ["그 네", "맞습니다."])
        self.assertEqual(dropped, {"filler": 2, "sound": 1})

    def test_a_filler_that_ended_a_sentence_still_ends_it(self):
        self.assertEqual(self.texts([w(0, "그렇죠"), w(0.4, " 어."), w(0.8, " 다음")]), ["그렇죠", "다음"])

    def test_rows_look_like_whisper_rows(self):
        row = captions.to_utterances([w(3.0, "니체는", end=3.5), w(3.5, " 말했다.", end=4.1)])[0][0]
        self.assertEqual((row["start_sec"], row["end_sec"], row["text"]), (3.0, 4.1, "니체는 말했다."))
        # 단어마다 앞 공백 하나 — whisper 단어와 같다. 번인 자막·교정 사전이 이 목록을 이어 붙인다.
        self.assertEqual(row["words"][0], {"start": 3.0, "end": 3.5, "text": " 니체는"})
        self.assertIsNone(row["avg_logprob"])


class RunForSourceTest(DbTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.cfg = make_config(self.root)
        self.source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status, youtube_id, language)"
            " values ('서강대', 'LECTURE', 'a.mp4', 'sha256:c', 'DONE', 'MVqTWMg4n0o', 'ko') returning id"
        ).fetchone()["id"]
        self.chunks = [
            self.conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path) values (%s, %s, %s, %s, 'c.wav')"
                " returning id",
                (self.source_id, idx, start, end),
            ).fetchone()["id"]
            for idx, (start, end) in enumerate([(0, 60), (60, 120)])
        ]
        self.conn.commit()

    def run_import(self, words):
        self.caption_file = self.root / "cap.ko.json3"

        def fetch(cfg, video_id, language):
            self.caption_file.write_text("{}", encoding="utf-8")
            return self.caption_file

        with mock.patch.object(ytdlp, "fetch_captions", side_effect=fetch) as fetched, \
                mock.patch.object(ytdlp, "parse_json3", return_value=words):
            result = captions.run_for_source(self.conn, self.cfg, self.source_id)
        self.fetched = fetched
        return result

    def utterances(self):
        return [dict(r) for r in self.conn.execute(
            "select u.*, c.idx as chunk_idx from utterances u join chunks c on c.id = u.chunk_id"
            " where c.source_id = %s order by c.idx, u.idx",
            (self.source_id,),
        )]

    def test_utterances_land_in_their_chunks_with_words_and_the_source_is_marked(self):
        result = self.run_import([w(10, "첫 문장."), w(59.8, "경계 문장."), w(70, "둘째 조각.")])
        rows = self.utterances()
        self.assertEqual([(r["chunk_idx"], r["idx"], r["text"]) for r in rows],
                         [(0, 0, "첫 문장."), (0, 1, "경계 문장."), (1, 0, "둘째 조각.")])
        self.assertEqual(rows[0]["words"], [{"start": 10.0, "end": 10.4, "text": " 첫 문장."}])
        self.assertEqual(result["utterances"], 3)
        source = self.conn.execute("select caption_source from sources").fetchone()
        self.assertEqual(source["caption_source"], "youtube")
        self.assertEqual(self.fetched.call_args.args[1:], ("MVqTWMg4n0o", "ko"))
        self.assertFalse(self.caption_file.exists())

    def test_the_whole_video_is_kept_but_only_the_chosen_range_is_used(self):
        result = self.run_import([w(30, "안."), w(500, "밖.")])
        self.assertEqual([r["text"] for r in self.utterances()], ["안."])
        self.assertEqual((result["captions"], result["outside"]), (2, 1))
        self.assertEqual(transcripts.counts(self.conn, self.source_id)["youtube"], 2)

    def test_the_fix_dictionary_applies_to_captions_too(self):
        self.conn.execute("""update sources set stt_fixes = '[{"from": "체자", "to": "최저"}]'""")
        self.conn.commit()
        result = self.run_import([w(1, "수능"), w(1.4, " 체자를.")])
        row = self.utterances()[0]
        self.assertEqual(row["text"], "수능 최저를.")
        self.assertEqual(row["words"][1]["text"], " 최저를.")
        self.assertEqual(result["fixed"], 1)

    def test_a_whisper_transcript_is_kept_aside_not_lost(self):
        # 🔴 몇십 분 걸린 음성 인식이 자막 한 번에 날아가면 안 된다 — 비교 화면과 [음성 인식 대사로 쓰기] 가 쓴다.
        transcripts.store_rows(self.conn, self.source_id, "whisper", [
            {"start_sec": 1, "end_sec": 2, "text": "지어낸 문장"}], replace=False)
        transcripts.use(self.conn, self.source_id, "whisper")
        self.conn.commit()
        self.run_import([w(1, "자막.")])
        self.assertEqual([r["text"] for r in self.utterances()], ["자막."])
        self.assertEqual(transcripts.counts(self.conn, self.source_id), {"youtube": 1, "whisper": 1})

    def test_fetching_again_replaces_only_the_captions(self):
        self.run_import([w(1, "처음."), w(3, "둘.")])
        self.run_import([w(1, "다시.")])
        self.assertEqual([r["text"] for r in self.utterances()], ["다시."])
        self.assertEqual(transcripts.counts(self.conn, self.source_id)["youtube"], 1)

    def test_the_call_is_recorded(self):
        self.run_import([w(1, "어"), w(1.4, " 자막.")])
        call = self.conn.execute("select * from stage_calls where stage = 'caption'").fetchone()
        self.assertEqual((call["source_id"], call["error"]), (self.source_id, None))
        self.assertEqual((call["params"]["utterances"], call["params"]["fillers"]), (1, 1))

    def test_no_caption_track_records_the_failure_and_keeps_what_is_used(self):
        transcripts.store_rows(self.conn, self.source_id, "whisper", [
            {"start_sec": 1, "end_sec": 2, "text": "전사"}], replace=False)
        transcripts.use(self.conn, self.source_id, "whisper")
        self.conn.commit()
        with mock.patch.object(ytdlp, "fetch_captions", side_effect=ytdlp.NoCaptions("자막 없음")):
            with self.assertRaises(ytdlp.NoCaptions):
                captions.run_for_source(self.conn, self.cfg, self.source_id)
        call = self.conn.execute("select error from stage_calls where stage = 'caption'").fetchone()
        self.assertIn("NoCaptions", call["error"])
        self.assertEqual([r["text"] for r in self.utterances()], ["전사"])

    def test_a_source_without_a_youtube_id_or_chunks_is_refused_before_any_download(self):
        with mock.patch.object(ytdlp, "fetch_captions") as fetch:
            self.conn.execute("delete from chunks")
            self.conn.commit()
            with self.assertRaises(captions.CaptionError):
                captions.run_for_source(self.conn, self.cfg, self.source_id)
            self.conn.execute("update sources set youtube_id = null")
            self.conn.commit()
            with self.assertRaises(captions.CaptionError):
                captions.run_for_source(self.conn, self.cfg, self.source_id)
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
