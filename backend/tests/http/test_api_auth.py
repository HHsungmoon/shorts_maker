"""API 경계 — 무엇이 인증 없이 열려 있는가.

이 파일이 지키는 것은 하나다: **backend(Spring) 프록시가 사라진 뒤에도 /api/** 가 닫혀
있는가.** 예전에는 도커 네트워크가 울타리였고 코드는 열려 있어도 괜찮았다. 지금은
코드가 유일한 울타리다.

라우터가 둘이 되면서(2026-09-06) 규칙이 하나 늘었다 — 앱의 모든 `/api/**` 는 스튜디오
라우터(인증 있음) **또는** 시청자 라우터(인증 없음) 소속이어야 하고, 시청자 라우터의 경로는
전부 `/api/watch/` 로 시작해야 한다. 스튜디오 엔드포인트를 실수로 시청자 라우터에 넣으면
무인증으로 새는데, 경로 접두사 검사가 그걸 잡는다.
"""

import dataclasses
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from fastapi.routing import APIRoute

from shorts_maker.db import store
from shorts_maker.http import server, auth, deps, studio, watch

from ..support import make_config, reset_db

PASSWORD = "test-admin-password"
TOKEN = "test-machine-token"


class ApiAuthTestCase(unittest.TestCase):
    """cfg 를 갈아끼운 앱으로 테스트한다.

    라우트들이 모듈 전역 `deps.cfg` 를 호출 시점에 읽으므로 이 교체가 실제 경로에 반영된다.
    DB 는 비운 테스트 Postgres(make_config 가 database_url 을 shorts_test 로 맞춘다) — 200 을
    기대하는 `/api/sources` 가 실제로 테이블을 읽으므로 스키마가 있어야 한다.
    """

    admin_password = PASSWORD
    api_token = ""

    def setUp(self):
        reset_db()
        auth.reset_throttle()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        base = make_config(
            root,
            web_dir=root / "nonexistent-dist",
            admin_password=self.admin_password,
            api_token=self.api_token,
            cookie_secure=False,
        )
        self._saved = deps.cfg
        deps.cfg = base
        self.client = TestClient(server.app)

    def tearDown(self):
        deps.cfg = self._saved
        self._tmp.cleanup()
        auth.reset_throttle()

    def login(self) -> None:
        response = self.client.post("/auth/login", json={"password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)


class ClosedByDefaultTest(ApiAuthTestCase):
    def test_api_is_refused_without_credentials(self):
        for path in ("/api/sources", "/api/status", "/api/media", "/api/jobs"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)

    def test_writes_are_refused_too(self):
        # 조회만 막고 실행을 열어두면 남이 서버에서 STT 와 Gemini 를 돌릴 수 있다.
        response = self.client.post("/api/sources/1/rank", json={"criteria": "x"})
        self.assertEqual(response.status_code, 401)

    def test_health_stays_public(self):
        # 도커 헬스체크와 nginx 가 자격증명 없이 부른다.
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_health_leaks_no_configuration(self):
        # 🔴 예전 /health 는 모델명·스키마 버전을 담았다. 그때는 도커 네트워크 안이었지만
        # 지금은 인터넷에 열려 있다.
        body = self.client.get("/health").json()
        for leaked in ("geminiKey", "geminiModel", "whisperModel", "schema"):
            self.assertNotIn(leaked, body)

    def test_a_session_cookie_opens_the_api(self):
        self.login()
        self.assertEqual(self.client.get("/api/sources").status_code, 200)

    def test_logout_closes_it_again(self):
        self.login()
        self.client.post("/auth/logout")
        self.assertEqual(self.client.get("/api/sources").status_code, 401)

    def test_a_forged_cookie_does_not_open_it(self):
        self.client.cookies.set(auth.COOKIE_NAME, "99999999999.not-a-real-signature")
        self.assertEqual(self.client.get("/api/sources").status_code, 401)


# 경로 변수에 넣을 더미 값. 401 은 본문·경로 검증 **전에** 나야 하므로 값이 실제로 존재하는지는
# 상관없지만, 타입은 맞춰서 422 가 먼저 튀지 않게 한다.
PATH_VALUES = {"name": "x.mp4", "job_id": "abc"}


def _fill(path: str) -> str:
    out = path
    for name, value in PATH_VALUES.items():
        out = out.replace("{" + name + "}", value)
    import re

    return re.sub(r"\{[a-z_]+\}", "1", out)


class EveryStudioRouteIsClosedTest(ApiAuthTestCase):
    """🔴 라우트 테이블을 **순회**한다. 몇 개 골라 확인하는 방식은 새 엔드포인트를 놓친다 —
    v8 이전에 그렇게 `/api/segments/{id}/preview` 가 열린 채로 며칠 있었다."""

    def test_the_router_has_routes(self):
        # 순회 테스트가 빈 목록을 돌며 통과하는 것을 막는다.
        self.assertGreaterEqual(len(studio.router.routes), 20)

    def test_every_route_and_method_returns_401_without_credentials(self):
        for route in studio.router.routes:
            assert isinstance(route, APIRoute)
            for method in route.methods:
                with self.subTest(method=method, path=route.path):
                    response = self.client.request(method, _fill(route.path), json={})
                    self.assertEqual(response.status_code, 401, response.text)

    def test_every_api_route_of_the_app_belongs_to_one_of_the_two_routers(self):
        # server.py 에 `/api/...` 를 직접 등록하면 라우터 레벨 인증이 안 걸린다. 그 실수를 잡는다.
        known = {(r.path, m) for router in (studio.router, watch.router) for r in router.routes for m in r.methods}
        for route in server.app.routes:
            if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
                continue
            for method in route.methods:
                with self.subTest(method=method, path=route.path):
                    self.assertIn((route.path, method), known)

    def test_the_public_router_only_owns_the_watch_prefix(self):
        # 🔴 스튜디오 엔드포인트를 시청자 라우터에 잘못 넣으면 무인증으로 샌다. 경로가 그 실수를 막는다 —
        # `/api/watch/` 밖의 경로가 저기 있으면 여기서 걸린다.
        for route in watch.router.routes:
            assert isinstance(route, APIRoute)
            with self.subTest(path=route.path):
                self.assertTrue(route.path.startswith("/api/watch/"), route.path)

    def test_the_two_routers_do_not_overlap(self):
        studio_paths = {r.path for r in studio.router.routes}
        watch_paths = {r.path for r in watch.router.routes}
        self.assertEqual(studio_paths & watch_paths, set())

    def test_the_public_router_is_reachable_without_credentials(self):
        # 반대 방향의 실수: 시청자 엔드포인트를 스튜디오 라우터에 넣으면 인증이 걸려 시청자가 못 쓴다.
        # 401 만 아니면 된다 — 404(없는 id)나 422(본문 없음)는 정상이다.
        for route in watch.router.routes:
            assert isinstance(route, APIRoute)
            for method in route.methods:
                with self.subTest(method=method, path=route.path):
                    response = self.client.request(method, _fill(route.path), json={})
                    self.assertNotEqual(response.status_code, 401, response.text)

    def test_the_only_routes_outside_api_are_health_auth_and_the_frontend(self):
        public = sorted(
            r.path for r in server.app.routes
            if isinstance(r, APIRoute) and not r.path.startswith("/api/")
        )
        self.assertEqual(
            public, ["/auth/login", "/auth/logout", "/auth/me", "/debug", "/health", "/{full_path:path}"]
        )


class LoginTest(ApiAuthTestCase):
    def test_a_wrong_password_is_refused(self):
        self.assertEqual(
            self.client.post("/auth/login", json={"password": "wrong"}).status_code, 401
        )

    def test_repeated_failures_get_locked_out(self):
        for _ in range(auth.MAX_FAILURES):
            self.client.post("/auth/login", json={"password": "wrong"})
        # 🔴 잠긴 뒤에는 **올바른 비밀번호도** 거부해야 한다. 안 그러면 잠금이 무의미하다.
        response = self.client.post("/auth/login", json={"password": PASSWORD})
        self.assertEqual(response.status_code, 429)

    def test_me_reports_the_state_without_erroring(self):
        self.assertEqual(
            self.client.get("/auth/me").json(), {"authenticated": False, "authRequired": True}
        )
        self.login()
        self.assertEqual(
            self.client.get("/auth/me").json(), {"authenticated": True, "authRequired": True}
        )

    def test_the_cookie_is_not_readable_by_scripts(self):
        self.login()
        header = self.client.post("/auth/login", json={"password": PASSWORD}).headers["set-cookie"]
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=lax", header)


class MachineTokenTest(ApiAuthTestCase):
    api_token = TOKEN

    def test_a_valid_token_opens_the_api(self):
        response = self.client.get("/api/sources", headers={"X-Shorts-Token": TOKEN})
        self.assertEqual(response.status_code, 200)

    def test_a_wrong_token_does_not(self):
        response = self.client.get("/api/sources", headers={"X-Shorts-Token": "nope"})
        self.assertEqual(response.status_code, 401)

    def test_the_cookie_still_works_alongside_it(self):
        self.login()
        self.assertEqual(self.client.get("/api/sources").status_code, 200)


class LocalDevModeTest(ApiAuthTestCase):
    """비밀번호도 토큰도 없는 상태 = 루프백 전용 로컬 개발. 인증하지 않는다."""

    admin_password = ""
    api_token = ""

    def test_the_api_is_open(self):
        self.assertEqual(self.client.get("/api/sources").status_code, 200)

    def test_the_frontend_is_told_no_login_is_needed(self):
        self.assertEqual(
            self.client.get("/auth/me").json(), {"authenticated": True, "authRequired": False}
        )

    def test_binding_beyond_loopback_is_refused_in_this_state(self):
        # 🔴 이 조합(인증 없음 + 외부 바인딩)이 사고 경로다. 기동 자체를 막는다.
        deps.cfg = dataclasses.replace(deps.cfg, api_host="0.0.0.0")
        with self.assertRaises(SystemExit):
            server.check_binding()

    def test_loopback_binding_is_allowed(self):
        deps.cfg = dataclasses.replace(deps.cfg, api_host="127.0.0.1")
        server.check_binding()


class SpaRoutingTest(ApiAuthTestCase):
    def test_unknown_api_paths_do_not_fall_through_to_the_frontend(self):
        # 🔴 catch-all 이 /api/** 를 삼키면 없는 엔드포인트가 index.html 로 200 을 받고,
        # 프론트는 HTML 을 JSON 으로 파싱하다 엉뚱한 곳에서 죽는다.
        self.login()
        for path in ("/api/nope", "/auth/nope"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)



class ClipOriginTest(ApiAuthTestCase):
    """🔴 클립이 **어디서 나왔는지**가 응답에 있어야 한다.

    질문에 답한 클립과 크리에이터가 자기 기준으로 뽑은 클립이 한 목록에 섞인다. "클립 3" 이라는
    이름만으로는 구분할 수 없어서 화면이 둘을 갈라 보여줄 수가 없었다(2026-09-08).
    """

    def seed(self) -> int:
        with store.connect(deps.cfg.database_url) as conn:
            source_id = conn.execute(
                "insert into sources (title, content_type, path, fingerprint, status)"
                " values ('강연', 'LECTURE', 'a.mp4', 'sha256:a', 'DONE') returning id"
            ).fetchone()["id"]
            chunk_id = conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path)"
                " values (%s, 0, 0, 600, 'c.wav') returning id", (source_id,)
            ).fetchone()["id"]
            segment_id = conn.execute(
                "insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx,"
                " end_utterance_idx) values (%s, 0, 0, 100, 0, 3) returning id", (chunk_id,)
            ).fetchone()["id"]
            # ① 크리에이터 기준으로 뽑은 클립
            criteria_run = conn.execute(
                "insert into runs (source_id, criteria_prompt) values (%s, '회사 문화는?') returning id",
                (source_id,),
            ).fetchone()["id"]
            conn.execute(
                "insert into clips (run_id, segment_id, start_sec, end_sec) values (%s, %s, 0, 30)",
                (criteria_run, segment_id),
            )
            # ② 시청자 질문에 답한 클립
            cluster_id = conn.execute(
                "insert into question_clusters (source_id, canonical_text) values (%s, '연봉은?')"
                " returning id", (source_id,)
            ).fetchone()["id"]
            for viewer in ("a", "b"):
                conn.execute(
                    "insert into questions (source_id, text, viewer_id, cluster_id)"
                    " values (%s, '연봉 얼마', %s, %s)", (source_id, viewer, cluster_id)
                )
            answer_run = conn.execute(
                "insert into runs (source_id, criteria_prompt, route) values (%s, '연봉은?', 'retrieval')"
                " returning id", (source_id,)
            ).fetchone()["id"]
            conn.execute(
                """insert into clips (run_id, segment_id, start_sec, end_sec, question_cluster_id)
                   values (%s, %s, 0, 30, %s)""",
                (answer_run, segment_id, cluster_id),
            )
            conn.commit()
        return source_id

    def test_each_clip_says_where_it_came_from(self):
        source_id = self.seed()
        self.login()
        clips = self.client.get(f"/api/sources/{source_id}").json()["clips"]
        by_question = {c["question"]: c for c in clips}
        self.assertEqual(set(by_question), {None, "연봉은?"})
        # 질문에서 나온 클립은 몇 명이 물었는지까지 온다.
        self.assertEqual(by_question["연봉은?"]["asked_by"], 2)
        self.assertEqual(by_question["연봉은?"]["route"], "retrieval")
        # 기준에서 나온 클립은 그때 쓴 기준 문장이 이름이 된다.
        self.assertEqual(by_question[None]["criteria_prompt"], "회사 문화는?")
        self.assertIsNone(by_question[None]["route"])



