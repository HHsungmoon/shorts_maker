"""대사 두 층의 API — 음성 인식 한 번 · 쓰는 대사 고르기 · 비교 (tease §5-1).

🔴 지키는 것: ① 다 들은 원본에 [음성 인식] 을 누르면 **큐에 넣기 전에** 409 — 같은 오디오는 한 번뿐이다
② 다른 작업이 원본을 쥐고 있으면(음성 인식이 도는 중) 대사를 바꾸지 못한다 ③ 원본 화면이 두 대사의 유무와
남은 음성 인식 범위를 준다 — 버튼이 그것으로 정해진다.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from shorts_maker.db import store
from shorts_maker.http import deps, server
from shorts_maker.pipeline import transcripts

from ..support import make_config, reset_db


class TranscriptApiTest(unittest.TestCase):
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
                "insert into sources (title, content_type, path, fingerprint, status, youtube_id)"
                " values ('삼성', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE', 'uL_OQBAxm5o') returning id"
            ).fetchone()["id"]
            conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path) values (%s, 0, 0, 100, 'c.wav')",
                (self.source_id,),
            )
            transcripts.store_rows(conn, self.source_id, "whisper", [
                {"start_sec": 1, "end_sec": 2, "text": "음성 인식",
                 "words": [{"start": 1, "end": 2, "text": " 음성 인식"}]}], replace=False)
            conn.execute(
                "insert into stt_spans (source_id, start_sec, end_sec, model) values (%s, 0, 100, 'faster-whisper:small')",
                (self.source_id,),
            )
            transcripts.use(conn, self.source_id, "whisper")
            conn.commit()

    def test_stt_on_fully_heard_audio_is_refused_before_queueing(self):
        with mock.patch.object(deps.queue, "submit") as submit:
            response = self.client.post(f"/api/sources/{self.source_id}/stt", json={"language": "ko"})
        self.assertEqual(409, response.status_code)
        self.assertIn("이미", response.json()["detail"])
        submit.assert_not_called()

    def test_the_source_view_tells_what_exists_and_what_is_left(self):
        body = self.client.get(f"/api/sources/{self.source_id}").json()
        self.assertEqual(body["transcripts"], {"youtube": 0, "whisper": 1})
        self.assertEqual((body["stt"]["heardSec"], body["stt"]["remainingSec"]), (100, 0))
        self.assertEqual(body["source"]["caption_source"], "whisper")

    def test_switching_needs_a_kept_transcript(self):
        response = self.client.post(f"/api/sources/{self.source_id}/transcript", json={"origin": "youtube"})
        self.assertEqual(400, response.status_code)
        with store.connect(self.url) as conn:
            transcripts.store_rows(conn, self.source_id, "youtube", [
                {"start_sec": 1, "end_sec": 2, "text": "자막"}], replace=True)
            conn.commit()
        response = self.client.post(f"/api/sources/{self.source_id}/transcript", json={"origin": "youtube"})
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.json()["utterances"], 1)

    def test_switching_while_the_source_is_busy_is_refused(self):
        with store.connect(self.url) as conn:
            transcripts.store_rows(conn, self.source_id, "youtube", [
                {"start_sec": 1, "end_sec": 2, "text": "자막"}], replace=True)
            conn.commit()
            # 다른 연결이 원본을 쥐고 있다 — 음성 인식이 도는 중이다.
            with store.source_lock(conn, self.source_id, "전사"):
                response = self.client.post(f"/api/sources/{self.source_id}/transcript", json={"origin": "youtube"})
        self.assertEqual(409, response.status_code)

    def test_the_diff_compares_what_whisper_heard(self):
        with store.connect(self.url) as conn:
            transcripts.store_rows(conn, self.source_id, "youtube", [
                {"start_sec": 1, "end_sec": 2, "text": "음성 인직",
                 "words": [{"start": 1, "end": 2, "text": " 음성 인직"}]}], replace=True)
            conn.commit()
        body = self.client.get(f"/api/sources/{self.source_id}/transcript-diff").json()
        self.assertTrue(body["available"])
        self.assertEqual([(s["caption"], s["whisper"]) for s in body["spots"]], [("음성 인직", "음성 인식")])
        self.assertEqual(404, self.client.get("/api/sources/9999/transcript-diff").status_code)


if __name__ == "__main__":
    unittest.main()
