"""숏폼 카피 후보(pipeline/headlines.py) — tease §5-10 "AI 카피 후보".

🔴 지키는 것: 카피의 숫자는 대사 · 제목에 있는 숫자만이다. 시청자는 카드의 숫자를 그대로 믿는다 — 지어낸 숫자가 든
후보는 화면에 오르지 않는다. 근거 구절은 대사에 있어야 근거다. 글자 수 넘침은 버리지 않고 표시한다.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shorts_maker import standards
from shorts_maker.adapters import gemini
from shorts_maker.pipeline import headlines

from ..support import DbTestCase, make_config

# 잡소리 카드의 카피 블록 — 둘째 줄이 검은 띠다.
BLOCK = {"type": "headline", "max_chars": 15, "lines": [{"color": "#111111"}, {"color": "#FFFFFF", "band": "#111111"}]}
SPEC = {"blocks": [BLOCK]}
TRANSCRIPT = "면접관은 자기소개서에서 경험을 봅니다. 저는 3년 동안 1,000명을 넘게 봤는데 숫자로 말하는 사람이 붙어요."


def reply(*candidates: dict) -> str:
    return json.dumps({"candidates": list(candidates)}, ensure_ascii=False)


def candidate(first: str, second: str, quote: str = "숫자로 말하는 사람이 붙어요", angle: str = "결론 먼저") -> dict:
    return {"lines": [first, second], "angle": angle, "quote": quote}


class CheckTest(unittest.TestCase):
    def check(self, raw: str, title: str | None = "면접관이 말하는 합격 답변"):
        return headlines.check(raw, block=BLOCK, transcript=TRANSCRIPT, title=title)

    def test_a_number_that_was_never_said_drops_the_candidate(self):
        kept, dropped = self.check(reply(
            candidate("면접관 3년 차가", "숫자로 말하면 붙는다"),
            candidate("합격률 90%", "숫자로 말하는 사람"),
        ))
        self.assertEqual([["면접관 3년 차가", "숫자로 말하면 붙는다"]], [c["lines"] for c in kept])
        self.assertIn("90", dropped[0]["why"])

    def test_a_number_inside_a_bigger_number_does_not_count(self):
        # "1,000명" 이 있다고 "100명" 이 대사에 있는 것은 아니다.
        kept, dropped = self.check(reply(candidate("100명을 본 면접관", "숫자로 말하라"), candidate("1000명을 본", "면접관의 결론")))
        self.assertEqual([["1000명을 본", "면접관의 결론"]], [c["lines"] for c in kept])
        self.assertEqual(1, len(dropped))

    def test_numbers_in_the_title_are_allowed(self):
        kept, _ = headlines.check(
            reply(candidate("잡소리 ep.48", "숫자로 말하라")), block=BLOCK, transcript=TRANSCRIPT, title="잡소리 ep.48"
        )
        self.assertEqual(1, len(kept))

    def test_wrong_line_counts_and_empty_lines_are_dropped(self):
        kept, dropped = self.check(reply(
            {"lines": ["한 줄뿐"], "angle": "질문", "quote": ""},
            candidate("첫 줄", " "),
        ))
        self.assertEqual([], kept)
        self.assertEqual(2, len(dropped))

    def test_a_quote_that_is_not_in_the_transcript_is_cleared(self):
        # 띄어쓰기만 다른 것은 같은 구절로 본다.
        kept, _ = self.check(reply(
            candidate("면접관이 보는 것", "자기소개서의 경험", quote="자기소개서에서  경험을봅니다"),
            candidate("면접관이 말한다", "경험이 전부다", quote="경험이 전부입니다"),
        ))
        self.assertEqual(["자기소개서에서 경험을봅니다", None], [c["quote"] for c in kept])

    def test_an_overlong_line_is_marked_not_dropped(self):
        kept, _ = self.check(reply(candidate("면접관이 자기소개서에서 제일 먼저 보는 것", "경험")))
        self.assertEqual([0], kept[0]["over"])

    def test_duplicates_are_kept_once(self):
        kept, _ = self.check(reply(candidate("면접관이 보는 것", "경험"), candidate("면접관이  보는 것", "경험")))
        self.assertEqual(1, len(kept))

    def test_a_broken_reply_raises_so_the_caller_can_retry(self):
        for raw in ("not json", json.dumps({"nope": []})):
            with self.subTest(raw=raw), self.assertRaises(headlines.HeadlineError):
                self.check(raw)


class PromptTest(unittest.TestCase):
    def test_the_band_line_gets_the_conclusion(self):
        prompt = headlines.build_prompt(BLOCK, title="제목", context=None, standard=None, transcript=TRANSCRIPT)
        self.assertIn("2줄: 검은 띠", prompt)
        self.assertIn("15자 이하", prompt)
        self.assertNotIn("영상 개요", prompt)
        self.assertNotIn("관리자 기준", prompt)

    def test_the_standard_and_overview_go_in_when_there_are_some(self):
        # 카피는 선호가 결과를 바꿔야 하는 자리다(standards.py "넣는다").
        prompt = headlines.build_prompt(
            BLOCK, title="제목", context="원티드 커리어 토크", standard="과장된 표현은 피한다", transcript=TRANSCRIPT
        )
        self.assertIn("영상 개요: 원티드 커리어 토크", prompt)
        self.assertIn(standards.HEADER.strip()[:10], prompt)
        self.assertIn("과장된 표현은 피한다", prompt)


class RunTest(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = make_config(Path(self._tmp.name))
        self.source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status)"
            " values ('대담', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE') returning id"
        ).fetchone()["id"]
        self.conn.commit()

    def run_copy(self, transcript: str = TRANSCRIPT) -> dict:
        return headlines.run(
            self.conn, self.cfg, source_id=self.source_id, clip_id=4, spec=SPEC, title="합격 답변", context=None,
            transcript=transcript,
        )

    def answer(self, raw: str) -> tuple[str, dict, int]:
        return raw, {"input_tokens": 900, "output_tokens": 300, "total_tokens": 1500}, 4200

    def calls(self) -> list[dict]:
        return list(self.conn.execute("select params, error, input_tokens from stage_calls where stage = 'copy' order by id"))

    def test_candidates_come_back_and_the_call_is_recorded(self):
        raw = reply(candidate("면접관 3년 차가", "숫자로 말하면 붙는다"), candidate("합격률 90%", "숫자"))
        with mock.patch.object(gemini, "generate_json", return_value=self.answer(raw)):
            out = self.run_copy()
        self.assertEqual(1, len(out["candidates"]))
        self.assertEqual(1, out["dropped"])
        (call,) = self.calls()
        self.assertEqual(900, call["input_tokens"])
        self.assertEqual(1, call["params"]["kept"])
        self.assertIn("90", call["params"]["dropped"][0]["why"])

    def test_a_broken_reply_is_asked_once_more(self):
        good = reply(candidate("면접관이 보는 것", "경험"))
        with mock.patch.object(gemini, "generate_json", side_effect=[self.answer("not json"), self.answer(good)]) as model:
            out = self.run_copy()
        self.assertEqual(2, model.call_count)
        self.assertEqual(1, len(out["candidates"]))
        self.assertIsNotNone(self.calls()[0]["error"])

    def test_no_dialog_means_no_call(self):
        with mock.patch.object(gemini, "generate_json") as model, self.assertRaises(headlines.NothingToWrite):
            self.run_copy("   ")
        model.assert_not_called()

    def test_a_model_failure_is_recorded_and_raised(self):
        with mock.patch.object(gemini, "generate_json", side_effect=RuntimeError("quota")), \
                self.assertRaises(headlines.HeadlineError):
            self.run_copy()
        self.assertIn("quota", self.calls()[0]["error"])


if __name__ == "__main__":
    unittest.main()
