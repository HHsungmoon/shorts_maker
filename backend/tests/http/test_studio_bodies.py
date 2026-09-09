"""프롬프트와 원본 응답 (마이그레이션 005).

🔴 여기서 지키는 것 둘:
  1. **목록에는 본문이 오지 않는다.** 한 건이 수십 KB 라 화면 한 번에 수 MB 가 실린다.
     `has_body` 로 펼칠 것이 있는지만 알려 준다
  2. **비용 계산이 본문을 끌어오지 않는다.** 숫자 하나 보려고 전 기록의 프롬프트를 메모리에
     올리면 안 된다

이게 있는 이유는 2026-09-09 에 겪은 일이다. 구간 검색이 왜 답을 놓치는지 알아내려고 임시
스크립트로 같은 호출을 두 번 재현해야 했다 — 모델이 무엇을 보고 무엇을 답했는지가 어디에도
남지 않았다. 무료 등급에서는 그 재현이 하루 할당량을 깎는다.
"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from shorts_maker.db import store
from shorts_maker.http import deps, server

from ..support import make_config, reset_db

BIG_PROMPT = "구간 머리글\n[0] 첫 줄\n[1] 둘째 줄\n" * 200


class StageCallBodyTest(unittest.TestCase):
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
                " values ('강연', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE') returning id"
            ).fetchone()["id"]
            self.with_body = conn.execute(
                """insert into stage_calls (source_id, stage, model, input_tokens, output_tokens,
                                           thinking_tokens, total_tokens, latency_ms, prompt, response)
                   values (%s, 'classify', 'gemini-x', 100, 20, 0, 120, 900, %s, %s) returning id""",
                (self.source_id, BIG_PROMPT, '{"route": "retrieval"}'),
            ).fetchone()["id"]
            self.without_body = conn.execute(
                "insert into stage_calls (source_id, stage, latency_ms)"
                " values (%s, 'render', 4000) returning id", (self.source_id,)
            ).fetchone()["id"]
            conn.commit()

    def test_the_listing_says_which_rows_have_a_body_but_omits_it(self):
        detail = self.client.get(f"/api/sources/{self.source_id}").json()
        by_id = {call["id"]: call for call in detail["stageCalls"]}
        self.assertTrue(by_id[self.with_body]["has_body"])
        self.assertFalse(by_id[self.without_body]["has_body"])
        # 🔴 본문 자체는 없어야 한다.
        self.assertNotIn("prompt", by_id[self.with_body])
        self.assertNotIn("response", by_id[self.with_body])

    def test_the_body_endpoint_returns_the_whole_prompt(self):
        body = self.client.get(f"/api/stage-calls/{self.with_body}").json()
        self.assertEqual(body["prompt"], BIG_PROMPT)
        self.assertEqual(body["response"], '{"route": "retrieval"}')
        self.assertEqual(body["stage"], "classify")

    def test_a_row_without_a_body_answers_with_nulls_not_an_error(self):
        # 본문이 없는 것과 없는 행을 구분해야 한다 — 화면이 다르게 그린다.
        body = self.client.get(f"/api/stage-calls/{self.without_body}").json()
        self.assertIsNone(body["prompt"])
        self.assertIsNone(body["response"])

    def test_an_unknown_call_is_404(self):
        self.assertEqual(self.client.get("/api/stage-calls/999999").status_code, 404)

    def test_the_cost_endpoint_does_not_read_bodies(self):
        # 🔴 비용은 토큰 합이다. 프롬프트를 끌어올 이유가 없다.
        cost = self.client.get("/api/cost").json()
        self.assertEqual(cost["inputTokens"], 100)
        self.assertEqual(cost["llmCalls"], 1)


if __name__ == "__main__":
    unittest.main()
