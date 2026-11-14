"""관리자 숏폼 AI 추천(pipeline/ideas.py) — tease §5-11.

🔴 지키는 것: 추천이 가리키는 구간은 목록에 있는 구간뿐이다(번호 → id 로 되찾는다). 모르는 컨셉 · 빈 가제 · 같은 구간
두 번 · 컨셉마다 셋 넘김은 버린다. 적합도는 늘 컨셉 셋을 같은 순서로 채운다. 구간을 다시 나누면 "오래된 추천" 이다.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shorts_maker import standards
from shorts_maker.adapters import gemini
from shorts_maker.pipeline import concepts, ideas

from ..support import DbTestCase, make_config

SEGMENTS = [
    {"id": 10, "idx": 0, "start_sec": 0, "end_sec": 40, "description": "팀이 일하는 방식"},
    {"id": 11, "idx": 1, "start_sec": 40, "end_sec": 90, "description": "서류에서 떨어지는 세 가지"},
    {"id": 12, "idx": 2, "start_sec": 90, "end_sec": 150, "description": "연봉 협상 이야기"},
]


def idea(concept: str = "info", segment=1, title: str = "서류 탈락 세 가지", criteria: str = "서류 기준이 나오는 대목") -> dict:
    return {"concept": concept, "title": title, "segment": segment, "criteria": criteria, "reason": "기준이 분명하다"}


def reply(ideas_: list[dict], fits: list[dict] | None = None) -> str:
    if fits is None:
        fits = [{"concept": key, "fit": 50, "note": "보통"} for key in concepts.KEYS]
    return json.dumps({"concepts": fits, "ideas": ideas_}, ensure_ascii=False)


class CheckTest(unittest.TestCase):
    def test_a_good_idea_keeps_the_segment_id(self):
        saved, dropped = ideas.check(reply([idea()]), SEGMENTS)
        self.assertEqual([], dropped)
        (kept,) = saved["ideas"]
        self.assertEqual((11, 1), (kept["segment_id"], kept["segment_idx"]))
        self.assertEqual("서류 기준이 나오는 대목", kept["criteria"])

    def test_bad_ideas_are_dropped_with_a_reason(self):
        saved, dropped = ideas.check(reply([
            idea(concept="funny"),          # 모르는 컨셉
            idea(segment=9),                # 없는 구간
            idea(segment="둘"),             # 번호가 아니다
            idea(title="   "),              # 빈 가제
            idea(),                         # 남는다
            idea(title="같은 구간 또"),     # 같은 컨셉에 같은 구간
        ]), SEGMENTS)
        self.assertEqual(1, len(saved["ideas"]))
        self.assertEqual(5, saved["dropped"])
        self.assertEqual(5, len(dropped))

    def test_the_same_segment_is_fine_under_another_concept(self):
        saved, _ = ideas.check(reply([idea("info", 1), idea("viral", 1, title="바로 떨어지는 이력서")]), SEGMENTS)
        self.assertEqual(["info", "viral"], [i["concept"] for i in saved["ideas"]])

    def test_at_most_three_per_concept(self):
        many = [idea("viral", n % 3, title=f"주제 {n}") for n in range(3)] + [idea("viral", 0, title="넷째")]
        saved, dropped = ideas.check(reply(many), SEGMENTS)
        self.assertEqual(ideas.PER_CONCEPT, len(saved["ideas"]))
        self.assertEqual(1, len(dropped))

    def test_a_blank_criteria_falls_back_to_the_title(self):
        saved, _ = ideas.check(reply([idea(criteria="")]), SEGMENTS)
        self.assertEqual("서류 탈락 세 가지", saved["ideas"][0]["criteria"])

    def test_fits_always_come_in_concept_order(self):
        fits = [{"concept": "info", "fit": 90, "note": "숫자가 많다"}, {"concept": "brand", "fit": 140, "note": "?"}]
        saved, dropped = ideas.check(reply([], fits), SEGMENTS)
        self.assertEqual(list(concepts.KEYS), [c["concept"] for c in saved["concepts"]])
        by_key = {c["concept"]: c for c in saved["concepts"]}
        self.assertEqual(90, by_key["info"]["fit"])
        self.assertIsNone(by_key["brand"]["fit"])  # 범위 밖
        self.assertIsNone(by_key["viral"]["fit"])  # 모델이 빠뜨렸다
        self.assertEqual(1, len(dropped))

    def test_a_broken_reply_raises(self):
        for raw in ("not json", json.dumps({"concepts": []}), json.dumps([1, 2])):
            with self.subTest(raw=raw), self.assertRaises(ideas.IdeaError):
                ideas.check(raw, SEGMENTS)


class PromptTest(unittest.TestCase):
    def test_every_concept_and_segment_is_in_the_prompt(self):
        prompt = ideas.build_prompt(SEGMENTS, "원티드 채용설명회", None)
        for concept in concepts.CONCEPTS:
            self.assertIn(concept["goal"], prompt)
        self.assertIn("[1] (50초) 서류에서 떨어지는 세 가지", prompt)
        self.assertIn("영상 개요: 원티드 채용설명회", prompt)
        self.assertNotIn(standards.HEADER.strip()[:10], prompt)

    def test_the_standard_goes_in(self):
        self.assertIn("과장은 피한다", ideas.build_prompt(SEGMENTS, None, "과장은 피한다"))

    def test_no_segments_means_no_prompt(self):
        with self.assertRaises(ideas.IdeaError):
            ideas.build_prompt([], None)


class RunTest(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = make_config(Path(self._tmp.name))
        self.source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status, context)"
            " values ('설명회', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE', '채용설명회') returning id"
        ).fetchone()["id"]
        chunk_id = self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path) values (%s, 0, 0, 200, 'c.wav') returning id",
            (self.source_id,),
        ).fetchone()["id"]
        self.segment_ids = [
            self.conn.execute(
                """insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx, end_utterance_idx, description)
                   values (%s, %s, %s, %s, %s, %s, %s) returning id""",
                (chunk_id, s["idx"], s["start_sec"], s["end_sec"], s["idx"] * 10, s["idx"] * 10 + 9, s["description"]),
            ).fetchone()["id"]
            for s in SEGMENTS
        ]
        self.conn.commit()

    def answer(self, raw: str) -> tuple[str, dict, int]:
        return raw, {"input_tokens": 2000, "output_tokens": 600, "total_tokens": 3000}, 9000

    def calls(self) -> list[dict]:
        return list(self.conn.execute("select params, error from stage_calls where stage = 'idea' order by id"))

    def test_the_result_is_saved_and_read_back(self):
        raw = reply([idea(segment=1), idea("brand", 0, title="이렇게 일하는 팀"), idea(segment=7)])
        with mock.patch.object(gemini, "generate_json", return_value=self.answer(raw)):
            idea_id = ideas.run_for_source(self.conn, self.cfg, self.source_id)
        found = ideas.latest(self.conn, self.source_id, set(self.segment_ids))
        self.assertEqual(idea_id, found["id"])
        self.assertEqual(2, len(found["ideas"]))
        self.assertEqual(1, found["dropped"])
        self.assertFalse(found["stale"])
        self.assertEqual(self.segment_ids[1], found["ideas"][0]["segment_id"])
        (call,) = self.calls()
        self.assertEqual(2, call["params"]["kept"])

    def test_resegmenting_makes_it_stale(self):
        with mock.patch.object(gemini, "generate_json", return_value=self.answer(reply([idea()]))):
            ideas.run_for_source(self.conn, self.cfg, self.source_id)
        # 구간을 다시 나눴다 — 지금 구간 id 에 그 구간이 없다.
        self.assertTrue(ideas.latest(self.conn, self.source_id, {999})["stale"])

    def test_the_newest_wins(self):
        with mock.patch.object(gemini, "generate_json", return_value=self.answer(reply([idea()]))):
            ideas.run_for_source(self.conn, self.cfg, self.source_id)
        with mock.patch.object(gemini, "generate_json", return_value=self.answer(reply([idea("viral", 2, title="연봉")]))):
            second = ideas.run_for_source(self.conn, self.cfg, self.source_id)
        found = ideas.latest(self.conn, self.source_id, set(self.segment_ids))
        self.assertEqual(second, found["id"])
        self.assertEqual("viral", found["ideas"][0]["concept"])

    def test_a_broken_reply_is_asked_once_more(self):
        with mock.patch.object(
            gemini, "generate_json", side_effect=[self.answer("not json"), self.answer(reply([idea()]))]
        ) as model:
            ideas.run_for_source(self.conn, self.cfg, self.source_id)
        self.assertEqual(2, model.call_count)
        self.assertIsNotNone(self.calls()[0]["error"])

    def test_a_model_failure_is_recorded_and_raised(self):
        with mock.patch.object(gemini, "generate_json", side_effect=RuntimeError("quota")), \
                self.assertRaises(ideas.IdeaError):
            ideas.run_for_source(self.conn, self.cfg, self.source_id)
        self.assertIn("quota", self.calls()[0]["error"])
        self.assertIsNone(ideas.latest(self.conn, self.source_id, set(self.segment_ids)))

    def test_nothing_yet_is_none(self):
        self.assertIsNone(ideas.latest(self.conn, self.source_id, set(self.segment_ids)))


if __name__ == "__main__":
    unittest.main()
