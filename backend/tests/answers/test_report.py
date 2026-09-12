"""회차 리포트 (기획서 §05-③ Type C, upgrade_plan §2-2).

🔴 여기서 지키는 것 셋:
  1. **답하지 못한 이유가 함께 간다.** 목록만 주면 크리에이터는 다음 회차에 무엇을 준비할지
     알 수 없다 — "답할 구간 없음" 딱지는 영상이 정말 안 다룬 건지 검색이 빗나간 건지 구분하지 못한다
  2. **세 가지 "못 답함" 을 섞지 않는다.** 영상에 없다 / 아직 안 눌렀다 / 만들었는데 물렸다
  3. 🔴 **비용은 회차 준비와 질문 답변을 가른다.** 합쳐서 질문 수로 나누면 질문이 하나일 때
     전사비가 통째로 그 질문에 얹힌다
"""

import dataclasses
import tempfile
import unittest
from pathlib import Path

from psycopg.types.json import Jsonb

from shorts_maker.answers import clusters, report

from ..support import DbTestCase, make_config


class ReportTestCase(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = dataclasses.replace(
            make_config(Path(self._tmp.name)),
            price_input_usd_per_1m=1.0, price_output_usd_per_1m=1.0, usd_krw=1000.0,
        )
        self.source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status, channel, published)"
            " values ('채용설명회', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE', '원티드', true) returning id"
        ).fetchone()["id"]

    def cluster(self, text: str, status: str = "OPEN", run_id: int | None = None,
                note: str | None = None, asked: int = 1) -> int:
        cluster_id = self.conn.execute(
            """insert into question_clusters (source_id, canonical_text, status, run_id)
               values (%s, %s, %s, %s) returning id""",
            (self.source_id, text, status, run_id),
        ).fetchone()["id"]
        for n in range(asked):
            self.conn.execute(
                "insert into questions (source_id, text, viewer_id, cluster_id)"
                " values (%s, %s, %s, %s)",
                (self.source_id, f"{text} ({n})", f"v{cluster_id}-{n}", cluster_id),
            )
        if run_id is not None and note is not None:
            self.conn.execute(
                "update runs set ranked = %s where id = %s",
                (Jsonb({"answerable": False, "reason": note}), run_id),
            )
        return cluster_id

    def make_run(self) -> int:
        """🔴 `run` 이라고 이름 짓지 않는다 — `TestCase.run` 을 가려서 테스트가 아예 안 돈다."""
        return self.conn.execute(
            "insert into runs (source_id) values (%s) returning id", (self.source_id,)
        ).fetchone()["id"]

    def call(self, stage: str, run_id: int | None, tokens: int) -> None:
        self.conn.execute(
            """insert into stage_calls (source_id, run_id, stage, model, input_tokens,
                                        output_tokens, total_tokens)
               values (%s, %s, %s, 'gemini-x', %s, 0, %s)""",
            (self.source_id, run_id, stage, tokens, tokens),
        )

    def build(self) -> dict:
        return report.build(self.conn, self.cfg, self.source_id)


