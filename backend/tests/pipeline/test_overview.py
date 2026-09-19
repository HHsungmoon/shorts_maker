"""영상 개요 초안 (`pipeline/overview.py`).

🔴 여기서 지키는 것 넷:

  1. **저장하지 않는다.** 초안을 돌려주고 멈춘다 — 이 글은 이후 모든 호출에 붙어서 틀린 한 줄이
     조용히 파이프라인 전체에 퍼진다. 사람이 읽고 누르는 관문이 있어야 한다
  2. **재료가 없으면 부르지 않는다.** 제목만으로 쓴 개요는 그럴듯하되 근거가 없다
  3. **요약을 고르게 솎는다.** 앞에서부터 자르면 후반 주제를 통째로 잃는다 — 2시간짜리는 구간이
     200개를 넘는다
  4. **호출을 기록한다.** 성공도 실패도(규약: 모든 단계)
"""

import dataclasses
import json
import unittest
from unittest import mock

from shorts_maker.adapters import gemini
from shorts_maker.pipeline import overview

from ..support import DbTestCase, make_config

USAGE = {
    "input_tokens": 300, "output_tokens": 40, "thinking_tokens": 0,
    "total_tokens": 340, "cached_tokens": 0, "attempts": 1,
}


class PickTest(unittest.TestCase):
    def test_everything_is_kept_when_it_fits(self):
        self.assertEqual(["가", "나", "다"], overview.pick(["가", "나", "다"], limit=5))

    def test_blank_descriptions_are_dropped(self):
        self.assertEqual(["가", "나"], overview.pick(["가", "", "   ", "나"], limit=5))

    def test_thinning_keeps_the_first_and_the_last(self):
        """🔴 앞에서부터 자르면 후반 주제를 통째로 잃는다 — 그러면 개요가 틀린다."""
        picked = overview.pick([f"구간 {i}" for i in range(200)], limit=10)
        self.assertEqual(10, len(picked))
        self.assertEqual("구간 0", picked[0])
        self.assertEqual("구간 199", picked[-1])

    def test_thinning_spreads_evenly(self):
        picked = overview.pick([f"{i}" for i in range(100)], limit=5)
        self.assertEqual(["0", "25", "50", "74", "99"], picked)


class PromptTest(unittest.TestCase):
    def test_the_prompt_carries_the_title_and_every_summary(self):
        prompt = overview.build_prompt("서강대 입학설명회", ["전형 안내", "면접 준비"])
        self.assertIn("서강대 입학설명회", prompt)
        self.assertIn("- 전형 안내", prompt)
        self.assertIn("- 면접 준비", prompt)

    def test_an_empty_title_does_not_leave_a_dangling_label(self):
        # 빈 자리를 그대로 두면 모델이 그 빈칸을 해석한다.
        self.assertIn("(제목 없음)", overview.build_prompt("  ", ["전형 안내"]))

    def test_the_prompt_asks_for_facts_only(self):
        """🔴 개요는 사실이다. 평가가 섞이면 그 취향이 3층으로 위장해 이후 모든 호출에 따라붙는다."""
        prompt = overview.build_prompt("제목", ["요약"])
        self.assertIn("사실만", prompt)
        self.assertIn("추측", prompt)


class ParseTest(unittest.TestCase):
    def test_it_reads_the_context_field(self):
        self.assertEqual("입학설명회", overview.parse_response('{"context": "  입학설명회  "}'))

    def test_an_empty_draft_is_refused(self):
        for raw in ('{"context": ""}', '{"context": "   "}', "{}"):
            with self.subTest(raw=raw):
                with self.assertRaises(overview.OverviewError):
                    overview.parse_response(raw)

    def test_broken_json_is_refused(self):
        with self.assertRaises(overview.OverviewError):
            overview.parse_response("개요입니다")

    def test_an_overlong_draft_is_cut_at_a_sentence_end(self):
        """프롬프트가 200자를 요구하므로 드물지만, 낱말 한가운데서 끊긴 초안은 사람이 고치기보다
        지우고 다시 쓰게 만든다."""
        body = "가" * 300 + "입니다. " + "나" * 300
        text = overview.parse_response(json.dumps({"context": body}, ensure_ascii=False))
        self.assertLessEqual(len(text), overview.MAX_CHARS)
        self.assertTrue(text.endswith("입니다."))

    def test_an_overlong_draft_with_no_sentence_end_is_still_capped(self):
        text = overview.parse_response(json.dumps({"context": "가" * 900}, ensure_ascii=False))
        self.assertEqual(overview.MAX_CHARS, len(text))


