"""발행하면 추천용 색인을 만든다 (update_plan D13).

🔴 여기서 지키는 것: **색인이 실패해도 발행은 된다.** 크리에이터의 행동을 임베딩 할당량에 묶지 않는다.
빠진 색인은 `sm answers index-clips` 로 채운다.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from shorts_maker.answers import suggest
from shorts_maker.db import store
from shorts_maker.http import deps, server

from ..support import make_config, reset_db


class PublishIndexTest(unittest.TestCase):
    def setUp(self):
        self.url = reset_db()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._saved = deps.cfg
        # 🔴 키가 비어 있다 — 실제 임베딩은 반드시 실패한다. 그래도 발행돼야 한다는 게 이 파일의 요지다.
        deps.cfg = make_config(root, web_dir=root / "none", admin_password="", api_token="", gemini_api_key="")
        self.client = TestClient(server.app)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(lambda: setattr(deps, "cfg", self._saved))
        with store.connect(self.url) as conn:
            source = conn.execute(
                "insert into sources (title, content_type, path, fingerprint, status, published)"
                " values ('강연', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE', true) returning id"
            ).fetchone()["id"]
            run = conn.execute("insert into runs (source_id) values (%s) returning id", (source,)).fetchone()["id"]
            chunk = conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path) values (%s, 0, 0, 600, 'c.wav') returning id",
                (source,),
            ).fetchone()["id"]
            conn.execute(
                "insert into utterances (chunk_id, idx, start_sec, end_sec, text) values (%s, 0, 0, 5, '쏘카의 프로덕트는')",
                (chunk,),
            )
            segment = conn.execute(
                "insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx, end_utterance_idx)"
                " values (%s, 0, 0, 5, 0, 0) returning id", (chunk,)
            ).fetchone()["id"]
            self.clip_id = conn.execute(
                "insert into clips (run_id, segment_id, start_sec, end_sec, rendered) values (%s, %s, 0, 5, true) returning id",
                (run, segment),
            ).fetchone()["id"]
            conn.commit()

    def published_at(self):
        with store.connect(self.url) as conn:
            return conn.execute("select published_at from clips where id = %s", (self.clip_id,)).fetchone()["published_at"]

    def test_a_failing_index_does_not_block_publishing(self):
        response = self.client.post(f"/api/clips/{self.clip_id}/publish")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"published": True, "indexed": False})
        self.assertIsNotNone(self.published_at())

    def test_the_failure_is_left_in_stage_calls(self):
        # 조용히 삼키면 "왜 이 숏폼은 추천이 안 뜨나" 를 알 수 없다.
        self.client.post(f"/api/clips/{self.clip_id}/publish")
        with store.connect(self.url) as conn:
            row = conn.execute(
                "select error, params from stage_calls where stage = 'embed' order by id desc limit 1"
            ).fetchone()
        self.assertIsNotNone(row["error"])
        self.assertEqual(row["params"]["clip_id"], self.clip_id)

    def test_a_crash_inside_indexing_still_publishes(self):
        with mock.patch.object(suggest, "index_clip", side_effect=RuntimeError("DB 흔들림")):
            response = self.client.post(f"/api/clips/{self.clip_id}/publish")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["indexed"])
        self.assertIsNotNone(self.published_at())

    def test_a_successful_index_is_reported(self):
        with mock.patch.object(suggest, "index_clip", return_value="indexed") as index:
            response = self.client.post(f"/api/clips/{self.clip_id}/publish")
        self.assertTrue(response.json()["indexed"])
        self.assertEqual(index.call_args.args[2], self.clip_id)


if __name__ == "__main__":
    unittest.main()
