"""관리자 숏폼의 컨셉 셋(pipeline/concepts.py) — tease §5-11.

🔴 지키는 것:
  1. 키가 DB 의 CHECK 와 같다 — 어긋나면 화면에서 고른 컨셉이 run 을 만들 때 터진다
  2. 컨셉이 없으면 모든 프롬프트가 전과 같다 — 질문 경로 · 컨셉 이전의 run 이 흔들리지 않는다
  3. 고르기 · 자르기 · 카피가 같은 컨셉의 글을 읽고, 단계마다 붙는 한 줄이 다르다
  4. 순서는 공통 기준 → 컨셉 → 이번 기준(뒤로 갈수록 이번 요청)
"""

import re
import unittest
from pathlib import Path

from shorts_maker.db import store
from shorts_maker.pipeline import concepts, cutting, headlines, ranking

from ..support import DbTestCase

SEGMENTS = [{"idx": 0, "start_sec": 0, "end_sec": 30, "description": "설명"}]
UTTERANCES = [{"idx": 0, "start_sec": 0.0, "end_sec": 5.0, "text": "발화"}]
BLOCK = {"type": "headline", "max_chars": 15, "lines": [{"color": "#111111"}, {"color": "#FFFFFF", "band": "#111111"}]}


class ConceptTest(unittest.TestCase):
    def test_the_keys_match_the_migration_check(self):
        sql = (Path(store.__file__).parent / "migrations" / "023_shorts_concepts.sql").read_text()
        found = re.search(r"concept in \(([^)]*)\)", sql)
        self.assertIsNotNone(found)
        self.assertEqual(set(concepts.KEYS), set(re.findall(r"'(\w+)'", found.group(1))))

    def test_three_concepts_in_screen_order(self):
        self.assertEqual(("brand", "viral", "info"), concepts.KEYS)
        for concept in concepts.CONCEPTS:
            with self.subTest(concept=concept["key"]):
                for field in ("label", "summary", "goal", "cut", "copy"):
                    self.assertTrue(concept[field].strip())
                self.assertTrue(concept["examples"])

    def test_check_accepts_blank_as_none_and_refuses_unknown(self):
        self.assertIsNone(concepts.check(None))
        self.assertIsNone(concepts.check("  "))
        self.assertEqual("viral", concepts.check(" viral "))
        with self.assertRaises(concepts.ConceptError):
            concepts.check("funny")

    def test_no_concept_leaves_no_trace(self):
        for stage in concepts.STAGES:
            self.assertEqual("", concepts.block(None, stage))

    def test_an_unknown_concept_or_stage_is_refused(self):
        with self.assertRaises(concepts.ConceptError):
            concepts.block("funny", "rank")
        with self.assertRaises(concepts.ConceptError):
            concepts.block("brand", "judge")

    def test_the_catalog_is_a_copy(self):
        # 화면용 목록을 고쳐도 프롬프트의 글이 바뀌지 않는다.
        catalog = concepts.catalog()
        catalog[0]["examples"].append("덧붙인 예시")
        self.assertNotIn("덧붙인 예시", concepts.CONCEPTS[0]["examples"])


class PromptTest(unittest.TestCase):
    def test_without_a_concept_every_prompt_is_as_before(self):
        # 🔴 질문 경로 · 컨셉 이전의 run 은 그대로여야 한다.
        self.assertEqual(ranking.build_prompt(SEGMENTS, None, "기준"), ranking.build_prompt(SEGMENTS, None, "기준", None, None))
        self.assertNotIn("이번 숏폼의 컨셉", ranking.build_prompt(SEGMENTS, None, "기준"))
        self.assertNotIn("이번 숏폼의 컨셉", cutting.build_prompt(UTTERANCES, "설명", None))
        self.assertNotIn(
            "이번 숏폼의 컨셉", headlines.build_prompt(BLOCK, title=None, context=None, standard=None, transcript="대사")
        )

    def test_the_rank_reads_the_goal_and_keeps_the_gate(self):
        prompt = ranking.build_prompt(SEGMENTS, None, None, None, "viral")
        viral = concepts.BY_KEY["viral"]
        self.assertIn(viral["goal"], prompt)
        self.assertNotIn(viral["cut"], prompt)
        # 자립성 관문은 그대로다 — 컨셉 줄이 1단계를 없애지 않는다.
        self.assertIn("1단계 — 자립성 관문", prompt)

    def test_the_order_is_standard_then_concept_then_criteria(self):
        prompt = ranking.build_prompt(SEGMENTS, None, "이번 기준 [이번표식]", "공통 [공통표식]", "info")
        self.assertLess(prompt.index("[공통표식]"), prompt.index("이번 숏폼의 컨셉"))
        self.assertLess(prompt.index("이번 숏폼의 컨셉"), prompt.index("[이번표식]"))

    def test_the_cut_reads_its_own_hint(self):
        prompt = cutting.build_prompt(UTTERANCES, "설명", None, None, "viral")
        self.assertIn(concepts.BY_KEY["viral"]["goal"], prompt)
        self.assertIn(concepts.BY_KEY["viral"]["cut"], prompt)

    def test_the_copy_reads_its_own_hint(self):
        prompt = headlines.build_prompt(BLOCK, title=None, context=None, standard=None, transcript="대사", concept="brand")
        self.assertIn(concepts.BY_KEY["brand"]["copy"], prompt)
        self.assertNotIn(concepts.BY_KEY["brand"]["cut"], prompt)


class RunConceptTest(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status)"
            " values ('설명회', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE') returning id"
        ).fetchone()["id"]
        self.conn.commit()

    def insert_run(self, concept: str | None) -> None:
        self.conn.execute(
            "insert into runs (source_id, status, concept) values (%s, 'DONE', %s)", (self.source_id, concept)
        )

    def test_the_database_takes_every_key_and_null(self):
        for key in (*concepts.KEYS, None):
            self.insert_run(key)
        self.conn.commit()

    def test_the_database_refuses_an_unknown_concept(self):
        import psycopg

        with self.assertRaises(psycopg.errors.CheckViolation):
            self.insert_run("funny")


if __name__ == "__main__":
    unittest.main()
