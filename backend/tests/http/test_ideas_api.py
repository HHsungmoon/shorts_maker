"""관리자 숏폼 — 컨셉 고르기 · AI 추천 엔드포인트 (tease §5-11).

🔴 지키는 것:
  1. 모르는 컨셉은 **잡을 띄우기 전에** 422 — 큐에 넣은 뒤 실패하면 사람은 한참 기다린 뒤에야 오타를 안다
  2. 고른 컨셉은 run 에 남는다(`runs.concept`) — 그 run 의 자르기 · AI 카피가 읽는다
  3. 추천은 구간이 있어야 돈다(없으면 409). 결과는 상세의 ideas 로 읽는다
"""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from shorts_maker.adapters import gemini
from shorts_maker.db import store
from shorts_maker.http import deps, server
from shorts_maker.pipeline import concepts

from ..support import make_config, reset_db

USAGE = {
    "input_tokens": 300, "output_tokens": 40, "thinking_tokens": 0,
    "total_tokens": 340, "cached_tokens": 0, "attempts": 1,
}


class IdeasApiTest(unittest.TestCase):
    def setUp(self):
        self.url = reset_db()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._saved = deps.cfg
        deps.cfg = make_config(root, web_dir=root / "none", admin_password="", api_token="")
        self.client = TestClient(server.app)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(lambda: setattr(deps, "cfg", self._saved))
        with store.connect(self.url) as conn:
            self.source_id = conn.execute(
                "insert into sources (title, content_type, path, fingerprint, status)"
                " values ('채용설명회', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE') returning id"
            ).fetchone()["id"]
            self.chunk_id = conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path)"
                " values (%s, 0, 0, 60, 'a-0.mp4') returning id", (self.source_id,)
            ).fetchone()["id"]
            conn.commit()

    def segment(self) -> int:
        with store.connect(self.url) as conn:
            segment_id = conn.execute(
                """insert into segments (chunk_id, idx, start_sec, end_sec, description,
                                         start_utterance_idx, end_utterance_idx)
                   values (%s, 0, 0, 30, '서류 기준 세 가지', 0, 1) returning id""",
                (self.chunk_id,),
            ).fetchone()["id"]
            conn.commit()
        return segment_id

    def wait_for(self, job_id: str) -> dict:
        for _ in range(100):
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("DONE", "FAILED"):
                return job
            time.sleep(0.02)
        self.fail("잡이 끝나지 않았다")

    def test_an_unknown_concept_is_refused_before_the_job(self):
        self.segment()
        with mock.patch.object(gemini, "generate_json") as model:
            response = self.client.post(f"/api/sources/{self.source_id}/rank", json={"concept": "funny"})
        self.assertEqual(422, response.status_code)
        self.assertIn("모르는 컨셉", response.json()["detail"])
        model.assert_not_called()

    def test_the_concept_is_kept_on_the_run(self):
        self.segment()
        payload = json.dumps({"excluded": [], "ranked": [{"idx": 0, "score": 80, "reason": "기준이 분명하다"}]})
        with mock.patch.object(gemini, "generate_json", return_value=(payload, USAGE, 900)) as model:
            started = self.client.post(f"/api/sources/{self.source_id}/rank", json={"concept": "info", "criteria": "서류"})
            job = self.wait_for(started.json()["id"])
        self.assertEqual("DONE", job["status"], job["error"])
        self.assertIn(concepts.BY_KEY["info"]["goal"], model.call_args.args[1])
        detail = self.client.get(f"/api/sources/{self.source_id}").json()
        self.assertEqual("info", detail["runs"][0]["concept"])

    def test_ideas_need_segments(self):
        response = self.client.post(f"/api/sources/{self.source_id}/ideas")
        self.assertEqual(409, response.status_code)
        self.assertEqual(404, self.client.post("/api/sources/9999/ideas").status_code)

    def test_ideas_come_back_on_the_detail(self):
        segment_id = self.segment()
        detail = self.client.get(f"/api/sources/{self.source_id}").json()
        self.assertIsNone(detail["ideas"])
        self.assertEqual(list(concepts.KEYS), [c["key"] for c in detail["concepts"]])

        payload = json.dumps({
            "concepts": [{"concept": "info", "fit": 85, "note": "기준이 많다"}],
            "ideas": [{"concept": "info", "title": "서류 탈락 세 가지", "segment": 0,
                       "criteria": "서류 기준이 나오는 대목", "reason": "숫자가 분명하다"}],
        }, ensure_ascii=False)
        with mock.patch.object(gemini, "generate_json", return_value=(payload, USAGE, 900)):
            started = self.client.post(f"/api/sources/{self.source_id}/ideas")
            self.assertEqual("ideas", started.json()["kind"])
            job = self.wait_for(started.json()["id"])
        self.assertEqual("DONE", job["status"], job["error"])

        ideas = self.client.get(f"/api/sources/{self.source_id}").json()["ideas"]
        self.assertFalse(ideas["stale"])
        self.assertEqual(segment_id, ideas["ideas"][0]["segment_id"])
        self.assertEqual(85, next(c for c in ideas["concepts"] if c["concept"] == "info")["fit"])


if __name__ == "__main__":
    unittest.main()