class ClipTitleEditTest(ApiAuthTestCase):
    """제목은 크리에이터가 고칠 수 있어야 한다 — 기본값(질문·구간 설명)은 제목으로 쓰라고 쓴 문장이 아니다."""

    def seed(self) -> int:
        with store.connect(deps.cfg.database_url) as conn:
            source_id = conn.execute(
                "insert into sources (title, content_type, path, fingerprint, status)"
                " values ('강연', 'LECTURE', 'a.mp4', 'sha256:t', 'DONE') returning id"
            ).fetchone()["id"]
            chunk_id = conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path)"
                " values (%s, 0, 0, 600, 'c.wav') returning id", (source_id,)
            ).fetchone()["id"]
            segment_id = conn.execute(
                "insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx,"
                " end_utterance_idx) values (%s, 0, 0, 100, 0, 3) returning id", (chunk_id,)
            ).fetchone()["id"]
            run_id = conn.execute(
                "insert into runs (source_id) values (%s) returning id", (source_id,)
            ).fetchone()["id"]
            clip_id = conn.execute(
                "insert into clips (run_id, segment_id, start_sec, end_sec, title)"
                " values (%s, %s, 0, 30, '옛 제목') returning id", (run_id, segment_id)
            ).fetchone()["id"]
            conn.commit()
        return clip_id

    def test_the_title_can_be_replaced(self):
        clip_id = self.seed()
        self.login()
        response = self.client.patch(f"/api/clips/{clip_id}", json={"title": "  새 제목  "})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["title"], "새 제목")

    def test_a_blank_or_overlong_title_is_refused(self):
        clip_id = self.seed()
        self.login()
        for title in ("", "   ", "가" * 201):
            with self.subTest(title=title[:8]):
                response = self.client.patch(f"/api/clips/{clip_id}", json={"title": title})
                self.assertIn(response.status_code, (400, 422))

    def test_editing_a_missing_clip_is_404(self):
        self.login()
        self.assertEqual(self.client.patch("/api/clips/9999", json={"title": "x"}).status_code, 404)



