"""데모 리허설 도구 (update_plan M9, upgrade_plan §3).

🔴 여기서 지키는 것 셋:
  1. **초기화가 영상 준비물을 건드리지 않는다.** 전사·구간 분할·임베딩은 편당 수십 분이 드는
     자산이다. 리허설마다 날리면 리허설을 못 한다
  2. 🔴 **답변에 쓴 호출 기록을 run 보다 먼저 지운다.** `stage_calls.run_id` 가
     `on delete set null` 이라 순서를 거꾸로 하면 고아가 남고, 그 순간 그것들이 **회차 준비
     비용으로 둔갑한다** — 리허설을 돌릴수록 준비비가 불어나는 거짓 숫자가 만들어진다
  3. **시드는 집계를 하지 않는다.** 그 버튼을 누르는 것이 데모 2단계다
"""

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from shorts_maker.answers import demo, report

from ..support import DbTestCase, make_config


class DemoTestCase(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = dataclasses.replace(
            make_config(Path(self._tmp.name)),
            price_input_usd_per_1m=1.0, price_output_usd_per_1m=1.0, usd_krw=1000.0,
        )
        self.source_id = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status, channel, published,"
            " youtube_id) values ('설명회', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE', '원티드', true,"
            " 'abc123') returning id"
        ).fetchone()["id"]

    def prepare(self) -> tuple[int, int]:
        """영상 준비물 — 리허설이 절대 지우면 안 되는 것."""
        chunk_id = self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path)"
            " values (%s, 0, 0, 600, 'c.wav') returning id", (self.source_id,)
        ).fetchone()["id"]
        self.conn.execute(
            "insert into utterances (chunk_id, idx, start_sec, end_sec, text)"
            " values (%s, 0, 0, 5, '발화')", (chunk_id,)
        )
        segment_id = self.conn.execute(
            "insert into segments (chunk_id, idx, start_sec, end_sec, description,"
            " start_utterance_idx, end_utterance_idx) values (%s, 0, 0, 30, '설명', 0, 0) returning id",
            (chunk_id,),
        ).fetchone()["id"]
        self.conn.execute(
            """insert into embeddings (kind, ref_id, model, dim, vector, task_type)
               values ('segment', %s, 'm', 2, %s, 'RETRIEVAL_DOCUMENT')""",
            (segment_id, b"\x00\x00\x80\x3f\x00\x00\x00\x00"),
        )
        return chunk_id, segment_id

    def answered(self, segment_id: int) -> int:
        """답하기가 만든 것들 — 리허설마다 지워져야 하는 것."""
        run_id = self.conn.execute(
            "insert into runs (source_id) values (%s) returning id", (self.source_id,)
        ).fetchone()["id"]
        cluster_id = self.conn.execute(
            """insert into question_clusters (source_id, canonical_text, status, run_id)
               values (%s, '질문', 'REVIEW', %s) returning id""", (self.source_id, run_id)
        ).fetchone()["id"]
        question_id = self.conn.execute(
            "insert into questions (source_id, text, viewer_id, cluster_id)"
            " values (%s, '원문', 'v1', %s) returning id", (self.source_id, cluster_id)
        ).fetchone()["id"]
        self.conn.execute(
            "insert into question_likes (question_id, viewer_id) values (%s, 'v2')", (question_id,)
        )
        self.conn.execute(
            """insert into clips (run_id, segment_id, question_cluster_id, start_sec, end_sec,
                                  total_sec) values (%s, %s, %s, 0, 30, 26)""",
            (run_id, segment_id, cluster_id),
        )
        self.conn.execute(
            "insert into viewer_events (viewer_id, source_id, kind) values ('v1', %s, 'short_play')",
            (self.source_id,),
        )
        return run_id

    def call(self, stage: str, run_id: int | None, tokens: int) -> None:
        self.conn.execute(
            """insert into stage_calls (source_id, run_id, stage, model, input_tokens, total_tokens)
               values (%s, %s, %s, 'gemini-x', %s, %s)""",
            (self.source_id, run_id, stage, tokens, tokens),
        )

    def count(self, table: str, where: str = "", params: tuple = ()) -> int:
        return self.conn.execute(
            f"select count(*) as n from {table} {where}", params
        ).fetchone()["n"]


