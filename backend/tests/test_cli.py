"""CLI 가 파이프라인 함수를 **맞는 인자로** 부르는가.

v8 에서 `requested_by_admin_id` 를 지울 때 API·pipeline 은 고쳤는데 CLI 만 옛 인자를 넘기고
있었고, CLI 를 도는 테스트가 없어 한 달 가까이 `sm rank run` 이 TypeError 로 죽었다.
`autospec=True` 가 시그니처를 강제하므로, 다음에 인자를 바꾸면 여기서 먼저 걸린다.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from shorts_maker import cli, config, ingest, ranking
from shorts_maker.db import store

from .support import make_config


class RankRunTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.cfg = make_config(Path(self._dir.name))
        with store.connect(self.cfg.db_path) as conn:
            store.apply_schema(conn)
            conn.execute(
                "insert into sources (title, content_type, path, fingerprint)"
                " values ('t', 'LECTURE', 'a.mp4', 'sha256:a')"
            )
            conn.execute(
                "insert into runs (source_id, status, ranked) values (1, 'DONE', ?)",
                (json.dumps({"ranked": [{"idx": 0, "score": 90, "reason": "r"}], "excluded": []}),),
            )
            conn.commit()

    def tearDown(self):
        self._dir.cleanup()

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
        self._dir = tempfile.TemporaryDirectory()
        self.cfg = make_config(Path(self._dir.name))
        with store.connect(self.cfg.db_path) as conn:
            store.apply_schema(conn)
            conn.execute(
                "insert into sources (title, content_type, path, fingerprint)"
                " values ('t', 'LECTURE', 'a.mp4', 'sha256:a')"
            )
            conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path) values (1, 0, 0, 60, 'c.wav')"
            )
            conn.execute("insert into stage_calls (source_id, stage, latency_ms) values (1, 'chunk', 1)")
            conn.commit()

    def tearDown(self):
        self._dir.cleanup()

    def test_replace_flag_reaches_add_chunk(self):
        with (
            mock.patch.object(config, "load", return_value=self.cfg),
            mock.patch.object(ingest, "add_chunk", autospec=True, return_value=1) as add,
            redirect_stdout(io.StringIO()),
        ):
            code = cli.main(["chunk", "add", "1", "--start", "0", "--end", "90", "--replace"])
        self.assertEqual(code, 0)
        self.assertEqual(add.call_args.args[2:], (1, 0.0, 90.0))
        self.assertEqual(add.call_args.kwargs, {"replace": True})