class SourceListingTest(ApiAuthTestCase):
    """홈 화면이 카드 하나에 진행 상황을 그리려면 목록에 그 수치가 있어야 한다.

    🔴 영상마다 상세를 따로 부르면 N+1 이다 — 영상이 열 개만 돼도 홈이 느려진다.
    """

    def seed(self) -> int:
        with store.connect(deps.cfg.database_url) as conn:
            source_id = conn.execute(
                "insert into sources (title, content_type, path, fingerprint, status, published)"
                " values ('강연', 'LECTURE', 'a.mp4', 'sha256:L', 'DONE', true) returning id"
            ).fetchone()["id"]
            chunk_id = conn.execute(
                "insert into chunks (source_id, idx, start_sec, end_sec, path)"
                " values (%s, 0, 0, 600, 'c.wav') returning id", (source_id,)
            ).fetchone()["id"]
            for idx in range(3):
                conn.execute(
                    "insert into utterances (chunk_id, idx, start_sec, end_sec, text)"
                    " values (%s, %s, %s, %s, '말')", (chunk_id, idx, idx * 10, idx * 10 + 10)
                )
            segment_id = conn.execute(
                "insert into segments (chunk_id, idx, start_sec, end_sec, start_utterance_idx,"
                " end_utterance_idx) values (%s, 0, 0, 30, 0, 2) returning id", (chunk_id,)
            ).fetchone()["id"]
            cluster_id = conn.execute(
                "insert into question_clusters (source_id, canonical_text) values (%s, '연봉은?')"
                " returning id", (source_id,)
            ).fetchone()["id"]
            for viewer in ("a", "b"):
                conn.execute(
                    "insert into questions (source_id, text, viewer_id, cluster_id)"
                    " values (%s, '연봉', %s, %s)", (source_id, viewer, cluster_id)
                )
            run_id = conn.execute(
                "insert into runs (source_id) values (%s) returning id", (source_id,)
            ).fetchone()["id"]
            conn.execute(
                "insert into clips (run_id, segment_id, start_sec, end_sec, published_at)"
                " values (%s, %s, 0, 30, now())", (run_id, segment_id)
            )
            conn.commit()
        return source_id

    def test_the_listing_carries_the_progress_of_each_video(self):
        self.seed()
        self.login()
        found = self.client.get("/api/sources").json()[0]
        self.assertEqual(found["chunk_count"], 1)
        self.assertEqual(found["utterance_count"], 3)
        self.assertEqual(found["segment_count"], 1)
        self.assertEqual(found["question_count"], 2)
        self.assertEqual(found["open_cluster_count"], 1)
        self.assertEqual(found["clip_count"], 1)
        self.assertEqual(found["published_clip_count"], 1)

    def test_a_video_with_nothing_done_reports_zeros(self):
        with store.connect(deps.cfg.database_url) as conn:
            conn.execute(
                "insert into sources (title, content_type, path, fingerprint, status)"
                " values ('새 영상', 'LECTURE', 'b.mp4', 'sha256:N', 'DONE')"
            )
            conn.commit()
        self.login()
        found = self.client.get("/api/sources").json()[0]
        for key in ("chunk_count", "utterance_count", "segment_count", "question_count",
                    "open_cluster_count", "clip_count", "published_clip_count"):
            with self.subTest(key=key):
                self.assertEqual(found[key], 0)


if __name__ == "__main__":
    unittest.main()
