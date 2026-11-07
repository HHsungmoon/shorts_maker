"""숏폼 편집 API(http/layouts.py) — 명세 저장 · 미리보기 · 시리즈 · 배지.

지키는 것:
  1. **열기만 해서는 DB 가 바뀌지 않는다.** 명세가 없는 클립을 열면 초안을 주고(`saved: false`) 저장은 PUT 이 한다.
  2. 미리보기는 행동이다 — 보기 전용은 새로 그리지 못하고(403), 그려 둔 PNG 는 읽는다.
  3. 배지는 이미지 바이트를 직접 보고 받는다(헤더만 믿지 않는다).
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from shorts_maker.adapters import ffmpeg, gemini
from shorts_maker.db import store
from shorts_maker.http import auth, deps, server

from ..pipeline.test_templates import card as card_spec
from ..support import make_config, reset_db

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
ADMIN = "layout-admin-password"
READONLY = "layout-readonly-password"


class LayoutApiTestCase(unittest.TestCase):
    passwords: tuple[str, str] = ("", "")

    def setUp(self):
        self.url = reset_db()
        auth.reset_throttle()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._saved = deps.cfg
        deps.cfg = make_config(
            root, web_dir=root / "none", admin_password=self.passwords[0], readonly_password=self.passwords[1],
            api_token="", cookie_secure=False,
        )
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(lambda: setattr(deps, "cfg", self._saved))
        self.addCleanup(auth.reset_throttle)
        deps.cfg.source_dir.mkdir(parents=True, exist_ok=True)
        (deps.cfg.source_dir / "a.mp4").write_bytes(b"video")
        for name, value in (("video_size", (1920, 1080)), ("font_available", True)):
            patcher = mock.patch.object(ffmpeg, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.still = mock.patch.object(
            ffmpeg, "render_still", side_effect=lambda dst, *a, **k: Path(dst).write_bytes(PNG)
        ).start()
        self.addCleanup(mock.patch.stopall)
        self.client = TestClient(server.app)

        with store.connect(self.url) as conn:
            self.source_id = conn.execute(
                "insert into sources (title, content_type, path, fingerprint, status, channel)"
                " values ('잡소리 ep.48', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE', '채널 원티드') returning id"
            ).fetchone()["id"]
            chunk_id = conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path) values (%s, 0, 0, 3000, 'c.wav')"
                " returning id", (self.source_id,),
            ).fetchone()["id"]
            segment_id = conn.execute(
                "insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx, end_utterance_idx)"
                " values (%s, 0, 0, 800, 0, 7) returning id", (chunk_id,),
            ).fetchone()["id"]
            run_id = conn.execute("insert into runs (source_id) values (%s) returning id", (self.source_id,)).fetchone()["id"]
            self.clip_id = conn.execute(
                "insert into clips (run_id, segment_id, start_sec, end_sec, title)"
                " values (%s, %s, 10, 40, '면접관이 말하는 합격 답변') returning id", (run_id, segment_id),
            ).fetchone()["id"]
            self.template_id = conn.execute(
                "insert into templates (name, spec) values ('잡소리 카드', %s) returning id", (Jsonb(card_spec(default=852)),)
            ).fetchone()["id"]
            self.series_id = conn.execute(
                "insert into series (name, template_id) values ('잡소리', %s) returning id", (self.template_id,)
            ).fetchone()["id"]
            conn.commit()

    def stored(self, sql: str, *params):
        with store.connect(self.url) as conn:
            return conn.execute(sql, params).fetchone()

    def opened(self) -> dict:
        response = self.client.get(f"/api/clips/{self.clip_id}/layout")
        self.assertEqual(200, response.status_code, response.text)
        return response.json()


class OpenAndSaveTest(LayoutApiTestCase):
    def test_opening_gives_a_draft_without_writing(self):
        body = self.opened()
        self.assertFalse(body["saved"])
        self.assertEqual(self.template_id, body["layout"]["template_id"])
        self.assertEqual("출처 : 채널 원티드", body["layout"]["credit"])
        self.assertEqual({"top": 494, "height": 852}, body["geometry"]["boxes"]["slot"])
        self.assertIsNone(self.stored("select layout from clips where id = %s", self.clip_id)["layout"])

    def test_the_draft_follows_the_series_of_the_source(self):
        with store.connect(self.url) as conn:
            conn.execute("update sources set series_id = %s where id = %s", (self.series_id, self.source_id))
            conn.commit()
        self.assertEqual(self.series_id, self.opened()["layout"]["series_id"])

    def test_saving_stores_the_normalized_layout(self):
        draft = self.opened()["layout"] | {"series_id": self.series_id, "headline": ["  첫 줄  ", "둘째 줄"]}
        response = self.client.put(f"/api/clips/{self.clip_id}/layout", json={"layout": draft})
        self.assertEqual(200, response.status_code, response.text)
        stored = self.stored("select layout from clips where id = %s", self.clip_id)["layout"]
        self.assertEqual(["첫 줄", "둘째 줄"], stored["headline"])
        body = response.json()
        self.assertTrue(body["saved"])
        # 렌더한 적이 없으니 다시 렌더해야 한다.
        self.assertTrue(body["stale"])

    def test_the_first_series_picked_becomes_the_source_default(self):
        draft = self.opened()["layout"] | {"series_id": self.series_id}
        self.client.put(f"/api/clips/{self.clip_id}/layout", json={"layout": draft})
        self.assertEqual(self.series_id, self.stored("select series_id from sources where id = %s", self.source_id)["series_id"])

    def test_a_bad_layout_is_refused_with_a_reason(self):
        draft = self.opened()["layout"] | {"slot_height": 2000}
        response = self.client.put(f"/api/clips/{self.clip_id}/layout", json={"layout": draft})
        self.assertEqual(422, response.status_code)
        self.assertIn("칸 높이", response.json()["detail"])

    def test_an_unknown_template_is_refused(self):
        draft = self.opened()["layout"] | {"template_id": 9999}
        self.assertEqual(422, self.client.put(f"/api/clips/{self.clip_id}/layout", json={"layout": draft}).status_code)

    def test_deleting_goes_back_to_blur(self):
        draft = self.opened()["layout"]
        self.client.put(f"/api/clips/{self.clip_id}/layout", json={"layout": draft})
        self.assertEqual(200, self.client.delete(f"/api/clips/{self.clip_id}/layout").status_code)
        self.assertIsNone(self.stored("select layout from clips where id = %s", self.clip_id)["layout"])

    def test_a_missing_source_file_is_a_conflict(self):
        (deps.cfg.source_dir / "a.mp4").unlink()
        self.assertEqual(409, self.client.get(f"/api/clips/{self.clip_id}/layout").status_code)


class PreviewTest(LayoutApiTestCase):
    def test_a_preview_is_drawn_once_and_then_served(self):
        draft = self.opened()["layout"]
        first = self.client.post(f"/api/clips/{self.clip_id}/layout/preview", json={"layout": draft})
        self.assertEqual(200, first.status_code, first.text)
        url = first.json()["cardUrl"]
        self.assertEqual(PNG, self.client.get(url).content)
        # 같은 명세면 다시 그리지 않는다 — 단계 기록도 한 번이다.
        self.client.post(f"/api/clips/{self.clip_id}/layout/preview", json={"layout": draft})
        self.assertEqual(1, self.still.call_count)
        calls = self.stored("select count(*) as n from stage_calls where stage = 'preview'")["n"]
        self.assertEqual(1, calls)
        # 열기 화면도 그려 둔 PNG 를 찾는다.
        self.assertEqual(url, self.opened()["cardUrl"])

    def test_the_preview_does_not_save(self):
        draft = self.opened()["layout"] | {"headline": ["저장 안 됨", ""]}
        self.client.post(f"/api/clips/{self.clip_id}/layout/preview", json={"layout": draft})
        self.assertIsNone(self.stored("select layout from clips where id = %s", self.clip_id)["layout"])

    def test_a_missing_font_is_said_before_drawing_squares(self):
        draft = self.opened()["layout"]
        with mock.patch.object(ffmpeg, "font_available", return_value=False):
            response = self.client.post(f"/api/clips/{self.clip_id}/layout/preview", json={"layout": draft})
        self.assertEqual(422, response.status_code)
        self.still.assert_not_called()

    def test_card_names_are_checked(self):
        self.assertEqual(404, self.client.get("/api/cards/..%2F..%2Fsecret.png").status_code)
        self.assertEqual(404, self.client.get("/api/cards/abc.png").status_code)


class ScenesTest(LayoutApiTestCase):
    """장면 나누기(tease §5-10 "장면마다 창") — ffmpeg 장면 점수로 경계를 잡고 보낸 명세의 창을 장면마다 나눈다."""

    def split(self, draft: dict, scores) -> dict:
        with mock.patch.object(ffmpeg, "scene_scores", return_value=scores) as probe:
            response = self.client.post(f"/api/clips/{self.clip_id}/layout/scenes", json={"layout": draft})
        self.assertEqual(200, response.status_code, response.text)
        # 클립 10~40초 = 원본 그 구간을 잰다.
        self.assertEqual((10.0, 30.0), probe.call_args.args[1:3])
        return response.json()

    def test_the_draft_is_split_at_the_cuts_without_saving(self):
        draft = self.opened()["layout"]
        body = self.split(draft, [(8.27, 0.39), (19.28, 0.41), (29.95, 0.46)])
        windows = body["layout"]["windows"]
        self.assertEqual([0.0, 8.27, 19.28], [w["start"] for w in windows])
        # 새 장면은 옛 상자를 물려받는다 — 나누기만 해서는 화면이 바뀌지 않는다.
        self.assertEqual({draft["windows"][0]["x"]}, {w["x"] for w in windows})
        self.assertEqual(3, len(body["geometry"]["crops"]))
        self.assertAlmostEqual(0.05, body["scenes"]["tailFlash"], places=2)
        self.assertIsNone(self.stored("select layout from clips where id = %s", self.clip_id)["layout"])
        self.assertEqual(1, self.stored("select count(*) as n from stage_calls where stage = 'scenes'")["n"])

    def test_a_split_layout_saves_and_reopens(self):
        draft = self.split(self.opened()["layout"], [(8.27, 0.39)])["layout"]
        draft["windows"][1] |= {"x": 0.2, "by": "person"}
        response = self.client.put(f"/api/clips/{self.clip_id}/layout", json={"layout": draft})
        self.assertEqual(200, response.status_code, response.text)
        reopened = self.opened()["layout"]["windows"]
        self.assertEqual([0.0, 8.27], [w["start"] for w in reopened])
        self.assertEqual("person", reopened[1]["by"])

    def test_a_scene_past_the_clip_end_is_refused(self):
        draft = self.opened()["layout"]
        draft["windows"] = [draft["windows"][0], draft["windows"][0] | {"start": 31.0}]
        self.assertEqual(422, self.client.put(f"/api/clips/{self.clip_id}/layout", json={"layout": draft}).status_code)


class FocusTest(LayoutApiTestCase):
    """AI 초점(tease §5-10 "포커스 규칙") — 장면마다 사람을 찾아 창을 맞춘다. 저장하지 않는다."""

    CLOSE_UP = {"scene": 1, "people": [{"box_2d": [53, 362, 1000, 804], "head_2d": [52, 483, 501, 680]}], "speaker": 0}

    def focus(self, draft: dict, **model) -> object:
        with (
            mock.patch.object(ffmpeg, "frame_jpeg", return_value=b"jpeg"),
            mock.patch.object(gemini, "generate_json", **model) as called,
        ):
            response = self.client.post(f"/api/clips/{self.clip_id}/layout/focus", json={"layout": draft})
        self.called = called
        return response

    def test_the_draft_gets_ai_windows_without_saving(self):
        reply = (json.dumps({"scenes": [self.CLOSE_UP]}), {"input_tokens": 900}, 1200)
        response = self.focus(self.opened()["layout"], return_value=reply)
        self.assertEqual(200, response.status_code, response.text)
        body = response.json()
        window = body["layout"]["windows"][0]
        self.assertEqual("ai", window["by"])
        # 원본 가운데(0.5)가 아니라 사람의 가운데(0.582)다.
        self.assertAlmostEqual(0.582, window["x"] + window["w"] / 2, places=2)
        self.assertEqual("one", body["focus"]["scenes"][0]["how"])
        self.assertIsNone(self.stored("select layout from clips where id = %s", self.clip_id)["layout"])
        self.assertEqual(1, self.stored("select count(*) as n from stage_calls where stage = 'focus'")["n"])

    def test_a_model_failure_is_a_bad_gateway_that_says_why(self):
        response = self.focus(self.opened()["layout"], side_effect=gemini.GeminiError("일일 할당량"))
        self.assertEqual(502, response.status_code)
        self.assertIn("일일 할당량", response.json()["detail"])


class SeriesTest(LayoutApiTestCase):
    def test_a_series_name_is_unique(self):
        self.assertEqual(200, self.client.post("/api/series", json={"name": "왓츠인 마이 AI"}).status_code)
        self.assertEqual(409, self.client.post("/api/series", json={"name": "왓츠인 마이 AI"}).status_code)

    def test_a_badge_image_is_stored_relative_and_served(self):
        response = self.client.put(
            f"/api/series/{self.series_id}/badge", content=PNG, headers={"Content-Type": "image/png"}
        )
        self.assertEqual(200, response.status_code, response.text)
        path = self.stored("select badge_path from series where id = %s", self.series_id)["badge_path"]
        # 🔴 DB 의 파일 경로는 work_dir 기준 상대경로다.
        self.assertTrue(path.startswith("badges/series"), path)
        self.assertEqual(PNG, self.client.get(response.json()["badgeUrl"]).content)

    def test_a_file_that_is_not_the_image_it_claims_is_refused(self):
        response = self.client.put(
            f"/api/series/{self.series_id}/badge", content=b"<svg/>", headers={"Content-Type": "image/png"}
        )
        self.assertEqual(415, response.status_code)

    def test_replacing_a_badge_removes_the_old_file(self):
        self.client.put(f"/api/series/{self.series_id}/badge", content=PNG, headers={"Content-Type": "image/png"})
        old = self.stored("select badge_path from series where id = %s", self.series_id)["badge_path"]
        self.client.put(
            f"/api/series/{self.series_id}/badge", content=PNG + b"x", headers={"Content-Type": "image/png"}
        )
        self.assertFalse(deps.cfg.work_file(old).exists())
        self.client.delete(f"/api/series/{self.series_id}/badge")
        self.assertIsNone(self.stored("select badge_path from series where id = %s", self.series_id)["badge_path"])


class ReadonlyTest(LayoutApiTestCase):
    passwords = (ADMIN, READONLY)

    def login(self, password: str) -> None:
        self.assertEqual(200, self.client.post("/auth/login", json={"password": password}).status_code)

    def test_readonly_can_open_but_not_draw_or_save(self):
        self.login(READONLY)
        draft = self.opened()["layout"]
        self.assertEqual(403, self.client.post(f"/api/clips/{self.clip_id}/layout/preview", json={"layout": draft}).status_code)
        self.assertEqual(403, self.client.put(f"/api/clips/{self.clip_id}/layout", json={"layout": draft}).status_code)
        # 장면 나누기도 ffmpeg 를 돌리는 행동이다.
        with mock.patch.object(ffmpeg, "scene_scores") as probe:
            self.assertEqual(
                403, self.client.post(f"/api/clips/{self.clip_id}/layout/scenes", json={"layout": draft}).status_code
            )
        probe.assert_not_called()
        # AI 초점은 유료 호출이다.
        with mock.patch.object(gemini, "generate_json") as model:
            self.assertEqual(
                403, self.client.post(f"/api/clips/{self.clip_id}/layout/focus", json={"layout": draft}).status_code
            )
        model.assert_not_called()
        self.still.assert_not_called()

    def test_readonly_sees_a_card_an_admin_already_drew(self):
        self.login(ADMIN)
        draft = self.opened()["layout"]
        url = self.client.post(f"/api/clips/{self.clip_id}/layout/preview", json={"layout": draft}).json()["cardUrl"]
        self.client.post("/auth/logout")
        self.login(READONLY)
        self.assertEqual(200, self.client.get(url).status_code)


if __name__ == "__main__":
    unittest.main()
