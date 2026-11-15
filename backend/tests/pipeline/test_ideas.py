"""관리자 숏폼 AI 추천(pipeline/ideas.py) — tease §5-11.

🔴 지키는 것: 추천이 가리키는 구간은 목록에 있는 구간뿐이다(번호 → id 로 되찾는다). 모르는 컨셉 · 빈 가제 · 같은 구간
두 번 · 컨셉마다 셋 넘김은 버린다. 적합도는 늘 컨셉 셋을 같은 순서로 채운다. 구간을 다시 나누면 "오래된 추천" 이다.
2026-11-14: 클립이 이미 쓰는 구간은 추천 입력에서 빠지고, 카드의 [클립 만들기](`cut`)도 거절한다 — 조합 클립의 조각까지 본다.
카드로 만든 클립은 주제 하나짜리 run(컨셉 · 기준 · idea_id)에 매달리고 제목은 가제다.
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


def idea(
    concept: str = "info", segment=1, title: str = "서류 탈락 세 가지", criteria: str = "서류 기준이 나오는 대목",
    score=80,
) -> dict:
    return {"concept": concept, "title": title, "segment": segment, "score": score, "criteria": criteria,
            "reason": "기준이 분명하다"}


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
        # 컨셉 순서(brand · viral · info)로 모인다 — 화면이 컨셉마다 칸을 나눈다
        self.assertEqual(["viral", "info"], [i["concept"] for i in saved["ideas"]])

    def test_at_most_five_per_concept_keeping_the_best(self):
        segments = [{"id": 100 + n, "idx": n, "start_sec": n * 10, "end_sec": n * 10 + 10, "description": f"구간 {n}"}
                    for n in range(7)]
        many = [idea("viral", n, title=f"주제 {n}", score=70 + n) for n in range(7)]
        saved, dropped = ideas.check(reply(many), segments)
        self.assertEqual(5, ideas.PER_CONCEPT)
        # 점수 높은 다섯이 높은 순으로 남는다(76 · 75 · 74 · 73 · 72). 70 · 71 은 다섯을 넘었다
        self.assertEqual([76, 75, 74, 73, 72], [i["score"] for i in saved["ideas"]])
        self.assertEqual(2, saved["dropped"])
        self.assertEqual(2, len(dropped))

    def test_below_the_bar_is_counted_apart(self):
        saved, dropped = ideas.check(reply([
            idea(segment=0, score=65),            # 문턱 — 남는다
            idea(segment=1, score=64),            # 약하다 — below
            idea(segment=2, score=None),          # 점수가 없다 — 버린다
            idea("viral", 2, score=101),          # 범위 밖 — 버린다
        ]), SEGMENTS)
        self.assertEqual([65], [i["score"] for i in saved["ideas"]])
        self.assertEqual((1, 2, 65), (saved["below"], saved["dropped"], saved["min_score"]))
        self.assertEqual(3, len(dropped))  # 기록에는 셋 다(이유와 함께)

    def test_the_same_segment_twice_keeps_the_higher_score(self):
        saved, _ = ideas.check(reply([idea(score=70, title="먼저"), idea(score=90, title="나중")]), SEGMENTS)
        self.assertEqual(["나중"], [i["title"] for i in saved["ideas"]])

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


class SourceCase(DbTestCase):
    """소스 하나 · 구간 셋. 추천 받기 · 카드로 만들기 테스트가 같이 쓴다."""

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
        self.chunk_id = chunk_id
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
        # 자르기(cutting)는 사용량 키를 전부 읽는다 — 추천만 쓰던 때의 셋으로는 모자란다.
        usage = {"input_tokens": 2000, "output_tokens": 600, "thinking_tokens": 0, "total_tokens": 3000, "cached_tokens": 0}
        return raw, usage, 9000

    def calls(self) -> list[dict]:
        return list(self.conn.execute("select params, error from stage_calls where stage = 'idea' order by id"))

    def clip_on(self, n: int, *, part_of: int | None = None) -> int:
        """구간 n 을 쓰는 클립. part_of 를 주면 첫 조각은 그 구간이고 n 은 두 번째 조각이다(조합 클립)."""
        run_id = self.conn.execute(
            "insert into runs (source_id, status) values (%s, 'DONE') returning id", (self.source_id,)
        ).fetchone()["id"]
        first = self.segment_ids[part_of if part_of is not None else n]
        clip_id = self.conn.execute(
            "insert into clips (run_id, segment_id, start_sec, end_sec) values (%s, %s, 0, 30) returning id",
            (run_id, first),
        ).fetchone()["id"]
        if part_of is not None:
            for ordinal, segment_id in enumerate((first, self.segment_ids[n])):
                self.conn.execute(
                    """insert into clip_parts (clip_id, ordinal, segment_id, start_sec, end_sec,
                                               start_utterance_idx, end_utterance_idx)
                       values (%s, %s, %s, 0, 10, 0, 1)""",
                    (clip_id, ordinal, segment_id),
                )
        self.conn.commit()
        return clip_id


class RunTest(SourceCase):
    def test_the_result_is_saved_and_read_back(self):
        raw = reply([idea(segment=1), idea("brand", 0, title="이렇게 일하는 팀"), idea(segment=7)])
        with mock.patch.object(gemini, "generate_json", return_value=self.answer(raw)):
            idea_id = ideas.run_for_source(self.conn, self.cfg, self.source_id)
        found = ideas.latest(self.conn, self.source_id, set(self.segment_ids))
        self.assertEqual(idea_id, found["id"])
        self.assertEqual(2, len(found["ideas"]))
        self.assertEqual(1, found["dropped"])
        self.assertFalse(found["stale"])
        info = next(i for i in found["ideas"] if i["concept"] == "info")
        self.assertEqual(self.segment_ids[1], info["segment_id"])
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

    # ---- 이미 있는 대목 (2026-11-14)

    def test_a_segment_with_a_clip_is_left_out_of_the_prompt(self):
        self.clip_on(2, part_of=1)  # 조합 클립 — 첫 조각은 1, 두 번째 조각이 2. clips.segment_id 에는 1 만 있다
        raw = reply([idea("brand", 0, title="이렇게 일하는 팀")])
        with mock.patch.object(gemini, "generate_json", return_value=self.answer(raw)) as model:
            ideas.run_for_source(self.conn, self.cfg, self.source_id)
        prompt = model.call_args.args[1]
        self.assertIn("팀이 일하는 방식", prompt)
        self.assertNotIn("서류에서 떨어지는 세 가지", prompt)
        self.assertNotIn("연봉 협상 이야기", prompt)  # 두 번째 조각도 빠진다
        (call,) = self.calls()
        self.assertEqual(2, call["params"]["skipped"])

    def test_some_segments_left_means_only_those_can_be_named(self):
        self.clip_on(1)
        raw = reply([idea(segment=1), idea("brand", 0, title="이렇게 일하는 팀")])
        with mock.patch.object(gemini, "generate_json", return_value=self.answer(raw)):
            ideas.run_for_source(self.conn, self.cfg, self.source_id)
        found = ideas.latest(self.conn, self.source_id, set(self.segment_ids))
        self.assertEqual([0], [i["segment_idx"] for i in found["ideas"]])  # 1 은 목록에 없어 버렸다
        self.assertEqual(1, found["dropped"])

    def test_every_segment_taken_means_no_call(self):
        for n in range(len(SEGMENTS)):
            self.clip_on(n)
        with mock.patch.object(gemini, "generate_json") as model, self.assertRaises(ideas.IdeaError):
            ideas.run_for_source(self.conn, self.cfg, self.source_id)
        model.assert_not_called()


class CutTest(SourceCase):
    """추천 카드의 [클립 만들기]."""

    def setUp(self) -> None:
        super().setUp()
        # 구간 1(40~90초)의 발화 10~19, 5초씩
        for n in range(10):
            self.conn.execute(
                "insert into utterances (chunk_id, idx, start_sec, end_sec, text) values (%s, %s, %s, %s, %s)",
                (self.chunk_id, 10 + n, 40 + n * 5, 45 + n * 5, f"발화 {n}"),
            )
        self.conn.commit()
        raw = reply([idea(segment=1, title="서류 탈락 세 가지"), idea("viral", 1, title="바로 떨어지는 이력서")])
        with mock.patch.object(gemini, "generate_json", return_value=self.answer(raw)):
            self.idea_id = ideas.run_for_source(self.conn, self.cfg, self.source_id)

    def cut(self, concept: str = "info", n: int = 1) -> int:
        payload = json.dumps({"start_idx": 10, "end_idx": 17, "reason": "기준 셋이 끝까지 나온다"})
        with mock.patch.object(gemini, "generate_json", return_value=self.answer(payload)):
            return ideas.cut(self.conn, self.cfg, self.source_id, self.idea_id, concept, self.segment_ids[n])

    def test_a_card_makes_a_clip_on_its_own_run(self):
        clip_id = self.cut()
        clip = self.conn.execute(
            """select cl.title, cl.segment_id, r.concept, r.criteria_prompt, r.idea_id, r.prompt
               from clips cl join runs r on r.id = cl.run_id where cl.id = %s""",
            (clip_id,),
        ).fetchone()
        self.assertEqual("서류 탈락 세 가지", clip["title"])  # 가제가 제목이다
        self.assertEqual(self.segment_ids[1], clip["segment_id"])
        self.assertEqual(("info", "서류 기준이 나오는 대목", self.idea_id), (clip["concept"], clip["criteria_prompt"], clip["idea_id"]))
        # 🔴 prompt 가 비어 있어야 새 클립 탭이 이 run 을 "고르기 결과" 로 읽지 않는다
        self.assertIsNone(clip["prompt"])

    def test_the_cut_reads_the_concept(self):
        payload = json.dumps({"start_idx": 10, "end_idx": 17, "reason": "센 한마디"})
        with mock.patch.object(gemini, "generate_json", return_value=self.answer(payload)) as model:
            ideas.cut(self.conn, self.cfg, self.source_id, self.idea_id, "viral", self.segment_ids[1])
        self.assertIn(concepts.BY_KEY["viral"]["cut"], model.call_args.args[1])

    def test_the_same_segment_is_not_made_twice(self):
        self.cut("info")
        # 다른 컨셉의 카드라도 같은 구간이면 거절한다 — 모델을 부르기 전에
        with mock.patch.object(gemini, "generate_json") as model, self.assertRaises(ideas.IdeaError):
            ideas.cut(self.conn, self.cfg, self.source_id, self.idea_id, "viral", self.segment_ids[1])
        model.assert_not_called()

    def test_a_segment_inside_a_stitched_clip_counts(self):
        self.clip_on(1, part_of=0)
        with self.assertRaises(ideas.IdeaError):
            self.cut()

    def test_an_unknown_card_is_refused(self):
        with self.assertRaises(ideas.IdeaError):
            ideas.cut(self.conn, self.cfg, self.source_id, self.idea_id, "brand", self.segment_ids[1])
        with self.assertRaises(ideas.IdeaError):
            ideas.cut(self.conn, self.cfg, self.source_id, self.idea_id + 99, "info", self.segment_ids[1])


if __name__ == "__main__":
    unittest.main()
