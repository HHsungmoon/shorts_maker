"""자막 ↔ 음성 인식 비교 (tease §5-1).

🔴 지키는 것: ① 음성 인식이 0초 단어로 지어낸 말은 "지어냈을 수 있음" 으로 맨 앞에 온다 — 뜻을 바꾸는 것이
그것이다(삼성 16:36 · 18:38 · 23:37 실측) ② 띄어쓰기·문장부호만 다른 것은 차이가 아니다 ③ 음성 인식이 들은
범위 밖은 맞대지 않는다.
"""

import unittest

from shorts_maker.pipeline import transcript_diff

from ..support import DbTestCase


def row(*words: tuple[float, float, str]) -> dict:
    return {
        "start_sec": words[0][0], "end_sec": words[-1][1], "text": "".join(t for _, _, t in words).strip(),
        "words": [{"start": a, "end": b, "text": t} for a, b, t in words],
    }


def kinds(result: dict) -> list[tuple[str, str, str]]:
    return [(s["kind"], s["caption"], s["whisper"]) for s in result["spots"]]


class CompareTest(unittest.TestCase):
    def test_spacing_and_punctuation_are_not_differences(self):
        caption = [row((0, 1, " 자기 자신을"), (1, 2, " 깊이있게."))]
        whisper = [row((0, 1, " 자기"), (1, 1.5, " 자신을,"), (1.5, 2, " 깊이 있게"))]
        result = transcript_diff.compare(caption, whisper, [(0, 10)])
        self.assertEqual(result["spots"], [])
        self.assertEqual(result["mismatchChars"], 0)

    def test_words_whisper_made_up_are_flagged_first(self):
        # 삼성 18:38: 0초 단어 다섯이 "알게 되는 것 같아요 그런 것들을" 을 만들었다.
        caption = [row((0, 1, " 깊이"), (1, 2, " 있게"), (2, 3, " 알고"), (3, 4, " 성찰하지"))]
        whisper = [row((0, 1, " 깊이"), (1, 2, " 있게"), (2, 2.5, " 알게"), (2.5, 2.5, " 되는"), (2.5, 2.5, " 것"),
                       (2.5, 2.5, " 같아요"), (2.5, 2.5, " 그런"), (2.5, 2.5, " 것들을"), (2.5, 3, " 알고"),
                       (3, 4, " 성찰하지"))]
        result = transcript_diff.compare(caption, whisper, [(0, 10)])
        self.assertEqual([k for k, _, _ in kinds(result)], ["invented"])
        self.assertIn("같아요", result["spots"][0]["whisper"])
        self.assertEqual(result["byKind"]["invented"], 1)

    def test_each_kind(self):
        caption = [row((0, 1, " 학보님들이"), (1, 2, " 세"), (2, 3, " 개를"), (3, 4, " 봅니다."),
                       (10, 11, " 그래서"), (11, 12, " 끝."))]
        whisper = [row((0, 1, " 학부모님들이"), (1, 2, " 3개를"), (2, 3, " 봅니다"), (5, 6, " 직무역량"),
                       (10, 11, " 그래서"))]
        found = kinds(transcript_diff.compare(caption, whisper, [(0, 20)]))
        self.assertIn(("different", "학보님들이", "학부모님들이"), found)
        # 자막의 "세" 와 음성 인식의 "3개를" — 단어 경계가 달라 양쪽 넓이가 다르다. 앞뒤 문맥이 같이 보인다.
        self.assertIn(("number", "세", "3개를"), found)
        self.assertIn(("caption_missing", "", "직무역량"), found)
        self.assertIn(("whisper_missing", "끝.", ""), found)

    def test_only_what_whisper_heard_is_compared(self):
        caption = [row((5, 6, " 같다")), row((50, 51, " 자막만"))]
        whisper = [row((5, 6, " 같다"))]
        result = transcript_diff.compare(caption, whisper, [(0, 10)])
        self.assertEqual(result["spots"], [])
        self.assertEqual(result["comparedSec"], 10)

    def test_a_spot_shows_the_words_around_it(self):
        caption = [row((0, 1, " 하나"), (1, 2, " 둘"), (2, 3, " 셋"), (3, 4, " 산발합니다"), (4, 5, " 다섯"))]
        whisper = [row((0, 1, " 하나"), (1, 2, " 둘"), (2, 3, " 셋"), (3, 4, " 선발합니다"), (4, 5, " 다섯"))]
        spot = transcript_diff.compare(caption, whisper, [(0, 10)])["spots"][0]
        self.assertEqual((spot["before"], spot["caption"], spot["whisper"], spot["after"]),
                         ("하나 둘 셋", "산발합니다", "선발합니다", "다섯"))
        self.assertEqual((spot["start_sec"], spot["end_sec"]), (3, 4))


class ForSourceTest(DbTestCase):
    def test_says_what_is_missing_before_comparing(self):
        source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint) values ('a', 'LECTURE', 'a.mp4', 'sha256:a')"
            " returning id"
        ).fetchone()["id"]
        self.assertFalse(transcript_diff.for_source(self.conn, source_id)["available"])
        self.conn.execute(
            "insert into stt_spans (source_id, start_sec, end_sec, model) values (%s, 0, 10, 'faster-whisper:small')",
            (source_id,),
        )
        result = transcript_diff.for_source(self.conn, source_id)
        self.assertFalse(result["available"])
        self.assertIn("자막", result["reason"])


if __name__ == "__main__":
    unittest.main()
