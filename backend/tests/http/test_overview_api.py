"""영상 개요 초안 엔드포인트 (`POST /api/sources/{id}/overview`).

🔴 여기서 지키는 것 셋:

  1. **전제는 잡을 띄우기 전에 본다.** 큐에 넣고 실패하면 사용자는 배너가 빨개진 뒤에야
     "구간이 아직 없다" 를 안다(`add_source_from_url` 과 같은 이유)
  2. **초안은 잡 결과로만 온다.** DB 에 쓰지 않는다 — 화면이 입력칸에 붓고, 저장은 사람이 누른다
  3. 잡 큐를 지난다. 다른 LLM 호출과 같은 자리다 — 예외를 두면 그 예외가 다음 예외의 근거가 된다
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

from ..support import make_config, reset_db

USAGE = {
    "input_tokens": 300, "output_tokens": 40, "thinking_tokens": 0,
    "total_tokens": 340, "cached_tokens": 0, "attempts": 1,
}


class OverviewApiTest(unittest.TestCase):
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
                " values ('서강대 입학설명회', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE') returning id"
            ).fetchone()["id"]
            self.chunk_id = conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path)"
                " values (%s, 0, 0, 60, 'a-0.mp4') returning id", (self.source_id,)
            ).fetchone()["id"]
            conn.commit()

    def segment(self, description: str | None) -> None:
        with store.connect(self.url) as conn:
            conn.execute(
                """insert into segments (chunk_id, idx, start_sec, end_sec, description,
                                         start_utterance_idx, end_utterance_idx)
                   values (%s, 0, 0, 30, %s, 0, 1)""",
                (self.chunk_id, description),
            )
            conn.commit()

    def wait_for(self, job_id: str) -> dict:
        for _ in range(50):
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("DONE", "FAILED"):
                return job
            time.sleep(0.02)
        self.fail("잡이 끝나지 않았다")

    def test_a_missing_source_is_404(self):
        response = self.client.post("/api/sources/9999/overview")
        self.assertEqual(404, response.status_code)

    def test_it_refuses_before_the_summaries_exist(self):
        """🔴 409 — 요청은 옳고 지금 상태와 충돌할 뿐이다. 400 이면 사용자가 잘못 보낸 것처럼 읽힌다."""
        self.segment(None)
        response = self.client.post(f"/api/sources/{self.source_id}/overview")
        self.assertEqual(409, response.status_code)
        self.assertIn("주제 분할", response.json()["detail"])

    def test_the_draft_comes_back_on_the_job_and_is_not_saved(self):
        self.segment("전형 일정 안내")
        payload = json.dumps({"context": "2027학년도 서강대 입학전형 설명회."}, ensure_ascii=False)
        with mock.patch.object(gemini, "generate_json", return_value=(payload, USAGE, 900)):
            started = self.client.post(f"/api/sources/{self.source_id}/overview")
            self.assertEqual(200, started.status_code)
            self.assertEqual("overview", started.json()["kind"])
            job = self.wait_for(started.json()["id"])

        self.assertEqual("DONE", job["status"], job["error"])
        self.assertEqual("2027학년도 서강대 입학전형 설명회.", job["result"]["context"])
        # 🔴 저장하지 않는다. 사람이 읽고 PATCH 로 넣는다.
        with store.connect(self.url) as conn:
            stored = conn.execute(
                "select context from sources where id = %s", (self.source_id,)
            ).fetchone()["context"]
        self.assertIsNone(stored)


if __name__ == "__main__":
    unittest.main()