class ResetTest(DemoTestCase):
    def test_the_expensive_preparation_survives(self):
        """🔴 전사·구간 분할·임베딩은 편당 수십 분이다. 리허설마다 날리면 리허설을 못 한다."""
        chunk_id, segment_id = self.prepare()
        self.answered(segment_id)
        demo.reset(self.conn, self.source_id)
        self.assertEqual(self.count("chunks"), 1)
        self.assertEqual(self.count("utterances"), 1)
        self.assertEqual(self.count("segments"), 1)
        self.assertEqual(self.count("embeddings", "where kind = 'segment'"), 1)
        self.assertEqual(self.count("sources"), 1)

    def test_the_questions_and_everything_they_made_are_gone(self):
        _, segment_id = self.prepare()
        self.answered(segment_id)
        demo.reset(self.conn, self.source_id)
        for table in ("questions", "question_likes", "question_clusters", "runs", "clips",
                      "viewer_events"):
            self.assertEqual(self.count(table), 0, table)

    def test_answer_costs_do_not_become_preparation_costs(self):
        """🔴 이 테스트가 이 파일의 이유다.

        `stage_calls.run_id` 는 `on delete set null` 이다. run 을 먼저 지우면 답변에 쓴 호출이
        고아로 남고, `report.cost_split` 이 run_id 로 가르므로 **회차 준비 비용으로 둔갑한다.**
        리허설을 돌릴수록 준비비가 불어나는 거짓 숫자가 만들어진다.
        """
        _, segment_id = self.prepare()
        run_id = self.answered(segment_id)
        self.call("segment", None, 1_000)      # 회차 준비
        self.call("rank", run_id, 500_000)     # 답변 — 지워져야 한다
        demo.reset(self.conn, self.source_id)
        cost = report.cost_split(self.conn, self.cfg, self.source_id)
        self.assertEqual(cost["prepare"]["inputTokens"], 1_000)
        self.assertEqual(cost["answer"]["inputTokens"], 0)

    def test_resetting_twice_is_harmless(self):
        _, segment_id = self.prepare()
        self.answered(segment_id)
        demo.reset(self.conn, self.source_id)
        again = demo.reset(self.conn, self.source_id)
        self.assertEqual(sum(again.values()), 0)

    def test_another_video_is_untouched(self):
        other = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status)"
            " values ('다른 편', 'LECTURE', 'b.mp4', 'sha256:b', 'DONE') returning id"
        ).fetchone()["id"]
        self.conn.execute(
            "insert into questions (source_id, text, viewer_id) values (%s, '남의 질문', 'v9')",
            (other,),
        )
        _, segment_id = self.prepare()
        self.answered(segment_id)
        demo.reset(self.conn, self.source_id)
        self.assertEqual(self.count("questions"), 1)


class SeedTest(DemoTestCase):
    QUESTIONS = [
        {"group": "salary", "text": "연봉은 얼마인가요?"},
        {"group": "salary", "text": "초봉이 궁금합니다"},
        {"group": "process", "text": "전형 절차가 어떻게 되나요?"},
    ]

    def test_questions_go_in_unclustered(self):
        """🔴 [집계]를 여기서 돌리지 않는다 — 그 버튼을 누르는 것이 데모 2단계다."""
        demo.seed(self.conn, self.source_id, self.QUESTIONS)
        self.assertEqual(self.count("questions", "where cluster_id is null"), 3)
        self.assertEqual(self.count("question_clusters"), 0)

    def test_likes_make_demand_visible(self):
        # 전부 0이면 목록이 입력 순서로 보이고 "몇 명이 같은 걸 물었나" 가 화면에 안 나타난다.
        demo.seed(self.conn, self.source_id, self.QUESTIONS, likes_per_group=3)
        top = self.conn.execute(
            """select q.text, count(l.id) as likes from questions q
               left join question_likes l on l.question_id = q.id
               group by q.id order by likes desc limit 1"""
        ).fetchone()
        self.assertEqual(top["likes"], 3)

    def test_each_seeded_question_has_its_own_viewer(self):
        # 같은 시청자로 넣으면 레이트리밋과 좋아요 중복 제약에 걸리고, 수요도 거짓이 된다.
        demo.seed(self.conn, self.source_id, self.QUESTIONS)
        distinct = self.conn.execute(
            "select count(distinct viewer_id) as n from questions"
        ).fetchone()["n"]
        self.assertEqual(distinct, 3)

    def test_the_shipped_question_file_loads(self):
        path = Path(__file__).resolve().parents[2] / "eval" / "questions.json"
        loaded = demo.load_questions(path)
        self.assertGreater(len(loaded), 10)
        self.assertTrue(all(q["text"].strip() for q in loaded))

    def test_a_file_without_a_wrapper_also_loads(self):
        # 리허설용 질문을 급히 만들 때 배열만 적어도 돌아가야 한다.
        path = Path(self._tmp.name) / "q.json"
        path.write_text(json.dumps([{"text": "질문", "group": "etc"}]), encoding="utf-8")
        self.assertEqual(len(demo.load_questions(path)), 1)


