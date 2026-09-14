"""사람 평가(쓸만함 / 아님) — 연타가 행을 불리지 않는다.

2026-09-14 버그: 버튼을 연달아 누르자 클립 하나에 "쓸만함" 14줄 · "아님" 4줄이 3초 안에 쌓였다.

🔴 여기서 지키는 것:
  1. **같은 평가를 반복하면 한 줄이다**
  2. **마음을 바꾼 기록은 남는다** — 평가 표는 append-only 이고 최신 행이 현재 평가다
  3. 🔴 **거의 동시에 온 두 요청도 한 줄이다.** 확인과 삽입 사이의 틈이 연타의 실체다
"""

import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from shorts_maker.db import store
from shorts_maker.http import deps, server

from ..support import make_config, reset_db


class ReviewTestCase(unittest.TestCase):
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
            source = conn.execute(
                "insert into sources (title, content_type, path, fingerprint, status)"
                " values ('강연', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE') returning id"
            ).fetchone()["id"]
            run = conn.execute("insert into runs (source_id) values (%s) returning id", (source,)).fetchone()["id"]
            chunk = conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path)"
                " values (%s, 0, 0, 600, 'c.wav') returning id", (source,)
            ).fetchone()["id"]
            segment = conn.execute(
                "insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx,"
                " end_utterance_idx) values (%s, 0, 0, 30, 0, 3) returning id", (chunk,)
            ).fetchone()["id"]
            self.clip_id = conn.execute(
                "insert into clips (run_id, segment_id, start_sec, end_sec) values (%s, %s, 0, 30) returning id",
                (run, segment),
            ).fetchone()["id"]
            conn.commit()

    def review(self, verdict: str, note: str | None = None) -> dict:
        response = self.client.post(f"/api/clips/{self.clip_id}/review", json={"verdict": verdict, "note": note})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def human_rows(self) -> list[str]:
        with store.connect(self.url) as conn:
            return [r["verdict"] for r in conn.execute(
                "select verdict from clip_reviews where clip_id = %s and reviewer = 'human' order by id",
                (self.clip_id,),
            )]


class IdempotencyTest(ReviewTestCase):
    def test_pressing_the_same_verdict_many_times_leaves_one_row(self):
        # 🔴 버그 재현: 실제로 14번 눌려 14줄이 됐다.
        first = self.review("OK")
        for _ in range(13):
            again = self.review("OK")
            self.assertFalse(again["created"])
            self.assertEqual(again["reviewId"], first["reviewId"])
        self.assertEqual(self.human_rows(), ["OK"])

    def test_changing_your_mind_is_recorded_and_the_latest_wins(self):
        self.review("OK")
        changed = self.review("NG")
        self.assertTrue(changed["created"])
        self.assertEqual(self.human_rows(), ["OK", "NG"])

    def test_going_back_and_forth_keeps_the_real_sequence(self):
        for verdict in ("OK", "NG", "NG", "OK", "OK"):
            self.review(verdict)
        self.assertEqual(self.human_rows(), ["OK", "NG", "OK"])

    def test_a_model_verdict_does_not_count_as_the_human_one(self):
        # 사람이 판정에 동의하는 것도 한 표다 — 모델 행을 보고 건너뛰면 일치율에서 사람 표가 사라진다.
        with store.connect(self.url) as conn:
            conn.execute(
                "insert into clip_reviews (clip_id, verdict, reviewer) values (%s, 'OK', 'llm')", (self.clip_id,)
            )
            conn.commit()
        self.assertTrue(self.review("OK")["created"])
        self.assertEqual(self.human_rows(), ["OK"])

    def test_a_different_note_is_a_new_review(self):
        self.review("OK", "자막이 좋다")
        self.assertTrue(self.review("OK", "시작이 어색하다")["created"])
        self.assertEqual(len(self.human_rows()), 2)

    def test_a_blank_note_is_the_same_as_no_note(self):
        # "" 와 null 을 다르게 보면 같은 평가가 두 줄이 된다.
        self.review("OK", None)
        self.assertFalse(self.review("OK", "   ")["created"])
        self.assertEqual(len(self.human_rows()), 1)

    def test_an_unknown_clip_is_404_and_a_bad_verdict_400(self):
        self.assertEqual(self.client.post("/api/clips/999999/review", json={"verdict": "OK"}).status_code, 404)
        self.assertEqual(
            self.client.post(f"/api/clips/{self.clip_id}/review", json={"verdict": "MAYBE"}).status_code, 400
        )


class ConcurrencyTest(ReviewTestCase):
    def test_a_request_arriving_mid_write_waits_and_then_skips(self):
        """🔴 연타의 실체. 확인과 삽입 사이에 틈이 있으면 두 요청이 둘 다 "아직 없음" 을 보고 둘 다 넣는다.

        앞 요청이 클립을 잠근 채 평가를 쓰는 중이라고 친다. 뒤 요청은 **기다려야 하고**, 앞 요청이
        커밋한 뒤에는 그 행을 보고 **건너뛰어야** 한다.
        """
        result: dict = {}

        def second_press():
            result["body"] = self.client.post(
                f"/api/clips/{self.clip_id}/review", json={"verdict": "OK"}
            ).json()

        with store.connect(self.url) as conn:
            conn.execute("select id from clips where id = %s for update", (self.clip_id,))
            thread = threading.Thread(target=second_press)
            thread.start()
            time.sleep(0.5)
            self.assertTrue(thread.is_alive(), "잠금을 기다리지 않고 지나갔다 — 틈이 남아 있다")
            conn.execute("insert into clip_reviews (clip_id, verdict) values (%s, 'OK')", (self.clip_id,))
            conn.commit()
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertFalse(result["body"]["created"])
        self.assertEqual(self.human_rows(), ["OK"])


if __name__ == "__main__":
    unittest.main()
