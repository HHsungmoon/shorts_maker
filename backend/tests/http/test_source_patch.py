"""원본 정보 고치기 — 제목 · 영상 개요 · 유튜브 id (`PATCH /api/sources/{id}`).

🔴 여기서 지키는 것 둘:

  1. **보낸 필드만 고친다.** 제목만 바꾸려는 요청이 개요를 지우면 안 된다. 한 폼에서 왔더라도
     고친 값만 실려 오고, 안 실린 것은 "지워라" 가 아니라 "건드리지 마라" 다.
  2. **제목은 비울 수 없다.** 목록과 시청자 화면이 이 값으로 영상을 가리킨다 — 빈 제목은
     클릭할 것이 없는 줄이 된다.

이 엔드포인트가 생긴 이유는 2026-09-19 에 겪은 일이다. 파일로 등록한 원본의 제목은 파일
이름(`MVqTWMg4n0o`)이었고 그게 시청자 화면에 그대로 나갔다. 유튜브 id 도 같은 자리에서
채운다 — 전에는 그걸 넣을 길이 psql 뿐이었다.
"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from shorts_maker.db import store
from shorts_maker.http import deps, server

from ..support import make_config, reset_db


class SourcePatchTest(unittest.TestCase):
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
                "insert into sources (title, content_type, path, fingerprint, status, context)"
                " values ('MVqTWMg4n0o', 'LECTURE', 'MVqTWMg4n0o.mp4', 'sha256:a', 'DONE', '입학설명회')"
                " returning id"
            ).fetchone()["id"]
            conn.commit()

    def stored(self) -> dict:
        with store.connect(self.url) as conn:
            return dict(
                conn.execute(
                    "select title, context, youtube_id from sources where id = %s", (self.source_id,)
                ).fetchone()
            )

    def test_the_title_can_be_fixed_without_touching_the_context(self):
        response = self.client.patch(
            f"/api/sources/{self.source_id}", json={"title": "2027 서강대 입학전형 설명회"}
        )
        self.assertEqual(200, response.status_code)
        row = self.stored()
        self.assertEqual("2027 서강대 입학전형 설명회", row["title"])
        # 🔴 보내지 않은 개요가 살아 있어야 한다.
        self.assertEqual("입학설명회", row["context"])

    def test_the_context_can_be_fixed_without_touching_the_title(self):
        self.client.patch(f"/api/sources/{self.source_id}", json={"context": "고3 대상 설명회"})
        row = self.stored()
        self.assertEqual("MVqTWMg4n0o", row["title"])
        self.assertEqual("고3 대상 설명회", row["context"])

    def test_an_empty_context_clears_it_but_an_absent_one_does_not(self):
        self.client.patch(f"/api/sources/{self.source_id}", json={"context": "   "})
        self.assertIsNone(self.stored()["context"])

    def test_the_title_cannot_be_emptied(self):
        response = self.client.patch(f"/api/sources/{self.source_id}", json={"title": "  "})
        self.assertEqual(400, response.status_code)
        self.assertEqual("MVqTWMg4n0o", self.stored()["title"])

    def test_a_youtube_url_is_stored_as_the_id_alone(self):
        """🔴 URL 을 그대로 저장하면 임베드가 조용히 깨진다 — 플레이어는 id 만 받는다."""
        response = self.client.patch(
            f"/api/sources/{self.source_id}",
            json={"youtubeId": "https://www.youtube.com/watch?v=MVqTWMg4n0o&t=30s"},
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("MVqTWMg4n0o", self.stored()["youtube_id"])

    def test_a_foreign_url_is_refused(self):
        response = self.client.patch(
            f"/api/sources/{self.source_id}", json={"youtubeId": "https://example.com/watch?v=abc"}
        )
        self.assertEqual(400, response.status_code)
        self.assertIsNone(self.stored()["youtube_id"])

    def test_an_empty_youtube_id_clears_it(self):
        self.client.patch(f"/api/sources/{self.source_id}", json={"youtubeId": "MVqTWMg4n0o"})
        self.client.patch(f"/api/sources/{self.source_id}", json={"youtubeId": ""})
        self.assertIsNone(self.stored()["youtube_id"])

    def test_a_request_that_changes_nothing_is_refused(self):
        """빈 요청을 200 으로 받아 주면 화면이 저장됐다고 말하고 아무 일도 안 일어난다."""
        response = self.client.patch(f"/api/sources/{self.source_id}", json={})
        self.assertEqual(400, response.status_code)

    def test_a_missing_source_is_404(self):
        response = self.client.patch("/api/sources/9999", json={"title": "없는 영상"})
        self.assertEqual(404, response.status_code)


if __name__ == "__main__":
    unittest.main()