class DraftTest(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.cfg = make_config(__import__("pathlib").Path("/tmp"))
        self.source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status)"
            " values ('서강대 입학설명회', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE') returning id"
        ).fetchone()["id"]
        self.chunk_id = self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path)"
            " values (%s, 0, 0, 60, 'a-0.mp4') returning id", (self.source_id,)
        ).fetchone()["id"]

    def segment(self, idx: int, description: str | None) -> None:
        self.conn.execute(
            """insert into segments (chunk_id, idx, start_sec, end_sec, description,
                                     start_utterance_idx, end_utterance_idx)
               values (%s, %s, %s, %s, %s, 0, 1)""",
            (self.chunk_id, idx, idx * 10, idx * 10 + 10, description),
        )

    def run_draft(self, answer: str):
        payload = json.dumps({"context": answer}, ensure_ascii=False)
        with mock.patch.object(gemini, "generate_json", return_value=(payload, USAGE, 900)) as llm:
            text = overview.draft(self.conn, self.cfg, self.source_id)
        return text, llm

    def test_it_drafts_from_the_segment_summaries(self):
        self.segment(0, "전형 일정 안내")
        self.segment(1, "면접 준비 방법")
        text, llm = self.run_draft("2027학년도 서강대 입학전형 설명회. 전형 일정과 면접 안내.")
        self.assertEqual("2027학년도 서강대 입학전형 설명회. 전형 일정과 면접 안내.", text)
        prompt = llm.call_args.args[1]
        self.assertIn("전형 일정 안내", prompt)
        self.assertIn("면접 준비 방법", prompt)

    def test_it_does_not_save_the_draft(self):
        """🔴 이 글은 이후 모든 호출에 붙는다. 사람이 읽고 누르기 전에는 들어가지 않는다."""
        self.segment(0, "전형 일정 안내")
        self.run_draft("입학설명회")
        stored = self.conn.execute(
            "select context from sources where id = %s", (self.source_id,)
        ).fetchone()["context"]
        self.assertIsNone(stored)

    def test_it_refuses_when_there_are_no_summaries_yet(self):
        """🔴 제목만으로 쓴 개요는 그럴듯하되 근거가 없다. 호출 자체를 하지 않는다."""
        self.segment(0, None)
        with mock.patch.object(gemini, "generate_json", autospec=True) as llm:
            with self.assertRaises(overview.OverviewError):
                overview.draft(self.conn, self.cfg, self.source_id)
        llm.assert_not_called()

    def test_a_missing_source_is_refused_before_the_call(self):
        with mock.patch.object(gemini, "generate_json", autospec=True) as llm:
            with self.assertRaises(overview.OverviewError):
                overview.draft(self.conn, self.cfg, 9999)
        llm.assert_not_called()

    def test_the_call_is_recorded_as_its_own_stage(self):
        """🔴 `describe` 를 재활용하지 않는다 — 구간 하나하나의 해설과 영상 전체의 개요는 뜻이 다르다."""
        self.segment(0, "전형 일정 안내")
        self.run_draft("입학설명회")
        row = self.conn.execute(
            "select stage, model, input_tokens, latency_ms, params from stage_calls"
            " where source_id = %s", (self.source_id,)
        ).fetchone()
        self.assertEqual("overview", row["stage"])
        self.assertEqual(300, row["input_tokens"])
        self.assertEqual(900, row["latency_ms"])
        self.assertEqual({"segments": 1, "sent": 1}, row["params"])

    def test_a_failed_call_is_recorded_too(self):
        """안 남기면 "왜 이 시각에 아무 일도 없었나" 를 못 푼다."""
        self.segment(0, "전형 일정 안내")
        with mock.patch.object(gemini, "generate_json", side_effect=RuntimeError("과부하")):
            with self.assertRaises(RuntimeError):
                overview.draft(self.conn, self.cfg, self.source_id)
        row = self.conn.execute(
            "select stage, error from stage_calls where source_id = %s", (self.source_id,)
        ).fetchone()
        self.assertEqual("overview", row["stage"])
        self.assertIn("과부하", row["error"])

    def test_a_rejected_draft_leaves_its_reason_on_the_call(self):
        # 호출은 성공했는데 내용이 검증에 걸린 경우. 같은 행에 사유가 남아야 추적된다.
        self.segment(0, "전형 일정 안내")
        with self.assertRaises(overview.OverviewError):
            self.run_draft("   ")
        row = self.conn.execute(
            "select error from stage_calls where source_id = %s", (self.source_id,)
        ).fetchone()
        self.assertIn("비어 있다", row["error"])

    def test_the_prompt_is_kept_when_storing_is_on(self):
        self.segment(0, "전형 일정 안내")
        self.run_draft("입학설명회")
        row = self.conn.execute(
            "select prompt, response from stage_calls where source_id = %s", (self.source_id,)
        ).fetchone()
        self.assertIn("전형 일정 안내", row["prompt"])
        self.assertIn("입학설명회", row["response"])

    def test_the_prompt_is_dropped_when_storing_is_off(self):
        self.cfg = dataclasses.replace(self.cfg, store_prompts=False)
        self.segment(0, "전형 일정 안내")
        self.run_draft("입학설명회")
        row = self.conn.execute(
            "select prompt, response from stage_calls where source_id = %s", (self.source_id,)
        ).fetchone()
        self.assertIsNone(row["prompt"])
        self.assertIsNone(row["response"])


if __name__ == "__main__":
    unittest.main()