class UnseedTest(DemoTestCase):
    """🔴 시드한 것만 지운다. 진짜 시청자 질문과 섞이면 수요 순위가 거짓이 되고,
    그건 이 제품이 화면에서 보여주려는 바로 그 숫자다."""

    def test_real_viewer_questions_survive(self):
        self.conn.execute(
            "insert into questions (source_id, text, viewer_id) values (%s, '진짜 질문', 'abc-uuid')",
            (self.source_id,),
        )
        demo.seed(self.conn, self.source_id, SeedTest.QUESTIONS)
        demo.unseed(self.conn, self.source_id)
        rows = [r["text"] for r in self.conn.execute("select text from questions")]
        self.assertEqual(rows, ["진짜 질문"])

    def test_the_seeded_likes_go_too(self):
        demo.seed(self.conn, self.source_id, SeedTest.QUESTIONS)
        demo.unseed(self.conn, self.source_id)
        self.assertEqual(self.count("question_likes"), 0)

    def test_an_empty_cluster_left_behind_is_cleaned_up(self):
        # 시드 질문만 붙어 있던 묶음은 빈 껍데기가 된다.
        cluster_id = self.conn.execute(
            "insert into question_clusters (source_id, canonical_text) values (%s, '연봉?') returning id",
            (self.source_id,),
        ).fetchone()["id"]
        demo.seed(self.conn, self.source_id, SeedTest.QUESTIONS)
        self.conn.execute("update questions set cluster_id = %s", (cluster_id,))
        demo.unseed(self.conn, self.source_id)
        self.assertEqual(self.count("question_clusters"), 0)

    def test_a_cluster_that_already_answered_is_kept(self):
        """🔴 run 이 붙은 묶음을 지우면 그 비용 기록까지 사라진다."""
        _, segment_id = self.prepare()
        self.answered(segment_id)
        demo.seed(self.conn, self.source_id, SeedTest.QUESTIONS)
        demo.unseed(self.conn, self.source_id)
        self.assertEqual(self.count("question_clusters"), 1)

    def test_another_videos_seed_is_untouched(self):
        other = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status)"
            " values ('다른 편', 'LECTURE', 'b.mp4', 'sha256:b', 'DONE') returning id"
        ).fetchone()["id"]
        demo.seed(self.conn, other, SeedTest.QUESTIONS)
        demo.seed(self.conn, self.source_id, SeedTest.QUESTIONS)
        demo.unseed(self.conn, self.source_id)
        self.assertEqual(self.count("questions"), 3)


class CheckTest(DemoTestCase):
    def test_it_reports_all_eight_steps(self):
        steps = demo.check(self.conn, self.source_id)
        self.assertEqual([s["step"] for s in steps], [1, 2, 3, 4, 5, 6, 7, 8])

    def test_a_video_with_nothing_prepared_fails_step_one(self):
        steps = {s["step"]: s for s in demo.check(self.conn, self.source_id)}
        self.assertFalse(steps[1]["ok"])

    def test_the_cross_video_step_needs_a_sibling(self):
        """🔴 데모에서 가장 똑똑해 보이는 장면인데 영상이 한 편이면 아예 안 나온다."""
        self.prepare()
        steps = {s["step"]: s for s in demo.check(self.conn, self.source_id)}
        self.assertFalse(steps[6]["ok"])
        self.assertIn("한 편도 없다", steps[6]["note"])

        other = self.conn.execute(
            "insert into sources (title, content_type, path, fingerprint, status, channel, published)"
            " values ('지난 편', 'LECTURE', 'b.mp4', 'sha256:b', 'DONE', '원티드', true) returning id"
        ).fetchone()["id"]
        chunk_id = self.conn.execute(
            "insert into chunks (source_id, idx, start_sec, end_sec, path)"
            " values (%s, 0, 0, 600, 'b.wav') returning id", (other,)
        ).fetchone()["id"]
        self.conn.execute(
            "insert into segments (chunk_id, idx, start_sec, end_sec, description,"
            " start_utterance_idx, end_utterance_idx) values (%s, 0, 0, 30, '설명', 0, 0)",
            (chunk_id,),
        )
        steps = {s["step"]: s for s in demo.check(self.conn, self.source_id)}
        self.assertTrue(steps[6]["ok"])

    def test_a_missing_source_is_said_plainly(self):
        steps = demo.check(self.conn, 999999)
        self.assertFalse(steps[0]["ok"])

    def test_seeding_unblocks_step_two(self):
        self.prepare()
        self.assertFalse({s["step"]: s for s in demo.check(self.conn, self.source_id)}[2]["ok"])
        demo.seed(self.conn, self.source_id, SeedTest.QUESTIONS)
        self.assertTrue({s["step"]: s for s in demo.check(self.conn, self.source_id)}[2]["ok"])


if __name__ == "__main__":
    unittest.main()
