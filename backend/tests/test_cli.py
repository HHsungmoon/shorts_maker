"""CLI 가 파이프라인 함수를 **맞는 인자로** 부르는가.

v8 에서 `requested_by_admin_id` 를 지울 때 API·pipeline 은 고쳤는데 CLI 만 옛 인자를 넘기고
있었고, CLI 를 도는 테스트가 없어 한 달 가까이 `sm rank run` 이 TypeError 로 죽었다.
`autospec=True` 가 시그니처를 강제하므로, 다음에 인자를 바꾸면 여기서 먼저 걸린다.

DB 는 비운 테스트 Postgres(tests/support.reset_db). CLI 가 `config.load()` 로 받는 cfg 의
database_url 이 그 DB 를 가리키므로 여기서 심은 행을 CLI 가 실제로 읽는다.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from psycopg.types.json import Jsonb

import importlib

from shorts_maker import cli, config
from shorts_maker.answers import clusters

main_module = importlib.import_module("shorts_maker.cli.main")
from shorts_maker.pipeline import ingest, ranking, segmentation, stt
from shorts_maker.db import store

from .support import make_config, reset_db


class RankRunTest(unittest.TestCase):
    def setUp(self):
        reset_db()
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.cfg = make_config(Path(self._dir.name))
        with store.connect(self.cfg.database_url) as conn:
            conn.execute(
                "insert into sources (title, content_type, path, fingerprint)"
                " values ('t', 'LECTURE', 'a.mp4', 'sha256:a')"
            )
            # ranked 는 jsonb — 문자열이 아니라 Jsonb 로 감싼 dict 를 넣는다.
            conn.execute(
                "insert into runs (source_id, status, ranked) values (1, 'DONE', %s)",
                (Jsonb({"ranked": [{"idx": 0, "score": 90, "reason": "r"}], "excluded": []}),),
            )
            conn.commit()

    def test_calls_run_for_source_with_its_real_signature(self):
        with (
            mock.patch.object(config, "load", return_value=self.cfg),
            mock.patch.object(ranking, "run_for_source", autospec=True, return_value=1) as run,
            redirect_stdout(io.StringIO()),
        ):
            code = cli.main(["rank", "run", "1", "--criteria", "핵심 논지"])
        self.assertEqual(code, 0)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[2:], (1, "핵심 논지"))


class ChunkAddTest(unittest.TestCase):
    def setUp(self):
        reset_db()
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.cfg = make_config(Path(self._dir.name))
        with store.connect(self.cfg.database_url) as conn:
            conn.execute(
                "insert into sources (title, content_type, path, fingerprint)"
                " values ('t', 'LECTURE', 'a.mp4', 'sha256:a')"
            )
            conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path) values (1, 0, 0, 60, 'c.wav')"
            )
            conn.execute("insert into stage_calls (source_id, stage, latency_ms) values (1, 'chunk', 1)")
            conn.commit()

    def test_replace_flag_reaches_add_chunks(self):
        # 🔴 구간은 사용자가 고르지 않는다 — 길이를 보고 코드가 나눈다. CLI 도 --start/--end 를 받지 않는다.
        with (
            mock.patch.object(config, "load", return_value=self.cfg),
            mock.patch.object(ingest, "add_chunks", autospec=True, return_value=[1, 2]) as add,
            redirect_stdout(io.StringIO()) as out,
        ):
            code = cli.main(["chunk", "add", "1", "--replace"])
        self.assertEqual(code, 0)
        self.assertEqual(add.call_args.args[2], 1)
        self.assertEqual(add.call_args.kwargs, {"replace": True})
        self.assertIn("조각 2개", out.getvalue())

    def test_stt_and_segment_take_a_source_id(self):
        # 청크 id 를 요구하면 사용자가 내부 구조를 알아야 한다.
        for argv, module, name in (
            (["stt", "run", "1"], stt, "run_for_source"),
            (["segment", "run", "1"], segmentation, "run_for_source"),
        ):
            with self.subTest(argv=argv):
                with (
                    mock.patch.object(config, "load", return_value=self.cfg),
                    mock.patch.object(module, name, autospec=True, return_value=[]),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(cli.main(argv), 0)


class AnswersCommandTest(unittest.TestCase):
    """`sm answers ...` 가 실제로 도는가.

    🔴 CLI 는 테스트가 없으면 조용히 썩는다 — v8 에서 `sm rank run` 이 한 달간 죽어 있었고,
    이번에도 `sm answers eval-cluster` 가 import 누락으로 죽은 채 스위트는 전부 통과했다.
    Gemini 는 부르지 않는다(대역). 여기서 보는 건 배선이지 묶기 품질이 아니다.
    """

    def setUp(self):
        self.url = reset_db()
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.cfg = make_config(Path(self._dir.name))
        with store.connect(self.cfg.database_url) as conn:
            conn.execute(
                "insert into sources (title, content_type, path, fingerprint, status)"
                " values ('강연', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE')"
            )
            conn.commit()

    def run_cli(self, argv, aggregate_result=None):
        with (
            mock.patch.object(config, "load", return_value=self.cfg),
            mock.patch.object(
                clusters, "aggregate", autospec=True,
                return_value=aggregate_result or {"newClusters": 0, "assigned": 0, "pending": 0, "theta": 0.85},
            ) as aggregate,
            redirect_stdout(io.StringIO()) as out,
        ):
            code = cli.main(argv)
        return code, out.getvalue(), aggregate

    def test_aggregate_calls_the_real_function_with_the_source_id(self):
        code, _, aggregate = self.run_cli(["answers", "aggregate", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(aggregate.call_args.args[2], 1)

    def test_list_reports_when_there_are_no_questions(self):
        with (
            mock.patch.object(config, "load", return_value=self.cfg),
            redirect_stdout(io.StringIO()) as out,
        ):
            code = cli.main(["answers", "list", "1"])
        self.assertEqual(code, 1)
        self.assertIn("질문이 없다", out.getvalue())

    def test_eval_inserts_the_whole_set_then_cleans_up(self):
        # 🔴 평가용 질문이 진짜 시청자 질문과 섞이면 수요 순위가 거짓이 된다. 기본은 지우는 것이다.
        seen = {}

        def fake_aggregate(conn, cfg, source_id):
            seen["theta"] = cfg.cluster_theta
            seen["inserted"] = conn.execute(
                "select count(*) as n from questions where source_id = %s", (source_id,)
            ).fetchone()["n"]
            return {"newClusters": 0, "assigned": 0, "pending": seen["inserted"], "theta": cfg.cluster_theta}

        with (
            mock.patch.object(config, "load", return_value=self.cfg),
            mock.patch.object(clusters, "aggregate", side_effect=fake_aggregate),
            redirect_stdout(io.StringIO()) as out,
        ):
            code = cli.main(["answers", "eval-cluster", "1", "--theta", "0.77"])
        self.assertEqual(code, 0)
        self.assertEqual(seen["theta"], 0.77)
        self.assertGreater(seen["inserted"], 10)
        self.assertIn("쪼개진 그룹", out.getvalue())
        with store.connect(self.cfg.database_url) as conn:
            left = conn.execute("select count(*) as n from questions").fetchone()["n"]
        self.assertEqual(left, 0)

    def test_eval_keeps_the_questions_when_asked(self):
        with (
            mock.patch.object(config, "load", return_value=self.cfg),
            mock.patch.object(clusters, "aggregate", autospec=True,
                              return_value={"newClusters": 0, "assigned": 0, "pending": 0, "theta": 0.85}),
            redirect_stdout(io.StringIO()),
        ):
            cli.main(["answers", "eval-cluster", "1", "--keep"])
        with store.connect(self.cfg.database_url) as conn:
            left = conn.execute("select count(*) as n from questions").fetchone()["n"]
        self.assertGreater(left, 10)

    def test_the_eval_set_parses_and_has_groups_worth_separating(self):
        payload = json.loads(main_module.EVAL_PATH.read_text(encoding="utf-8"))
        groups = {q["group"] for q in payload["questions"]}
        self.assertGreaterEqual(len(groups), 5)
        # 붙으면 안 되는 쌍이 실제로 들어 있어야 θ 의 상한을 잴 수 있다.
        self.assertIn("salary", groups)
        self.assertIn("overtime", groups)

    def test_eval_restores_real_questions_and_removes_its_clusters(self):
        """🔴 평가가 실제 데이터를 건드리면 안 된다.

        실제로 겪었다(2026-09-06): 사용자가 시청자 화면에 남긴 진짜 질문 하나가 평가 실행의
        집계에 함께 묶여, 평가용 문장에 맞춰 지어진 클러스터에 남았다.
        """
        with store.connect(self.cfg.database_url) as conn:
            real_cluster = conn.execute(
                "insert into question_clusters (source_id, canonical_text) values (1, '원래 있던 그룹?') returning id"
            ).fetchone()["id"]
            real_question = conn.execute(
                "insert into questions (source_id, text, viewer_id, cluster_id)"
                " values (1, '진짜 시청자 질문', 'v1', %s) returning id",
                (real_cluster,),
            ).fetchone()["id"]
            conn.commit()

        def fake_aggregate(conn, cfg, source_id):
            # 집계는 실제로 미분류 질문을 묶는다 — 평가용 질문에 진짜 질문이 딸려 들어가는 상황을 흉내낸다.
            made = conn.execute(
                "insert into question_clusters (source_id, canonical_text) values (%s, '평가가 지은 문장?') returning id",
                (source_id,),
            ).fetchone()["id"]
            conn.execute("update questions set cluster_id = %s where source_id = %s", (made, source_id))
            conn.commit()
            return {"newClusters": 1, "assigned": 1, "pending": 1, "theta": cfg.cluster_theta}

        with (
            mock.patch.object(config, "load", return_value=self.cfg),
            mock.patch.object(clusters, "aggregate", side_effect=fake_aggregate),
            redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(cli.main(["answers", "eval-cluster", "1"]), 0)

        self.assertIn("실제 질문 1개", out.getvalue())
        with store.connect(self.cfg.database_url) as conn:
            row = conn.execute(
                "select text, cluster_id from questions where id = %s", (real_question,)
            ).fetchone()
            names = [r["canonical_text"] for r in conn.execute("select canonical_text from question_clusters")]
        # 진짜 질문은 남고, 원래 소속으로 돌아왔고, 평가가 지은 클러스터는 사라졌다.
        self.assertEqual(row["text"], "진짜 시청자 질문")
        self.assertEqual(row["cluster_id"], real_cluster)
        self.assertEqual(names, ["원래 있던 그룹?"])