class WhatItContainsTest(ReportTestCase):
    def test_the_reason_travels_with_the_unanswered_question(self):
        # 🔴 이게 리포트의 값이다. 목록만으로는 다음 회차에 무엇을 준비할지 알 수 없다.
        run_id = self.make_run()
        self.cluster("복지 혜택이 있나요?", "UNANSWERABLE", run_id,
                     note="제공된 대사에 복지 관련 내용이 없습니다", asked=3)
        row = self.build()["missing"][0]
        self.assertEqual(row["askedBy"], 3)
        self.assertIn("복지", row["reason"])

    def test_the_original_wordings_are_kept(self):
        # 대표 문장은 LLM 이 지은 것이다. 사람들이 실제로 뭐라고 물었는지도 있어야 한다.
        self.cluster("복지가 궁금합니다", "UNANSWERABLE", self.make_run(), asked=2)
        self.assertEqual(len(self.build()["missing"][0]["texts"]), 2)

    def test_the_three_kinds_of_unanswered_are_not_mixed(self):
        """🔴 뜻이 다르다 — 영상에 없다 / 아직 안 눌렀다 / 만들었는데 물렸다.

        섞으면 크리에이터가 할 일을 못 고른다. 앞의 것은 다음 회차 큐시트고,
        가운데는 그냥 버튼을 누르면 되고, 뒤의 것은 다시 만들어야 한다.
        """
        self.cluster("없는 것", "UNANSWERABLE", self.make_run())
        self.cluster("아직 안 한 것", "OPEN")
        self.cluster("물린 것", "DECLINED", self.make_run())
        built = self.build()
        self.assertEqual(
            [len(built["missing"]), len(built["waiting"]), len(built["declined"])], [1, 1, 1]
        )
        self.assertEqual(built["summary"]["missing"], 1)

    def test_answered_questions_carry_their_clip(self):
        run_id = self.make_run()
        cluster_id = self.cluster("프로덕트는?", "REVIEW", run_id)
        chunk_id = self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path)"
            " values (%s, 0, 0, 600, 'c.wav') returning id", (self.source_id,)
        ).fetchone()["id"]
        segment_id = self.conn.execute(
            "insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx,"
            " end_utterance_idx) values (%s, 0, 0, 30, 0, 3) returning id", (chunk_id,)
        ).fetchone()["id"]
        self.conn.execute(
            """insert into clips (run_id, segment_id, question_cluster_id, start_sec, end_sec,
                                  total_sec, rendered)
               values (%s, %s, %s, 0, 30, 26.1, true)""",
            (run_id, segment_id, cluster_id),
        )
        row = self.build()["answered"][0]
        self.assertEqual(row["totalSec"], 26.1)
        self.assertFalse(row["published"])

    def test_questions_not_yet_grouped_are_counted(self):
        # [집계] 를 안 눌렀으면 묶음이 0인데 질문은 있다. 그 상태를 화면이 설명해야 한다.
        self.conn.execute(
            "insert into questions (source_id, text, viewer_id) values (%s, '미분류', 'v9')",
            (self.source_id,),
        )
        summary = self.build()["summary"]
        self.assertEqual((summary["questions"], summary["unclustered"]), (1, 1))


class CostSplitTest(ReportTestCase):
    """🔴 회차 준비와 질문 답변을 가른다 — 합치면 "질문당 얼마" 가 안 나온다."""

    def test_preparation_is_not_charged_to_the_questions(self):
        run_id = self.make_run()
        self.cluster("질문", "REVIEW", run_id)
        self.call("stt", None, 0)
        self.call("segment", None, 1_000_000)   # 회차 준비 — 비싸다
        self.call("rank", run_id, 100_000)      # 이 질문 몫
        cost = self.build()["cost"]
        self.assertEqual(cost["prepare"]["inputTokens"], 1_000_000)
        self.assertEqual(cost["answer"]["inputTokens"], 100_000)
        # 단가 1달러/1M · 환율 1000 이라 10만 토큰 = 100원.
        self.assertEqual(cost["perQuestionKrw"], 100.0)

    def test_two_questions_halve_the_average(self):
        first, second = self.make_run(), self.make_run()
        self.cluster("질문1", "REVIEW", first)
        self.cluster("질문2", "REVIEW", second)
        self.call("rank", first, 100_000)
        self.call("rank", second, 100_000)
        cost = self.build()["cost"]
        self.assertEqual(cost["attempted"], 2)
        self.assertEqual(cost["perQuestionKrw"], 100.0)

    def test_a_creator_criteria_run_is_not_counted_as_a_question(self):
        """🔴 기준 경로의 run 은 시청자 질문에 답한 것이 아니다.

        섞으면 크리에이터가 자기 기준으로 클립을 뽑을수록 "질문당 비용" 이 올라간다.
        """
        criteria_run = self.make_run()   # 클러스터가 가리키지 않는 run
        self.call("rank", criteria_run, 500_000)
        answer_run = self.make_run()
        self.cluster("질문", "REVIEW", answer_run)
        self.call("rank", answer_run, 100_000)
        cost = self.build()["cost"]
        self.assertEqual(cost["attempted"], 1)
        self.assertEqual(cost["perQuestionKrw"], 100.0)
        # 기준 경로 비용은 사라지지 않고 준비 쪽에 남는다.
        self.assertEqual(cost["prepare"]["inputTokens"], 500_000)

    def test_no_answered_question_gives_null_not_zero(self):
        # 🔴 0 으로 두면 "질문당 0원" 으로 읽힌다.
        self.call("segment", None, 1_000)
        self.assertIsNone(self.build()["cost"]["perQuestionKrw"])


if __name__ == "__main__":
    unittest.main()
